# 数据库说明

## 文件边界

- `db/schema.sql`：纯 DDL。可重复执行，不删除数据。
- `db/seed.sql`：幂等种子，只有权限点、内置角色和角色授权；**没有用户、密码、API Key、业务数据**。
- 历史迁移脚本未收入移交包；老库升级需要时从原项目迁移目录按序执行，并注意 `HANDOFF.md` 中「不可回退」几条。

## 表

| 表 | 作用 |
|---|---|
| `users` | 账号、bcrypt 哈希、状态（pending/active/rejected/suspended） |
| `permissions` / `roles` / `role_permissions` / `user_roles` | RBAC 目录与授权 |
| `contracts` | 合同主表，含 `owner_id`、`status`、`archived_at/by`；`source_path` 存原件相对路径 |
| `analysis_jobs` | 每次分析尝试、阶段、错误、lease |
| `contract_reports` | 报告版本、JSON 载荷、`report_path`（相对路径）与编辑标记 |
| `report_items` + `report_items_a..g` | A–G 分区的核查条目，含 `edited_fields` |
| `llm_config` | 单行配置，Worker 热读取 |
| `audit_logs` | 操作审计 |
| `worker_heartbeats` | Worker 心跳；`/api/health` 据此判活 |

`report_items` 按 `section` 做 LIST 分区；排序必须用数字后缀（`regexp_replace(code,'[^0-9]','','g')::numeric`），不能按字符串排，否则 A10 会排在 A2 前面。

## RLS 判据

- `app_uid()` 读 `current_setting('app.uid', true)`；未设置返回 NULL，保护性拒绝。
- `is_admin()` 只用于系统表（users / roles / audit / llm_config），**不参与业务四表**。
- `has_perm(p_code)` 只在实际授权或 `admin.%` + superuser 时返回真。
- `contracts_read / jobs_read / reports_read / items_read`：owner 可见；归档件额外允许 `contract.archive_view` 只读。INSERT/UPDATE 仅 owner，不放宽归档。
- API 用 `app_api` 连接并 `SET LOCAL app.uid`；Worker/引导用超级用户连接，绕过 RLS 推进状态。

## 初始化顺序

```bash
createdb -h 127.0.0.1 -p 5434 -U postgres contract_review
psql -h 127.0.0.1 -p 5434 -U postgres -d contract_review -f db/schema.sql
psql -h 127.0.0.1 -p 5434 -U postgres -d contract_review -f db/seed.sql
# 再按 .env 设置 app_api 口令，然后运行 bootstrap_admin.py
```

不要在此处写入任何真实密码。若需要重跑 `schema.sql`，它是幂等的；`seed.sql` 用 `ON CONFLICT DO NOTHING`，不会覆盖你在 App 里改过的角色授权。

## 备份

- 全量：`pg_dump -h 127.0.0.1 -p 5434 -U postgres -Fc contract_review > backup.dump`
- 文件：`$CONTRACT_DATA_DIR/contracts/`（原件）与 `$CONTRACT_DATA_DIR/reports/`（PDF + model.json）需一并备份。DB 里只存**相对路径**（如 `contracts/CT-xxx/original.pdf`），恢复时用同一份 data 目录即可，路径无需重写。
- 迁移脚本：`backend/db/migrate_relative_paths.py` 把历史绝对路径转成相对路径（默认 dry-run，`--yes` 执行，幂等）。新库从 `schema.sql` 建表后天然就是相对路径，不需要跑。
- 恢复前先停 API 与 Worker，避免心跳和 lease 写入竞争。
