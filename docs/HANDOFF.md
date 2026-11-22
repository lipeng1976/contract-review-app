# 交接注意事项（Contract Review App）

适用对象：接手继续开发/运维的工程师。请先读根目录 `README.md` 完成一次本地启动，再读本文件。

## 1. 四条不可回退的设计红线

1. **归档合同默认不参与任何普通功能**。列表、查询、待办、统计、导出、批量操作一律带 `AND c.archived_at IS NULL`。只有显式「归档合同」入口才查 `archived_at IS NOT NULL`，并要求 `contract.archive_view`。每加一个新功能，先回答「已归档合同是否参与」，默认答案是不参与。
2. **归档合同只读**。禁止编辑、重新分析、删除；下载原报告允许。RLS 只对 SELECT 放宽，不要为了省事改 UPDATE/INSERT 策略。
3. **admin 是纯系统管理员**。只允许 `admin.users / admin.roles / admin.llm / admin.audit`，菜单从「系统设置」开始。**任何 `contract.*` 权限都不能塞给 admin**。需要业务权限的管理员必须额外分配 operator/viewer/archivist。三层一致性：`role_permissions` 字面授权、`has_perm()` 通配仅限 `admin.%`、业务表 RLS 不含 `is_admin()`。
4. **人工修改过的报告必须保护**。`contract_reports.edited_at / edit_count`、`report_items.edited_fields` 是判据；重新分析前必须二次确认，服务端以 `confirm=1` 兜底。无实际变化的保存（`updated: []`）不能锁住报告。

## 2. 上手路径

1. 复制 `.env.example` 为 `.env`，填数据库与 `JWT_SECRET`。
2. 跑 `db/schema.sql` → `db/seed.sql` → 设置 `app_api` 口令 → `bootstrap_admin.py`。
3. 启动 API 与 Worker，打开 `/api/health` 确认 `api/database/worker` 全绿。
4. 登录后在「大模型接口设置」填 LLM 配置；Worker 每次任务实时读表，无需重启。
5. 上传一份合同跑通：上传 → `queued` → Worker S0–S7 → 入库 → PDF → H5 展示。

## 3. 代码地图与修改入口

| 要改什么 | 去哪里 |
|---|---|
| 新增 API | `backend/app/routers/` 对应模块；公共门禁放 `core.py`；不要往 `main.py` 堆路由 |
| 改 H5 页面 | `frontend/h5/index.html`（单文件，无构建） |
| 改报告 PDF 版式 | `backend/skills/property-contract-review-report/scripts/build_review_report.py`，并同步 `SKILL.md` |
| 改 A–G 目录/判据 | `backend/skills/.../references/`，同时改 `worker/validate.py` 的硬校验 |
| 改 Worker 阶段 | `backend/worker/runner.py`（S0–S7）、`extract.py`、`parse.py`、`render.py` |
| 改权限/RLS | `db/schema.sql`（新环境）+ 新增幂等迁移脚本（老环境），两边必须同步 |

本次移交已把原单文件 `app/main.py` 拆成 `core.py + 5 个 router`。`main.py` 只负责装配，路由 include 顺序保持稳定：`system → auth → admin → workbench → contracts`。`/api/contracts/archived` 必须定义在 `/api/contracts/{contract_no}` 之前。

## 2b. 本目录已转正为运行主目录（2026-11-20）

原来的 `contract-review-app/` 已冻结（见其 `FROZEN.md`），**所有修改只改本目录**。运行中的 API / Worker cwd 都是 `handoff/backend`，看门狗来自 `handoff/ops/`，Windows 启动项也已指向这里。

运行时数据刻意外置，由 `.env` 指定，不要删原目录：

| 变量 | 当前值 | 作用 |
|---|---|---|
| `CONTRACT_DATA_DIR` | `contract-review-app\data` | 所有原件/报告/模型/temp 的统一根目录。**DB 只存相对此目录的路径**（如 `contracts/CT-xxx/original.pdf`）；搬机器只需拷贝 data 并改这里，DB 不用动。留空用本目录 `backend/data` |
| `CONTRACT_PGDATA` | `contract-review-app\pgdata` | PostgreSQL 数据目录（运行中） |
| `CONTRACT_PGBIN` | `contract-review-app\pg\bin` | PostgreSQL 程序目录 |
| `SKILL_BUILD_SCRIPT` | 留空 | 留空=用本目录内置 skill；**不要填路径**，历史 bug：`Path("")`→`.` 会让渲染器指向当前目录 |

新装环境留空这三项即可用本目录内的 `backend/data`、`pgdata/`、`pg/`。

### 2c. 文件路径规则（2026-11-20 改，强制）

所有文件（原件 / 报告 PDF / model.json / tmp）都在 `CONTRACT_DATA_DIR` 下，**DB 只存相对路径**（`contracts.source_path`、`contract_reports.report_path`）。

- 读写一律走 `backend/app/paths.py` 的 `resolve_path()` / `to_relative()`。**新增任何文件类型都不许自己拼绝对路径或 `Path(...)`**，否则搬机器就断。
- `app/core.py`、`worker/runner.py`、`app/report_pdf.py`、`db/purge_contracts.py`、`db/ingest_report.py` 已全部改为从 `.env` 派生路径；曾发现 `report_pdf.py` / `purge_contracts.py` 硬编码 `ROOT/"data"` 的漏网点，已修，别回退。
- 老库升级：`cd backend && python db/migrate_relative_paths.py --yes`（默认 dry-run、幂等、逐行校验文件存在）。`resolve_path()` 同时兼容历史绝对路径，所以迁移前后服务都不中断。
- 搬迁：拷贝 data 目录 + 改 `CONTRACT_DATA_DIR` 即可，DB 零改动。`pgdata` 是数据库自身目录，与路径字段无关，换机器要走 pg_dump，不能只拷贝。

## 4. 已知坑（踩过，别再踩）

- **Worker 心跳的 `age_seconds=0`**：曾写 `(age or 10**6) <= 45`，心跳落在同一秒时 0 是 falsy，被误判死亡，看门狗连续 3 次就无辜重启 Worker，桌面周期弹 `python.exe` 黑框。必须用 `age is not None`。判活只看 `/api/health` 的 `components.worker`。
- **venv 的 `python.exe` 是 shim**：它再派生一个带控制台的解释器；Node `windowsHide` 只隐藏第一层。看门狗拉服务一律用 `pythonw.exe`（`CONTRACT_PYW`）。
- **旧 `contract-watchdog-stop.cmd` 用 `taskkill /T`**：会把 PG/API/Worker 一起带走。移交版已去掉 `/T`，只结束 node 进程。
- **启动器不能用 `start /min`**：最小化仍常驻黑窗口；移交版用 VBScript `Run cmd, 0, False` 全隐藏。工具会话里 `Start-Process`/`wscript`/`Invoke-CimMethod` 会被安全策略拦截，不要据此判断守护链坏了；看 `logs/*.lock` 里的 PID 是否真存活。
- **PG 不要用 `pg_ctl start`**：本机实测派生的 postmaster 会随启动命令被回收。用 `postgres.exe -D pgdata -h 127.0.0.1 -p 5434` 直启 + detached。
- **中文 PDF 方框**：英文报告用 Helvetica，无中文字形。渲染脚本必须对所有 `Paragraph` 文本走 `cjk_safe()`，新增文本入口也要遵守。
- **`Path("")` 不等于「没配置」**：`Path("")` 会变成 `Path(".")` 且 `exists()` 为真。渲染器配置（`SKILL_BUILD_SCRIPT`）必须用 `raw.strip()` 判空后再构造 Path，并校验 `is_file()`，否则导出 PDF 报 `can't find '__main__' module in '<cwd>'`。`app/report_pdf.py` 与 `worker/render.py` 都已修，改这两处时别回退。
- **路由拆分后的相对导入**：`app/routers/*.py` 位于 `app/routers/` 下，引用 `app/report_pdf.py` 要写 `from ..report_pdf`（两个点）。曾因漏改一个点导致 `/edited-report` 500。改路由位置时先 grep `from \.`。
- **编辑栏样式**：修改单元格同字体同字号，中文用楷体 + 深蓝 `#17366B` 斜体；徽章保留原底色只改字色。
- **测试号**：`qa.edited` / `qa.arch` / `qa.view` 是 admin 叠加业务角色，会看到业务菜单，属预期，不是权限泄漏。
- **后台任务 exit 127 / 1**：多为工具会话回收临时进程，以 `curl /api/health` 为准，不要看到通知就重启服务。

## 5. 数据与安全

- 本移交包**不含** `.env`、`pgdata/`、`data/`、`logs/`、`*.keystore`、`*.apk`。接手方必须自行生成密钥与签名证书。
- `JWT_SECRET` 一旦丢失/轮换，所有已签发会话立即失效。
- 签名证书丢失后无法给已安装用户做覆盖升级，只能换新包名重装。
- 生产库改动前先备份 `pgdata` 与 `data/`；`db/purge_contracts.py` 默认 dry-run，执行会先备份文件再删除。

## 5b. 版本控制与移交包

- 本目录已是 git 仓库。基线提交 `1000919`，标签 `v0.8.0-handoff`（2026-11-20 转正版本）。
  后续所有改动在**本目录**里 `git add/commit`，不要再改已冻结的原目录。
- `.gitattributes` 固定仓库内 LF 换行；`.gitignore` 排除 `.env`、`data/`、`logs/`、`pgdata/`、
  `*.keystore`、`*.apk` 等，提交前确认 `git status` 无敏感文件。
- 根目录的 `contract-review-app-handoff.zip` 是与基线同步的交付包（72 个文件，不含任何
  密钥与运行时数据）。代码有改动后需从本目录重新打包，保持 zip 与 git 一致。

### 5c. 公开到 GitHub / 分享给外人（2026-11-22 脱敏后）

本仓库已按"可公开"标准清理，**推送前仍需再跑一遍下面的检查**：

- **凭据外置**：`backend/tests/creds.py` 统一从环境变量或本地 `.env` 读测试账号，
  测试脚本里**不得出现明文密码**。本地要跑 e2e，先在 `.env` 填 `TEST_ADMIN_PASSWORD`
  等（见 `.env.example`）；这些变量已被 git 忽略。
- **外发检查命令**（推之前逐条跑，都应为空/clean）：
  - `git status --short`（工作区干净）
  - `git ls-files | grep -Ei '(\.env$|\.keystore$|\.apk$|\.pem$)'`（应无输出）
  - `grep -rInE '"(password|api_key)": *"[^"<$]' backend/tests/`（应无输出，凭据必须来自 creds.py）
  - `grep -rInE 'sk-[A-Za-z0-9]{20,}|BEGIN .* PRIVATE KEY' --exclude-dir=.git .`（应无输出）
- **不要提交**：真实 `.env`、签名证书 `*.keystore`、APK、`data/`、`pgdata/`、任何
  合同原件/报告 PDF、`*.dump`。这些按设计都在仓库之外。
- **公网地址**：`frontend/android/assets/backend-url.txt` 里的公网 IP 是部署地址，
  不是密钥；若分享给不希望连到本机服务的人，让对方在 App 里改地址即可。

## 6. 验收清单（接手后建议逐项跑）

- [ ] `/api/health`：`api=up`、`database=up`、`worker=alive`、`llm_configured=true`
- [ ] admin 登录只看到 4 个系统菜单；`/api/contracts` 返回 403
- [ ] operator 可上传、列表只看到自己的**未归档**合同
- [ ] archivist 只能从归档入口看到归档件，且编辑/重试/删除均 403/409
- [ ] 上传 → 分析 → 报告 PDF → H5 A–G 展示全链路通
- [ ] 人工编辑报告后再次点重新分析会二次确认；导出「修改后报告 PDF」样式正确、中文不方框
- [ ] 连续 30 次 `/api/health` 不出现 `not_running` 抖动
- [ ] 所有服务进程 `MainWindowHandle=0`，桌面不再弹 python 窗口
- [ ] `git status` 里没有 `.env`、密钥、合同数据或证书
