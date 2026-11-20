# 架构说明

## 组件关系

```
                       ┌────────────────────┐
   手机 / PC 浏览器 ───▶ │ H5 (frontend/h5)    │
   Android WebView 壳 ─▶ │ Android shell      │
                       └─────────┬──────────┘
                                 │ HTTPS/HTTP + JWT Cookie
                       ┌─────────▼──────────┐
                       │ FastAPI (backend)   │  routers/* → core.py → db.py
                       └────┬──────────┬─────┘
                            │          │
              server_conn() │          │ api_session(uid) → RLS
                            │          ▼
                       ┌────▼────────────────┐
                       │ PostgreSQL 18        │  users / contracts / jobs /
                       │ + worker_heartbeats  │  reports / items(A-G) / llm_config
                       └────▲─────────────────┘
                            │ 心跳 + lease + 状态推进
                       ┌────┴─────────────────┐
                       │ Worker (S0 → S7)      │  LLM → JSON → validate → ingest → PDF
                       └────┬─────────────────┘
                            │ subprocess
                       ┌────▼─────────────────┐
                       │ bundled skill/reportlab│  build_review_report.py
                       └──────────────────────┘
```

- **API 进程**：只处理请求。业务查询走 `app_api` 低权限角色并 `SET LOCAL app.uid`，数据库 RLS 做最后一道隔离。
- **Worker 进程**：不监听端口，靠 `worker_heartbeats` 表被 `/api/health` 判活；用超级用户连接推进状态（客户端 RLS 故意禁止这些写操作）。
- **看门狗**：Node 进程分别探 PG 端口、API health、Worker 心跳；外层 supervisor 在看门狗退出 5 秒后拉起它。

## 分析流水线（S0–S7）

| 阶段 | 做什么 |
|---|---|
| S0 | claim：领取 `queued` 任务并加 lease，防止多 Worker 重复处理 |
| S1 | 解析合同 PDF 文本与页码（`worker/parse.py`） |
| S2 | 扫描关键条款/姓名/法条，补充上下文（`worker/extract.py` + skill 目录） |
| S3 | 调 LLM 产出严格 JSON（A–G 82 行），4 并发、超时重试 |
| S4 | `worker/validate.py` 硬校验：行数、编号、状态枚举、来源页码、第二 Vol/Folio 等 |
| S5 | `worker/render.py` 调 skill 的 `build_review_report.py` 生成 PDF |
| S6 | `db/ingest_report.py` 把 JSON 按 A–G 分区入库，写 sha256 |
| S7 | 更新 `contracts.status`、报告版本，清理 lease |

失败时写 `analysis_jobs.error_code/error_msg`，前端据此展示「失败/待分析/分析中/完成/排队」五态；`queued` 是否有人处理由 Worker 心跳判定，而不是只看状态字段。

## 权限模型

- 认证：bcrypt 校验后签发 8 小时 JWT 到 HttpOnly Cookie `cr_session`。
- 每次请求都重新从 DB 读 `status / roles / permissions`，审批、停用、改角色即时生效，不依赖 JWT 里的旧内容。
- `require_active` 是状态门禁；`require_perm(code)` 是能力门禁；`require_contract_read` 同时接受 `contract.view` 和 `contract.archive_view`，具体哪一类由 endpoint 在知道合同是否归档后再判断。
- `has_perm()` 只对 `admin.%` 给 superuser 通配；业务权限必须字面授权。这一点是 admin 纯系统管理员的实现基础。

## 报告与编辑

- 原始报告 PDF 保存路径在 `contract_reports.report_path`（相对 `CONTRACT_DATA_DIR`，如 `reports/CT-xxx/v1/report.pdf`），sha256 校验。所有文件的路径读写统一走 `app/paths.py` 的 `resolve_path()` / `to_relative()`，DB 不存绝对路径。
- 编辑合同/条目时写 `edited_at`、`edit_count`、`edited_fields`；再次分析需 `confirm=1`。
- 「修改后报告 PDF」复用原渲染器：从 DB 取最新值覆盖 `model.json`，按 `edited_fields` 构造 `_edited` 标记，字体字号不变，仅斜体深蓝（中文楷体）。
