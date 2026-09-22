#!/bin/sh
# =============================================================================
#  时钟贴图自检
#
#  这个脚本存在的唯一理由：**「Kindle 能不能把一张小图贴到屏幕指定位置」
#  只能在真机上验证**，模拟不出来。官方文档说 eips 的 -x/-y 在 K4 上不生效，
#  老固件（比如 2015 年的 PW3）到底支不支持，只有试过才知道。
#
#  跑一次大概 20~40 秒，结束时请**抬头看一眼屏幕**，三种结果对应三种处置：
#
#    · 时钟出现在日历条右上角（原本该在的位置）
#        → 一切正常，可以放心把 CLOCK_MODE 保持 local
#    · 时钟出现在**屏幕左上角**
#        → 这个固件不支持 -x/-y，贴图全被丢到原点。把 CLOCK_MODE 改成 off，
#          同时把 dashboard/config.yaml 里的 clock.mode 改回 image
#    · 什么都没出现
#        → 精灵图没拷全，或者 eips 报错了（下面会打印返回码和原始输出）
#
#  在书库里点「时钟贴图自检」，或者 SSH 执行 sh bin/clock_check.sh
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

echo "=== AI 信息屏 · 时钟贴图自检 ==="
echo "设备时间  : $(date '+%Y-%m-%d %H:%M:%S')   （epoch $(date +%s 2>/dev/null)）"
echo ""

# --- 1. 配置 -----------------------------------------------------------------
echo "[1/5] 配置"
echo "      CLOCK_MODE     : ${CLOCK_MODE:-未设置}"
echo "      CLOCK_INTERVAL : ${CLOCK_INTERVAL:-未设置}s"
echo "      CLOCK_WAVE     : ${CLOCK_WAVE:-未设置}"
echo "      CLOCK_SYNC     : ${CLOCK_SYNC:-未设置}（容差 ${CLOCK_SYNC_TOLERANCE:-?}s）"

if [ "$CLOCK_MODE" != "local" ]; then
    echo ""
    echo "!! CLOCK_MODE 不是 local，本机不会贴时钟。"
    echo "   如果你在 dashboard/config.yaml 里已经把 clock.mode 改成了 local，"
    echo "   那图里那块就是空白的 —— 两边必须同时切，先把这里改成 local。"
    exit 1
fi

# --- 2. 精灵图 ---------------------------------------------------------------
echo ""
echo "[2/5] 精灵图和坐标"
if [ ! -f "$CLOCK_CONF" ]; then
    echo "      !! 找不到 $CLOCK_CONF"
    echo "      在电脑上跑：python dashboard/tools/make_clock_assets.py"
    echo "      然后把整个 clock/ 目录拷到 $CLOCK_DIR/"
    exit 1
fi
. "$CLOCK_CONF"
# CRLF 检测：生成脚本写的是 LF，但如果有人用 Windows 记事本改过这个文件，
# 行尾会变成 CRLF，CLOCK_X 就成了 "644\r" —— eips 不会报错，只会贴错位置。
# 用 case 匹配而不是 $'\r'：Kindle 上是 busybox 的 ash，不认 bash 那种写法。
CR=$(printf '\r')
case "$CLOCK_X" in
    *"$CR"*)
        echo "      !! clock.conf 里有 CR（回车），大概率被 Windows 编辑器改过。"
        echo "         认真解法：回电脑重跑 make_clock_assets.py 并重拷。"
        echo "         本次先自动剥掉回车继续测。"
        ;;
esac
CLOCK_X=$(printf '%s' "$CLOCK_X" | tr -d '\r')
CLOCK_Y=$(printf '%s' "$CLOCK_Y" | tr -d '\r')
CLOCK_W=$(printf '%s' "$CLOCK_W" | tr -d '\r')
CLOCK_H=$(printf '%s' "$CLOCK_H" | tr -d '\r')
echo "      坐标文件 : $CLOCK_CONF"
echo "      贴图位置 : ($CLOCK_X, $CLOCK_Y)   画布 ${CLOCK_W}x${CLOCK_H}"
echo "      资产指纹 : ${CLOCK_TAG:-无}"
echo "      精灵字体 : ${CLOCK_FONT:-未记录}"

count=$(ls "$CLOCK_DIR"/*.png 2>/dev/null | wc -l)
echo "      精灵图数 : $count / 1440"
if [ "$count" -lt 1440 ]; then
    echo "      !! 少 $((1440 - count)) 张，拷贝过程中断了。重拷一次整个目录。"
fi

label=$(date +%H%M 2>/dev/null)
sprite="$CLOCK_DIR/$label.png"
echo "      当前时刻 : $label → $(basename "$sprite")"
if [ ! -f "$sprite" ]; then
    echo "      !! 这张不存在，函数没法继续测"
    exit 1
fi

# --- 3. 取一张整图 -----------------------------------------------------------
echo ""
echo "[3/5] 取一张整图（顺便对表）"
lipc-set-prop com.lab126.cmd wirelessEnable 1 >/dev/null 2>&1
waited=0
ok=0
while [ "$waited" -lt "$WIFI_TIMEOUT" ]; do
    if lipc-get-prop com.lab126.wifid cmState 2>/dev/null | grep -qi connected; then
        if ping -c 1 -W 3 "$WIFI_TEST_IP" >/dev/null 2>&1; then ok=1; break; fi
    fi
    sleep 2
    waited=$((waited + 2))
done

if [ "$ok" = "1" ]; then
    rm -f "$TMP" "$HDR"
    got=0
    for url in "$DASHBOARD_URL" "$DASHBOARD_FALLBACK_URL"; do
        [ -n "$url" ] || continue
        if command -v curl >/dev/null 2>&1; then
            curl -L --silent --show-error --max-time "$HTTP_TIMEOUT" \
                 -D "$HDR" --output "$TMP" "$url" && got=1
        else
            wget -q -T "$HTTP_TIMEOUT" --server-response -O "$TMP" "$url" 2>"$HDR" && got=1
        fi
        [ "$got" = "1" ] && break
    done
    if [ "$got" = "1" ] && [ -s "$TMP" ]; then
        cp "$TMP" "$IMG"
        echo "      下载成功 $(wc -c <"$IMG" | tr -d ' ') 字节"
        srv=$(grep -i '^x-epoch:' "$HDR" 2>/dev/null | tail -n 1 | tr -cd '0-9')
        dev=$(date +%s 2>/dev/null)
        if [ -n "$srv" ] && [ -n "$dev" ]; then
            drift=$((srv - dev))
            echo "      服务器 epoch : $srv"
            echo "      设备   epoch : $dev"
            echo "      偏差         : ${drift}s"
            abs=$drift
            [ "$abs" -lt 0 ] && abs=$((0 - abs))
            if [ "$abs" -le "${CLOCK_SYNC_TOLERANCE:-120}" ]; then
                echo "      → 设备时间够准，本机时钟可信"
            else
                echo "      → !! 偏太多。屏幕上的时间会一直是错的。"
                if [ "$CLOCK_SYNC" = "1" ]; then
                    echo "        CLOCK_SYNC=1，下次拉整图时会自动尝试校准（并记日志）"
                else
                    echo "        CLOCK_SYNC=0，不会自动校准。建议改成 1。"
                fi
            fi
        else
            echo "      没取到 X-Epoch 头，跳过对表"
        fi
    else
        echo "      下载失败，改用空白屏幕继续测贴图"
        rm -f "$IMG"
    fi
else
    echo "      WiFi 连不上（${waited}s），改用空白屏幕继续测贴图"
    rm -f "$IMG"
fi

# --- 4. 铺底 -----------------------------------------------------------------
echo ""
echo "[4/5] 铺底"
if [ -f "$IMG" ]; then
    eips -c >/dev/null 2>&1
    eips -f -g "$IMG" >/dev/null 2>&1
    echo "      已显示整图（注意：此时右上角的时钟位置**应该是空白的**）"
else
    eips -c >/dev/null 2>&1
    echo "      已清屏（没有整图可用，只测贴图位置）"
fi

# --- 5. 贴时钟 ---------------------------------------------------------------
echo ""
echo "[5/5] 贴时钟"
out=$(eips -g "$sprite" -w "${CLOCK_WAVE:-du}" -x "$CLOCK_X" -y "$CLOCK_Y" 2>&1)
rc=$?
echo "      eips 返回码 : $rc"
[ -n "$out" ] && echo "      eips 输出   : $out"

rm -f "$TMP" "$HDR"
echo ""
echo "======================================================================"
echo " 现在抬头看屏幕："
echo ""
echo "   ① 时钟在**日历条右上角**  → 正常，-x/-y 生效，方案可用"
echo "   ② 时钟在**屏幕左上角**    → 固件不支持 -x/-y，改成 CLOCK_MODE=off，"
echo "                               并把 dashboard/config.yaml 的 clock.mode 改回 image"
echo "   ③ 什么都没有              → 精灵图问题，看上面的返回码和输出"
echo ""
echo " 注意：这个脚本只是贴一张图给你看，**不会常驻**。"
echo "       如果你正在跑信息屏，下一分钟它就会自己再贴一次、把这里的结果覆盖掉。"
echo "       要长期跑请点「启动信息屏」。"
echo "======================================================================"
