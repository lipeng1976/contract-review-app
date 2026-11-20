# 运维说明

## 日常状态

```bash
ops/status.cmd                 # Windows：端口、锁、health、公网地址
curl http://127.0.0.1:8790/api/health
curl http://127.0.0.1:8790/api/system/worker-status   # 需登录 Cookie
```

日志位置（运行时生成，交接包不含）：`logs/watchdog.log`、`logs/supervisor.log`、`logs/api.log`、`logs/worker.log`、`logs/pg-run.log`。

## 看门狗链路

```
Startup 文件夹 / contract-watchdog.cmd
  └─ contract-watchdog-launch.vbs (wscript, 窗口样式 0 = 完全隐藏)
       └─ watchdog-supervisor.mjs  (watchdog 退出 5s 后重开)
            └─ watchdog.mjs        (每 10s 探 PG / API / Worker)
                 ├─ PG      :5434 TCP 探活
                 ├─ API     /api/health 探活
                 └─ Worker  health.components.worker 探活
```

- 探活失败 3 次才重启；单服务退避 10s → 30s → 60s → 5min。
- `dependsOn` 保证 DB/API 没就绪时不反复重启 Worker。
- 拉服务一律用 `pythonw.exe`，避免 shim 派生带控制台的子进程弹黑框。
- `contract-watchdog-stop.cmd` 只结束 supervisor/watchdog 的 PID，**不**带 `/T`，不会连带杀服务；要停服务需单独结束 PG/API/Worker。

## 手工启动（调试用）

```bash
# 终端 1
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8790 --no-access-log
# 终端 2
cd backend && python -m worker.runner
```

PG 需自行启动并监听 `PGPORT`。Windows 上不要用 `pg_ctl start` 作为常驻方式，子进程会随启动命令被回收；用 `postgres.exe -D pgdata -h 127.0.0.1 -p 5434` 直启并保持进程独立。

## 常见故障

| 现象 | 先查什么 |
|---|---|
| App 弹「设置后台地址」 | `curl /api/health` 是否通；地址配置是否指向正确公网地址；服务是否全停 |
| 一直显示「待分析」 | `components.worker` 是否 alive；`worker_heartbeats.last_seen` 是否新鲜；lease 是否被旧 Worker 占住（必要时 `force=1`） |
| Worker 被反复重启、桌面弹 python 窗口 | `worker_status()` 是否把 `age_seconds=0` 当 falsy；看门狗是否仍用 `python.exe` 而非 `pythonw.exe` |
| 中文变蓝色方框 | 渲染器是否走 `cjk_safe()`；新增 Paragraph 文本是否漏包 |
| admin 仍看到业务菜单 | `/api/auth/me` 的 permissions、admin 的 `role_permissions`、`has_perm()` 通配、业务表 RLS 四层是否一致 |
| 归档合同出现在普通列表 | SQL 是否漏 `AND c.archived_at IS NULL`；路由顺序是否被改坏 |
| 登录态全部失效 | `JWT_SECRET` 是否轮换；轮换后需所有人重新登录 |

## 上线前检查

- `.env` 不在版本库；密钥、keystore、pgdata、data、logs 均被 `.gitignore` 排除。
- 数据库使用 `app_api` 低权限账号运行 API，RLS 开启。
- `/api/health` 全绿，`llm_configured=true`。
- 备份策略覆盖数据库 + `data/contracts` + `data/reports` + 签名证书。
