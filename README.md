# 合同审查系统（Contract Review App）

一套可移交的本地/内网合同审查工程：上传合同 PDF → 后台 Worker 调用大模型产出结构化 JSON → 按 A–G 清单入库 → 生成与原报告同版式的 PDF，并提供 H5 与 Android WebView 两种入口。

> 本目录是**干净移交版**：不含任何真实密钥、用户数据、合同原件、报告 PDF、数据库文件或签名证书。所有机密统一通过根目录 `.env` 注入，模板见 `.env.example`。

---

## 1. 技术栈

| 层 | 技术 |
|---|---|
| 前端 H5 | 单文件 `frontend/h5/index.html`（原生 HTML/CSS/JS，无构建步骤），PDF 风格报告展示、A–G 标签、编辑、归档 |
| 移动端 | Android WebView 壳（`frontend/android/`），Java + aapt2/d8/apksigner 脚本构建，无 Gradle 依赖 |
| 后端 API | Python 3.13 + FastAPI + Uvicorn，路由按 `system / auth / admin / contracts / workbench` 拆分 |
| 鉴权 | bcrypt 密码哈希 + JWT HttpOnly Cookie；RBAC 权限点来自数据库 `role_permissions` |
| 数据库 | PostgreSQL 18（兼容 14+），行级安全 RLS；API 用低权限 `app_api`，Worker/引导用超级用户连接 |
| 分析 Worker | 独立 Python 进程，S0–S7 流水线，心跳表判活，lease 防重复领取 |
| 报告渲染 | 内置 skill `backend/skills/property-contract-review-report`（reportlab），中英文字体回退 |
| 运维 | Node.js 看门狗 + 外层 supervisor，Windows 启动项隐藏拉起 |

---

## 2. 目录结构

```
contract-review-app-handoff/
├── .env.example                     # 环境变量样例（唯一存放密钥的位置）
├── .gitignore                       # 忽略 .env / 数据 / 日志 / 数据库 / 签名证书
├── README.md                        # 本文件
├── backend/                         # 【后端】
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py                  # 应用装配：include_router + 静态 H5
│   │   ├── core.py                  # 共享常量、审计、权限门禁、worker 判活
│   │   ├── db.py                    # server_conn / api_session 两种数据库连接
│   │   ├── auth.py                  # 密码、JWT、current_user、require_perm
│   │   ├── report_pdf.py            # 修改后报告 PDF 导出
│   │   └── routers/
│   │       ├── system.py            # /health、/api/config、worker-status
│   │       ├── auth.py              # 注册 / 登录 / 登出 / 改密 / me
│   │       ├── admin.py             # 用户审批、角色、LLM 配置、审计日志
│   │       ├── contracts.py         # 上传、查询、归档、编辑、下载、重试、删除
│   │       └── workbench.py         # 跨合同追问/待办
│   ├── worker/
│   │   ├── runner.py                # S0–S7 主循环
│   │   ├── llm.py                   # OpenAI 兼容接口，热读取 llm_config
│   │   ├── extract.py               # 按 skill 目录做 JSON 抽取
│   │   ├── parse.py                 # PDF 文本/页码解析
│   │   ├── render.py                # 调用 skill 渲染 PDF
│   │   ├── validate.py              # 82 行硬校验
│   │   └── buyer_tables.py          # 买方附表抽取
│   ├── db/
│   │   ├── bootstrap_admin.py       # 创建首个管理员（密码走环境变量）
│   │   ├── ingest_report.py         # model JSON 按 A–G 分区入库
│   │   └── purge_contracts.py       # 维护工具：默认 dry-run
│   ├── skills/property-contract-review-report/
│   │   ├── SKILL.md                 # A–G 规则与渲染契约
│   │   ├── references/              # 清单目录、状态与格式规范
│   │   └── scripts/                 # build_review_report.py 等
│   └── tests/                       # 端到端回归脚本
├── frontend/                        # 【前端】
│   ├── h5/index.html
│   └── android/
│       ├── AndroidManifest.xml
│       ├── build.sh                 # 环境变量提供签名口令，无硬编码密码
│       ├── assets/backend-url.txt   # 出包前替换成实际后台地址
│       ├── java/cn/contractreview/app/*.java
│       └── res/drawable-nodpi/ic_launcher.png
├── db/                              # 【数据库】只含 DDL + 幂等种子，无业务数据
│   ├── schema.sql                   # 建表 / 分区 / 索引 / RLS / 函数 / 授权
│   └── seed.sql                     # 权限点、内置角色、角色授权
├── docs/                            # 【文档】
│   ├── ARCHITECTURE.md              # 组件关系、S0–S7 流水线、权限模型
│   ├── API.md                       # 路由清单与错误约定
│   ├── DATABASE.md                  # 表、RLS、初始化与备份
│   ├── OPERATIONS.md                # 看门狗、日志、故障排查
│   ├── HANDOFF.md                   # 接手注意事项与验收清单
│   ├── TREE.md                      # 完整目录树
│   ├── FILE_INDEX.md                # 关键文件清单与行数（均为完整源码）
│   ├── contract-review-app-design.md
│   └── 合同审查系统功能说明书.docx
└── ops/                             # 本机守护脚本（Windows 启动项）
    ├── watchdog.mjs                 # PG / API / Worker 探活与拉起
    ├── watchdog-supervisor.mjs      # 看门狗自身的父进程
    ├── contract-watchdog.cmd        # 隐藏启动入口
    ├── contract-watchdog-launch.vbs # 无窗口 VBScript 启动器
    ├── contract-watchdog-stop.cmd   # 仅停自动拉起，不停服务
    └── status.cmd                   # 查端口 / 锁 / health / 公网地址
```

后端原先单文件 `app/main.py` 已按职责拆成 `core.py + routers/*`，所有发布出来的文件都是完整源码，不是片段。

---

## 3. 本地启动步骤

### 3.1 准备环境变量

```bash
cp .env.example .env
# 编辑 .env：至少填 PGPASSWORD、PGAPIPASSWORD、JWT_SECRET
```

`JWT_SECRET` 生成方式：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 3.2 初始化数据库

假设 PostgreSQL 已安装并在 `PGPORT`（默认 5434）监听。以下用超级用户 `PGUSER` 执行：

```bash
createdb -h 127.0.0.1 -p 5434 -U postgres contract_review
psql    -h 127.0.0.1 -p 5434 -U postgres -d contract_review -f db/schema.sql
psql    -h 127.0.0.1 -p 5434 -U postgres -d contract_review -f db/seed.sql
```

`db/schema.sql` 会创建无密码的 `app_api` 登录角色；按 `.env` 中的 `PGAPIPASSWORD` 设置口令，例如 PowerShell：

```powershell
$pw = $env:PGAPIPASSWORD
psql -h 127.0.0.1 -p 5434 -U postgres -d contract_review -c "ALTER ROLE app_api PASSWORD '$pw';"
```

> 不要把口令写进 `db/*.sql` 或提交到仓库。`db/schema.sql`、`db/seed.sql` 可重复执行（幂等 DDL / `ON CONFLICT DO NOTHING`），但**不会删除任何业务数据**。

### 3.3 创建第一个管理员

```bash
cd backend
# PowerShell: $env:BOOTSTRAP_PASSWORD='<至少12位强口令>'
python -m db.bootstrap_admin admin "系统管理员"
```

密码从 `BOOTSTRAP_PASSWORD` 环境变量读取，不进命令行历史。脚本会同时写入 `users` 与 `user_roles`，因此 RBAC 生效。

### 3.4 启动 API 与 Worker

在**两个终端**分别执行：

```bash
cd backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8790 --no-access-log
```

```bash
cd backend
python -m worker.runner
```

验证：

```bash
curl http://127.0.0.1:8790/api/health
# 期望 components: api/database/worker 全 up / alive，llm_configured 视配置而定
```

浏览器打开 `http://127.0.0.1:8790/`。

### 3.5 配置大模型

登录后进入「大模型接口设置」，填写兼容 OpenAI 协议的 Base URL / API Key / 模型并保存。配置存 `llm_config` 表，Worker 每次任务实时读取，改完无需重启。`.env` 中的 `LLM_*` 只是表缺失时的兜底种子。

### 3.6 Android 壳（可选）

1. 修改 `frontend/android/assets/backend-url.txt` 为实际后台地址（末尾带 `/`）。
2. 设置 `ANDROID_SDK`、`JAVA_HOME`、`ANDROID_KS_PASS`、`ANDROID_KEY_PASS`。
3. 执行 `bash frontend/android/build.sh`，产物在 `frontend/android/dist/`。
4. 签名证书 `contract-review.keystore` **不要提交仓库**；丢失后无法对已安装用户做覆盖升级。

### 3.7 常驻守护（可选，Windows）

`ops/contract-watchdog.cmd` 可由启动项拉起，内部通过 VBScript 全隐藏运行，不会再弹黑窗口。前置：环境变量 `CONTRACT_ROOT`（移交目录，复制到启动文件夹时必填）、`CONTRACT_PY`（建议指向 `pythonw.exe`）、`CONTRACT_NODE` 指向本机 Node。用 `ops/status.cmd` 查状态，`ops/contract-watchdog-stop.cmd` 只停自动拉起、不停服务。

---

## 4. 环境变量说明

| 变量 | 必填 | 说明 |
|---|---|---|
| `APP_HOST` / `APP_PORT` | 否 | Uvicorn 监听地址与端口，默认 `0.0.0.0:8790` |
| `PUBLIC_BASE_URL` | 否 | 对外完整地址；用于状态脚本与文档，前端不硬编码 |
| `PGHOST` / `PGPORT` / `PGDATABASE` | 是 | PostgreSQL 连接参数 |
| `PGUSER` / `PGPASSWORD` | 是 | 超级用户连接，仅用于鉴权、审计、Worker 状态推进 |
| `PGAPIUSER` / `PGAPIPASSWORD` | 是 | 低权限业务连接，RLS 依赖 `app_api` 角色 |
| `JWT_SECRET` | 是 | 会话签名密钥，丢失会导致全部登录态失效 |
| `BOOTSTRAP_PASSWORD` | 仅初始化 | 创建首个管理员时使用，运行期不需要 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 否 | 仅作 `llm_config` 缺表时的兜底；正式配置在 App 内完成 |
| `LLM_MODEL_VISION` / `LLM_TIMEOUT` / `LLM_MAX_TOKENS` | 否 | 视觉模型、超时、最大 token |
| `WORKER_POLL_SECONDS` / `WORKER_LEASE_MINUTES` | 否 | 轮询间隔与任务租约 |
| `WORKER_ALIVE_SECONDS` / `STALL_SECONDS` | 否 | 心跳判活窗口（默认 45s）、卡住阈值（默认 600s） |
| `SKILL_BUILD_SCRIPT` | 否 | 留空即使用 `backend/skills/...` 内置渲染器；**不要填无效路径**，`Path("")` 会被当成 `.` |
| `CONTRACT_DATA_DIR` | 否 | **所有文件（原件/报告/模型/temp）的统一根目录**。DB 只存相对该目录的路径（如 `contracts/CT-xxx/original.pdf`），换机器只需拷贝 data 目录并改此变量，无需改 DB；新装留空用 `backend/data` |
| `CONTRACT_PGDATA` / `CONTRACT_PGBIN` | 否 | PostgreSQL 数据/程序目录；新装留空用项目根的 `pgdata/`、`pg/bin` |
| `CONTRACT_ROOT` / `CONTRACT_PY` / `CONTRACT_PYW` / `CONTRACT_NODE` | 否 | 移交目录与看门狗使用的解释器、Node 命令；复制到启动文件夹时 `CONTRACT_ROOT` 必填 |
| `ANDROID_SDK` / `JAVA_HOME` | 否 | Android 构建工具链位置 |
| `ANDROID_KEYSTORE` / `ANDROID_KS_PASS` / `ANDROID_KEY_PASS` / `ANDROID_KEY_ALIAS` | 打包时必填 | 签名材料，禁止入库 |

完整样例见 `.env.example`。**任何密钥都不要写进源码、SQL、README 或提交历史。**

### 4.1 文件存储路径规则（重要）

系统产生的所有文件都放在 `CONTRACT_DATA_DIR` 下，分三个子目录：

```
<CONTRACT_DATA_DIR>/
├── contracts/<合同号>/original.pdf      合同原件
├── reports/<合同号>/v<N>/report.pdf     生成的报告 PDF
├── reports/<合同号>/v<N>/model.json     报告渲染模型（导出用）
└── tmp/<合同号>/...                     解析缓存
```

**数据库里只存相对路径**，例如 `contracts/CT-20261118-QG6NAJ/original.pdf`，不存 `D:\...` 这种绝对路径。相关字段是 `contracts.source_path` 和 `contract_reports.report_path`。

- 所有路径读写统一走 `backend/app/paths.py` 的 `resolve_path()`（DB→磁盘）和 `to_relative()`（磁盘→DB）。新增任何文件类型都必须用这两个函数，不要自己拼 `Path(...)`。
- **搬迁 / 换机器**：拷贝整个 data 目录 → 修改 `.env` 的 `CONTRACT_DATA_DIR` 指向新位置 → 完成。DB 一行都不用改。
- 兼容旧数据：`resolve_path()` 同时认识旧的绝对路径和新的相对路径；老库可用 `python db/migrate_relative_paths.py --yes` 一次性转成相对路径（默认 dry-run，幂等）。
- `db/purge_contracts.py`、`app/report_pdf.py` 等命令行/导出入口也都已改为从 `CONTRACT_DATA_DIR` 派生，不再硬编码 `data/`。

---

## 5. 数据库说明

`db/` 只有两类文件：

- `schema.sql`：DDL。建表、A–G 分区、索引、RLS 策略、`app_uid()/is_admin()/has_perm()` 函数、最小授权。会创建 `app_api` 角色但**不设密码**。
- `seed.sql`：幂等种子。权限点、内置角色 `admin/operator/viewer/archivist`、角色—权限映射。**没有用户账户、没有密码哈希、没有业务合同数据。**

关键设计约定：

1. `admin` 是纯系统管理员，只有 `admin.*`；需要业务权限必须额外叠加 `operator/viewer/archivist`。
2. `has_perm()` 的 superuser 通配仅限 `admin.%`；业务表 RLS 不含 `is_admin()`。
3. 归档合同默认不参与普通查询；只有 `contract.archive_view` 可见，且只读。
4. Worker 用超级用户连接推进状态；客户端只能用 `api_session(uid)` 走 RLS。

历史迁移脚本按交接要求未收入本目录。现有库升级请参考 `docs/HANDOFF.md`；新环境从 `schema.sql + seed.sql` 初始化即可。

---

## 6. 交接说明

完整接手清单、已知坑与不得回退的规则见 [`docs/HANDOFF.md`](docs/HANDOFF.md)。功能语义见 `docs/合同审查系统功能说明书.docx`，设计演进见 `docs/contract-review-app-design.md`。
