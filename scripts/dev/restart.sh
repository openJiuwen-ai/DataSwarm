#!/usr/bin/env bash
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# ===========================================================================
# DataSwarm 服务控制脚本——all 模式（macOS / Linux / Windows Git Bash / WSL）
#
# dataswarm-start all 单命令全起：app 进程（网关 + AgentServer，web 槽位
# $GW_PORT）+ app_web（前端静态 dist + /api 一体，监听 $WEB_PORT）。
# 前端改码需要热更时用 ./restart-dev.sh（dev 分离模式：后端 + 独立 API + vite dev）。
#
# 用法：
#   ./restart.sh              重启（默认）
#   ./restart.sh start        启动
#   ./restart.sh stop         停止
#   ./restart.sh status       查看状态
#   ./restart.sh logs         实时看日志（Ctrl+C 退出）
#
# 默认后台启动 all 模式，前端访问 http://127.0.0.1:16173
# 平台适配：Windows Git Bash 用 powershell/taskkill；macOS/Linux 用 lsof/pgrep/kill
# ===========================================================================
set -euo pipefail

# 切到项目根（脚本所在目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"  # 脚本在 scripts/dev/, 根在上一上级
cd "$SCRIPT_DIR"

ACTION="${1:-restart}"

# 服务端口——DataSwarm 独立端口组（基座默认组整组 +11000，dataswarm_boot.py 同款）：
# 与基座 jiuwenswarm 同机共存互不抢占；GW_PORT 是 app 进程的 web 槽位（基座 19000 对应位）。
# all 模式 app_web 监听 WEB_PORT（前端静态 + /api 一体），无 dev 模式的独立 API_PORT
WEB_PORT=16173
GW_PORT=30000
AGENT_PORT=29092

# DataSwarm 运行时隔离：默认根目录 ~/.dataswarm + 端口基准组。
# shell 已显式设置优先（与 dataswarm_boot.py 的 setdefault 语义一致）；dataswarm-start
# 入口会注入同值默认，此处 shell 层显式导出与 restart-dev.sh 保持同一基准
export JIUWENSWARM_DATA_DIR="${JIUWENSWARM_DATA_DIR:-$HOME/.dataswarm}"
export JIUWENSWARM_AGENT_SERVER_PORT="${JIUWENSWARM_AGENT_SERVER_PORT:-29092}"
export JIUWENSWARM_WEB_PORT="${JIUWENSWARM_WEB_PORT:-30000}"
export JIUWENSWARM_GATEWAY_PORT="${JIUWENSWARM_GATEWAY_PORT:-30001}"
export JIUWENSWARM_FRONTEND_PORT="${JIUWENSWARM_FRONTEND_PORT:-16173}"

# 日志目录
mkdir -p logs

# ---------------------------------------------------------------------------
# 平台检测：win = Windows Git Bash（powershell/taskkill）；unix = macOS/Linux
# ---------------------------------------------------------------------------
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) PLATFORM="win" ;;
  *)                    PLATFORM="unix" ;;
esac

# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
  C_RESET="\033[0m"; C_INFO="\033[1;34m"; C_OK="\033[1;32m"
  C_WARN="\033[1;33m"; C_ERR="\033[1;31m"
else
  C_RESET=""; C_INFO=""; C_OK=""; C_WARN=""; C_ERR=""
fi

log()   { echo -e "${C_INFO}[*]${C_RESET} $1"; }
ok()    { echo -e "${C_OK}[v]${C_RESET} $1"; }
warn()  { echo -e "${C_WARN}[!]${C_RESET} $1"; }
err()   { echo -e "${C_ERR}[x]${C_RESET} $1"; }

# ---------------------------------------------------------------------------
# 平台适配原语：端口监听查询 / 按端口取 PID / 杀进程
# ---------------------------------------------------------------------------
# 端口是否处于 LISTEN（输出 1/0）
port_listening() {
  local port="$1" out
  if [[ "$PLATFORM" == "win" ]]; then
    out=$(powershell -NoProfile -Command \
      "if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { \"1\" } else { \"0\" }" 2>/dev/null || echo 0)
  else
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then out="1"; else out="0"; fi
  fi
  echo "$out"
}

# 端口监听进程 PID 列表（可能为空；unix 下多行，win 下空格/换行分隔）
port_listener_pids() {
  local port="$1"
  if [[ "$PLATFORM" == "win" ]]; then
    powershell -NoProfile -Command \
      "Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { \$_.OwningProcess }" 2>/dev/null || true
  else
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true
  fi
}

# 强杀进程（成功返回 0）
kill_pid() {
  local pid="$1"
  if [[ "$PLATFORM" == "win" ]]; then
    taskkill //F //PID "$pid" >/dev/null 2>&1
  else
    kill -9 "$pid" >/dev/null 2>&1
  fi
}

# A restart may terminate only processes launched from this checkout.  Port
# ownership by another checkout/instance is a conflict, not permission to kill.
pid_belongs_to_checkout() {
  local pid="$1"
  if [[ "$PLATFORM" == "win" ]]; then
    local win_root
    win_root=$(cygpath -w "$SCRIPT_DIR")
    # Windows PowerShell Treats tokens after -Command as part of the command
    # text rather than populating $args reliably.  Pass values via the
    # environment so the ownership check sees the actual PID and checkout.
    DATASWARM_PROCESS_ID="$pid" DATASWARM_CHECKOUT_ROOT="$win_root" \
      powershell -NoProfile -Command \
        '$proc=Get-CimInstance Win32_Process -Filter ("ProcessId=" + $env:DATASWARM_PROCESS_ID) -ErrorAction SilentlyContinue; if (-not $proc) { exit 1 }; $root=[IO.Path]::GetFullPath($env:DATASWARM_CHECKOUT_ROOT).TrimEnd([char]92).ToLowerInvariant(); $cmd=[string]$proc.CommandLine; if ($cmd.ToLowerInvariant().Contains($root)) { exit 0 } else { exit 1 }' \
        >/dev/null 2>&1
  else
    local cwd cmd root
    root=$(cd "$SCRIPT_DIR" && pwd -P)
    cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null || lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1 || true)
    cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
    [[ "$cwd" == "$root" || "$cwd" == "$root"/* || "$cmd" == *"$root"* ]]
  fi
}

kill_owned_pid() {
  local pid="${1//$'\r'/}" label="$2"
  if pid_belongs_to_checkout "$pid"; then
    kill_pid "$pid" && ok "$label killed $pid" || warn "kill $pid 失败（可能已退出）"
  else
    # ${pid}/${label} 花括号必须：bash 3.2（macOS 自带）会把紧跟变量的全角字符
    # 首字节并进变量名（裸 $pid 后紧跟全角括号会解析成 pid<FFFD>），set -u 下崩（2026-10-29 实测）
    warn "跳过非本仓库进程 PID=${pid}（${label}）"
  fi
}

# 端口能否在 127.0.0.1/::1 上 bind 成功（unix 专用）
# 与 dataswarm-start 的 bind 探测对齐（不设 SO_REUSEADDR）：
# 除活跃 LISTEN 外，kill -9 残留的 TIME_WAIT 也算「不可绑定」，
# 否则 dataswarm-start 会静默回退到备用端口组，导致本脚本探测错端口
port_bindable() {
  local port="$1" py
  if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
    py="$SCRIPT_DIR/.venv/bin/python"
  else
    py="python3"
  fi
  "$py" - "$port" <<'PYEOF' >/dev/null 2>&1
import errno
import socket
import sys

port = int(sys.argv[1])
for host, family in (("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)):
    try:
        sock = socket.socket(family)
    except OSError:
        continue  # 本机无 IPv6，跳过
    try:
        sock.bind((host, port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            sys.exit(1)  # 被占用（活跃 LISTEN 或 TIME_WAIT 残留）
    finally:
        sock.close()
PYEOF
}

# 所有服务端口均可 bind（输出 1/0；unix 专用）
all_ports_bindable() {
  local p
  for p in $WEB_PORT $GW_PORT $AGENT_PORT; do
    if ! port_bindable "$p"; then
      echo 0
      return
    fi
  done
  echo 1
}

# 等待 TIME_WAIT 残留散尽（最多 wait_s 秒；unix 专用）
wait_ports_bindable() {
  local wait_s="$1" waited=0
  while [[ "$(all_ports_bindable)" != "1" && $waited -lt $wait_s ]]; do
    sleep 2
    waited=$((waited + 2))
  done
  [[ "$(all_ports_bindable)" == "1" ]]
}

# ---------------------------------------------------------------------------
# 停止：杀 dataswarm-start / jiuwenswarm 后端进程 + 端口兜底
# ---------------------------------------------------------------------------
stop_services() {
  log "停止 DataSwarm 服务..."

  local pids pid
  if [[ "$PLATFORM" == "win" ]]; then
    # 0) 先杀残留的 uv 启动器，避免其继续持有 uv 下载缓存锁
    pids=$(powershell -NoProfile -Command \
      'Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "uv.exe" -and ($_.CommandLine -like "*dataswarm-start*" -or $_.CommandLine -like "*jiuwenswarm.channels.web.app_web*") } | ForEach-Object { $_.ProcessId }' 2>/dev/null || true)
    for pid in $pids; do
      kill_owned_pid "$pid" "残留 uv 启动器"
    done

    # 1) 后端：找所有命令行含 jiuwenswarm 的 python 进程（app / app_web 均为 -m jiuwenswarm.*）
    pids=$(powershell -NoProfile -Command \
      'Get-CimInstance Win32_Process | Where-Object { $_.Name -like "python*" -and $_.CommandLine -like "*jiuwenswarm*" } | ForEach-Object { $_.ProcessId }' 2>/dev/null || true)
    for pid in $pids; do
      kill_owned_pid "$pid" "后端"
    done
  else
    # 后端：命令行含 jiuwenswarm 的进程（app / app_web 均为 python -m jiuwenswarm.*）
    #    注：pgrep 不会匹配自身；本脚本路径不含 jiuwenswarm，安全
    pids=$(pgrep -f "jiuwenswarm" 2>/dev/null || true)
    for pid in $pids; do
      kill_owned_pid "$pid" "后端"
    done
  fi

  # 2) 兜底：按端口杀（防残留进程占着端口；从 dev 模式切过来时也负责杀 vite）
  local p
  for p in $WEB_PORT $GW_PORT $AGENT_PORT; do
    for pid in $(port_listener_pids "$p"); do
      kill_owned_pid "$pid" "端口 $p 占用进程"
    done
  done

  sleep 3
  # unix 下等 TIME_WAIT 残留散尽（否则 dataswarm-start 的 bind 探测会把
  # TIME_WAIT 误判为占用，静默回退到备用端口组，本脚本后续探测就错端口了）
  if [[ "$PLATFORM" != "win" ]]; then
    if ! wait_ports_bindable 30; then
      warn "端口尚有 TIME_WAIT 残留（最多等了 30s），继续启动——若 dataswarm-start 回退备用端口组，start 会显式报错"
    fi
  fi
  # 复查前端端口是否还 LISTEN
  if [[ "$(port_listening "$WEB_PORT")" == "1" ]]; then
    if [[ "$PLATFORM" == "win" ]]; then
      warn "$WEB_PORT 仍被 LISTEN 占用，请手动查：powershell -Command \"Get-NetTCPConnection -LocalPort $WEB_PORT -State Listen\""
    else
      warn "$WEB_PORT 仍被 LISTEN 占用，请手动查：lsof -nP -iTCP:$WEB_PORT -sTCP:LISTEN"
    fi
  else
    ok "服务已全部停止"
  fi
}

# ---------------------------------------------------------------------------
# 仓库根 .env 加载：导出给全部子进程（网关 / app_web）
# ---------------------------------------------------------------------------
# app_web 默认不读仓库根 .env（dotenv_early 只处理 --dotenv/--name 实例文件），
# 而 SEMANTICA_*/EMBED_* 等运行时凭据只认进程环境——这里在 shell 层导出兜底。
# 语义：**shell 已显式设置的键优先**（.env 不覆盖），与 dotenv 的 clobber
# 纪律一致；逐行解析 KEY=VALUE，值不经 shell 展开（$/反引号原样保留）。
load_repo_env() {
  local env_file="$SCRIPT_DIR/.env"
  [[ -f "$env_file" ]] || return 0
  local line key value loaded=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line// }" || "$line" == \#* ]] && continue
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    # 去可选成对引号（勿用 ${value:1:-1}：负长度是 bash 4.2+ 语法，
    # macOS 自带 bash 3.2 报 "substring expression < 0"，分两步剥离）
    if [[ ${#value} -ge 2 && "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
      value="${value:1}"
      value="${value%?}"
    elif [[ ${#value} -ge 2 && "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
      value="${value:1}"
      value="${value%?}"
    fi
    if [[ -z "${!key+x}" ]]; then
      export "$key=$value"
      loaded=$((loaded + 1))
    fi
  done < "$env_file"
  ok "已加载 .env（导出 ${loaded} 个未设置键给子进程；shell 已有值优先）"
}

# ---------------------------------------------------------------------------
# 启动：all 模式 = dataswarm-start all（app 进程 + app_web 前端静态 + /api 一体）
# ---------------------------------------------------------------------------
start_services() {
  # 先检查端口是否被 LISTEN 占用（TimeWait 残留不算）
  local p
  for p in $WEB_PORT $GW_PORT $AGENT_PORT; do
    if [[ "$(port_listening "$p")" == "1" ]]; then
      err "端口 $p 仍被 LISTEN 占用，请先 ./restart.sh stop"
      exit 1
    fi
  done

  load_repo_env

  log "预同步依赖（uv sync --locked），避免 uv run 隐式解析拖慢启动..."
  if ! uv sync --locked; then
    err "依赖同步失败，请先解决 uv sync --locked 的报错"
    return 1
  fi

  local stamp
  stamp=$(date +%Y%m%d-%H%M%S 2>/dev/null || echo "$(ls -1 logs/ | wc -l)")
  local logfile="logs/run-${stamp}.log"

  # unix 下最后确认端口可 bind（含 TIME_WAIT 检查；win 的启动器有同样的
  # bind 探测，但 Git Bash 下无便捷手段，保持原有行为）
  if [[ "$PLATFORM" != "win" ]] && [[ "$(all_ports_bindable)" != "1" ]]; then
    if ! wait_ports_bindable 30; then
      err "端口 $WEB_PORT/$GW_PORT/$AGENT_PORT 在 30s 内仍不可 bind（TIME_WAIT 残留或被占），中止启动"
      err "排查: lsof -nP -iTCP:$WEB_PORT,$GW_PORT,$AGENT_PORT"
      exit 1
    fi
  fi

  log "后台启动 dataswarm-start all ..."
  nohup uv run --no-sync dataswarm-start all > "$logfile" 2>&1 &
  local launcher_pid=$!
  ok "启动器 PID=${launcher_pid}，日志: $logfile"
  log "等待前端（app_web，端口 $WEB_PORT）就绪（最多 60s）..."

  local i code
  for i in $(seq 1 60); do
    # 检测 dataswarm-start 是否回退了端口组（TIME_WAIT 误判会导致静默回退，
    # 回退后服务在备用端口上，本脚本探测的 $WEB_PORT 永远不通，只能提前报错）
    if grep -q "Falling back to alternative port group" "$logfile" 2>/dev/null; then
      err "后端端口冲突，已回退到备用端口组（详见 $logfile 开头），本脚本探测错端口"
      err "请稍等 TIME_WAIT 散尽后重试 ./restart.sh start"
      return 1
    fi
    # curl 失败时 -w 已输出 000，勿在 $() 内再 || echo（会拼出 000000 之类的脏值，
    # 曾绕过就绪的 != "000" 判断造成假就绪），统一用外置赋值 || code=xxx
    code=$(curl -s -m 2 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$WEB_PORT/" 2>/dev/null) || code=000
    # app_web 对根路径 200（dist 已构建）/404（无 dist）都算端口就绪，000 = 无响应
    if [[ "$code" != "000" ]]; then
      ok "前端就绪 after ${i}s（HTTP ${code}）"
      echo
      status_services
      echo
      ok "浏览器打开 http://127.0.0.1:$WEB_PORT"
      ok "日志: tail -f $logfile"
      return 0
    fi
    sleep 1
  done
  err "前端 60s 内未就绪，请查日志: tail -f $logfile"
  return 1
}

# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------
status_services() {
  echo "── 服务进程（python/jiuwenswarm）──"
  if [[ "$PLATFORM" == "win" ]]; then
    powershell -NoProfile -Command \
      'Get-CimInstance Win32_Process | Where-Object { $_.Name -like "python*" -and $_.CommandLine -like "*jiuwenswarm*" } | Select-Object ProcessId, @{N="StartTime";E={$_.CreationDate}} | Format-Table -AutoSize' 2>/dev/null || warn "无服务进程"
  else
    pgrep -fl "jiuwenswarm" || warn "无服务进程"
  fi

  echo "── 端口 ──"
  local p code
  for p in $WEB_PORT $GW_PORT $AGENT_PORT; do
    code=$(curl -s -m 2 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$p/" 2>/dev/null) || code=down
    printf "  %-7s -> %s\n" "$p" "$code"
  done
  echo "  前端访问: http://127.0.0.1:$WEB_PORT"
}

# ---------------------------------------------------------------------------
# 实时日志
# ---------------------------------------------------------------------------
show_logs() {
  local latest
  latest=$(ls -t logs/run-*.log logs/swarm-*.log 2>/dev/null | head -1)
  if [[ -z "$latest" ]]; then
    warn "无日志文件"
    exit 1
  fi
  log "实时跟踪: $latest （Ctrl+C 退出）"
  tail -f "$latest"
}

# ---------------------------------------------------------------------------
# 分发
# ---------------------------------------------------------------------------
case "$ACTION" in
  start)   start_services ;;
  stop)    stop_services ;;
  restart) stop_services; echo; start_services ;;
  status)  status_services ;;
  logs)    show_logs ;;
  *)
    echo "用法: ./restart.sh [start|stop|restart|status|logs]"
    echo "  start    启动服务（后台 all 模式：dataswarm-start all）"
    echo "  stop     停止服务"
    echo "  restart  重启（默认）"
    echo "  status   查看进程与端口状态"
    echo "  logs     实时跟踪最新日志"
    exit 1
    ;;
esac
