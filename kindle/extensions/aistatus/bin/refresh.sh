#!/bin/sh
# =============================================================================
#  立即刷新一次（带详细诊断）
#
#  这是排障时最有用的一个脚本：它不依赖后台主循环，自己把整条链路走一遍，
#  每一步都会打印结果。第一次配置时建议先单独跑这个，跑通了再启动主循环。
#  在书库里点「测试刷新一次」（它就是一个 scriptlet），或者 SSH 执行 sh bin/refresh.sh
#
#  最后两步会按当前配置把**时钟**和**电量角标**贴上去，所以看到的画面和常驻模式一致。
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
BATTERY_DIR="$DIR/battery"
BATTERY_CONF="$BATTERY_DIR/battery.conf"
BATTERY_X=""
BATTERY_Y=""
[ -f "$BATTERY_CONF" ] && . "$BATTERY_CONF"
BATTERY_X=$(printf '%s' "$BATTERY_X" | tr -d '\r')
BATTERY_Y=$(printf '%s' "$BATTERY_Y" | tr -d '\r')

# 和主循环里那份是同一个对照表。两个脚本各留一份是有意的：设备上的脚本要能
# 单独拷、单独跑，不该为了几行文本去 source 对方。
rc_meaning() {
    case "$1" in
        0)  echo "成功" ;;
        6)  echo "域名解析失败" ;;
        7)  echo "连不上服务器" ;;
        28) echo "超时" ;;
        35) echo "TLS 握手失败" ;;
        60) echo "证书不受信任" ;;
        90) echo "下载回来是空文件" ;;
        *)  echo "未知返回码" ;;
    esac
}

# 输出必须落盘。这个脚本是从书库里点开的，stdout 没有任何地方能看到 ——
# 它号称"排障首选"，但所有诊断其实都进了虚空，只能靠主循环那几行日志猜。
# 现在自己再跑一遍自己，屏幕上照常打印，同时整份写进 refresh.log。
LOG_FILE="$DIR/refresh.log"
if [ -z "$REFRESH_LOGGED" ] && command -v tee >/dev/null 2>&1; then
    REFRESH_LOGGED=1
    export REFRESH_LOGGED
    /bin/sh "$0" 2>&1 | tee "$LOG_FILE"
    exit 0
fi

echo "=== AI 信息屏 · 单次刷新诊断 ==="
echo "时间      : $(date)"
echo "出口地址（按顺序试）："
for u in $DASHBOARD_URLS; do echo "   $u"; done

# 空地址、或者还留着 <你的...> 这类占位符，都是没配好
if [ -z "$DASHBOARD_URLS" ]; then
    echo ""
    echo "!! config.sh 里的 DASHBOARD_URLS 是空的，先填上图片地址"
    exit 1
fi
case "$DASHBOARD_URLS" in
    *example*|*"<"*)
        echo ""
        echo "!! DASHBOARD_URLS 看起来还是示例/占位地址，请先改 config.sh"
        exit 1
        ;;
esac

# --- 1. WiFi ---
echo ""
echo "[1/7] 检查 WiFi…"
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

# --- 2. 下载：每个出口都试一遍，逐个记下结果 ---
# 这一步同时是"网络出口探针"。这台机器所在的网络对 GitHub 的几个域名区别对待
# （raw 的 IP 直接连不上，jsDelivr 和 Pages 通），而电脑上装的加速工具会把这件事
# 掩盖掉 —— 所以只能在设备上测。一次跑完就知道该把哪个地址放最前面。
echo ""
echo "[2/7] 下载图片（逐个出口试）…"
if command -v curl >/dev/null 2>&1; then TOOL=curl; else TOOL=wget; fi
echo "      用 $TOOL，单个出口最多等 $HTTP_TIMEOUT s"
CHOSEN=""
for url in $DASHBOARD_URLS; do
    rm -f "$TMP" "$HDR"
    # 带一次性参数破 CDN 缓存：jsDelivr 对分支引用最长缓存 12 小时
    case "$url" in
        *\?*) full="$url&t=$(date +%s)" ;;
        *)    full="$url?t=$(date +%s)" ;;
    esac
    start=$(date +%s)
    if [ "$TOOL" = "curl" ]; then
        curl -L --silent --show-error --max-time "$HTTP_TIMEOUT" \
             -D "$HDR" --output "$TMP" "$full"
    else
        wget -q -T "$HTTP_TIMEOUT" --server-response -O "$TMP" "$full" 2>"$HDR"
    fi
    rc=$?
    cost=$(( $(date +%s) - start ))
    host=$(printf '%s' "$url" | sed 's#^https\?://##; s#/.*##')
    [ "$rc" = "0" ] && [ ! -s "$TMP" ] && rc=90
    if [ "$rc" != "0" ]; then
        echo "      ✗ $host  rc=$rc（$(rc_meaning "$rc")）  ${cost}s"
        continue
    fi
    magic=$(head -c 4 "$TMP" 2>/dev/null | od -An -tx1 | tr -d ' \n')
    if [ "$magic" != "89504e47" ]; then
        echo "      ✗ $host  回来的不是 PNG（文件头 $magic，多半是 404 页面）  ${cost}s"
        continue
    fi
    CHOSEN="$host"
    echo "      ✓ $host  $(wc -c <"$TMP") 字节  ${cost}s"
    break
done

if [ -z "$CHOSEN" ]; then
    echo ""
    echo "      所有出口都没拿到图。返回码对照："
    echo "        6  = 域名解析失败（WiFi 没通或 DNS 有问题）"
    echo "        7  = 连不上服务器（IP 被挡、或对方没在听）"
    echo "        28 = 超时（链路丢包，或 HTTP_TIMEOUT 太小）"
    echo "        35/60 = TLS 问题（设备时间不对会全线 https 失败，先校时）"
    echo "        90 = 命令成功但下载回来是空文件"
    exit 1
fi

# --- 3. 校验 ---
echo ""
echo "[3/7] 校验文件…"
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
echo "[4/7] 设备时间对表…"
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
echo "[5/7] 写入屏幕…"
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
echo "[6/7] 本机时钟…"
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

# --- 7. 电量 ---
echo ""
echo "[7/7] 本机电量角标…"
if [ "$BATTERY_MODE" != "local" ]; then
    echo "      BATTERY_MODE=$BATTERY_MODE，跳过（电量由出图时画死）"
elif [ ! -f "$BATTERY_CONF" ]; then
    echo "      !! 缺 $BATTERY_CONF —— 右上角那块会一直是空白。"
    echo "         电脑上跑 python dashboard/tools/make_battery_assets.py，"
    echo "         然后把 battery/ 整个目录拷到 extensions/aistatus/ 下。"
else
    bat=$(gasgauge-info -c 2>/dev/null)
    if [ -z "$bat" ]; then
        echo "      gasgauge-info 没读到电量，跳过"
    else
        lvl=$(( (bat + 5) / 10 * 10 ))
        [ "$lvl" -gt 100 ] && lvl=100
        sprite="$BATTERY_DIR/$(printf '%03d' "$lvl").png"
        if [ ! -f "$sprite" ]; then
            echo "      !! 缺 $sprite —— battery/ 没拷全，整目录重拷一次"
        else
            echo "      电量 ${bat}% → 贴 $(basename "$sprite") 到 ($BATTERY_X,$BATTERY_Y)，波形 ${BATTERY_WAVE:-du}"
            bout=$(eips -g "$sprite" -w "${BATTERY_WAVE:-du}" -x "$BATTERY_X" -y "$BATTERY_Y" 2>&1)
            echo "      eips 返回码 $?${bout:+，输出：$bout}"
            echo "      请看一眼：右上角那块空白应该已经变成电量角标了。"
        fi
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
