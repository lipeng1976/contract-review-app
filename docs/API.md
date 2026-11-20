# API 参考

基础路径：本地 `http://127.0.0.1:8790`。所有 `/api/*` 业务接口使用 Cookie `cr_session` 鉴权；`/health` 与 `/api/health` 匿名可访问。

## System（匿名）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health`、`/api/health` | 健康检查：`api / database / worker / llm_configured`；worker 由心跳表算出 |
| GET | `/api/system/worker-status` | Worker 心跳明细、判活窗口、stall 阈值（需登录） |
| GET | `/api/config` | 返回 `publicBaseUrl` 与 `apiBase` |

## Auth

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/register` | 注册，账号 3–64 位字母数字及 `._-`，新用户 `pending` |
| POST | `/api/auth/login` | 登录，写 HttpOnly Cookie |
| POST | `/api/auth/logout` | 清除 Cookie |
| GET | `/api/auth/me` | 当前用户资料、角色、权限点、superuser 标志 |
| POST | `/api/auth/change-password` | 修改自己的密码 |

## Admin（纯系统管理权限）

| 方法 | 路径 | 所需权限 |
|---|---|---|
| GET | `/api/admin/pending-users` | `admin.users` |
| POST | `/api/admin/users/{uid}/approve` | `admin.users` |
| POST | `/api/admin/users/{uid}/reject` | `admin.users` |
| POST | `/api/admin/users/{uid}/suspend` | `admin.users` |
| GET | `/api/admin/users` | `admin.users` |
| PUT | `/api/admin/users/{uid}/roles` | `admin.users` |
| GET | `/api/admin/permissions` | `admin.roles` |
| GET/POST | `/api/admin/roles` | `admin.roles` |
| PUT/DELETE | `/api/admin/roles/{rid}` | `admin.roles` |
| GET/PUT | `/api/admin/llm-config` | `admin.llm` |
| POST | `/api/admin/llm-config/test` | `admin.llm` |
| GET | `/api/admin/audit` | `admin.audit` |

LLM 配置返回时 API Key 做掩码处理；测试接口只发一次最小请求验证连通性。

## Contracts

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/contracts` | 上传 PDF；同 owner 同 sha256 幂等返回原合同 |
| GET | `/api/contracts` | 普通列表，**排除归档件** |
| GET | `/api/contracts/archived` | 归档列表，需 `contract.archive_view`（路由必须在 `{contract_no}` 之前） |
| GET | `/api/contracts/{no}` | 详情；归档件按权限隔离 |
| PUT | `/api/contracts/{no}/visibility` | 改可见性 |
| POST | `/api/contracts/{no}/archive` | 归档/恢复，需 `contract.archive`；归档后禁止编辑/重试/删除 |
| GET | `/api/contracts/{no}/items` | A–G 条目，按 `section + 数字编号` 排序（A1、A2…A10） |
| PUT | `/api/contracts/{no}` | 编辑合同字段 |
| POST | `/api/contracts/{no}/items` | 新增条目 |
| PUT | `/api/contracts/{no}/items/{item_id}` | 编辑条目；记录 `edited_fields` |
| GET | `/api/contracts/{no}/audit` | 该合同操作日志 |
| GET | `/api/contracts/{no}/export` | CSV/文本导出 |
| GET | `/api/contracts/{no}/original` | 下载合同原件，需 `contract.download` |
| GET | `/api/contracts/{no}/report` | 下载原始报告 PDF |
| GET | `/api/contracts/{no}/edited-report` | 下载修改后报告 PDF |
| POST | `/api/contracts/{no}/retry` | 重新分析；已修改报告需 `confirm=1`；卡住任务需 `force=1` |
| DELETE | `/api/contracts/{no}` | 删除合同及从属数据，归档件禁止 |

## Workbench

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/followups` | 跨合同待办/追问，支持 `q / contract_no / limit`；**不含归档件** |

## 错误约定

- `401` 未登录/会话失效；`403` 权限或状态不足；`404` 资源不存在或无权看到（归档隔离常用）。
- `409 REPORT_EDITED_CONFIRM_REQUIRED`：报告已被人工修改，重新分析必须先确认。
- `400`：参数/账号格式错误；`500` 由异常处理器统一返回，不暴露堆栈。
