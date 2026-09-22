#!/bin/sh
# =============================================================================
#  立即刷新一次（带详细诊断）
#
#  这是排障时最有用的一个脚本：它不依赖后台主循环，自己把整条链路走一遍，
#  每一步都会打印结果。第一次配置时建议先单独跑这个，跑通了再启动主循环。
#  在书库里点「测试刷新一次」（它就是一个 scriptlet），或者 SSH 执行 sh bin/refresh.sh
#
#  最后一步会按当前配置**把时钟也贴上**，所以你看到的画面和常驻模式是一样的。
# =============================================================================

DIR=/mnt/us/extensions/aistatus
[ -f "$DIR/config.sh" ] || DIR="$(cd "$(dirname "$0")/.." && pwd)"
. "$DIR/config.sh"

IMG=/tmp/aistatus.png
TMP=/tmp/aistatus.tmp
HDR=/tmp/aistatus.hdr
CLOCK_DIR="$DIR/clock"
CLOCK_CONF="$CLOCK_DIR/clock.conf"
CLOCK_X=""
CLOCK_Y=""
[ -f "$CLOCK_CONF" ] && . "$CLOCK_CONF"
# CRLF 会让 CLOCK_X 变成 "644\r" 而 eips 不报错。生成脚本写的是 LF，
# 这里再剥一道，防的是有人手改过。
CLOCK_X=$(printf '%s' "$CLOCK_X" | tr -d '\r')
CLOCK_Y=$(printf '%s' "$CLOCK_Y" | tr -d '\r')

echo "=== AI 信息屏 · 单次刷新诊断 ==="
echo "时间      : $(date)"
echo "主地址    : $DASHBOARD_URL"
[ -n "$DASHBOARD_FALLBACK_URL" ] && echo "备用地址  : $DASHBOARD_FALLBACK_URL"

# 空地址、或者还留着 <你的...> 这类占位符，都是没配好
case "$DASHBOARD_URL" in
    "")
        echo ""
        echo "!! config.sh 里的 DASHBOARD_URL 是空的，先填上图片地址"
        exit 1
        ;;
    *example*|*"<"*)
        echo ""
        echo "!! DASHBOARD_URL 看起来还是示例/占位地址，请先改 config.sh"
        exit 1
        ;;
esac

# --- 1. WiFi ---
echo ""
echo "[1/6] 检查 WiFi…"
lipc-set-prop com.lab126.cmd wirelessEnable 1 >/dev/null 2>&1
state=$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)
echo "      wifid 状态: ${state:-读取失败}"

waited=0
ok=0
while [ "$waited" -lt "$WIFI_TIMEOUT" ]; do
    if lipc-get-prop com.lab126.wifid cmState 2>/dev/null | grep -qi connected; then
        if ping -c 1 -W 3 "$WIFI_TEST_IP" >/dev/null 2>&1; then
            ok=1
            break
        fi
    fi
    sleep 2
    waited=$((waited + 2))
done
if [ "$ok" = "1" ]; then
    echo "      已连通（耗时 ${waited}s，探测目标 $WIFI_TEST_IP）"
else
    echo "      连接失败：请先在系统设置里连上 WiFi，或换一个 WIFI_TEST_IP"
    echo "      提示：主地址在云端时 WIFI_TEST_IP 要填**外网**地址（223.5.5.5）；"
    echo "            只用局域网备用地址时才填网关（192.168.x.1）。"
    exit 1
fi

# --- 2. 下载 ---
echo ""
echo "[2/6] 下载图片…"
rm -f "$TMP" "$HDR"
start=$(date +%s)
if command -v curl >/dev/null 2>&1; then
    echo "      使用 curl"
    curl -L --silent --show-error --max-time "$HTTP_TIMEOUT" \
         -D "$HDR" --output "$TMP" "$DASHBOARD_URL"
    rc=$?
else
    echo "      使用 wget"
    wget -q -T "$HTTP_TIMEOUT" --server-response -O "$TMP" "$DASHBOARD_URL" 2>"$HDR"
    rc=$?
fi
echo "      返回码 $rc，耗时 $(( $(date +%s) - start ))s"
if [ "$rc" -ne 0 ] || [ ! -s "$TMP" ]; then
    echo "      下载失败。按返回码对照："
    echo "        6  = 域名解析失败（WiFi 没通或 DNS 有问题）"
    echo "        7  = 连不上服务器（服务没在跑，或防火墙挡了）"
    echo "        28 = 超时（网络慢，把 HTTP_TIMEOUT 调大）"
    echo "        35/60 = TLS 问题（设备时间不对会全线 https 失败，先校时）"
    exit 1
fi

# --- 3. 校验 ---
echo ""
echo "[3/6] 校验文件…"
size=$(wc -c <"$TMP" 2>/dev/null || echo 0)
magic=$(head -c 4 "$TMP" 2>/dev/null | od -An -tx1 | tr -d ' \n')
echo "      大小 $size 字节，文件头 $magic"
if [ "$magic" != "89504e47" ]; then
    echo "      不是 PNG！服务端回的是一张错误页（404 / 5xx）而不是图片。"
    echo "      前 200 字节预览："
    head -c 200 "$TMP"
    echo ""
    exit 1
fi
if [ "$size" -le 1024 ]; then
    echo "      文件过小，可能不完整"
    exit 1
fi

# --- 4. 对表 ---
echo ""
echo "[4/6] 设备时间对表…"
srv=$(grep -i '^x-epoch:' "$HDR" 2>/dev/null | tail -n 1 | tr -cd '0-9')
dev=$(date +%s 2>/dev/null)
if [ -n "$srv" ] && [ -n "$dev" ]; then
    drift=$((srv - dev))
    echo "      服务器 $(date -d "@$srv" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "epoch $srv")"
    echo "      设备   $(date '+%Y-%m-%d %H:%M:%S')   偏差 ${drift}s"
    abs=$drift; [ "$abs" -lt 0 ] && abs=$((0 - abs))
    if [ "$abs" -le "${CLOCK_SYNC_TOLERANCE:-120}" ]; then
        echo "      时间够准"
    else
        echo "      !! 偏 ${drift}s。时钟由本机画，偏多少屏幕上的时间就错多少。"
    fi
else
    echo "      没取到 X-Epoch 头（服务端可能还是旧版），跳过"
fi

# --- 5. 显示 ---
echo ""
echo "[5/6] 写入屏幕…"
cp "$TMP" "$IMG"
eips -c >/dev/null 2>&1
eips -f -g "$IMG" >/dev/null 2>&1
if [ "$CLOCK_MODE" = "local" ]; then
    echo "      整图已刷。注意右上角**此时是空白的** —— 时钟由下一步补上。"
else
    echo "      完成。屏幕应该已经换成信息屏了。"
fi

# --- 6. 时钟 ---
echo ""
echo "[6/6] 本机时钟…"
if [ "$CLOCK_MODE" != "local" ]; then
    echo "      CLOCK_MODE=$CLOCK_MODE，跳过（时间由出图时画死）"
else
    label=$(date +%H%M 2>/dev/null)
    sprite="$CLOCK_DIR/$label.png"
    if [ ! -f "$CLOCK_CONF" ]; then
        echo "      !! 缺 $CLOCK_CONF —— 精灵图没拷过。右上角会一直是空白。"
    elif [ ! -f "$sprite" ]; then
        echo "      !! 缺 $sprite —— 精灵图没拷全。"
    else
        echo "      贴 $label.png 到 ($CLOCK_X, $CLOCK_Y)，波形 ${CLOCK_WAVE:-du}"
        cout=$(eips -g "$sprite" -w "${CLOCK_WAVE:-du}" -x "$CLOCK_X" -y "$CLOCK_Y" 2>&1)
        echo "      eips 返回码 $?${cout:+，输出：$cout}"
        echo "      请看一眼：时钟应该在日历条**右上角**。"
        echo "      如果在屏幕左上角 → 这个固件不支持 -x/-y，跑「时钟贴图自检」看详情。"
    fi
fi

# --- 设备状态 ---
echo ""
echo "[设备状态]"
printf "      屏幕    : "; eips -i 2>/dev/null | grep -E 'xres|yres|bits_per_pixel' | tr -d ' ' | tr '\n' ' '
echo ""
echo "      电量    : $(gasgauge-info -c 2>/dev/null)"
echo "      分辨率  : 图片必须是上面 xres × yres 这个尺寸，否则显示会错位"
# 唤醒节点：这台 PW3 上没有老内核的 wakeup_enable，只有 /sys/class/rtc/*/wakealarm。
# 探测顺序必须和 aistatus.sh 里那段一致，否则这里报"未找到"、那边其实用得好好的，
# 白白把人往错方向带一个来回。
rtc=$(ls /sys/class/rtc/rtc0/wakealarm /sys/class/rtc/rtc1/wakealarm \
         /sys/class/rtc/rtc2/wakealarm 2>/dev/null | head -n 1)
[ -n "$rtc" ] || rtc=$(ls /sys/devices/platform/*rtc*/wakeup_enable 2>/dev/null | head -n 1)
echo "      RTC     : ${rtc:-!! 两代唤醒节点都没有，会退化成普通 sleep（整夜醒着）}"

rm -f "$TMP" "$HDR"
echo ""
echo "=== 完成。确认屏幕正常后，点「启动信息屏」进入常驻模式 ==="
