#!/usr/bin/env bash
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# ===========================================================================
# DataSwarm 服务控制脚本 - Linux 前后端分离版（服务器专用）
#
# 由 restart-dev.sh 移植：后端 dataswarm-start app + API app_web(30002) +
# 前端 vite dev(16173)。端口组与 dataswarm_boot.py 一致（基座 19000 组整组 +11000），
# 与基座 jiuwenswarm 同机共存互不抢占
#
# 用法：
#   ./restart-dev-linux.sh start     启动
#   ./restart-dev-linux.sh stop      停止
#   ./restart-dev-linux.sh restart   重启
#   ./restart-dev-linux.sh status    状态
#   ./restart-dev-linux.sh logs      实时日志
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"  # 脚本在 scripts/dev/, 根在上一上级
cd "$SCRIPT_DIR"

ACTION="${1:-restart}"

# 服务端口——DataSwarm 独立端口组（基座默认组整组 +11000，dataswarm_boot.py 同款）：
# 与基座 jiuwenswarm 同机共存互不抢占；GW_PORT 是 app 进程的 web 槽位（基座 19000 对应位）
WEB_PORT=16173
GW_PORT=30000
API_PORT=30002
AGENT_PORT=29092

# DataSwarm 运行时隔离（dev 开发期一起切）：默认根目录 ~/.dataswarm + 端口基准组。
# shell 已显式设置优先（与 dataswarm_boot.py 的 setdefault 语义一致）；
# python -m 直启的子进程（app_web）不经 CLI 入口，靠这里的导出拿到同一基准。
export JIUWENSWARM_DATA_DIR="${JIUWENSWARM_DATA_DIR:-$HOME/.dataswarm}"
export JIUWENSWARM_AGENT_SERVER_PORT="${JIUWENSWARM_AGENT_SERVER_PORT:-29092}"
export JIUWENSWARM_WEB_PORT="${JIUWENSWARM_WEB_PORT:-30000}"
export JIUWENSWARM_GATEWAY_PORT="${JIUWENSWARM_GATEWAY_PORT:-30001}"
export JIUWENSWARM_FRONTEND_PORT="${JIUWENSWARM_FRONTEND_PORT:-16173}"

export FRONTEND_HOST=0.0.0.0

# agent 的 bash 工具继承服务进程 PATH：前置仓库 venv 的可执行目录——uv run 起
# python -m 不激活 venv，PATH 无 venv 时 agent 的裸 `python` 解析到系统 Python
# （同 restart-dev.sh 的注入语义）
_VENV_BIN="$SCRIPT_DIR/.venv/bin"
case ":$PATH:" in
  *":$_VENV_BIN:"*) ;;
  *) export PATH="$_VENV_BIN:$PATH" ;;
esac

WEB_DEV_DIR="$SCRIPT_DIR/jiuwenswarm/channels/web/frontend"

mkdir -p logs

C_RESET="\033[0m"; C_INFO="\033[1;34m"; C_OK="\033[1;32m"; C_WARN="\033[1;33m"; C_ERR="\033[1;31m"
log()   { echo -e "${C_INFO}[*]${C_RESET} $1"; }
ok()    { echo -e "${C_OK}[v]${C_RESET} $1"; }
warn()  { echo -e "${C_WARN}[!]${C_RESET} $1"; }
err()   { echo -e "${C_ERR}[x]${C_RESET} $1"; }

# ---------------------------------------------------------------------------
# 平台抽象（Linux）
# ---------------------------------------------------------------------------
port_listeners() {
  local port=$1
  lsof -i :"$port" -sTCP:LISTEN -t 2>/dev/null || true
}

kill_pid() {
  local pid=$1
  kill -9 "$pid" 2>/dev/null
}

port_is_listening() {
  local port=$1
  lsof -i :"$port" -sTCP:LISTEN -t 2>/dev/null | grep -q .
}

# curl 探活：返回 HTTP 状态码（000 = 无响应）。不用 `|| echo 000`——curl 有输出时也会拼接
http_code() {
  local url=$1
  local code
  code=$(curl -s -m 2 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
  echo "${code:-000}"
}

# ---------------------------------------------------------------------------
# 停止：等端口真正释放（防漂移的关键）
# ---------------------------------------------------------------------------
stop_services() {
  log "停止 jiuwenswarm 服务..."

  # 1) 后端 python 进程
  local pids
  pids=$(pgrep -f 'python.*jiuwenswarm' 2>/dev/null || true)
  for pid in $pids; do
    kill_pid "$pid" && ok "killed $pid" || true
  done

  # 2) 前端 vite node 进程
  local web_pids
  web_pids=$(pgrep -f 'vite' 2>/dev/null | while read -r p; do
    grep -q node "/proc/$p/comm" 2>/dev/null && echo "$p"
  done || true)
  for pid in $web_pids; do
    kill_pid "$pid" && ok "vite killed $pid" || true
  done

  # 3) uv run 包装进程
  pids=$(pgrep -f 'uv run' 2>/dev/null || true)
  for pid in $pids; do
    kill_pid "$pid" && ok "uv killed $pid" || true
  done

  # 4) 循环等待端口释放（最多 15s），未释放补刀
  local waited=0
  while :; do
    local busy=0
    for p in $WEB_PORT $GW_PORT $API_PORT $AGENT_PORT; do
      port_is_listening "$p" && busy=1 && break
    done
    (( busy == 0 )) && break
    if (( waited >= 15 )); then
      warn "15s 后端口仍未释放，补刀一轮..."
      for p in $WEB_PORT $GW_PORT $API_PORT $AGENT_PORT; do
        for pid in $(port_listeners "$p"); do kill_pid "$pid" 2>/dev/null || true; done
      done
      sleep 2
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done

  local still_busy=0
  for p in $WEB_PORT $GW_PORT $API_PORT $AGENT_PORT; do
    port_is_listening "$p" && still_busy=1 && warn "端口 $p 仍被占用，请手动查：lsof -i :$p"
  done
  if (( still_busy == 0 )); then
    ok "服务已全部停止（端口已释放）"
  else
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# 启动：后端 + API + vite dev
# ---------------------------------------------------------------------------
start_services() {
  for p in $WEB_PORT $GW_PORT $API_PORT $AGENT_PORT; do
    if port_is_listening "$p"; then
      err "端口 $p 仍被 LISTEN 占用，请先 ./restart-dev-linux.sh stop"
      exit 1
    fi
  done

  local stamp
  stamp=$(date +%Y%m%d-%H%M%S)
  local be_logfile="logs/run-${stamp}.log"
  local api_logfile="logs/api-${stamp}.log"
  local fe_logfile="logs/vite-${stamp}.log"

  # 预同步依赖，避免两个 uv run 并发下载锁冲突（同 restart-dev.sh）
  if ! uv sync --locked; then
    err "依赖同步失败，请先解决 uv sync --locked 的报错"
    return 1
  fi

  # 1) 后端：网关 + AgentServer（等它就绪再起 API——API 的 proxy-target 指向网关）
  log "后台启动后端 dataswarm-start app ..."
  WEB_API_PORT=$API_PORT nohup uv run --no-sync dataswarm-start app > "$be_logfile" 2>&1 &
  ok "后端启动器 PID=$!，日志: $be_logfile"

  log "等待后端网关就绪（最多 90s）..."
  local ready=0
  for i in $(seq 1 90); do
    code=$(http_code "http://127.0.0.1:$GW_PORT/")
    # 404 也算就绪：HTTP 服务在监听且已响应，只是根路径未挂 handler（新版网关行为）
    if [[ "$code" != "000" ]]; then
      ok "后端网关就绪 after ${i}s（HTTP $code）"
      ready=1
      break
    fi
    # 网关端口组漂移检测：日志出现 fallback 即失败退出，别让 API 连错端口
    if grep -q "Falling back to alternative port group" "$be_logfile" 2>/dev/null; then
      err "后端端口组漂移（$GW_PORT 被占），见日志: $be_logfile"
      err "先执行 ./restart-dev-linux.sh stop 再 start"
      exit 1
    fi
    sleep 1
  done
  if (( ready == 0 )); then
    err "后端 90s 内未就绪，请查日志: tail -f $be_logfile"
    exit 1
  fi

  # 2) API 服务器：app_web 在 $API_PORT 处理 HTTP API（网关就绪后才起）
  log "后台启动 API 服务器 (app_web) 端口 $API_PORT ..."
  FRONTEND_PORT=$API_PORT nohup uv run --no-sync python -m jiuwenswarm.channels.web.app_web \
    --port $API_PORT \
    --host 127.0.0.1 \
    --proxy-target "http://127.0.0.1:$GW_PORT" \
    --dist "$WEB_DEV_DIR" \
    > "$api_logfile" 2>&1 &
  ok "API 服务器 PID=$!，日志: $api_logfile"
  # 等 API 就绪
  for i in $(seq 1 30); do
    code=$(http_code "http://127.0.0.1:$API_PORT/")
    [[ "$code" != "000" ]] && ok "API 服务器就绪 after ${i}s" && break
    sleep 1
  done

  # 4) 前端 vite dev
  if [[ ! -d "$WEB_DEV_DIR/node_modules" ]]; then
    err "前端依赖未装，请先: cd $WEB_DEV_DIR && npm install"
    exit 1
  fi
  log "后台启动前端 vite ..."
  # WEB_API_PORT/WEB_PORT 必须内联注入：vite.config.ts 的 /api、/ws 代理目标读这两个
  # env（缺省已回落 DataSwarm 组，注入保证与后端实际端口一致）；JIUWENSWARM_DATA_DIR
  # 同样内联：file-api 中间件按它解析可下载产物根目录（同 restart-dev.sh）
  WEB_API_PORT=$API_PORT WEB_PORT=$GW_PORT \
    JIUWENSWARM_DATA_DIR="${JIUWENSWARM_DATA_DIR:-$HOME/.dataswarm}" \
    nohup bash -c "cd '$WEB_DEV_DIR' && npx vite --host 0.0.0.0 --port $WEB_PORT" > "$fe_logfile" 2>&1 &
  ok "前端 vite PID=$!，日志: $fe_logfile"
  log "等待前端就绪（最多 60s）..."
  for i in $(seq 1 60); do
    code=$(http_code "http://127.0.0.1:$WEB_PORT/")
    if [[ "$code" != "000" ]]; then
      ok "前端就绪 after ${i}s（HTTP $code）"
      break
    fi
    sleep 1
  done

  echo
  status_services
  echo
  ok "浏览器打开 http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 127.0.0.1):$WEB_PORT"
  ok "后端日志: tail -f $be_logfile"
  ok "前端日志: tail -f $fe_logfile"
}

# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------
status_services() {
  echo "── 服务进程 ──"
  local found=0
  for pid in $(pgrep -f 'python.*jiuwenswarm' 2>/dev/null || true); do
    echo "  python  PID=$pid  $(cat /proc/$pid/comm 2>/dev/null)"
    found=1
  done
  for pid in $(pgrep -f 'vite' 2>/dev/null || true); do
    grep -q node "/proc/$pid/comm" 2>/dev/null && echo "  vite    PID=$pid" && found=1
  done
  (( found == 0 )) && warn "无进程"

  echo "── 端口 ──"
  for p in $WEB_PORT $GW_PORT $API_PORT $AGENT_PORT; do
    code=$(curl -s -m 2 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$p/" 2>/dev/null || echo "down")
    printf "  %-7s -> %s\n" "$p" "$code"
  done
  echo "  前端访问: http://0.0.0.0:$WEB_PORT"
}

# ---------------------------------------------------------------------------
# 实时日志
# ---------------------------------------------------------------------------
show_logs() {
  local latest
  latest=$(ls -t logs/run-*.log logs/api-*.log logs/vite-*.log 2>/dev/null | head -1)
  if [[ -z "$latest" ]]; then
    warn "无日志文件"
    exit 1
  fi
  log "实时跟踪: $latest （Ctrl+C 退出）"
  tail -f "$latest"
}

case "$ACTION" in
  start)   start_services ;;
  stop)    stop_services ;;
  restart) stop_services; sleep 5; echo; start_services ;;
  status)  status_services ;;
  logs)    show_logs ;;
  *)       echo "用法: $0 {start|stop|restart|status|logs}"; exit 1 ;;
esac
