// 看门狗的外层守护：watchdog.mjs 退出就重新拉起。
//
// ## 为什么需要这一层
// 启动文件夹里只能放一个短命令，命令跑完就结束，没法负责
// 「看门狗自己挂了怎么办」。这一层的职责只有一句：
// **watchdog.mjs 退出（不管什么原因），5 秒后重开**。
//
// ## 为什么不把重启逻辑写进 watchdog 自己
// 进程无法在崩溃后重启自己 —— 它已经不在了。必须有一个不被它影响的父进程。
//
// ## 本层自己不设防
// 只有一个 for 循环 + spawn，不碰网络、不解析配置，崩溃概率趋近于零。
// 真挂了（比如被手动杀）只能等下次登录，这是本机权限条件下能接受的最差情况。
//
// 日志：logs\supervisor.log
import {
  appendFileSync, mkdirSync, openSync, closeSync,
  existsSync, readFileSync, writeFileSync, unlinkSync,
} from 'node:fs';
import { spawn } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const LOGS = join(here, '..', 'logs');
mkdirSync(LOGS, { recursive: true });
const LOG = join(LOGS, 'supervisor.log');

function say(msg) {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  const line = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
    + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())} ${msg}`;
  try {
    appendFileSync(LOG, line + '\n');
  } catch {
    /* 忽略 */
  }
  if (process.stdout.isTTY) console.log(line);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 启动文件夹可能被重复触发（登录一次 + 手动双击一次）。watchdog 自己有锁，
// 但第二个 supervisor 会不断把「抢不到锁、立刻退出」的 watchdog 重新拉起，
// 日志里全是噪音。这一层自己也上锁，从根上只留一条守护链。
// 锁里记 PID 而非「文件存在」：被强杀时来不及删锁，只看存在会永远起不来。
const LOCK = join(LOGS, 'supervisor.lock');
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
      say(`已有 supervisor 在跑（pid=${pid}），本实例退出`);
      process.exit(0);
    }
    say(`接管失效锁（旧 pid=${pid} 已不在）`);
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
acquireLock();
process.on('exit', releaseLock);

say(`supervisor 启动 pid=${process.pid}`);
for (;;) {
  let fd = -1;
  try {
    fd = openSync(join(LOGS, 'watchdog.log'), 'a');
  } catch {
    fd = -1;
  }
  const child = spawn(process.execPath, [join(here, 'watchdog.mjs')], {
    cwd: here,
    windowsHide: true,
    // watchdog 自己的日志写文件；这里只兜「模块加载失败」这类它来不及记录的错。
    stdio: ['ignore', fd >= 0 ? fd : 'ignore', fd >= 0 ? fd : 'ignore'],
    env: process.env,
  });
  if (fd >= 0) closeSync(fd);
  const started = Date.now();
  const code = await new Promise((res) => {
    child.on('error', (e) => {
      say(`spawn watchdog 失败：${e.message}`);
      res(-1);
    });
    child.on('close', (c) => res(c));
  });
  const lived = Math.round((Date.now() - started) / 1000);
  say(`watchdog 退出 code=${code}，存活 ${lived}s；5 秒后重启`);
  await sleep(5000);
}
