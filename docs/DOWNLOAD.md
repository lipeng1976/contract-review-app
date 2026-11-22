# 下载与运行指南（给拿到仓库的人）

本仓库是「澳洲维州土地合同审查系统」的完整源码：FastAPI 后端 + H5 前端 +
Android WebView 壳 + PostgreSQL 表结构/权限种子 + 合同审查报告渲染 skill。

**仓库不含任何真实数据、密钥、证书或合同文件**（设计如此）。要跑起来需要自备
PostgreSQL 和一个 OpenAI 兼容的大模型接口。

---

## 一、下载源码（二选一）

### 方式 A：git clone（推荐，方便后续更新）

```bash
git clone <仓库地址>.git
cd contract-review-app-handoff
```

以后更新：`git pull` 即可。

### 方式 B：下载 zip（不想装 git 时）

在仓库页面点绿色 `Code` 按钮 → `Download ZIP` → 解压，得到
`contract-review-app-handoff/` 目录。

> 注意：zip 方式无法用 `git pull` 增量更新，每次要重新下载解压。

---

## 二、跑起来（4 步）

以下步骤在 `contract-review-app-handoff/` 目录下操作。

### 1. 准备 Python 环境

需要 Python 3.11+。建议用虚拟环境：

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# 回到项目根目录
cp .env.example .env
```

编辑 `.env`，**至少填这几项**：

| 变量 | 说明 |
|---|---|
| `PGHOST` / `PGPORT` / `PGUSER` / `PGPASSWORD` | 你的 PostgreSQL 连接信息 |
| `PGDATABASE` | 数据库名，如 `contract_review` |
| `PGAPIUSER` / `PGAPIPASSWORD` | 应用运行时用的只读/受限数据库账号 |
| `JWT_SECRET` | 随机串，生成：`python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 大模型接口（也可启动后在管理页配置） |

其余保持默认即可。详见 `.env.example` 注释与 `README.md` §4。

### 3. 初始化数据库

先建库并执行表结构与种子（种子只有权限/角色，无真实用户）：

```bash
createdb -h 127.0.0.1 -p 5434 -U postgres contract_review
psql -h 127.0.0.1 -p 5434 -U postgres -d contract_review -f db/schema.sql
psql -h 127.0.0.1 -p 5434 -U postgres -d contract_review -f db/seed.sql
```

创建第一个管理员（密码从环境变量读，不写进命令行历史）：

```bash
cd backend
BOOTSTRAP_PASSWORD='你的强密码' python -m db.bootstrap_admin admin "系统管理员"
```

### 4. 启动服务

在 `backend/` 目录开两个终端：

```bash
# 终端 1：API + H5 页面（默认 8790 端口）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8790 --no-access-log

# 终端 2：分析 worker
python -m worker.runner
```

浏览器打开 `http://127.0.0.1:8790/` 即可看到登录页。
健康检查：`curl http://127.0.0.1:8790/api/health`，返回 `"status":"ok"` 即正常。

---

## 三、常见调整

- **换端口 / 公网访问**：改 `.env` 的 `APP_PORT`、`PUBLIC_BASE_URL`，并用反向代理
  暴露 `0.0.0.0:8790`（PostgreSQL 只绑 `127.0.0.1`，不要对外）。
- **文件存哪**：上传原件、报告 PDF 都放在 `CONTRACT_DATA_DIR` 指定目录；数据库只存
  相对路径。换机器时拷走这个目录 + 改一个环境变量即可，数据库无需改。详见 README §4.1。
- **Android 壳**：源码在 `frontend/android/`，出厂默认后台地址在
  `assets/backend-url.txt`，构建脚本 `build.sh`（需 JDK17 + Android SDK）。签名证书
  不在仓库里，需要自己生成。
- **跑测试**：`backend/tests/` 下有 e2e 脚本，凭据从本地 `.env` 的 `TEST_*` 读取
  （见 `.env.example`），脚本本身不含明文密码。

---

## 四、文档地图

| 文件 | 内容 |
|---|---|
| `README.md` | 技术栈、完整启动步骤、环境变量、数据库说明 |
| `docs/ARCHITECTURE.md` | 架构与模块职责 |
| `docs/API.md` | 接口清单与鉴权方式 |
| `docs/DATABASE.md` | 表结构、备份与恢复 |
| `docs/OPERATIONS.md` | 运维命令与健康检查 |
| `docs/HANDOFF.md` | 交接红线、已知坑、外发检查清单 |
| `docs/TREE.md` / `docs/FILE_INDEX.md` | 目录结构与文件索引 |

遇到问题先看 `docs/HANDOFF.md` 的「已知坑」章节。
