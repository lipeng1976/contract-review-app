-- Contract Review App schema (本机 PostgreSQL 18.4, port 5434)
-- 报告按 A-G 拆分为 7 个物理分区，逻辑上仍是一张 report_items 表。

BEGIN;

-- Least-privilege role used by the API (RLS depends on it).
-- Password is intentionally NOT set here: inject it from your secret store after
-- initdb, e.g. psql -c "ALTER ROLE app_api PASSWORD :'api_password'".
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_api') THEN
    CREATE ROLE app_api LOGIN;
  END IF;
END
$$;

CREATE SEQUENCE IF NOT EXISTS report_items_id_seq;

CREATE TABLE IF NOT EXISTS users (
    uid            BIGSERIAL PRIMARY KEY,
    username       TEXT UNIQUE NOT NULL,   -- 登录账号：不限手机号，字母/数字/点/下划线/连字符
    password_hash  TEXT NOT NULL,
    display_name   TEXT,
    role           TEXT NOT NULL DEFAULT 'viewer',  -- 主角色标签；权限以 user_roles 为准
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','rejected','suspended')),
    approved_by    BIGINT REFERENCES users(uid),
    approved_at    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT users_role_ck CHECK (char_length(role) BETWEEN 1 AND 64)
);

-- ── RBAC：权限点 / 角色 / 角色权限 / 用户角色 ──────────────────────────────
CREATE TABLE IF NOT EXISTS permissions (
    code      TEXT PRIMARY KEY,
    category  TEXT NOT NULL,
    label_zh  TEXT NOT NULL,
    label_en  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS roles (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT UNIQUE NOT NULL,
    label_zh     TEXT NOT NULL,
    label_en     TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    is_system    BOOLEAN NOT NULL DEFAULT false,
    is_superuser BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by   BIGINT
);
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id   BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    perm_code TEXT NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
    PRIMARY KEY (role_id, perm_code)
);
CREATE TABLE IF NOT EXISTS user_roles (
    uid     BIGINT NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
    role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (uid, role_id)
);


CREATE TABLE IF NOT EXISTS contracts (
    id                  BIGSERIAL PRIMARY KEY,
    contract_no         TEXT UNIQUE NOT NULL,
    owner_id            BIGINT NOT NULL REFERENCES users(uid),
    address             TEXT,
    source_path         TEXT,
    source_sha256       TEXT,
    status              TEXT NOT NULL DEFAULT 'queued',
    report_version      INT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at         TIMESTAMPTZ,              -- NULL = 在库；非 NULL = 已归档
    archived_by         BIGINT REFERENCES users(uid),
    -- 从报告 A/C 节提升的可检索标量
    property_address    TEXT,
    title_volume        TEXT,
    title_folio         TEXT,
    plan_number         TEXT,
    lot_number          TEXT,
    registered_owner    TEXT,
    vendor_name         TEXT,
    land_area_sqm       NUMERIC(10,2),
    perimeter_m         NUMERIC(10,2),
    council_rates_amt   NUMERIC(12,2),
    water_charges_amt   NUMERIC(12,2)
);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id            BIGSERIAL PRIMARY KEY,
    contract_no   TEXT NOT NULL REFERENCES contracts(contract_no),
    attempt       INT NOT NULL DEFAULT 1,
    status        TEXT NOT NULL,
    model_id      TEXT,
    prompt_version TEXT,
    worker_id     TEXT,
    error_code    TEXT,
    error_msg     TEXT,
    stage         TEXT,
    stage_msg     TEXT,
    stage_at      TIMESTAMPTZ,
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    lease_until   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS contract_reports (
    id             BIGSERIAL PRIMARY KEY,
    contract_no    TEXT NOT NULL REFERENCES contracts(contract_no),
    job_id         BIGINT REFERENCES analysis_jobs(id),
    version        INT NOT NULL DEFAULT 1,
    overall_status TEXT,
    followups_json JSONB,
    other_json     JSONB,
    bullets_json   JSONB,
    note_box       JSONB,
    report_path    TEXT,
    report_sha256  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    edited_at      TIMESTAMPTZ,          -- 人工修改过此版本报告的时间；NULL = 原始生成
    edit_count     INT NOT NULL DEFAULT 0,
    UNIQUE (contract_no, version)
);

CREATE TABLE IF NOT EXISTS report_items (
    id            BIGINT DEFAULT nextval('report_items_id_seq') NOT NULL,
    report_id     BIGINT NOT NULL REFERENCES contract_reports(id),
    section       CHAR(1) NOT NULL CHECK (section IN ('A','B','C','D','E','F','G')),
    section_title TEXT NOT NULL,
    code          TEXT NOT NULL,
    item          TEXT NOT NULL,
    provided      TEXT,
    verified      TEXT,
    contents_json JSONB NOT NULL,
    contents_text TEXT,
    pages         TEXT[],
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    edited_fields TEXT[] NOT NULL DEFAULT '{}',  -- 被人工改过的列名，导出时斜体深蓝
    edited_at     TIMESTAMPTZ,
    PRIMARY KEY (id, section)
) PARTITION BY LIST (section);

CREATE TABLE report_items_a PARTITION OF report_items FOR VALUES IN ('A');
CREATE TABLE report_items_b PARTITION OF report_items FOR VALUES IN ('B');
CREATE TABLE report_items_c PARTITION OF report_items FOR VALUES IN ('C');
CREATE TABLE report_items_d PARTITION OF report_items FOR VALUES IN ('D');
CREATE TABLE report_items_e PARTITION OF report_items FOR VALUES IN ('E');
CREATE TABLE report_items_f PARTITION OF report_items FOR VALUES IN ('F');
CREATE TABLE report_items_g PARTITION OF report_items FOR VALUES IN ('G');

COMMENT ON TABLE report_items_a IS 'A. PROPERTY & TITLE INFORMATION - 物业与产权信息';
COMMENT ON TABLE report_items_b IS 'B. PLANNING, ZONING & GOVERNMENT - 规划/分区/政府';
COMMENT ON TABLE report_items_c IS 'C. RATES, CHARGES & OUTGOINGS - 费率/税费/支出';
COMMENT ON TABLE report_items_d IS 'D. BUILDING, CONSTRUCTION & CONDITION - 建筑/施工/状况';
COMMENT ON TABLE report_items_e IS 'E. LEASES, OCCUPANCY & THIRD PARTY INTERESTS - 租赁/占用/第三方权益';
COMMENT ON TABLE report_items_f IS 'F. RISKS, LITIGATION & SPECIAL MATTERS - 风险/诉讼/特别事项';
COMMENT ON TABLE report_items_g IS 'G. REQUIRED DOCUMENTS & ATTACHMENTS - 必备文件与附件';

-- 管理员在 App 里可视配置的大模型接口（Worker 每次任务实时读取，热更新）
CREATE TABLE IF NOT EXISTS llm_config (
    id            INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    base_url      TEXT NOT NULL DEFAULT '',
    api_key       TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    model_vision  TEXT NOT NULL DEFAULT '',
    timeout       INT NOT NULL DEFAULT 120,
    max_tokens    INT NOT NULL DEFAULT 8192,
    enabled       BOOLEAN NOT NULL DEFAULT true,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by    BIGINT REFERENCES users(uid)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          BIGSERIAL PRIMARY KEY,
    actor_uid   BIGINT,
    action      TEXT NOT NULL,
    target_type TEXT,
    target_id   TEXT,
    detail      JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 去重按 owner 隔离：同一用户重复上传返回原合同；不同用户各自建单。
CREATE UNIQUE INDEX IF NOT EXISTS contracts_owner_sha256_key
    ON contracts (owner_id, source_sha256) WHERE source_sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contracts_address ON contracts (property_address);
CREATE INDEX IF NOT EXISTS idx_contracts_archived ON contracts (archived_at) WHERE archived_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contracts_title   ON contracts (title_volume, title_folio);
CREATE INDEX IF NOT EXISTS idx_reports_contract  ON contract_reports (contract_no, version);
CREATE INDEX IF NOT EXISTS idx_items_report_code ON report_items (report_id, code);
CREATE INDEX IF NOT EXISTS idx_items_pages       ON report_items USING GIN (pages);
CREATE INDEX IF NOT EXISTS idx_items_fts         ON report_items USING GIN (to_tsvector('simple', coalesce(contents_text,'')));
CREATE INDEX IF NOT EXISTS idx_items_verified    ON report_items (report_id, verified);
CREATE INDEX IF NOT EXISTS idx_jobs_status       ON analysis_jobs (status, lease_until);

-- RLS：用 current_setting('app.uid', true) 缺值时不报错（返回 NULL = 拒绝）
CREATE OR REPLACE FUNCTION app_uid() RETURNS BIGINT LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.uid', true), '')::bigint;
$$;

-- SECURITY DEFINER：函数体以建表者身份只读判定。
-- policy 表达式由普通用户求值，因此必须 GRANT EXECUTE；SET search_path 防劫持。
-- is_admin 只用于系统管理表（users / roles / audit / llm_config）；
-- has_perm 的 superuser 通配限定在 admin.* —— 超级管理员不会凭 flag 拿到业务权限。
CREATE OR REPLACE FUNCTION is_admin() RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id
        WHERE ur.uid = app_uid() AND r.is_superuser
          AND EXISTS (SELECT 1 FROM users u WHERE u.uid=app_uid() AND u.status='active')
    );
$$;
CREATE OR REPLACE FUNCTION has_perm(p_code TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM users u WHERE u.uid=app_uid() AND u.status='active')
    AND (
        EXISTS (SELECT 1 FROM user_roles ur JOIN role_permissions rp ON rp.role_id=ur.role_id
                WHERE ur.uid=app_uid() AND rp.perm_code=p_code)
        OR (p_code LIKE 'admin.%' AND EXISTS
            (SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id
             WHERE ur.uid=app_uid() AND r.is_superuser))
    );
$$;
GRANT EXECUTE ON FUNCTION is_admin() TO PUBLIC;
GRANT EXECUTE ON FUNCTION has_perm(TEXT) TO PUBLIC;

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE contract_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE role_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_roles ENABLE ROW LEVEL SECURITY;

CREATE POLICY users_read ON users FOR SELECT USING (uid = app_uid() OR is_admin());
CREATE POLICY users_insert ON users FOR INSERT WITH CHECK (uid = app_uid() AND role = 'viewer' AND status = 'pending');
CREATE POLICY users_admin_update ON users FOR UPDATE USING (is_admin()) WITH CHECK (is_admin());

-- 业务表不认 superuser 身份：admin 只能靠显式业务角色（operator/viewer/archivist）
-- 访问合同。归档件的只读例外仍由 contract.archive_view 控制；写策略仅放行 owner。
CREATE POLICY contracts_read ON contracts FOR SELECT USING (
    owner_id = app_uid()
    OR (archived_at IS NOT NULL AND has_perm('contract.archive_view')));
CREATE POLICY contracts_insert ON contracts FOR INSERT WITH CHECK (owner_id = app_uid());
CREATE POLICY contracts_update ON contracts FOR UPDATE USING (owner_id = app_uid());

CREATE POLICY jobs_read ON analysis_jobs FOR SELECT USING (
    EXISTS (SELECT 1 FROM contracts c WHERE c.contract_no = analysis_jobs.contract_no AND
      (c.owner_id = app_uid()
       OR (c.archived_at IS NOT NULL AND has_perm('contract.archive_view'))))
);
-- 客户端只能为自己的合同入队，且必须是初始状态；状态推进只由 Worker（超级用户连接）完成
CREATE POLICY jobs_insert ON analysis_jobs FOR INSERT WITH CHECK (
    status = 'queued' AND attempt = 1 AND
    EXISTS (SELECT 1 FROM contracts c WHERE c.contract_no = analysis_jobs.contract_no AND c.owner_id = app_uid())
);
CREATE POLICY reports_read ON contract_reports FOR SELECT USING (
    EXISTS (SELECT 1 FROM contracts c WHERE c.contract_no = contract_reports.contract_no AND
      (c.owner_id = app_uid()
       OR (c.archived_at IS NOT NULL AND has_perm('contract.archive_view'))))
);
CREATE POLICY items_read ON report_items FOR SELECT USING (
    EXISTS (SELECT 1 FROM contract_reports r WHERE r.id = report_items.report_id
            AND EXISTS (SELECT 1 FROM contracts c WHERE c.contract_no = r.contract_no
                        AND (c.owner_id = app_uid()
                             OR (c.archived_at IS NOT NULL AND has_perm('contract.archive_view')))))
);
CREATE POLICY audit_read ON audit_logs FOR SELECT USING (is_admin());

CREATE POLICY llm_config_read ON llm_config FOR SELECT USING (is_admin());
CREATE POLICY llm_config_write ON llm_config FOR UPDATE USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT, UPDATE ON llm_config TO app_api;

-- RBAC catalogs: readable by all active users, writable by admins only.
CREATE POLICY roles_read ON roles FOR SELECT USING (true);
CREATE POLICY permissions_read ON permissions FOR SELECT USING (true);
CREATE POLICY roleperms_read ON role_permissions FOR SELECT USING (true);
CREATE POLICY userroles_read ON user_roles FOR SELECT USING (uid = app_uid() OR is_admin());
CREATE POLICY roles_admin_write ON roles FOR ALL USING (is_admin()) WITH CHECK (is_admin());
CREATE POLICY roleperms_admin_write ON role_permissions FOR ALL USING (is_admin()) WITH CHECK (is_admin());
CREATE POLICY userroles_admin_write ON user_roles FOR ALL USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT, INSERT, UPDATE, DELETE ON roles TO app_api;
GRANT SELECT ON permissions TO app_api;
GRANT SELECT, INSERT, DELETE ON role_permissions TO app_api;
GRANT SELECT, INSERT, DELETE ON user_roles TO app_api;

COMMIT;
