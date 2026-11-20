# 合同审查 App 技术方案（设计稿 v3.5）

日期：2026-11-18　状态：M0 + M1 + M2 + RBAC + 安卓 APK 已落地并端到端实测通过（M1 13 / M2 13 / admin 配置 11 / RBAC 35 = 72 项）；M3 复核门禁强化未做
参考流程：`property-contract-review-report` skill 处理 36 Hinkler Road 的实战（79 页 PDF → 82 行 A–G 清单 → 12 页 PDF 报告）

> **v3.5 实施更新（同日，RBAC + 安卓 App）**：
> - **RBAC 权限模型**：当前只有 admin/member 两角色，升级为「权限点 → 角色 → 用户」三层。6 个权限点（`contract.view/upload/download`、`admin.users/roles/llm`）；3 个内置角色（`admin` 超级管理员含全部权限 / `operator` 操作员可上传分析 / `viewer` 只读仅查看下载）；admin 可在 App 内新建自定义角色、勾选权限、分配给成员，改完即时生效（每次请求从库重读权限，不依赖 JWT）。
>   - 提权防护：非 superuser 不能授予 superuser 角色、不能停权 superuser；不能移除最后一个 superuser。系统角色不可删/改名（标签可改）；superuser 角色权限集锁定。
>   - 注册默认 viewer（最小权限），可申请 operator，不能自学申请 admin；admin 审批时定最终角色。
>   - 数据库：`roles`/`permissions`/`role_permissions`/`user_roles` 四表（幂等迁移 `db/migrate_rbac.py`）；`is_admin()` 改由「持 is_superuser 角色」判定，新增 `has_perm(code)`；**新表必须 GRANT 给 app_api**（只加 RLS policy 不够，同 llm_config 的坑）。
>   - 接口门禁：上传挂 `contract.upload`、查询挂 `contract.view`、下载挂 `contract.download`、管理接口分别挂 `admin.users/roles/llm`；`require_perm(code)` 依赖工厂。
> - **安卓 App（WebView 壳）**：`android/` 工程，复用 libretv 的无 Gradle 构建链（aapt2+javac+d8+apksigner，JDK17/SDK36）。`cn.contractreview.app` v1.3，默认地址 `http://192.168.3.80:8790/`，改后台地址有两条路：**登录页「修改服务器地址」链接**（走桥 `CRApp.serverConfig()`）或**长按页面空白处**（输入框内长按仍是系统粘贴菜单）；地址弹窗带「粘贴」按钮。允许明文 HTTP、返回键回退、网络错误弹地址框。上传/下载由原生补（`onShowFileChooser` + `CRApp.download()`）。底部菜单与双语由 H5 提供。产物 `android/dist/contract-review-v1.3.apk`（已签名，versionCode=4）。
>   - **撑满窗口 / 多机型适配**的口径（照抄伴学 Flutter 的分层）：撑满靠布局（等价 `Scaffold`= flex 外壳，`body` 不滚、内容区 `flex:1;min-height:0` 自己滚）；安全区靠容器（原生 `onApplyWindowInsets` 取 `max(navigationBars, ime)` 垫底——targetSdk 35 强制 edge-to-edge 后 WebView 的 `env(safe-area-inset-bottom)` 恒为 0，纯 CSS 修不了；`adjustResize` 也不再缩窗口，必须自己垫 IME）；宽窄自适应靠等分与 `max-width:720px`，不写死尺寸。
> - **H5 底部 TabBar**：登录后 6 个菜单——合同录入 / 合同查询 / 个人信息 / 功能一 / 功能二 / 功能三（后 3 个为「敬请期待」占位）。个人信息页含角色/权限展示 + 修改密码；admin 的管理面板（用户与角色 / 角色管理 / 大模型配置）置于个人信息页并按权限显隐；按 `contract.upload` 权限显隐上传入口、按 `contract.download` 显隐下载按钮。
> - **测试脚本修复**：`e2e_llm_config.py` 曾在真实库上清空 key 且无备份（已如实告知需重填）。现改为开头快照、finally 恢复，重复运行不再破坏真实配置。

> **v3.4 实施更新（同日，M2 + 三项增强）**：
> - **注册账号不再限手机号**：`users.phone` 列迁移为 `username`（幂等脚本 `db/migrate_username.py`），允许 3–64 位字母/数字/点/下划线/连字符，海外邮箱式账号可用；API 同时兼容 `username`/`phone` 字段，返回值两者都给。
> - **去重按 owner 隔离**：原全局 `UNIQUE(source_sha256)` 导致"A 上传过的 PDF，B 再传就 500"。改为部分唯一索引 `(owner_id, source_sha256)`（`db/migrate_dedup_scope.py`）：同一用户重复上传返回原合同，不同用户各自建单。
> - **前端中英文双语**：H5 顶部语言按钮一键切换，localStorage 持久化，按浏览器语言自动选择；静态文案、状态徽章、错误提示、A–G 章节标题全量双语。
> - **大模型接口 admin 可视配置**：新增 `llm_config` 表（`db/migrate_llm_config.py`，仅 admin 可读写，key 脱敏）；admin 在 App 内填 Base URL / API Key / Model / Vision Model，Worker 每次任务实时读库**热更新，无需重启**；留空 key 时任务进 `awaiting_llm` 优雅降级而非崩溃。
> - **M2 Worker 全链路**：`worker/` 目录实现 S0 原子领任务（租约+attempt+`SKIP LOCKED`，修正了 OR/AND 优先级导致的错领 bug）→ S1 PyMuPDF 逐页解析 → S3 OpenAI 兼容协议分批抽取（火山方舟/百炼/OpenAI 通用）→ S4 82 行校验 → S5 复用 skill 渲染器出 12 页 PDF → S6 门禁 → S7 A–G 分区入库。用 36 Hinkler Road replay 回归：79 页 → 82 行（A11/B8/C13/D14/E3/F3/G30）→ 12 页 PDF → 6 个关键项 Action 门禁 → 端到端查询+下载全通过。

> **v3.3 实施更新（同日）**：M1 的注册/审批/上传/入库/查询 API + 手机 H5 已写完并跑通。
> - 默认超级管理员：账号 `admin`（原 11 位手机号账号已由 `db/migrate_admin_account.py` 改名，uid=3 不变，密码不改）；用 `db/bootstrap_admin.py <account> <pw> [name]` 可幂等重建/提权。新注册成员默认 `pending`，需该管理员审批。
> - API 用 `app_api` 非超级用户角色连库，`SET LOCAL app.uid` 让 RLS 真生效；注册/登录/审计走 `server_conn()`（超级用户，因无 uid）。
> - 实测踩坑（已修）：① `require_admin(user: dict)` 会被 FastAPI 误判成请求体 → 依赖函数必须收 `Request`；② `analysis_jobs` 缺 INSERT 策略 → 补 `jobs_insert`（只允许 status=queued/attempt=1）；③ `db_status()` 重复传 `connect_timeout` 被 except 吞成假 down；④ `note_box` 是对象，字段必须 JSONB；⑤ 静态目录要存在且用 `from fastapi.staticfiles import StaticFiles` 正常导入。
> - 端到端脚本 `db/../tests/e2e_m1.py` 覆盖：注册→pending 门禁→管理员审批→上传→SHA-256 去重→明细→A/B/C…分节查询→精确状态筛选→原件下载→跨用户 RLS 隔离，**13/13 PASS**；H5 用无头 Edge 注入探针实测登录并渲染 A 节 11 条明细、7 个 tab。
> - H5 与 API 同源（`/` + `/api`），手机地址 `http://192.168.3.80:8790/`，前端全用相对路径——切云端只改反代，代码不动。

> **v3 相对 v2 的范围收窄（本版只按这个做）**：
> - **当前阶段只做局域网直连**：手机和笔记本在同一 Wi-Fi，手机浏览器直接访问笔记本内网地址。
> - **不考虑 Tailscale**（v2 已排除，本版不再讨论）。
> - **云端 frp 通道已打通，本版不展开**：将来要外网访问时，只改访问地址（见 2.3），后台和数据不动。
> - 后台 + PostgreSQL + 原件/报告全在笔记本这一点不变（v2 已定）。
>
> **v3.1 实测修正（M0 落地后）**：
> - **端口改 `8790`**：8787 是伴学 AI 网关、8788 是伴学数据服务、8000 也占用，均不能用。
> - **PostgreSQL 已装好**：不是装系统服务，而是从伴学的 PG 18.4 二进制复制出**独立实例**
>   （`contract-review-app\pg\`，数据 `pgdata\`，端口 **5434**），与伴学的 5433 实例完全隔离。
>   伴学用的是 `D:\easy_learn_home\pgsql` + `pgdata`（PG 18.4，库 `easylearn_db`，端口 5433）。

---

## 1. 目标与验收标准

### 目标
手机端完成：注册 → 管理员审批 → 上传合同原件 → 后台自动按 skill 出报告 → 入库 → 按唯一标识查询合同明细与报告。**当前阶段在同一 Wi-Fi 内可用。**

### 验收标准
1. 新注册账号默认 `pending`，未审批不能上传、不能查明细；审批由默认管理员在管理端完成。
2. 管理员可查看全部合同、审批成员、重跑分析；普通成员只能看自己上传的。
3. 每份合同有全局唯一 `contract_no`；同一 PDF 重复上传按 SHA-256 去重，不产生第二条记录。
4. 上传成功后自动生成分析任务，按 A–G 82 行输出报告；校验脚本通过（82 编号齐全、状态合法、页码存在、关键编号可检索）。
5. 明细页能查到：合同标识、物业地址、状态、原件、报告 PDF、结构化清单、跟进事项、审计记录。
6. 任务失败有明确错误码和重试入口，不允许静默失败。
7. 原件和报告仅授权用户可读；预览链接短期有效，不写进日志。
8. **手机与笔记本同 Wi-Fi 时，浏览器打开笔记本内网地址即可完整走通上述流程。**
9. **换云端地址时，前端不改代码、后台不改代码、数据库不迁移**——只改访问入口（见 2.3）。

---

## 2. 部署拓扑与已定方案

### 2.1 三个已定选择

| 项 | 选择 | 为什么不选别的 |
|---|---|---|
| 当前访问方式 | **局域网直连**：手机和笔记本同 Wi-Fi，浏览器访问 `http://<笔记本内网IP>:8790` | 云端通道已通但本阶段不用；Tailscale 已排除；局域网零配置、今天就能跑 |
| 数据库 | **本机 PostgreSQL（真实服务，非嵌入式）** | 审批状态、审计、SHA-256 唯一约束靠 DB 强保证；SQLite 并发写和权限要自己兜 |
| 后台计算 | **笔记本本地跑**（Python FastAPI 后台 + Worker） | 只有本地能真跑 PyMuPDF + skill 渲染脚本；无 Python worker 的托管服务做不到 |

### 2.2 网络链路（当前阶段）

```text
[手机浏览器]  同一 Wi-Fi
        │  http://<笔记本内网IP>:8790/    （前端页面 + API 同源）
        ▼
[笔记本 8790]  FastAPI：托管前端静态页 + 提供 /api/...
        ├── Worker：解析 PDF / 调 LLM / 渲染报告
        ├── PostgreSQL 5434（只绑 127.0.0.1，不对外）
        └── 本地磁盘：原件 / 报告 / 临时文件
```

**同源部署是关键决定**：FastAPI 同时托管前端 H5 静态页和 API，手机只访问一个地址。
好处是 **没有 CORS、Cookie 同源、换地址时前端一个字都不用改**。
后台生成报告预览链接时用相对路径或请求里的 Host，**不写死任何主机地址**（否则换地址就断链）。

### 2.3 将来切云端：只改访问入口

云端 frp 通道已打通，本阶段不配置、不依赖。将来要外网访问时：

1. 把云端反代指到本机 8790（frp 那条链路你们已有，按现成配置加一条映射即可）；
2. 手机改用云端域名访问，**浏览器书签换掉即可**；
3. 前端零改动（用相对路径请求 `/api/...`）；后台零改动（不写死 host）；DB 不迁移。

可选环境变量 `PUBLIC_BASE_URL`：仅当后台需要生成绝对链接时设置；留空则用相对路径。
**这是整套设计里唯一跟"地址"有关的开关**——放在 `config/.env`，不进 Git。

### 2.4 端形态与局域网的硬限制

手机用 **H5/浏览器页面**，鸿蒙 NEXT 无需装任何 App。

- ⚠️ **局域网 `http://192.168.x.x` 不是安全上下文，安卓 Chrome 不允许注册 Service Worker** →
  当前阶段只能"浏览器打开 + 加书签"，**不能算完整 PWA，也没有离线缓存/安装到桌面**。
- 将来切到云端 **https 域名**后，Service Worker 才能注册，那时再启用 PWA 能力。
- 换后端地址不用重出包——这正是 H5 相对 Flutter APK 的好处（伴学把地址用 `--dart-define` 烧进 APK，换地址要重构建）。

### 2.5 笔记本内网地址处理

- 用 `ipconfig` 查当前 WLAN IPv4（伴学当时是 `192.168.3.80`，**本系统请实测，不要照抄**）。
- 建议在路由器给笔记本做 **DHCP 静态分配/地址保留**，否则 IP 漂移会让手机书签失效。
- **后台 FastAPI 必须监听内网网卡**（`0.0.0.0:8790`），只绑 `127.0.0.1` 手机连不上；
  **PostgreSQL 相反，只绑 `127.0.0.1`，不对外**。两者别配反了。

---

## 3. 角色与权限矩阵

| 能力 | 未登录 | pending 已注册 | member（active） | admin |
|---|---|---|---|---|
| 注册 / 登录 | 允许 | — | — | — |
| 查看等待审批页 | — | 允许 | — | — |
| 上传合同 | 否 | **否** | 允许 | 允许 |
| 查看自己的合同 | 否 | 否 | 允许 | 允许 |
| 查看全部合同 | 否 | 否 | 否 | 允许 |
| 审批/拒绝新成员 | 否 | 否 | 否 | 允许 |
| 重跑分析 / 查看审计 | 否 | 否 | 否 | 允许 |
| 改自己的角色 | 否 | 否 | 否 | 否（仅管理端） |

**默认管理员 bootstrap（自建认证）**：
1. 部署时用一次性脚本 `bootstrap_admin.py` 建首个 admin（交互式输入账号密码，**密码不写进代码、不写进文档**）；
2. 首次登录强制改密；
3. 之后所有新账号由该管理员在 App 内审批通过才 active。
   管理员凭据用脚本建、只建一次，和「代码里写死管理员密码」是两回事。

**鉴权实现**：自签 JWT（HttpOnly Cookie + CSRF token），密码 bcrypt 哈希；
会话与角色在网关层校验，**并在数据层用 RLS 兜底**（见第 6 节），前端门禁只是第一道门。
局域网 http 下 Cookie 不能设 `Secure`，靠 HttpOnly + SameSite 防护（见第 10 节风险）。

---

## 4. 端到端处理流程

1. **注册**：用户填手机号 + 密码 → 建 `users` 行（`status='pending', role='member'`），密码 bcrypt 入库。
2. **审批**：管理员在「成员管理」看到 pending 列表 → 通过/拒绝；写 `approved_by / approved_at`，`audit_logs` 留痕。
3. **登录门禁**：登录后签发 JWT；每次请求解析 JWT 得到 uid + role；非 active 只返回等待页，所有 API 在网关层拦掉，RLS 再兜底。
4. **上传**：选择 PDF（≤100 MB、仅 `application/pdf`）→ **服务端**计算 SHA-256 → 命中已有记录直接返回既有 `contract_no` → 否则生成 `contract_no` → 原件写本机磁盘 `data/contracts/<no>/original.pdf` → 插 `contracts` + `analysis_jobs(status='queued')`。
5. **调度**：本机 Worker 领 queued 任务（带租约 + attempt，避免重复跑），状态转 `analyzing`。
6. **自动分析**：按第 7 节 8 段流水线执行，产出 `report.pdf` + `model.json`（**全部写本机磁盘，不需要签名 URL**）。
7. **复核门禁**：关键项出现 `Action/Unclear` 或置信度低 → 转 `review`，管理员确认后发布；否则直接 `completed`。
8. **入库**：`contract_reports` 写报告级结果与本机路径；82 行清单按 A–G 拆分写入 `report_items` 对应分区，A/C 节关键标量回填 `contracts` 便于检索；`contracts.status='completed'`，写审计。
9. **查询**：明细页按 `contract_no` / 地址 / 状态检索；报告走 API 鉴权后流式返回或短时预览链接（本机文件）。

---

## 5. 状态机

**用户**：`pending` → `active`（管理员通过） / `rejected`（管理员拒绝）；`active` → `suspended`（管理员停用）。

**合同任务**：`queued` → `analyzing` → `review` → `completed`；异常分支 `analyzing/review` → `failed`（可重试，attempt+1，最多 3 次）→ `queued`。

状态只能按上表流转；`completed` 不可直接改回 `queued`，必须新建一次分析版本（version+1），保留历史报告。

### 5.1 实现态：怎么一眼看出「在排队 / 在分析 / 已经死了」（v0.8.0-progress）

`analysis_jobs.status` 只有 `queued/analyzing/completed/failed/awaiting_llm`，单看它分不清
「5 秒后就开始」和「根本没有 Worker，永远跑不了」。**上传是纯异步入队**，
`POST /api/contracts` 写完 `contracts` + `analysis_jobs(queued)` 就返回，真正干活的是独立进程
`python -m worker.runner`（`claim_job` 用 `FOR UPDATE SKIP LOCKED` 原子领取）。没有这个进程，
任务就永远停在 `queued`。

| 观测到的组合 | 真实含义 | 界面 |
|---|---|---|
| `queued` + `job.started_at IS NULL` + 有存活心跳 | 真排队，Worker 在线 | 绿：排队中，几秒内领取 |
| 同上，但无存活心跳（`last_seen` > 45s 或 `status='stopped'`） | **Worker 没运行**，不会自动开始 | 黄：待分析 · Worker 未运行 |
| `analyzing` + `stage_age_seconds ≤ 600` | 正在跑（可能正等模型返回） | 绿闪：分析中 · S3 抽取 A 2/7 · 23 秒前 |
| `analyzing` + `stage_age_seconds > 600` | 疑似中断 | 黄：分析中断 + 「强制重新分析」 |
| `failed` | 真失败 | 红：失败 · `error_code` + `error_msg` |

判据的三条硬规则：

1. **心跳只回答「有没有人在等这份活」**。`worker_heartbeats` 由 Worker 独立线程每 10s 刷新
   （分析期间主循环阻塞在 `process()`，只靠 `set_stage` 会在等模型时断更）。API：
   `GET /api/system/worker-status`；`/api/health.components.worker` 报的是**进程存活**
   （`alive`/`not_running`），不再是「LLM 已配置」。
2. **停滞只看 `stage_age_seconds`，不掺心跳**：一次长模型调用心跳能空 2–5 分钟，掺进去必误报。
3. `queued` **不画进度条**——此刻没有「进度」可言，只有「有没有人在等」。
   `POST /retry?force=1` 仅在**持有该任务 lease 的 worker 已死**时放行（判「有没有任意 worker 活着」
   会把崩溃后重启的进程算成占用者，任务被锁死 120 分钟）。

---

## 6. 数据模型（本机 PostgreSQL）

### 6.1 设计原则：报告按 A–G 拆分入库

报告的 82 行清单**不做成一个 JSON blob**。按报告的 7 个章节拆开存，每章的语义就是章节标题
（例：`A. PROPERTY & TITLE INFORMATION` = 物业与产权信息）。

但 7 节的行结构完全相同（`code / item / provided / verified / contents[] / notes`），
所以采用 **一张逻辑表 + 7 个物理分区**：PostgreSQL 声明式分区 `report_items PARTITION BY LIST(section)`。
这样既是"A 表存物业产权、B 表存规划……"的语义拆分，又不用为 7 节各写一套 CRUD；
每个分区可单独索引、查询、备份，且用 `COMMENT ON TABLE` 把章节标题写进数据库元数据。

| 分区表 | 章节标题（报告原文） | 行数 | 存的语义 |
|---|---|---|---|
| `report_items_a` | A. PROPERTY & TITLE INFORMATION | 11 | 物业/产权一致性、产权卷宗、登记业主、抵押与警告、地役权、出售权限 |
| `report_items_b` | B. PLANNING, ZONING & GOVERNMENT | 8 | 道路通行、山火区、s199 规划证书、分区与 overlay、树木管制、道路征用、遗产/洪水 |
| `report_items_c` | C. RATES, CHARGES & OUTGOINGS | 13 | 费率税项、法定收费、分期合同、GAIC、市政/水费/土地税/暴利税 |
| `report_items_d` | D. BUILDING, CONSTRUCTION & CONDITION | 14 | 卖家风险保险、owner-builder、建筑/占用许可、服务接入、业主委员会 |
| `report_items_e` | E. LEASES, OCCUPANCY & THIRD PARTY INTERESTS | 3 | 租约/租赁/许可、可转让许可、空置交付 |
| `report_items_f` | F. RISKS, LITIGATION & SPECIAL MATTERS | 3 | 政府通知、农化/牲畜通知、强制征用 |
| `report_items_g` | G. REQUIRED DOCUMENTS & ATTACHMENTS | 30 | 产权图则、地役文书、各类证书、附件齐备性、文件完整性 |

### 6.2 表结构

```text
users             uid BIGSERIAL PK, phone TEXT UNIQUE, password_hash TEXT, display_name,
                  role(member|admin), status(pending|active|rejected|suspended),
                  approved_by, approved_at, created_at

contracts         id BIGINT PK, contract_no TEXT UNIQUE, owner_id BIGINT REFERENCES users(uid),
                  -- 合同级
                  address TEXT, status, source_path, source_sha256 TEXT UNIQUE,
                  report_version INT DEFAULT 0, created_at,
                  -- 从 A/C 节提升的可检索标量（避免按地址/产权/金额查全表扫 JSON）
                  property_address TEXT,          -- A1 物业地址
                  title_volume TEXT, title_folio TEXT,   -- A2 产权卷/册
                  plan_number TEXT, lot_number TEXT,     -- A2 图则号/地块号
                  registered_owner TEXT, vendor_name TEXT, -- A3 登记业主/卖方
                  land_area_sqm NUMERIC(10,2), perimeter_m NUMERIC(10,2), -- A5
                  council_rates_amt NUMERIC(12,2), water_charges_amt NUMERIC(12,2) -- C1

analysis_jobs     id BIGINT PK, contract_no, attempt INT, status, model_id,
                  prompt_version, worker_id, error_code, error_msg,
                  started_at, finished_at, lease_until

contract_reports  id BIGINT PK, contract_no, job_id, version INT,
                  overall_status,
                  followups_json JSONB, other_json JSONB,   -- 跟进事项 / 其他重要信息
                  bullets_json JSONB, note_box JSONB,
                  report_path, report_sha256, created_at,
                  UNIQUE(contract_no, version)

report_items      id BIGINT, report_id BIGINT REFERENCES contract_reports(id),
                  section CHAR(1) NOT NULL CHECK (section IN ('A','B','C','D','E','F','G')),
                  section_title TEXT NOT NULL,     -- 章节标题全文，冗余存便于导出/审计
                  code TEXT NOT NULL, item TEXT NOT NULL,
                  provided TEXT, verified TEXT,    -- Present|N/A|Unclear / Checked|Action|NA|Unclear
                  contents_json JSONB NOT NULL,    -- 原文数组，保留 <b> 标签与 (p.NN) 页码
                  contents_text TEXT,              -- 去标签纯文本，供全文检索
                  pages TEXT[],                    -- 从 contents 抽出的页码，如 ['p.8','p.44']
                  notes TEXT,                      -- 法条依据，如 's 32I(a)'
                  created_at,
                  PRIMARY KEY (id, section)
) PARTITION BY LIST (section);

  -- 7 个物理分区（应用只查父表 report_items，禁止直连分区表，以保 RLS 生效）
  report_items_a PARTITION OF report_items FOR VALUES IN ('A');
  report_items_b PARTITION OF report_items FOR VALUES IN ('B');
  report_items_c PARTITION OF report_items FOR VALUES IN ('C');
  report_items_d PARTITION OF report_items FOR VALUES IN ('D');
  report_items_e PARTITION OF report_items FOR VALUES IN ('E');
  report_items_f PARTITION OF report_items FOR VALUES IN ('F');
  report_items_g PARTITION OF report_items FOR VALUES IN ('G');

audit_logs        id BIGINT PK, actor_uid, action, target_type, target_id,
                  detail JSONB, created_at
```

- `contract_no = CT-{yyyyMMdd}-{6 位大写字母数字}`，例 `CT-20261118-7QX2M1`；DB 唯一约束 + `source_sha256` 唯一约束双保险。
- **分区表主键必须含分区键**，故 `report_items` 主键是 `(id, section)`；`id` 用序列跨分区统一分配。
- 索引：`report_items (report_id, code)`、GIN `pages`、GIN `to_tsvector('simple', contents_text)`；
  `contracts (property_address)`、`contracts (title_volume, title_folio)`。
- **RLS 兜底**：每张业务表开 RLS，策略用 `app_uid()` 比对 `owner_id`；该函数内部用
  `current_setting('app.uid', true)`——**缺值时返回 NULL 而不是报错**（实测：未设 uid 的查询返回 0 行且不抛异常）。
  `report_items` / `contract_reports` 通过 `report_id → contract_no → contracts.owner_id` 关联判定归属。
  每个请求在事务开始 `SET LOCAL app.uid = '<uid>'`。`users.insert` 策略只允许新用户写自己且
  `role='member' AND status='pending'`；`role/status` 只有管理员路径能改，防止前端自我提权。
- **管理员跨用户读**：`is_admin()` 是 `SECURITY DEFINER` 只读判定函数，内部 `SET search_path = public, pg_temp` 防劫持；
  **必须 `GRANT EXECUTE ... TO PUBLIC`**，否则普通用户的 RLS policy 求值时无权调用该函数（已实测修正）。
- `analysis_jobs` / `contract_reports` / `report_items` 的写入只由 Worker 身份执行；客户端只能插 job，不能伪造 `completed`。
- `audit_logs` 只由服务端写，客户端无权限。

### 6.3 skill model JSON → 数据库字段映射

| model JSON 路径 | 落库位置 |
|---|---|
| `sections[].heading` | `report_items.section` + `section_title`（去 HTML 实体） |
| `sections[].rows[]` 每一行的 6 个字段 | `report_items` 对应分区**一行**（共 82 行） |
| `rows[].contents[]` | `contents_json` 原样存 + `contents_text` 去标签 + `pages` 抽页码 |
| `followup.rows[]` / `other.rows[]` | `contract_reports.followups_json` / `other_json` |
| `bullets[]` / `note_box` | `contract_reports.bullets_json` / `note_box` |
| `meta` / `cards` / `footer` | 渲染用，不入明细表；报告 PDF 本身入本机磁盘 |
| A1/A2/A3/A5/C1 内容正则抽取 | 回填 `contracts` 的可检索标量（带 `userId` 之外的来源页码标注于 `audit_logs`） |

### 6.4 明细查询示例

```sql
-- 只看 A 表：物业与产权信息（走 report_items_a 分区）
SELECT code, item, provided, verified, contents_text, pages
FROM report_items WHERE report_id = $1 AND section = 'A' ORDER BY code;

-- 跨节找所有风险项（Action / Unclear）
SELECT section, code, item, verified, contents_text FROM report_items
WHERE report_id = $1 AND verified IN ('Action','Unclear') ORDER BY section, code;

-- 按证据页码反查：第 44 页支撑了哪些结论
SELECT section, code, item FROM report_items
WHERE report_id = $1 AND 'p.44' = ANY(pages);

-- 按物业地址找合同（走 contracts 标量索引，不扫 JSON）
SELECT contract_no, address, status FROM contracts WHERE property_address ILIKE '%Hinkler%';
```

### 6.5 实测验证（2026-11-18，PG 18.4）

用 `D://xinxin//contract//_work//hinkler//model_v3.json`（36 Hinkler Road，Rev.3）跑 `db/ingest_report.py`：

- **分区行数**：A=11 / B=8 / C=13 / D=14 / E=3 / F=3 / G=30，合计 **82**，与设计一致；
- **内容检索**：A6 取到 `C/E A881068` 与页码 `p.50/p.46/p.48`，G2 取到 `A 608840`；按 `p.44 = ANY(pages)` 反查命中 A1/A2/A3/A4/A8/A9/C4/C7；
- **风险项**：`verified IN ('Action','Unclear')` 命中 18 行；
- **标量回填**：地址、卷 08118/册 799、Lot 126 / LP 021090、NICKY POUKI、693.00 ㎡、市政 $773.00、水费 $113.80 全部从 A/C 节正则抽出；
- **分区裁剪**：`EXPLAIN SELECT ... WHERE section='A'` 只扫 `report_items_a`；
- **RLS 隔离**：非超级用户 `app_client` 设 `app.uid=2`（owner）见 82 行；设 `999999` 见 0 行；不设 uid 见 0 行且不报错。

入库脚本：`contract-review-app/db/ingest_report.py`；表结构：`contract-review-app/db/schema.sql`。
注意：这是**回归测试数据**（合同号 `CT-20261118-H1NKL3`、测试账号），正式数据由 Worker 写入。

---

## 7. 自动分析流水线（把 skill 拆成机器可执行的 8 段）

| 段 | 做什么 | 落地要点 |
|---|---|---|
| S0 领取 | 原子领任务、写租约、attempt+1 | 超时 2 小时自动释放；重复领取幂等；**进程崩溃后 lease 到期可重新领取，任务不丢** |
| S1 解析 | PyMuPDF 逐页抽文字；无文字层页渲染为图 | **不能因「没有文字层」就判内容不可核实**——本次 E-1/A881068 与 HO54 都在纯图片页上，必须视觉读图 |
| S2 分段 | 按 A–G 82 行与页码映射，切成 7 个批次 | 每批只喂相关页，控制上下文；页表落库便于溯源 |
| S3 抽取 | 每批输出 JSON：`code/provided/verified/contents[]/notes` | 系统提示写死规则：每条结论必须带页码；无证据 → Unclear；**禁止编造页码和证号**；同一事实跨行状态一致；空白条件条款读为 N/A 但写依据 |
| S4 校验 | 82 编号唯一、状态枚举、页码范围、跨行一致性、金额/证号交叉比对 | 硬失败不产出报告；本次抓到豆包把 GAIC 依据写成 p.70（该页无 GAIC）、A6 与 G2 结论互斥 |
| S5 渲染 | 用 skill 的 `build_review_report.py` + reportlab 出 PDF | 模型 JSON 结构与 skill 完全兼容，复用现成渲染脚本 |
| S6 门禁 | 关键项（卖方权限、姓名一致、抵押冲突、地役文书、火险通知）出现 Action/Unclear → 管理员复核 | 防止全自动把不确定结论当成 Checked 发布 |
| S7 入库 | PDF + JSON 写本机磁盘，写 DB 与审计，通知申请人 | 文件 hash 入表防篡改；预览链接短时有效（≤10 分钟） |
| S8 LLM | 本机后台直连大模型（OpenAI 兼容协议） | **唯一必须出网的依赖**：断网时数据服务仍可用，AI 分析不可用；按成本/上下文分批调用 |

**质量基线**：本次人工核查 36 Hinkler Road 的结论作为回归测试集——82 行、姓名 4 种拼法、遗嘱认证只有 1/3 页、E-1/A881068、A 608840、HO54、7 July 2026 证书过期等必须被正确识别。

### 7.1 判定质量对标：skill 手工版 vs 流水线版（2026-11-18）

拿 `22 Calista Avenue Oakleigh East` 同一份包分别跑两条路线，逐字对 PDF 原文核验。两份用的是同一个渲染脚本，**差别全在内容**。

skill 手工版的四处硬错（共同的根因是"没去看那一页"）：

| 行 | 手工版结论 | 原文实际 | 正确判定 |
|---|---|---|---|
| A1 | `Identity spelling: MATCH (p.44, p.67, p.71)` | p.67/71 = `TUAN TRINH`，p.58 = `T D Trinh` | `Present / Action` + `MISMATCH` |
| G8 | `s199 certificate: supplied (p.52)` Checked | p.52 自述 `NOT a Planning Certificate issued pursuant to Section 199…` | `Unclear / Unclear` |
| D1–D4、G22/G23 | 全 `N/A (p.39)` | p.10 特约 3 披露 2023 改造、卖方无法提供 permit 与保险 | `Action` / `Missing, Action` |
| G26 | 去 p.16 找 grant 残页 | p.16 是 `Form of Guarantee for Directors` | 整词匹配 Grant of Probate/Representation |

手工版强于流水线、已固化为规则的六处：A5 面积取市政报告 `Area: 705 sq. m / Perimeter: 119 m (p.49)`；A6 以 registered plan 图面为准；A8–A10 判 `Present + N/A`（表单未勾也是表态）；A11 只答地役权/契约；C1 用 `See C10-C13` 交叉引用而非重复判 Action；尾部两张表与 bullets 全部有内容。

**落地改动**

| 位置 | 改动 |
|---|---|
| `references/status-and-format.md` | 第 2 节区分 `N/A - explicit` / `N/A - box unmarked`；第 7 节加三条红线（规划报告≠s199、Guarantee≠Grant、主表留空≠不存在）；新增第 8 节表单读法（含坐标判勾、封面目录信 +`N of M` 核附件）、第 9 节条目间分工、第 10 节自证矛盾检查 |
| `references/checklist-catalog.md` | A5/A6/A7/A8–A11/D2–D7/G4/G5/G8/G22/G23/G26/G30 补抽取要点；尾部两表补"必须非空、按主题合并、页码取并集" |
| `scripts/check_model.py`（新） | 机检 82 行/枚举/自造判定词/页码/A1 双结论/Checked 同向性/D3-G22 与 D4-G23 同源/姓名 Action 铺散/尾部空表 |
| `scripts/scan_names.py`（新） | 全包姓名变体扫描，3 行窗口区分姓名字段与参考号；分类不出口径时降级 REVIEW，不给假 MATCH |
| `worker/extract.py` | SYSTEM_PROMPT 加 6b（表单式读法：第 8 节勾 X=未接通、未勾=不适用、主表留空须查特约、附件按 `N of M` 核）、6c（GRANT≠GUARANTEE）、红线 2 项、rule 7 加"一个发现一个主场" |
| `worker/buyer_tables.py`（新） | 规则化从 82 行派生 followup / other / bullets，不调模型；runner 在 validate 后调用。此前 `extract_all()` 把这三块硬编码为空数组，报告的买方结论整块缺失 |

复现：`python scripts/scan_names.py --pdf pack.pdf` 与 `python scripts/check_model.py --model model.json`（后者已接入 skill 工作流第 5 步，有 error 不得出报告）。

---

## 8. 本机目录布局

```text
<应用根目录>\
  app\                FastAPI 后台（API + 鉴权 + 上传 + 托管前端静态页）
  web\                前端 H5 静态页（用相对路径请求 /api/...）
  worker\             分析 Worker（解析/抽取/校验/渲染）
  config\.env         唯一地址开关 PUBLIC_BASE_URL（可留空），不进 Git
  data\
    contracts\<contract_no>\original.pdf
    reports\<contract_no>\v<N>\model.json, report.pdf
    tmp\               解析中间产物，任务结束清理
  pgdata\             PostgreSQL 数据目录（真实服务，非 embedded）
  backups\            pg_dump + 原件/报告副本（见第 10 节）
```

---

## 9. 接口清单（自建 REST）

- Auth：`POST /auth/register`、`POST /auth/login`、`POST /auth/logout`、`GET /auth/me`、`POST /auth/change-password`。
- Admin：`GET /admin/pending-users`、`POST /admin/users/{uid}/approve`、`POST /admin/users/{uid}/reject`、`POST /admin/users/{uid}/suspend`。
- Contract：`POST /contracts`（multipart 上传 + SHA-256 去重）、`GET /contracts`（分页/按状态筛）、`GET /contracts/{contract_no}`（明细 + A–G 汇总）、`GET /contracts/{contract_no}/items?section=A`（**按章节明细，A–G 任一节；走对应分区**）、`GET /contracts/{contract_no}/items?verified=Action`（跨节筛风险项）、`GET /contracts/{contract_no}/original`、`GET /contracts/{contract_no}/report`。
- Job：`GET /jobs/{id}`（状态/错误码）、`POST /admin/jobs/{id}/retry`、`POST /admin/jobs/{id}/approve-review`。
- Worker 内部：直接读本机 `analysis_jobs` 表领任务（不经 HTTP）；也可加本机回环 `POST /jobs/claim` / `POST /jobs/{id}/result`，**只对 127.0.0.1 开放**。

---

## 10. 风险与缓解

1. **局域网明文 http**：同一 Wi-Fi 下可嗅探合同内容与登录 Cookie。当前阶段按"仅自家可信 Wi-Fi"接受这个风险；
   Cookie 用 HttpOnly + SameSite=Lax（http 下不能设 `Secure`）。**切云端 https 域名后这一问题自动消失**。
   不要在公共 Wi-Fi 用当前内网地址办公。
2. **笔记本不是 7×24**：断电、休眠、重启、断网都会让手机连不上。缓解：电源设置「合盖不休眠」；
   后台 + PG 包成 **Windows 服务（nssm / sc.exe）或计划任务开机自启 + 失败重启**。
   ⚠️ 别在 WorkBuddy 沙箱里跑常驻进程——沙箱会把常驻 postgres 当残留回收（症状：起来几秒自己没了）。
3. **IP 漂移**：笔记本内网 IP 变化 → 手机书签失效。缓解：路由器 DHCP 静态分配/保留（见 2.5）。
4. **备份**：每日 `pg_dump` + 原件/报告目录复制到**另一块盘或异地**；同一块硬盘的副本不算备份。
5. **LLM 成本与上下文**：79 页分批调用，单份成本需压测；图像页数量设上限；无文字层页才送视觉模型。
6. **税法/法律边界**：报告只做文件核查，不构成法律意见；复核门禁必须保留。
7. **敏感信息**：手机号脱敏、原件不外链未审批用户、预览链接不落日志。

---

## 11. 实施里程碑

- **M0 局域网打通**：笔记本起 FastAPI（绑内网网卡）+ PG，手机同 Wi-Fi 打开 `http://<内网IP>:8790/health` 拿到 OK；先验证链路再写业务。
- **M1 权限与入库**：注册、pending 门禁、管理员审批、上传去重、明细查询（不含分析）。
- **M2 分析跑通**：Worker 接入 skill 流水线，单份合同端到端产出报告；用 36 Hinkler Road 做回归。
- **M3 复核与审计**：review 门禁、重跑版本、审计日志、失败重试、崩溃续跑。
- **M4 切云端（以后再说）**：反代指到本机 8790，手机换云端域名；如需 PWA 安装/离线能力，此时启用。
- **M4' 产品化**：备份脚本、限流、批量上传、验收标准逐条走查。

---

## 12. 待核实项（诚实标注，实施前补）

- [ ] 笔记本**当前实际**内网 IPv4（`ipconfig` 实测；伴学的 `192.168.3.80` 不要照抄）。
- [ ] 本机是否装了真实 PostgreSQL；若没有，M0 先装（伴学本机当时没装）。
- [ ] 笔记本电源策略能否设为不休眠（决定后台是否常驻可用）。
- [ ] 大模型用哪一家（伴学当前走火山方舟百炼；合同视觉读图要选支持图像的模型）。
- [ ] 单份 79 页合同的 LLM 调用成本压测结果。
- [ ] 是否接受局域网 http 明文风险（若不接受，需在内网做自签证书，手机装 CA，成本另算）。

---

## 13. 实现状态补充（v0.5.1-edit，2026-11-18）

> 本节记录落地后与上游章节不一致的地方，以本节为准。

### 13.1 权限模型已改为 RBAC（替代第 3 节的二值 admin/member 矩阵）

四张表：`roles` / `permissions` / `role_permissions` / `user_roles`，
`is_admin()`、`has_perm(code)` 以 SECURITY DEFINER 函数 + RLS 兜底，权限改动热生效（JWT 里不带权限，每次请求现查）。

| 权限点 | admin（系统管理） | operator（业务） | viewer（只读） |
|---|---|---|---|
| `contract.view` 查看 | — | ✓ | ✓ |
| `contract.upload` 上传并分析 | — | ✓ | — |
| `contract.download` 下载原件/报告 | — | ✓ | ✓ |
| `contract.edit` **修改合同数据** | — | ✓ | — |
| `admin.users` / `admin.roles` / `admin.audit` / `admin.llm` | ✓ | — | — |

**职责分离（2026-11-18 调整）**：`admin` 是纯系统管理员——只做用户审批、角色分配、操作日志、大模型 Key；
**不持有任何 `contract.*` 权限**，因此看不到、也改不了任何合同。合同的导入与维护归 operator（注册用户）。
`is_superuser` 标记不再隐式授予全部权限：权限一律按 `role_permissions` 字面生效（`app/auth.py::_rbac_for`），
否则"管理员不参与合同"无法成立。

admin 可自建角色并任意组合权限点、给用户分配角色；内置三角色不可删除改名。
内置角色的权限以代码为准（`db/migrate_rbac.py` 重跑会把多出来的权限点清掉，自定义角色不受影响）。

### 13.2 合同数据编辑规则（`contract.edit`）

设计原则：**机器产出的报告骨架不可被静默改写**，只允许人工补漏。

- 可改合同标量：地址、产权 Volume/Folio、Lot/Plan、登记业主、土地面积、市政费、水费（数字字段服务端校验）。
  不可改：合同号、所有者、源文件哈希、状态、报告版本（系统管理）。
- 清单条目（`report_items`）：`item` / `code` / `section` / `section_title` **不可修改**，**不可删除**；
  只允许改 `provided`（下拉：Present/Missing/Unclear/Awaiting/NA）、`verified`（下拉：Checked/Action/Unclear/NA）、
  `contents`（多行正文）、`notes`、`pages`。需要新条目就**新增**，编号留空自动递增。
- 新增/修改都要求报告已生成；写操作全部落审计表（`edit_contract` / `edit_item` / `add_item`）。

| 接口 | 说明 |
|---|---|
| `PUT /api/contracts/{no}` | 改标量字段；无可改字段 → 400；数字非法 → 400 |
| `POST /api/contracts/{no}/items` | 新增条目；`code` 可省略自动生成，重复 → 409，跨章节前缀 → 400 |
| `PUT /api/contracts/{no}/items/{id}` | 只改内容；带 `item`/`code`/`section` → 400 |
| `DELETE /api/contracts/{no}/items/{id}` | **不提供**（返回 405），删除条目请新建报告版本 |

### 13.3 已知修复

- 条目查询曾跨报告版本重复（重跑分析后同一 A1 出现两次，章节计数翻倍）→ 已钉到 `max(version)`；
  `GET /api/contracts` 的 `risk_count` 同样改为只统计当前版本。
- 切换中英文会把用户从明细页弹回列表（`applyIdentity` → `pickTab` → `closeDetail`）→ 语言切换改为记住并恢复当前明细。

### 13.4 测试

`tests/e2e_edit.py`（33 项，含快照+还原）；`tests/e2e_rbac.py` 断言改为从权限目录推导，
不再写死权限点数量。

### 13.5 大模型配置 = 系统参数（v0.5.2-view）

- 配置只存在数据库 `llm_config` 单行表，**admin 保存即生效**，不需要重启 API 或 Worker；下次 admin 修改前一直沿用。
- Worker 在**每个任务开始时冻结一份配置**（`llm.begin_job_config()`），任务结束释放。
  这样分析途中改配置不会让同一份报告前半段用旧模型、后半段用新模型；新配置作用于下一个任务。
- 管理端显示"已生效/未生效 + 模型 @ BaseURL + 更新于 + 修改人 + 来源：数据库系统参数"，
  `GET /api/admin/llm-config` 返回 `effective` / `source` / `refresh` / `updated_by`。
- 已验证：任务进行中改库，`get_config()` 返回冻结值；任务结束后立即读到新值。

### 13.6 查看层（v0.5.2-view）

- 明细页顶部 4 个指标：状态 / 风险项 / 需跟进 / 报告版本。
- 章节页签末尾增加 `⚠ n` **需跟进**页签，直接呈现报告 `followups_json`（matter / finding / action / pages）。
- **合同内搜索**：`GET /api/contracts/{no}/items?q=` 跨 A–G 检索条款号、条目名、正文，前端高亮命中；
  可与 `section` 组合使用。
- **修改记录**：`GET /api/contracts/{no}/audit`（仅 `contract.edit`）展示 `edit_contract` / `edit_item` / `add_item`
  的操作人、时间与变更字段，字段名映射为可读标签。
- 切换语言不再丢失明细页状态（见 13.3）。

`tests/e2e_view.py`（23 项，只读）。

### 13.7 底部三个预留菜单已落地（v0.6.0-workbench，2026-11-18）

原来占位的「功能一/二/三」换成三块真实工作台，都不依赖大模型，纯读写已有数据。

#### 待办跟进（`tab_more1`）

跨合同的跟进事项工作台：把每份合同**最新版本报告**里的 `followups_json` 摊平成一张清单。
这是律师真正要处理的事——对方律师还需要补交或澄清什么。

| 接口 | 说明 |
|---|---|
| `GET /api/followups?q=&contract_no=&limit=` | 返回 `{total, contracts, items[]}`，item 含 `contract_no / address / status / version / matter / finding[] / action[] / pages` |

- 关键字在 matter + finding + action + 合同号 + 地址 上做子串匹配（大小写不敏感）。
- 走 `api_session`，因此 **RLS 自动生效**：操作员只看到自己上传的合同。
- 页面顶部显示「N 项 · M 份合同」；卡片里的合同号可直接点开该合同明细。
- 「生成问题清单」把全部 action 按合同分组拼成纯文本（`## 合同号 — 地址` + 编号事项 + 页码），
  弹层里可一键复制，直接贴给买方律师/对方律师。

#### 导出（`tab_more2`）

| 接口 | 说明 |
|---|---|
| `GET /api/contracts/{no}/export?what=items` | A–G 全量核查清单 CSV |
| `GET /api/contracts/{no}/export?what=followups` | 跟进事项 CSV |
| `what` 非法 → 400；合同不存在 → 404；**报告未生成 → 409** |

- CSV 带 **UTF-8 BOM**（`\ufeff`），否则 Windows Excel 打开中文备注是乱码；行内换行交给 `csv` 模块加引号。
- 文件名 `<合同号>_checklist.csv` / `_followups.csv`。
- 合同下拉把 `completed` 排在最前，默认选中第一份可用合同；未完成合同导出时前端直接提示"报告尚未生成"。
- 手机上 WebView 可能不弹下载 → 提供「预览 / 复制」，把同一份 CSV 文本放进弹层（先去 BOM）供复制。

#### 操作日志（`tab_more3`，`admin.audit`）

| 接口 | 说明 |
|---|---|
| `GET /api/admin/audit?limit=&action=&q=` | 全局写操作流水，联 `users` 取操作人；另返回 `actions[]` 供筛选下拉 |

- 新增权限点 `admin.audit`（`db/migrate_rbac.py`，幂等重跑即授予 admin）。
  没有该权限时**整个底部菜单项隐藏**，不是显示一个空页面。
- 动作名映射为可读标签（`act_*`），下拉按**译名**排序而不是原始动作码；
  切换语言时重置 `auditActionsLoaded` 让下拉按新语言重建。

#### 其它顺带修正

- 登出时清空 `contractsCache / fuItems / auditQ`，避免换账号后看到上一个账号的缓存行。
- 切换语言会重建底部菜单文案与日志下拉，并保持当前页签。

`tests/e2e_workbench.py`（43 项，只读）：跟进清单/筛选/RLS 隔离、CSV BOM 与 A–G 完整性、409/400/404、
日志权限与筛选、新权限点入目录且 operator 不持有。

### 13.8 账号、职责分离与整屏自适应（v0.6.1-shell，2026-11-18）

#### 管理员账号改为 `admin`

`db/migrate_admin_account.py`（幂等）：把 `13800138000` 改名为 `admin`，uid 不变、密码不变、角色不变；
若 `admin` 已存在则不动并给出提示。登录页账号框本来就接受任意 `^[A-Za-z0-9._-]{3,64}$`，无需改前端。

#### admin 只管系统，不碰合同

| 谁 | 能做什么 |
|---|---|
| `admin` | 审批注册、分配角色、建自定义角色、看操作日志、配置大模型 Key |
| `operator` | 上传合同、触发分析、查看/下载、修改合同与核查条目 |
| `viewer` | 查看、下载 |

实现要点：

- `role_permissions` 对内置角色**以代码为准**：`migrate_rbac.py` 重跑时会删掉定义里没有的权限行（自定义角色不受影响）。
- `app/auth.py::_rbac_for` 不再给 superuser 注入全部权限点，权限严格按角色授予生效。
  `is_superuser` 只保留给 RLS / 兼容用的 `require_admin`。
- `contract.edit` 从 admin 移到 operator（合同维护属于业务）。
- 前端按权限裁剪底部菜单：`entry` 需 `contract.upload`，`query` / `待办` 需 `contract.view`，
  `导出` 需 `contract.download`，`日志` 需 `admin.audit`；当前页签不可见时自动落到第一个可见页签
  （admin 因此只看到「个人信息 + 日志」两个菜单）。

#### 整屏自适应（底部菜单不再被切掉）

原来 `body` 是普通文档流 + `.bottom` 固定定位，长内容或手机工具栏会让底部栏掉出可视区。
现在改成标准 App 外壳：

```css
html, body { height:100% }
body  { display:flex; flex-direction:column; height:100vh; height:100dvh; overflow:hidden }
.top  { flex:0 0 auto }                 /* 头部不滚动 */
.wrap { flex:1 1 auto; min-height:0; overflow-y:auto }   /* 只有内容区滚动 */
.bottom { flex:0 0 auto }               /* 菜单是布局的一行，永远在可视区底部 */
```

- `100dvh` 跟随移动端动态工具栏；不支持的浏览器退回 `100vh`。
- `wrap` 加 `min-height:0`（flex 子项默认不收缩，少了它内容会把父级撑破）。
- `.btab` 改 `flex:1 1 0` + `white-space:nowrap`，6 个菜单在窄屏也不会换行/被截。
- 弹层 `max-height:90vh` → 补 `90dvh`。
- 实测（无头 Edge）：`390×844` 与 `390×520`（实际 504 宽）下，菜单 `bottom === innerHeight` 完全贴底，
  `body` 自身不滚动（`scrollHeight-clientHeight = 0`），内容全部在 `wrap` 内滚动，最后一条条目不被菜单遮挡。
