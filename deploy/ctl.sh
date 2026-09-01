#!/usr/bin/env bash
# =============================================================================
# Drawing2Model 一键启停脚本 (Ubuntu, systemd 优先, 无 systemd 时回退前台进程)
#
# 用法: ./deploy/ctl.sh {start|stop|restart|status|logs|version}
#   start    启动服务 (后台守护)
#   stop     停止服务
#   restart  重启服务
#   status   运行状态 + 健康检查 (/api/status)
#   logs     跟踪最近日志 (Ctrl+C 退出)
#   version  查看当前构建版本
# =============================================================================
set -euo pipefail

TARGET="/data/drawing2model"
SERVICE="drawing2model"
PORT="${D2M_PORT:-8410}"
[[ -d "$TARGET" ]] || TARGET="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

has_systemd() { command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files "$SERVICE.service" >/dev/null 2>&1; }
svc_file()    { [[ -f /etc/systemd/system/$SERVICE.service ]] || [[ -f "$TARGET/deploy/$SERVICE.service" ]]; }

health() {
  command -v curl >/dev/null 2>&1 || { echo "  (无 curl, 跳过健康检查)"; return 0; }
  if curl -fsS "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then
    echo "  健康检查: http://127.0.0.1:$PORT/api/status OK"
  else
    echo "  健康检查: 暂未就绪 (稍等几秒重试, 或 logs 看启动日志)"
  fi
}

do_start() {
  if has_systemd && svc_file; then
    sudo systemctl start "$SERVICE" && echo "started via systemd"
    sleep 2; health
  else
    echo "[ctl] 未检测到 systemd 单元, 回退前台启动 (Ctrl+C 停止; 正式部署请用 deploy.sh 装 systemd)"
    cd "$TARGET"
    set -a; [[ -f .env ]] && . ./.env; set +a
    export D2M_WORKER_PY="$TARGET/venv_worker/bin/python"
    exec "$TARGET/venv_server/bin/python" server.py
  fi
}

do_stop() {
  if has_systemd && svc_file; then
    sudo systemctl stop "$SERVICE" && echo "stopped"
  else
    pkill -f "venv_server/bin/python server.py" 2>/dev/null \
      && echo "stopped (pid matched)" || echo "未在运行"
  fi
}

do_status() {
  if has_systemd && svc_file; then
    systemctl status "$SERVICE" --no-pager -l | head -n 15 || true
  else
    pgrep -af "venv_server/bin/python server.py" || echo "未在运行 (无 systemd 单元)"
  fi
  health
}

do_logs() {
  if has_systemd && svc_file; then
    sudo journalctl -u "$SERVICE" -f -n 50
  else
    echo "[ctl] 无 systemd 单元, 前台启动时的输出即日志"
  fi
}

do_version() {
  if [[ -f "$TARGET/web/dist/index.html" ]]; then
    grep -oE "data-build', '[0-9a-z]+'" "$TARGET/web/dist/index.html" | head -1 || true
  else
    echo "(web/dist 不存在)"
  fi
}

case "${1:-}" in
  start)   do_start ;;
  stop)    do_stop ;;
  restart) do_stop || true; sleep 1; do_start ;;
  status)  do_status ;;
  logs)    do_logs ;;
  version) do_version ;;
  *)
    echo "用法: $0 {start|stop|restart|status|logs|version}"
    exit 1
    ;;
esac
