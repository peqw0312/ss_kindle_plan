#!/usr/bin/env bash
# =============================================================================
#  Kindle AI 信息屏 · 服务器一键部署
#  目标机器：Ubuntu 24.04 LTS（腾讯云轻量免费试用实例）
#  用法：sudo bash deploy.sh
#
#  这个脚本做四件事：
#    1. 装系统依赖（Python + 中文字体）
#    2. 建虚拟环境、装三个 Python 包
#    3. 生成 systemd 服务（开机自启 + 崩溃自愈）
#    4. 校验中文字体没解析成 DejaVu（否则屏幕上全是方块）
#
#  它**不碰**和风凭据 —— 那五个值由你单独放，见脚本末尾的提示。
# =============================================================================
set -euo pipefail

APP_DIR="/opt/kindle-plan"
ENV_DIR="/etc/kindle-screen"
SERVICE="/etc/systemd/system/kindle-screen.service"
PORT=8731

say() { printf '\n=== %s ===\n' "$*"; }
die() { printf '\n!! 失败：%s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "请用 root 跑：sudo bash deploy.sh"

say "1/5 系统依赖"
apt-get update -qq
# fonts-noto-cjk 是**必须**的：config.yaml 里写的是 Windows 字体路径，
# 在 Linux 上不存在，book_for() 会退回 fontconfig 扫描。
# 没有这套字体就会扫到 DejaVu —— 那个不含汉字，屏幕上全是豆腐块 □□□。
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip fonts-noto-cjk ca-certificates

say "2/5 校验中文字体（最关键的一步）"
if fc-list | grep -qi "cjk"; then
    echo "找这些字体："
    fc-list | grep -i "cjk" | head -5
else
    die "没有 CJK 字体！屏幕上会全是方块。先手动 apt install fonts-noto-cjk"
fi

say "3/5 代码与虚拟环境"
[ -d "$APP_DIR" ] || die "找不到 $APP_DIR —— 代码还没上传，见 guide/06 步骤 2"
cd "$APP_DIR"
[ -f "dashboard/requirements.txt" ] || die "dashboard/requirements.txt 不在，目录结构不对"

python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r dashboard/requirements.txt
.venv/bin/python -c "import PIL, yaml, requests; print('依赖已就绪：Pillow', PIL.__version__)"

# 自检：字体解析有没有退回 DejaVu（这是 Linux 上最容易出、又最难发现的故障）
say "4/5 渲染层字体自检"
.venv/bin/python - <<'PY' || echo "  ↑ 自检没通过，但服务仍会启动（图上中文可能变方块）"
import sys
sys.path.insert(0, "dashboard")
from aiinfo import fonts
try:
    from aiinfo.config import load_config
    cfg = load_config("dashboard/config.yaml")
except Exception as exc:
    print(f"  读配置失败：{exc}"); sys.exit(1)
f = fonts.book_for(cfg, bold=False)
name = getattr(f, "path", "?")
print(f"  实际解析到：{name}")
bad = ("dejavu", "liberation", "arial", "noto sans.ttf")
if any(b in str(name).lower() for b in bad):
    print("  !! 这是一个不含汉字的字体，中文会显示成方块")
    sys.exit(1)
print("  字体 OK")
PY

say "5/5 systemd 服务"
mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"

# 凭据文件：先建空模板，五个值由你填（绝不写进仓库）
if [ ! -f "$ENV_DIR/qweather.env" ]; then
    cat > "$ENV_DIR/qweather.env" <<'EOF'
# 和风天气 v1 · JWT 凭据。**这个文件不要进任何仓库、不要截图、不要贴群里。**
# 五个值从和风控制台抄：
QWEATHER_HOST=
QWEATHER_ISS=
QWEATHER_SUB=
QWEATHER_KID=
# 私钥不写在这里，另存一个 PEM 文件，然后在这填路径：
QWEATHER_PRIVATE_KEY_FILE=/etc/kindle-screen/qweather_ed25519.pem
EOF
    chmod 600 "$ENV_DIR/qweather.env"
    echo "  已生成凭据模板：$ENV_DIR/qweather.env（现在还是空的，必须填）"
fi

cat > "$SERVICE" <<EOF
[Unit]
Description=Kindle AI Screen (dashboard/serve.py)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_DIR/qweather.env
# 出图每 60 分钟一轮（serve.py 里 AUTO_BUILD_MINUTES 的默认值）
ExecStart=$APP_DIR/.venv/bin/python dashboard/serve.py --bind 0.0.0.0 --port $PORT
Restart=always
RestartSec=10
# 日志进 journald：journalctl -u kindle-screen -f
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable kindle-screen >/dev/null
echo "  服务已注册（开机自启已开），但**先别启动** —— 凭据还是空的"

cat <<'NEXT'

================================================================
 部署完成，接下来还差三步
================================================================

 ① 上传代码（如果你还没传）
    本地打包后传到 /opt/kindle-plan

 ② 填和风凭据
    nano /etc/kindle-screen/qweather.env
    # 五个值填好后，把私钥单独放：
    nano /etc/kindle-screen/qweather_ed25519.pem
    chmod 600 /etc/kindle-screen/qweather_ed25519.pem

 ③ 启动 + 验证
    systemctl start kindle-screen
    journalctl -u kindle-screen -f          # 看日志
    curl -I http://127.0.0.1:8731/dashboard.png   # 应该 200 + image/png

 ④ 放行端口（在腾讯云控制台的防火墙里加规则）
    协议 TCP / 端口 8731 / 来源 0.0.0.0/0
    ⚠️ 这一步做完，公网任何人都能取到你的图

================================================================
NEXT

echo
echo "服务状态："
systemctl is-enabled kindle-screen 2>/dev/null || true
