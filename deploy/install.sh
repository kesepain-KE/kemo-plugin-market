#!/usr/bin/env bash
# 在 Debian/Ubuntu 上安装 Kemo 插件市场静态服务。
#
#   sudo ./deploy/install.sh
#
# 幂等：重复执行只会更新代码与单元文件，不动已有插件包和索引。

set -euo pipefail

APP_DIR=/opt/kemo-market
MARKET_ROOT=/srv/kemo-plugin-market
SERVICE=kemo-market
RUN_USER=kemomarket
PORT=8099
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

if [[ $EUID -ne 0 ]]; then
    echo "需要 root 权限运行：sudo $0" >&2
    exit 1
fi

echo "==> 检查依赖"
for module in yaml packaging; do
    if ! python3 -c "import $module" 2>/dev/null; then
        echo "缺少 Python 模块 $module，尝试安装"
        apt-get update -qq
        # packaging 由 python3-packaging 提供，PyYAML 由 python3-yaml 提供
        apt-get install -y -qq python3-yaml python3-packaging
        break
    fi
done
python3 - <<'PY'
import sys
import yaml
import packaging
print(f"    Python {sys.version.split()[0]}, PyYAML {yaml.__version__}, packaging {packaging.__version__}")
PY

echo "==> 创建运行用户 $RUN_USER"
if ! id -u "$RUN_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$RUN_USER"
    echo "    已创建"
else
    echo "    已存在"
fi

echo "==> 同步代码到 $APP_DIR"
mkdir -p "$APP_DIR"
# 只同步包目录，避免把仓库里的示例与测试带到生产目录
rm -rf "$APP_DIR/kemo_plugin_market"
cp -r "$SOURCE_DIR/kemo_plugin_market" "$APP_DIR/"
cp "$SOURCE_DIR/requirements.txt" "$APP_DIR/" 2>/dev/null || true
find "$APP_DIR/kemo_plugin_market" -name '__pycache__' -type d -prune -exec rm -rf {} +
chown -R root:root "$APP_DIR"
chmod -R a+rX "$APP_DIR"

echo "==> 准备内容根 $MARKET_ROOT"
mkdir -p "$MARKET_ROOT/packages"
chown -R root:root "$MARKET_ROOT"
chmod 0755 "$MARKET_ROOT" "$MARKET_ROOT/packages"

if [[ ! -f "$MARKET_ROOT/market.yaml" ]]; then
    if [[ -f "$SOURCE_DIR/examples/market.yaml" ]]; then
        cp "$SOURCE_DIR/examples/market.yaml" "$MARKET_ROOT/market.yaml"
        chmod 0644 "$MARKET_ROOT/market.yaml"
        echo "    已写入示例 market.yaml，请按实际域名修改 base_url"
    fi
else
    echo "    保留已有 market.yaml"
fi

echo "==> 安装 systemd 单元"
install -m 0644 "$SOURCE_DIR/deploy/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"

echo "==> 等待服务就绪"
for _ in $(seq 1 20); do
    if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/index.json" 2>/dev/null; then
        break
    fi
    # 索引可能尚不存在（还没 build 过），端口能连上也算就绪
    if ss -tlnH "sport = :$PORT" | grep -q .; then
        break
    fi
    sleep 0.5
done

if systemctl is-active --quiet "$SERVICE"; then
    echo "    服务运行中：$(systemctl is-active "$SERVICE")"
else
    echo "    服务未启动，查看：journalctl -u $SERVICE -n 50 --no-pager" >&2
    exit 1
fi

echo "==> 监听状态"
ss -tlnH "sport = :$PORT" || echo "    端口 $PORT 未监听"

cat <<EOF

安装完成。

服务只监听 127.0.0.1:$PORT，必须经反向代理对外暴露。
反向代理配置见 deploy/nginx-location.conf，三条硬约束：
  proxy_intercept_errors off / 不要补斜杠 / 不要 try_files 兜底。

生成索引：
  cd $APP_DIR && python3 -m kemo_plugin_market build --root $MARKET_ROOT

校验已发布的源：
  python3 -m kemo_plugin_market verify --url https://你的域名

EOF
