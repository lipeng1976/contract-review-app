-- Idempotent seed data: capability catalog, built-in roles and role grants.
-- NO user accounts, NO passwords, NO API keys, NO business data.
-- Run AFTER db/schema.sql:  psql -d contract_review -f db/seed.sql
BEGIN;

INSERT INTO permissions (code,category,label_zh,label_en) VALUES
    ('contract.view','contracts','查看合同','View contracts'),
    ('contract.upload','contracts','上传并分析合同','Upload & analyze contracts'),
    ('contract.edit','contracts','编辑合同与核查条目','Edit contract data & checklist items'),
    ('contract.download','contracts','下载合同与报告','Download contracts & reports'),
    ('contract.archive','contracts','归档/取消归档合同','Archive & restore contracts'),
    ('contract.archive_view','contracts','查询归档合同','View archived contracts'),
    ('admin.users','admin','用户审批与授权','Approve users & assign roles'),
    ('admin.roles','admin','角色管理','Manage roles'),
    ('admin.llm','admin','大模型接口配置','Configure LLM API'),
    ('admin.audit','admin','操作日志','View operation log')
ON CONFLICT (code) DO NOTHING;

-- The admin role is a PURE system administrator: users, roles, operation log
-- and LLM configuration. Business rights must be granted by a separate role
-- (operator / viewer / archivist / custom) assigned in addition to admin.
INSERT INTO roles (name,label_zh,label_en,description,is_system,is_superuser) VALUES
    ('admin','超级管理员','Super Admin','系统管理：用户审批、角色、操作日志、大模型配置；不参与合同上传与维护',true,true),
    ('operator','操作员','Operator','上传合同、触发分析、查看并下载自己上传的合同',true,false),
    ('viewer','只读','Viewer','只能查看并下载合同，不能上传或分析',true,false),
    ('archivist','档案员','Archivist','只读查询归档合同与下载其报告，不能归档或修改',true,false)
ON CONFLICT (name) DO NOTHING;

INSERT INTO role_permissions (role_id,perm_code)
    SELECT r.id, p.code FROM roles r JOIN permissions p
      ON p.code IN ('admin.users','admin.roles','admin.llm','admin.audit')
    WHERE r.name='admin'
ON CONFLICT DO NOTHING;
INSERT INTO role_permissions (role_id,perm_code)
    SELECT r.id, p.code FROM roles r CROSS JOIN permissions p
    WHERE r.name='operator' AND p.code IN ('contract.view','contract.upload','contract.download')
ON CONFLICT DO NOTHING;
INSERT INTO role_permissions (role_id,perm_code)
    SELECT r.id, p.code FROM roles r CROSS JOIN permissions p
    WHERE r.name='viewer' AND p.code IN ('contract.view','contract.download')
ON CONFLICT DO NOTHING;
INSERT INTO role_permissions (role_id,perm_code)
    SELECT r.id, p.code FROM roles r CROSS JOIN permissions p
    WHERE r.name='archivist' AND p.code IN ('contract.view','contract.archive_view','contract.download')
ON CONFLICT DO NOTHING;
INSERT INTO llm_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

COMMIT;
