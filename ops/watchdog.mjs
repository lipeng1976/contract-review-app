// 合同审查 App —— 本机后台看门狗。
//
// 与 D:\easy_learn_home\watchdog.mjs 同一套设计，只是把服务换成合同项目的三个：
//   PG :5434 → FastAPI :8790 → 分析 Worker（不监听端口）
//
// ## 为什么不用 Windows 服务
// 本机没有管理员权限，`sc create` / NSSM 都装不了。退一层：用**启动文件夹**
// 拉起 supervisor，由 supervisor 看住本脚本，本脚本看住三个服务。
//
// ## 判据是「业务端点/端口」而不是「进程还在不在」
// 进程活着 ≠ 服务可用。PG 处于恢复模式、API 事件循环卡死、连接池耗尽时，
// 进程都在、请求却不返回；只看进程的守护会对这种状态报「一切正常」。
// 所以：PG 探 TCP 5434，API 探 /api/health，Worker 探 /api/health 里的
// components.worker（那是心跳表算出来的，比"进程在"可靠）。
//
// ## 为什么不自己管子进程
// 手工用 start-backend.cmd 起的服务也要纳入守护。用**端口**当唯一事实来源：
// 探活通过就什么都不做（无论谁起的），失败才按端口找 PID 杀掉重起。
// 这样看门狗可以随时启停，不会出现「两套在抢」。
//
// ## 退避
// 配置写错时服务会「起来就崩」。连续失败按 10s → 30s → 60s → 5min 退避，
// 成功一次归零，避免把日志刷爆、CPU 打满。
//
// 用法：
//   node watchdog.mjs            # 前台跑（调试用，Ctrl+C 退出）
//   node watchdog.mjs --once     # 只做一轮探测 + 修复，然后退出（验证用）

import { spawn, execFileSync } from 'node:child_process';
import {
  appendFileSync, closeSync, existsSync, mkdirSync, openSync, readFileSync, renameSync,
  statSync, writeFileSync, unlinkSync,
} from 'node:fs';
import net from 'node:net';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = process.env.CONTRACT_ROOT ?? join(here, '..');
const BACKEND = join(ROOT, 'backend');
const LOGS = join(ROOT, 'logs');
const PGDATA = process.env.CONTRACT_PGDATA ?? join(ROOT, 'pgdata');
const PGBIN = process.env.CONTRACT_PGBIN ?? join(ROOT, 'pg', 'bin');
const PY = process.env.CONTRACT_PY ?? 'python';
// 这个 venv 的 python.exe 是 shim：它自己再拉起 versions/3.13.12/python.exe。
// Node 的 windowsHide 只作用于直接 spawn 的 shim，孙子进程仍会建控制台窗口 ——
// 表现为桌面周期性闪出 python.exe 黑框。pythonw.exe 全链路无窗口，日志照写。
const PYW = process.env.CONTRACT_PYW
  ?? PY.replace(/python\.exe$/i, 'pythonw.exe');

const API_PORT = Number(process.env.APP_PORT ?? 8790);
const PG_PORT = Number(process.env.PGPORT ?? 5434);
const API_HEALTH = `http://127.0.0.1:${API_PORT}/api/health`;

// ── 系统命令用绝对路径 ──────────────────────────────────────────────
// 被启动文件夹、别的 shell 拉起时 PATH 可能被裁剪，execFileSync('taskkill')
// 会抛 ENOENT；而这类调用通常包在 try/catch 里 —— 结果就是"杀不掉进程"
// 被当成"没有进程"，一路静默。绝对路径从根上消除这个失败模式。
const SYSTEM32 = join(process.env.SystemRoot ?? 'C:\\Windows', 'System32');
const NETSTAT = join(SYSTEM32, 'netstat.exe');
const TASKKILL = join(SYSTEM32, 'taskkill.exe');
const POWERSHELL = join(SYSTEM32, 'WindowsPowerShell', 'v1.0', 'powershell.exe');

mkdirSync(LOGS, { recursive: true });

// ── 参数 ────────────────────────────────────────────────────────────
const ONCE = process.argv.includes('--once');
const INTERVAL_MS = Number(process.env.WATCHDOG_INTERVAL_MS ?? 10_000);
// --once 的语义是「一轮探测 + 修复」，所以阈值降为 1：
// 否则要等 3 次才动手，单轮跑完什么都不做，验证时会被误判成"修不好"。
const MAX_FAILS = ONCE ? 1 : Number(process.env.WATCHDOG_MAX_FAILS ?? 3);
const BACKOFF_MS = [10_000, 30_000, 60_000, 300_000];

// ── 日志：追加 + 超 2MB 轮转一次 ────────────────────────────────────
const LOG_PATH = join(LOGS, 'watchdog.log');
// 隐藏启动时没有控制台，写 stdout 可能失败 —— 只在真有终端时回显。
const TTY = process.stdout.isTTY === true || ONCE;
function log(msg) {
  // 用本地时间：日志给人看，UTC 会把「19:36 崩」读成「11:36」。
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
    + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  const line = `[${stamp}] ${msg}`;
  if (TTY) console.log(line);
  try {
    if (existsSync(LOG_PATH) && statSync(LOG_PATH).size > 2 * 1024 * 1024) {
      renameSync(LOG_PATH, LOG_PATH + '.1');
    }
    appendFileSync(LOG_PATH, line + '\n');
  } catch {
    /* 日志写不进去不该让看门狗停摆 */
  }
}

// ── 单实例锁 ────────────────────────────────────────────────────────
// 锁里记 PID，不是「文件存在」：看门狗被强杀时来不及删锁文件，
// 只看文件在不在会导致永远起不来（自愈机制自己成了单点故障）。
const LOCK = join(LOGS, 'watchdog.lock');
function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
function acquireLock() {
  if (existsSync(LOCK)) {
    const pid = parseInt(readFileSync(LOCK, 'utf8'), 10);
    if (Number.isFinite(pid) && pid !== process.pid && alive(pid)) {
      log(`已有看门狗在跑（pid=${pid}），本实例退出`);
      process.exit(0);
    }
    log(`接管失效锁（旧 pid=${pid} 已不在）`);
  }
  writeFileSync(LOCK, String(process.pid));
}
function releaseLock() {
  try {
    if (existsSync(LOCK) && parseInt(readFileSync(LOCK, 'utf8'), 10) === process.pid) unlinkSync(LOCK);
  } catch {
    /* 忽略 */
  }
}
if (!ONCE) acquireLock();
process.on('exit', releaseLock);

// 无窗口启动时 stderr 是丢掉的。没有兜底，顶层 await 抛错会让进程安静消失，
// 表现为「自愈脚本自己不自愈」，日志里还只有正常启动那两行。
process.on('uncaughtException', (e) => log(`✗ 未捕获异常：${e?.stack ?? e}`));
process.on('unhandledRejection', (e) => log(`✗ 未处理的 Promise 拒绝：${e?.stack ?? e}`));
process.on('SIGINT', () => {
  log('收到 Ctrl+C，看门狗退出（三个服务继续运行）');
  releaseLock();
  process.exit(0);
});

// ── 探活 ────────────────────────────────────────────────────────────
async function fetchHealth() {
  try {
    const r = await fetch(API_HEALTH, { signal: AbortSignal.timeout(4000) });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}
function probeTcp(host, port) {
  return new Promise((res) => {
    const s = net.connect({ host, port });
    const done = (v) => { s.destroy(); res(v); };
    s.setTimeout(3000);
    s.once('connect', () => done(true));
    s.once('timeout', () => done(false));
    s.once('error', () => done(false));
  });
}

// ── 端口占用者 ──────────────────────────────────────────────────────
function pidsOnPort(port) {
  try {
    const out = execFileSync(NETSTAT, ['-ano'], { encoding: 'utf8', windowsHide: true });
    const pids = new Set();
    for (const line of out.split(/\r?\n/)) {
      if (!line.includes('LISTENING')) continue;
      const cols = line.trim().split(/\s+/);
      // TCP  0.0.0.0:8790  0.0.0.0:0  LISTENING  28804
      if (cols.length < 5) continue;
      const local = cols[1];
      const at = local.lastIndexOf(':');
      if (at < 0 || local.slice(at + 1) !== String(port)) continue;
      const pid = parseInt(cols[cols.length - 1], 10);
      if (Number.isFinite(pid) && pid > 0) pids.add(pid);
    }
    return [...pids];
  } catch {
    return [];
  }
}
function killPid(pid, why) {
  try {
    execFileSync(TASKKILL, ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
    log(`  已结束${why} pid=${pid}`);
    return true;
  } catch (e) {
    log(`  ⚠️ 结束 pid=${pid} 失败：${e.message}`);
    return false;
  }
}
function killPort(port) {
  const pids = pidsOnPort(port);
  for (const pid of pids) killPid(pid, `占用 :${port} 的进程`);
  return pids.length;
}

// ── Worker 进程定位 ─────────────────────────────────────────────────
// Worker 不监听任何端口，只能按命令行找。tasklist 不给命令行，用 PowerShell 查。
// 注意：必须匹配 `worker.runner`，否则会把 uvicorn 一起杀掉。
function workerPids() {
  try {
    const out = execFileSync(
      POWERSHELL,
      ['-NoProfile', '-NonInteractive', '-Command',
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
        + '| Where-Object { $_.CommandLine -match \'worker\\.runner\' } '
        + '| ForEach-Object { $_.ProcessId }'],
      { encoding: 'utf8', windowsHide: true, timeout: 15000 },
    );
    return out.split(/\r?\n/)
      .map((s) => parseInt(s.trim(), 10))
      .filter((n) => Number.isFinite(n) && n > 0);
  } catch {
    return [];
  }
}
function stopWorker() {
  const pids = workerPids();
  for (const pid of pids) killPid(pid, ' worker');
  return pids.length;
}

// ── 启动器 ──────────────────────────────────────────────────────────
function spawnDetached(cmd, args, cwd, logName) {
  let fd = -1;
  try {
    fd = openSync(join(LOGS, logName), 'a');
  } catch {
    fd = -1;
  }
  return new Promise((res) => {
    const child = spawn(cmd, args, {
      cwd,
      windowsHide: true,
      // detached 必须为 true：否则三个服务共享看门狗的控制台，
      // 看门狗一被杀，子进程收到 CTRL_CLOSE_EVENT 一起退出。
      // 看门狗本来就不靠退出事件判断存活（判据是探活），独立进程才与设计一致。
      detached: true,
      stdio: ['ignore', fd >= 0 ? fd : 'ignore', fd >= 0 ? fd : 'ignore'],
      env: process.env,
    });
    child.on('error', (e) => {
      log(`  ✗ spawn 失败：${e.message}`);
      if (fd >= 0) closeSync(fd);
      res(false);
    });
    child.on('spawn', () => {
      log(`  ▶ 已启动 pid=${child.pid}（cwd=${cwd}）`);
      if (fd >= 0) closeSync(fd);
      child.unref();
      res(true);
    });
  });
}

// postmaster.pid 残留是这里必须处理的：PG 被强杀后 pid 文件还在，
// 新的 postgres.exe 会拒绝启动（lock file already exists），看门狗一直重试 ——
// 表现成「PG 永远起不来」，日志里只有一句笼统的启动失败。
function clearStalePgPid() {
  const f = join(PGDATA, 'postmaster.pid');
  if (!existsSync(f)) return;
  const pid = parseInt(readFileSync(f, 'utf8').split(/\r?\n/)[0], 10);
  if (!Number.isFinite(pid)) return;
  if (alive(pid)) {
    // 能走到这里说明 5434 探活已经判失败。pid 还活着 = postmaster 僵住 /
    // recovery 卡死，端口没在听但锁文件还在。不结束它，新进程必然被拒，
    // 这一项就永远修不好。探活通过时本函数根本不会被调用，不会误杀。
    log(`  postmaster.pid 指向 pid=${pid} 仍存活但端口不通，先结束它`);
    killPid(pid, ' postmaster');
  }
  try {
    renameSync(f, f + '.stale');
    log(`  清理残留 postmaster.pid（旧 pid=${pid}）`);
  } catch (e) {
    log(`  ⚠️ 清理 postmaster.pid 失败：${e.message}`);
  }
}

// ⚠️ 不用 pg_ctl：本机实测 `pg_ctl start` 派生的 postmaster 会随启动它的
// 命令/控制台一起被回收（日志写了「准备接受连接」，端口立刻消失）。
// 直接跑 postgres.exe + detached + unref 才能跨会话活下来；当前机器上
// 正在跑的 PG 也正是这个形态（`postgres.exe -D pgdata -p 5434`）。
function startPg() {
  clearStalePgPid();
  const pg = join(PGBIN, 'postgres.exe');
  if (!existsSync(pg)) {
    log(`  ✗ 找不到 ${pg}`);
    return Promise.resolve(false);
  }
  // -h / -p 显式写死：postgresql.conf 被改过也不会把库暴露到公网，
  // 也不会因为端口漂移让 5434 的探活永远失败。
  return spawnDetached(
    pg,
    ['-D', PGDATA, '-h', '127.0.0.1', '-p', String(PG_PORT)],
    ROOT,
    'pg-run.log',
  );
}

function startApi() {
  if (!existsSync(join(BACKEND, 'app', 'main.py'))) {
    log(`  ✗ 找不到 ${join(BACKEND, 'app', 'main.py')}`);
    return Promise.resolve(false);
  }
  const py = existsSync(PYW) ? PYW : PY;
  if (!existsSync(py)) {
    log(`  ✗ 找不到 Python：${py}`);
    return Promise.resolve(false);
  }
  return spawnDetached(
    py,
    ['-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', String(API_PORT), '--no-access-log'],
    BACKEND,
    'api.log',
  );
}

function startWorker() {
  if (!existsSync(join(BACKEND, 'worker', 'runner.py'))) {
    log(`  ✗ 找不到 ${join(BACKEND, 'worker', 'runner.py')}`);
    return Promise.resolve(false);
  }
  const py = existsSync(PYW) ? PYW : PY;
  if (!existsSync(py)) {
    log(`  ✗ 找不到 Python：${py}`);
    return Promise.resolve(false);
  }
  return spawnDetached(py, ['-m', 'worker.runner'], BACKEND, 'worker.log');
}

// ── 服务定义 ────────────────────────────────────────────────────────
const SERVICES = [
  {
    key: 'pg',
    label: 'PostgreSQL :5434',
    port: PG_PORT,
    // 崩溃后要跑 redo，恢复比 API/Worker 慢；20s 不够会把刚启动的
    // postmaster 判成失败，下一轮又杀掉重起，反而永远起不来。
    readyTimeoutMs: 45_000,
    probe: () => probeTcp('127.0.0.1', PG_PORT),
    start: startPg,
  },
  {
    key: 'api',
    label: 'FastAPI :8790',
    port: API_PORT,
    // 没有 PG 就起不来（启动即 ECONNREFUSED），依赖它。
    dependsOn: 'pg',
    // 只要求「HTTP 200 且 components.api=up」。不要顺手要求 status==='ok'：
    // DB 挂时 health 返回 status='degraded'，但那是 pg 这一项该修的问题，
    // 重启 API 毫无意义（起来照样连不上库），反而会把日志刷满。
    probe: async () => {
      const h = await fetchHealth();
      return !!(h && h.components && h.components.api === 'up');
    },
    start: startApi,
  },
  {
    key: 'worker',
    label: '分析 Worker',
    // 探活完全借道 health（见下），所以 PG 或 API 没就绪时它必然报 false。
    // 不声明依赖的话，DB 挂掉会被误读成「worker 死了」而反复重启 worker。
    dependsOn: ['pg', 'api'],
    // ⚠️ 与其它两项本质不同：worker 不监听端口，所以没有 port。
    //    探活走 API 的 health —— 那个 alive 是心跳表算出来的，
    //    比"进程在不在"可靠（进程活着但卡死时心跳会过期）。
    probe: async () => {
      const h = await fetchHealth();
      return !!(h && h.components && h.components.worker === 'alive');
    },
    // 一个 worker 就够；多开会重复领任务。停的时候全停。
    stop: stopWorker,
    start: startWorker,
  },
];

// ── 等待就绪 ────────────────────────────────────────────────────────
// 默认 20 秒而不是 60 秒：正常 1–3 秒就绪；等太久会把「这个服务起不来」
// 拖成「整个看门狗失明 60 秒」。PG 走自己的 readyTimeoutMs（要跑 redo）。
async function waitReady(svc, timeoutMs = svc.readyTimeoutMs ?? 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await svc.probe()) return true;
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
}

// ── 日志轮转 ────────────────────────────────────────────────────────
// 只在重启时轮转：不额外制造重启，又能保证日志不会无限长。
const LOG_ROTATE_BYTES = 20 * 1024 * 1024;
const SERVICE_LOG_NAMES = { api: 'api.log', worker: 'worker.log' };
function rotateServiceLog(key) {
  const name = SERVICE_LOG_NAMES[key];
  if (!name) return;
  const p = join(LOGS, name);
  try {
    if (!existsSync(p) || statSync(p).size < LOG_ROTATE_BYTES) return;
    const bak = p + '.1';
    if (existsSync(bak)) unlinkSync(bak);
    renameSync(p, bak);
    log(`  ↺ 日志轮转：${name} → ${name}.1`);
  } catch (e) {
    log(`  ⚠️ 日志轮转失败（${name}）：${e.message}`);
  }
}

// ── 重启 ────────────────────────────────────────────────────────────
// 重启前先确认依赖：数据库没起来就去重启后端，只会得到
// 「起来就 ECONNREFUSED 崩溃」的循环，把日志刷满、毫无意义。
async function restart(svc, fails) {
  const deps = svc.dependsOn
    ? (Array.isArray(svc.dependsOn) ? svc.dependsOn : [svc.dependsOn])
    : [];
  for (const key of deps) {
    const dep = SERVICES.find((s) => s.key === key);
    // 只有 ok===true 才算就绪：undefined（首轮还没探到）也不能动手，
    // 否则 DB 一挂就被误读成「worker 死了」，白重启一遍 worker。
    if (dep && state.get(dep.key).ok !== true) {
      log(`… 暂不重启 ${svc.label}：依赖的 ${dep.label} 还没就绪`);
      // 三态里的「跳过」：这不是失败，不该进入退避
      // （否则 PG 慢几秒恢复，API 就要白等 10 秒才重试）。
      return 'skipped';
    }
  }
  log(`↻ 重启 ${svc.label}（连续 ${fails} 次探活失败）`);
  if (typeof svc.stop === 'function') svc.stop();
  else if (svc.port) killPort(svc.port);
  await new Promise((r) => setTimeout(r, 1500));
  // 进程被杀、文件句柄释放之后才轮转 —— Windows 下占用的文件改不了名。
  rotateServiceLog(svc.key);
  const started = await svc.start();
  if (!started) {
    log(`  ✗ ${svc.label} 启动命令失败`);
    return false;
  }
  const readyMs = svc.readyTimeoutMs ?? 20_000;
  const ok = await waitReady(svc, readyMs);
  log(ok ? `  ✓ ${svc.label} 已恢复` : `  ✗ ${svc.label} 启动后 ${readyMs / 1000}s 内仍未就绪`);
  return ok;
}

// ── 主循环 ──────────────────────────────────────────────────────────
// ⚠️ 每个服务各自一条循环，不是串行 for。
//    串行时会踩坑：PG 挂了 → API 起不来 → waitReady 一等 20 秒，
//    这期间 PG 的探测根本轮不到，**一个服务卡住 = 全体失明**。
const state = new Map(
  SERVICES.map((s) => [
    s.key,
    { fails: 0, backoff: 0, nextRetryAt: 0, ok: undefined, restartRequested: false },
  ]),
);

log(`看门狗启动 pid=${process.pid}  间隔=${INTERVAL_MS / 1000}s  阈值=${MAX_FAILS} 次`);
log(`  监控：${SERVICES.map((s) => s.label).join(' · ')}`);

async function tickOne(svc) {
  const st = state.get(svc.key);

  // PG 从「坏」变「好」的那一刻，API 连接池里的连接全是死的 —— 探活却是绿的
  // （health 只证明进程活着）。所以主动重启一次，否则客户端会一直拿 500。
  if (st.restartRequested) {
    st.restartRequested = false;
    log(`↻ ${svc.label}：依赖已恢复，主动重建连接池`);
    const r = await restart(svc, MAX_FAILS);
    st.fails = 0;
    if (r === 'skipped') {
      st.nextRetryAt = Date.now() + 5000;
    } else {
      st.backoff = r ? 0 : st.backoff + 1;
      st.nextRetryAt = r ? 0 : Date.now() + BACKOFF_MS[Math.min(st.backoff, BACKOFF_MS.length - 1)];
    }
    return;
  }

  const ok = await svc.probe();
  if (ok) {
    if (st.ok === false) {
      log(`✓ ${svc.label} 已恢复探活`);
      if (svc.key === 'pg') {
        const api = state.get('api');
        if (api) api.restartRequested = true;
      }
    }
    st.ok = true;
    st.fails = 0;
    st.backoff = 0;
    st.nextRetryAt = 0;
    return;
  }
  st.ok = false;

  st.fails += 1;
  if (st.fails < MAX_FAILS) return;
  if (Date.now() < st.nextRetryAt) return; // 退避期内静默等待，不刷日志
  const r = await restart(svc, st.fails);
  st.fails = 0;
  if (r === 'skipped') {
    st.nextRetryAt = Date.now() + 5000;
  } else if (r) {
    st.backoff = 0;
    st.nextRetryAt = 0;
  } else {
    const wait = BACKOFF_MS[Math.min(st.backoff, BACKOFF_MS.length - 1)];
    st.backoff += 1;
    st.nextRetryAt = Date.now() + wait;
    log(`  ⏳ ${svc.label} ${wait / 1000}s 后再试（第 ${st.backoff} 次退避）`);
  }
}

async function runService(svc) {
  for (;;) {
    try {
      await tickOne(svc);
    } catch (e) {
      log(`✗ ${svc.label} 探测异常：${e?.stack ?? e}`);
    }
    await new Promise((r) => setTimeout(r, svc.intervalMs ?? INTERVAL_MS));
  }
}

if (ONCE) {
  for (const svc of SERVICES) await tickOne(svc);
  log('--once 完成');
  releaseLock();
  process.exit(0);
}

await Promise.all(SERVICES.map(runService));
