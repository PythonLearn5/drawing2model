#!/usr/bin/env bash
# =============================================================================
# Drawing2Model 一键部署 (Ubuntu 20.04+ x86_64)
#
# 用法 (在克隆的仓库根目录或 deploy/ 目录下):
#   sudo ./deploy/deploy.sh
#
# 部署内容:
#   1. 代码复制到 /data/drawing2model (保留已有 .env 与 output 产物)
#   2. venv_server : 主服务依赖 (FastAPI/uvicorn/识别/渲染/报告)
#   3. venv_worker : 建模内核 (cadquery + OCP + numpy, 沙箱执行 LLM 代码用)
#   4. systemd 服务 drawing2model (开机自启, 端口 8410)
#
# 重复执行 = 升级部署 (幂等): 停服 -> 更新代码与依赖 -> 启服。
# 依赖较多时可用国内镜像: PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple sudo ./deploy/deploy.sh
# =============================================================================
set -euo pipefail

TARGET="/data/drawing2model"
SERVICE="drawing2model"
APP_USER="drawing2model"

log()  { echo -e "\033[1;32m[deploy]\033[0m $*"; }
warn() { echo -e "\033[1;33m[deploy][WARN]\033[0m $*"; }
die()  { echo -e "\033[1;31m[deploy][ERROR]\033[0m $*" >&2; exit 1; }

# ---- 0. root 权限 ----
if [[ $EUID -ne 0 ]]; then
  warn "需要 root 权限, 自动 sudo 重新执行..."
  exec sudo bash "$0" "$@"
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$SRC_DIR/server.py" ]] || die "在 $SRC_DIR 未找到 server.py, 请在 drawing2model 仓库内运行本脚本"
log "源目录: $SRC_DIR"
log "目标目录: $TARGET"

# ---- 1. 系统依赖检查 ----
command -v python3 >/dev/null 2>&1 || die "缺少 python3: apt update && apt install -y python3 python3-venv"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "需要 python3 >= 3.10 (当前: $(python3 -V 2>&1)); cadquery 轮子要求 3.10~3.13"
python3 -m venv --help >/dev/null 2>&1 || die "缺少 venv 模块: apt install -y python3-venv"
log "python3: $(python3 -V 2>&1)"

# ---- 2. 服务用户 ----
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  log "创建服务用户 $APP_USER"
  useradd -r -m -d "/home/$APP_USER" -s /usr/sbin/nologin "$APP_USER"
fi

# ---- 3. 停旧服务 (升级部署) ----
if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
  log "停止旧服务 $SERVICE ..."
  systemctl stop "$SERVICE" || true
fi

# ---- 4. 复制代码 (保留现场: .env / output / venv 不覆盖) ----
# 先解包到暂存目录再合入目标, 避免"源目录==目标目录"的自升级场景下边读边写
mkdir -p "$TARGET"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
log "复制代码 (排除 .git / output / node_modules / venv / .env / 缓存) ..."
tar -C "$SRC_DIR" \
    --exclude='.git' --exclude='output' --exclude='outputs' --exclude='.buildtmp' \
    --exclude='web/node_modules' --exclude='venv_server' --exclude='venv_worker' \
    --exclude='.env' --exclude='__pycache__' --exclude='*.pyc' --exclude='*.log' \
    -cf - . | tar -C "$STAGING" -xf -
cp -a "$STAGING"/. "$TARGET"/
rm -rf "$STAGING"

# ---- 5. 环境配置 .env (已存在则保留, 不覆盖密钥) ----
ENV_FILE="$TARGET/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<'EOF'
# Drawing2Model 运行配置 (systemd EnvironmentFile 加载)
# LLM API (OpenAI 兼容); 留空 = 离线模式 (仅模板/规则, 无法 evolve)
LLM_API_KEY=
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.8-max
# 可选: 分别覆盖视觉/文本模型
#LLM_VL_MODEL=
#LLM_TEXT_MODEL=
EOF
  warn "已生成 $ENV_FILE —— 请填写 LLM_API_KEY 后重启服务 (ctl.sh restart)"
else
  log "保留现有 .env (不覆盖)"
fi

# ---- 6. venv_server: 主服务依赖 ----
log "安装主服务依赖 (venv_server) ..."
if [[ ! -x "$TARGET/venv_server/bin/python" ]]; then
  python3 -m venv "$TARGET/venv_server"
fi
"$TARGET/venv_server/bin/pip" install -q --upgrade pip
"$TARGET/venv_server/bin/pip" install -q -r "$TARGET/requirements.txt"
log "venv_server 完成"

# ---- 7. venv_worker: 建模内核 (cadquery/OCP) ----
log "安装建模内核依赖 (venv_worker: cadquery + numpy) ..."
if [[ ! -x "$TARGET/venv_worker/bin/python" ]]; then
  python3 -m venv "$TARGET/venv_worker"
fi
"$TARGET/venv_worker/bin/pip" install -q --upgrade pip
"$TARGET/venv_worker/bin/pip" install -q cadquery numpy pymupdf pillow
"$TARGET/venv_worker/bin/python" -c "import cadquery, OCP" \
  || die "cadquery/OCP 安装失败 (要求 x86_64 + python 3.10~3.13, 建议 3.12; 检查网络/镜像)"
"$TARGET/venv_worker/bin/python" -c "import cadquery; print('  cadquery', cadquery.__version__)"
log "venv_worker 完成"

# ---- 8. 前端 (仓库自带 web/dist; 无则尝试 node 构建) ----
if [[ -f "$TARGET/web/dist/index.html" ]]; then
  log "前端构建产物就绪 (web/dist)"
elif command -v node >/dev/null 2>&1; then
  log "检测到 node $(node -v), 构建前端 ..."
  (cd "$TARGET/web" && npm install --no-audit --no-fund && npm run build)
else
  warn "无 web/dist 且无 node —— Web UI 不可用 (REST/MCP 不受影响); 可本机构建后重跑部署"
fi

# ---- 9. systemd 服务 ----
log "安装 systemd 服务 ..."
install -m 0644 "$TARGET/deploy/drawing2model.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1

# ---- 10. 权限与产物目录 ----
mkdir -p "$TARGET/output"
chown -R "$APP_USER":"$APP_USER" "$TARGET"

# ---- 11. 启动 + 健康检查 ----
systemctl start "$SERVICE"
sleep 3
if ! systemctl is-active --quiet "$SERVICE"; then
  journalctl -u "$SERVICE" -n 40 --no-pager || true
  die "服务启动失败, 日志见上方"
fi
log "服务已启动 (systemd: $SERVICE)"
if command -v curl >/dev/null 2>&1; then
  for i in $(seq 1 15); do
    if curl -fsS "http://127.0.0.1:8410/api/status" >/dev/null 2>&1; then
      log "健康检查通过 (/api/status)"
      break
    fi
    sleep 1
  done
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
IP="${IP:-<服务器IP>}"
log "================ 部署完成 ================"
log " Web UI / REST : http://$IP:8410"
log " MCP SSE       : http://$IP:8410/mcp/sse"
log " 服务管理      : $TARGET/deploy/ctl.sh {start|stop|restart|status|logs}"
log " 配置文件      : $ENV_FILE (填 LLM_API_KEY 后 restart 生效)"
log " 防火墙        : 如需外网访问请放行 8410 (ufw allow 8410/tcp)"
