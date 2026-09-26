#!/bin/sh
# =============================================================================
#  AI 信息屏 · Kindle 端主循环
#
#  省电逻辑说明（这是整个项目最关键的一段，别随手改）：
#    设备绝大多数时间在 `echo mem` 里睡死，墨水屏保持画面本身零功耗。
#    醒来的节奏由两件事决定，取更短的那个：
#      · 取图节奏 —— 设备本机算：每 FETCH_EVERY_MINUTES 分钟一次，对齐到固定格子
#        第 FETCH_ALIGN_MINUTE 分；QUIET_START~QUIET_END 这段时间完全不联网
#      · STOP_CHECK_MAX_SLEEP —— 为了「停止信息屏」能在 10 分钟内生效，
#        再长的觉也切成 10 分钟一段来睡（一天醒 144 次，仍然远比贴钟时省）
#
#    必须用 `echo mem > /sys/power/state` 强制休眠，而不是靠 lipc 的
#    rtcWakeup —— 后者在多个机型上被实测证明"叫不醒"，这是别人的坑，别再踩。
#
#  CLOCK_MODE=local 是另一套故事：那时候每分钟醒一次贴时钟精灵图（1440 次/天），
#  续航直接崩掉。2026-09-22 已经把时钟砍了（CLOCK_MODE=off），
#  上面那两档节奏才是现在的真实行为。
# =============================================================================

DIR=/mnt/us/extensions/aistatus
[ -f "$DIR/config.sh" ] || DIR="$(cd "$(dirname "$0")/.." && pwd)"
. "$DIR/config.sh"

IMG=/tmp/aistatus.png
TMP=/tmp/aistatus.tmp
HDR=/tmp/aistatus.hdr
LOG="$DIR/aistatus.log"
RUN_FLAG="$DIR/.running"
STOP_FLAG="$DIR/.stop"
PID_FILE="$DIR/.pid"

# 唤醒节点。两代内核写法不一样，用错一代 = 永远找不到 = 永远不真睡：
#   K4 及更早：/sys/devices/platform/*rtc*/wakeup_enable，写进去的是"还差几秒"
#   K5（PW3）：/sys/class/rtc/rtcN/wakealarm，写进去的是 "+几秒"，且要先写 0 清空
# 实测这台 PW3 上**没有** wakeup_enable（见 system_probe.txt），只有三个可写的
# wakealarm。以前只找老写法，所以每次都掉进"退化成普通 sleep"那条退路 ——
# 设备整夜醒着，一晚掉 60% 电，而日志上完全看不出来。
RTC=""
RTC_KIND=""
for r in /sys/class/rtc/rtc0/wakealarm /sys/class/rtc/rtc1/wakealarm \
         /sys/class/rtc/rtc2/wakealarm; do
    if [ -w "$r" ]; then RTC="$r"; RTC_KIND="wakealarm"; break; fi
done
if [ -z "$RTC" ]; then
    r=$(ls /sys/devices/platform/*rtc*/wakeup_enable 2>/dev/null | head -n 1)
    [ -n "$r" ] && [ -w "$r" ] && { RTC="$r"; RTC_KIND="wakeup_enable"; }
fi

# 时钟精灵图的坐标由 make_clock_assets.py 生成，跟出图共用同一份几何。
# 坐标文件不在就是没生成过，stamp_clock 会点名报错而不是默默什么都不做。
CLOCK_DIR="$DIR/clock"
CLOCK_CONF="$CLOCK_DIR/clock.conf"
CLOCK_X=""
CLOCK_Y=""
[ -f "$CLOCK_CONF" ] && . "$CLOCK_CONF"
# 万一 clock.conf 被人用 Windows 编辑器改成了 CRLF，CLOCK_X 会是 "644\r"，
# eips 拿到带回车的参数就贴错位置（而且不报错）。这里把回车剥掉，
# 不指望所有人都记得"别用记事本另存"。生成脚本本身已经写 LF，这是第二道保险。
CLOCK_X=$(printf '%s' "$CLOCK_X" | tr -d '\r')
CLOCK_Y=$(printf '%s' "$CLOCK_Y" | tr -d '\r')
CLOCK_W=$(printf '%s' "$CLOCK_W" | tr -d '\r')
CLOCK_H=$(printf '%s' "$CLOCK_H" | tr -d '\r')

# 电量精灵图的坐标，同 clock.conf 的套路：source 进来 + 剥回车
BATTERY_DIR="$DIR/battery"
BATTERY_CONF="$BATTERY_DIR/battery.conf"
BATTERY_X=""
BATTERY_Y=""
[ -f "$BATTERY_CONF" ] && . "$BATTERY_CONF"
BATTERY_X=$(printf '%s' "$BATTERY_X" | tr -d '\r')
BATTERY_Y=$(printf '%s' "$BATTERY_Y" | tr -d '\r')

refresh_count=0
consecutive_failures=0
clock_warned=0          # 精灵图缺失只报一次，不然日志会被刷爆
battery_warned=0        # 同上，电量精灵图缺失也只报一次
last_battery_lvl=""     # 上一次贴上去的是哪一档。档位没变就不重复贴、不写日志
sync_disabled=0         # 校时失败一次就别再试，免得反复折腾
notice_shown=0          # 低电量提示占着整屏时不贴时钟
woken_early=0           # 本轮是被人为唤醒的（不是闹钟），要重贴整图
taps=0                  # 连点计数（人为唤醒连续几次就算"主动要退出"）
last_tap_at=0           # 上一次人为唤醒的时刻
sleep_logged=0          # 休眠实测只记头三次，第四次起只在真睡够时记一行
suspend_bounces=0       # 连续"echo mem 秒弹回"的次数
# 休眠质量计数，每次取图时汇总成一行写进日志后清零。
# 为什么要有这三个数：以前只在**每个进程的头三次**记"计划 vs 实际"，于是"过去
# 一整天到底睡没睡进去"这件事在日志里根本没有证据 —— 掉电快的时候只能猜。
sleep_ok=0              # 睡够八成时间的次数
sleep_ok_seconds=0      # 这些一共睡了多少秒
sleep_short=0           # 睡进去但被提前叫醒的次数
sleep_bounce=0          # 一秒弹回的次数
bounce_warned=0         # 空转警告只喊一次
# 屏幕上**此刻**是哪张图（按我们上次真的画出去的那份内容算）。
# 为什么不拿 /tmp 里的缓存文件当依据：那样干过一次，把屏幕永久弄白了 ——
# 缓存文件在，不代表屏幕上还是它（framework 一停、或者显示过充电提醒，屏幕就变了），
# 于是"和上次一样"= 什么都不画 = 一直白着。见 refresh() 里那段注释。
screen_hash=""          # 空 = 不知道屏幕上是什么，必须画
skip_streak=0           # 连续跳过几次了；到上限强制画一次兜底

# ---------------------------------------------------------------------------
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"
    # 日志无限增长会撑爆分区，定期裁掉
    lines=$(wc -l <"$LOG" 2>/dev/null || echo 0)
    if [ "$lines" -gt "$LOG_MAX_LINES" ]; then
        tail -n "$LOG_MAX_LINES" "$LOG" >"$LOG.tmp" 2>/dev/null
        # 只有 tmp 真拿到内容才替换。以前是无条件 mv：tail 一旦失败（内存紧、
        # 读失败都可能），mv 就把一份好日志换成 0 字节 —— 2026-09-26 整份
        # 历史就是这么没的，而那次正好是要靠它判断 WiFi 是从哪一轮开始坏的。
        if [ -s "$LOG.tmp" ]; then
            mv "$LOG.tmp" "$LOG"
        else
            rm -f "$LOG.tmp"
        fi
    fi
}

# 只接受纯数字，避免空串或乱码把 $(( )) 弄崩
int_of() {
    case "$1" in
        ''|*[!0-9]*) echo 0 ;;
        *) echo "$1" ;;
    esac
}

now_epoch() {
    int_of "$(date +%s 2>/dev/null)"
}

# ---------------------------------------------------------------------------
wifi_on()  { lipc-set-prop com.lab126.cmd wirelessEnable 1 >/dev/null 2>&1; }
wifi_off() { lipc-set-prop com.lab126.cmd wirelessEnable 0 >/dev/null 2>&1; }

wifi_connected() {
    lipc-get-prop com.lab126.wifid cmState 2>/dev/null | grep -qi connected
}

# 连不上时到底卡在哪一层：射频没开 / 没关联上 AP / 关联上了但没 IP / 有 IP 却出不去。
# 以前只写一句"WiFi 连接超时"，这四种完全不同的病在日志里长得一模一样 ——
# 跟当年 curl 只写"下载失败"是同一个毛病，别再犯。
wifi_state() {
    radio=$(lipc-get-prop com.lab126.cmd wirelessEnable 2>/dev/null)
    cm=$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)
    ip=$(ifconfig wlan0 2>/dev/null | tr -c '0-9.' '\n' \
         | grep -x '[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}' | head -1)
    case "$ip" in
        "") ping_result="没发" ;;
        *) if ping -c 1 -W 3 "$WIFI_TEST_IP" >/dev/null 2>&1; then ping_result="通"; else ping_result="不通"; fi ;;
    esac
    echo "射频=${radio:-读不到} · cmState=${cm:-读不到} · wlan0=${ip:-无IP} · ping $WIFI_TEST_IP=$ping_result"
}

wait_for_wifi() {
    waited=0
    while [ "$waited" -lt "$WIFI_TIMEOUT" ]; do
        if wifi_connected; then
            # cmState 说连上了，但 DHCP/路由可能还没好，实际探一下才算数
            if ping -c 1 -W 3 "$WIFI_TEST_IP" >/dev/null 2>&1; then
                return 0
            fi
        fi
        sleep 2
        waited=$((waited + 2))
    done
    return 1
}

# ---------------------------------------------------------------------------
# 下载图片。优先 curl，其次 wget —— 两者在不同固件上的去留不一样，所以都试。
# 顺手把响应头存下来：服务器会带一个 X-Epoch，用来给设备校时。
# 返回码的含义写进日志用。以前只写"下载失败"，于是"DNS 不通 / 连不上 / 证书被拒 /
# 超时"这四种完全不同的病在日志里长得一模一样，只能靠反复插拔试出来。
rc_meaning() {
    case "$1" in
        0)  echo "成功" ;;
        6)  echo "域名解析失败（DNS 不通）" ;;
        7)  echo "连不上服务器（被墙、IP 不通、或对方没在听）" ;;
        28) echo "超时（链路丢包，或 HTTP_TIMEOUT 太小）" ;;
        35) echo "TLS 握手失败" ;;
        60) echo "证书不受信任（设备时间错、或根证书太旧）" ;;
        90) echo "命令没问题，但下载回来是空文件" ;;
        *)  echo "未知返回码" ;;
    esac
}

# 下一个地址。失败原因留在 last_rc 里给调用方写日志。
# 每次请求都带一个一次性参数：jsDelivr 对分支引用最长缓存 12 小时，
# 不破除的话屏幕能停在半天前；raw 和 Pages 会忽略这个参数，所以三家统一加。
download_image() {
    url="$1"
    out="$2"
    stamp=$(/bin/date +%s 2>/dev/null || echo 0)
    case "$url" in
        *\?*) full="$url&t=$stamp" ;;
        *)    full="$url?t=$stamp" ;;
    esac
    rm -f "$HDR" "$out" 2>/dev/null
    if command -v curl >/dev/null 2>&1; then
        curl -L --silent --show-error --max-time "$HTTP_TIMEOUT" \
             -D "$HDR" --output "$out" "$full"
        last_rc=$?
        [ -s "$out" ] || last_rc=90
        return "$last_rc"
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -q -T "$HTTP_TIMEOUT" --server-response -O "$out" "$full" 2>"$HDR"
        last_rc=$?
        [ -s "$out" ] || last_rc=90
        return "$last_rc"
    fi
    log "错误：curl 和 wget 都没有，无法下载"
    last_rc=127
    return 1
}

# 图片必须是 PNG 且大于 1KB，否则多半是错误页或空文件
image_is_sane() {
    [ -s "$1" ] || return 1
    size=$(wc -c <"$1" 2>/dev/null || echo 0)
    [ "$size" -gt 1024 ] || return 1
    # 校验 PNG magic，防止把 HTML 错误页当图片显示出来
    head -c 4 "$1" 2>/dev/null | od -An -tx1 | tr -d ' \n' | grep -qi '^89504e47'
}

# ---------------------------------------------------------------------------
# 设备时间校准：拿响应头里的 X-Epoch 对表。
# 只有我们自己跑的 HTTP 服务才发这个头，GitHub 不发 —— 那时这个函数找不到
# 头就静默返回，不报错也不校（故意的：缺头是常态，不是故障）。
sync_clock() {
    [ "$CLOCK_SYNC" = "1" ] || return 0
    [ "$sync_disabled" = "1" ] && return 0
    [ -s "$HDR" ] || return 0

    srv=$(grep -i '^x-epoch:' "$HDR" 2>/dev/null | tail -n 1 | tr -cd '0-9')
    [ -n "$srv" ] || return 0
    dev=$(now_epoch)
    [ "$dev" -gt 0 ] || return 0

    drift=$((srv - dev))
    abs=$drift
    [ "$abs" -lt 0 ] && abs=$((0 - abs))
    if [ "$abs" -le "$CLOCK_SYNC_TOLERANCE" ]; then
        return 0
    fi

    log "设备时间偏了 ${drift}s，尝试用服务器时间校准"
    # 不同固件的 date 支持的写法不一样，几种都试一遍，**每次都必须回读验证** ——
    # 宁可校不上，也不能把时间设成一个乱七八糟的值。
    for attempt in "@$srv" "$srv"; do
        date -s "$attempt" >/dev/null 2>&1
        back=$(now_epoch)
        backdrift=$((srv - back))
        [ "$backdrift" -lt 0 ] && backdrift=$((0 - backdrift))
        if [ "$back" -gt 0 ] && [ "$backdrift" -le 5 ]; then
            hwclock -w >/dev/null 2>&1
            log "校准成功，设备时间现在是 $(date '+%Y-%m-%d %H:%M:%S')"
            return 0
        fi
    done

    sync_disabled=1
    log "!! 校时失败：这个固件的 date 不接受 epoch。已停止重试。"
    log "   屏幕上的时间会一直偏 ${drift}s。临时办法：把 STOP_FRAMEWORK 改成 0"
    log "   启动一次原生界面让它自己对时，再改回 1。"
    return 1
}

# ---------------------------------------------------------------------------
# 问服务器「下一次什么时候来才有新图」。
#
# 只有我们自己跑的 HTTP 服务才会发这个头。GitHub 的 raw 地址是静态文件，
# 发不了自定义头，所以走 GitHub 时这个函数基本恒返回 1 —— 这是预期的，
# 时刻表由下面的 minutes_until_next_fetch 在本机算。留着这条分支是因为它无副作用：
# 哪天把自建服务当备用出口再开起来，不用改这边一行。
#
# 单位是 epoch 秒；被中间层改坏、或设备时间被校歪时，只接受
# 「在未来、且不超过 12 小时」的值，否则退回本机节奏。
next_image_from_headers() {
    [ -s "$HDR" ] || return 1
    raw=$(grep -i '^[[:space:]]*x-next-image:' "$HDR" 2>/dev/null | tail -n 1 | tr -cd '0-9')
    [ -n "$raw" ] || return 1
    hnow=$(now_epoch)
    [ "$hnow" -gt 0 ] || return 1
    # 只接受「在未来、且不超过 12 小时」的值。设备时间被校歪、或者这个头被
    # 中间层改坏时，宁可退回轮询也不能把屏幕冻在一张图上不动 —— 后者是静默故障。
    [ "$raw" -gt "$hnow" ] || return 1
    [ $((raw - hnow)) -le 43200 ] || return 1
    echo "$raw"
}

# ---------------------------------------------------------------------------
# 贴时钟。只刷那一小块（局部刷新），不闪屏、快。
stamp_clock() {
    [ "$CLOCK_MODE" = "local" ] || return 0

    if [ ! -f "$CLOCK_CONF" ]; then
        if [ "$clock_warned" = "0" ]; then
            clock_warned=1
            log "!! CLOCK_MODE=local 但找不到 $CLOCK_CONF"
            log "   把 make_clock_assets.py 生成的 clock/ 整个目录拷进来即可。"
        fi
        return 1
    fi

    label=$(date +%H%M 2>/dev/null)
    [ -n "$label" ] || return 1
    sprite="$CLOCK_DIR/$label.png"
    if [ ! -f "$sprite" ]; then
        if [ "$clock_warned" = "0" ]; then
            clock_warned=1
            log "!! 精灵图缺文件：$sprite —— 大致是没拷全，重新整目录拷一次"
        fi
        return 1
    fi

    eips -g "$sprite" -w "$CLOCK_WAVE" -x "$CLOCK_X" -y "$CLOCK_Y" >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# 贴电量。和贴时钟同一条路：整图右上角是留白的，刷完整图补这一小块。
# 缺精灵图 / 读不到电量都只记一次日志然后跳过 —— 右上角留白是无害的，
# 不能因为电量把整轮刷新搞失败。
#
# 参数给 `force` 表示"整屏刚被重画过，那块现在是空的，必须贴"。不给参数就走
# 档位缓存：这一步现在每次心跳都跑（默认 10 分钟一次），而电量一小时才挪一档
# 左右 —— 重复贴同一张图既费电，又会把日志灌满（只留 500 行，灌满了真要看的
# 故障记录就被冲掉了）。
stamp_battery() {
    [ "$BATTERY_MODE" = "local" ] || return 0

    if [ ! -f "$BATTERY_CONF" ]; then
        if [ "$battery_warned" = "0" ]; then
            battery_warned=1
            log "!! BATTERY_MODE=local 但找不到 $BATTERY_CONF"
            log "   把 make_battery_assets.py 生成的 battery/ 整个目录拷进来即可。"
        fi
        return 1
    fi

    bat=$(battery_level)
    [ -n "$bat" ] || return 1
    lvl=$(( (bat + 5) / 10 * 10 ))
    [ "$lvl" -gt 100 ] && lvl=100
    if [ "$1" != "force" ] && [ "$lvl" = "$last_battery_lvl" ]; then
        return 0
    fi
    sprite="$BATTERY_DIR/$(printf '%03d' "$lvl").png"
    if [ ! -f "$sprite" ]; then
        if [ "$battery_warned" = "0" ]; then
            battery_warned=1
            log "!! 电量精灵图缺文件：$sprite —— 大致是没拷全，重新整目录拷一次"
        fi
        return 1
    fi

    eips -g "$sprite" -w "$BATTERY_WAVE" -x "$BATTERY_X" -y "$BATTERY_Y" >/dev/null 2>&1
    last_battery_lvl=$lvl
    # 成功也要留证据。以前只有失败才写日志，结果"电量到底准不准、有没有真贴上去"
    # 在日志里完全看不出来 —— 2026-09-24 查一次电量就得重新插拔一轮，就是因为没记录。
    log "电量 ${bat}% → 贴 $(basename "$sprite") 于 ($BATTERY_X,$BATTERY_Y)"
}

# ---------------------------------------------------------------------------
battery_level() {
    gasgauge-info -c 2>/dev/null | tr -cd '0-9'
}

file_hash() {
    # busybox 一定有 cksum；md5sum 在部分固件上没编进去，别赌。
    cksum "$1" 2>/dev/null | cut -d' ' -f1,2
}

show_image() {
    # 局部刷新快且不闪屏，但会累积残影；贴时钟的那 60 次/小时更是明显，
    # 所以整图这一下默认每次都全刷（FULL_REFRESH_EVERY=1），等于每小时清一次鬼影。
    if [ "$FULL_REFRESH_EVERY" -le 1 ] || \
       [ $((refresh_count % FULL_REFRESH_EVERY)) -eq 0 ]; then
        eips -f -g "$IMG" >/dev/null 2>&1
        log "已全屏刷新（第 $refresh_count 次）"
    else
        eips -g "$IMG" >/dev/null 2>&1
    fi
    refresh_count=$((refresh_count + 1))
    # 只有"我们真的把这张画上去了"才敢记下屏幕内容 —— 这是跳过重绘的唯一依据。
    screen_hash=$(file_hash "$IMG")
    skip_streak=0
}

show_status_line() {
    [ "$SHOW_STATUS_LINE" = "1" ] || return 0
    bat=$(battery_level)
    eips "$STATUS_COL" "$STATUS_ROW" \
         "$(date '+%m-%d %H:%M')  电量 ${bat}%  ${consecutive_failures} 次失败" \
         >/dev/null 2>&1
}

show_low_battery_notice() {
    bat=$(battery_level)
    # 直接用系统字模在屏幕上敲字，不依赖网络也不依赖图片
    eips -c >/dev/null 2>&1
    # 屏幕上已经不是那张信息图了 —— 必须作废，否则下一轮"图没变"会跳过重绘，
    # 充电提醒就永远留在屏上。
    screen_hash=""
    eips 10 8 "  AI 信息屏 · 电量不足  " >/dev/null 2>&1
    eips 10 11 "  当前电量：${bat}%  " >/dev/null 2>&1
    eips 10 14 "  请尽快接上充电器，  " >/dev/null 2>&1
    eips 10 17 "  否则屏幕将停止刷新。  " >/dev/null 2>&1
    log "电量 ${bat}%，已显示充电提醒"
}

# ---------------------------------------------------------------------------
is_low_battery() {
    bat=$(battery_level)
    [ -n "$bat" ] || return 1
    [ "$bat" -le "$LOW_BATTERY_THRESHOLD" ]
}

# 这个小时在不在安静期里。QUIET_START / QUIET_END 任一为空 = 不启用。
# 支持跨零点（比如 22 到 7）。
in_quiet_hour() {
    [ -n "$QUIET_START" ] && [ -n "$QUIET_END" ] || return 1
    s=$QUIET_START
    e=$QUIET_END
    case "$s$e" in *[!0-9]*) return 1 ;; esac
    [ "$s" -le 23 ] && [ "$e" -le 23 ] || return 1
    if [ "$s" -lt "$e" ]; then
        [ "$1" -ge "$s" ] && [ "$1" -lt "$e" ]
    else
        [ "$1" -ge "$s" ] || [ "$1" -lt "$e" ]
    fi
}

# ---------------------------------------------------------------------------
# 本机时刻表：算出"距离下一个出图点还有多少秒"。
#
# 走 GitHub 之后这一步必须在设备上做 —— raw.githubusercontent.com 是静态文件，
# 发不了自定义头，云端那种"服务器告诉你几点再来"没有了。
#
# 全程只用整数加减乘和 date 的字段，不碰 `date -d`：busybox 的 date 对 `-d`
# 的支持随固件版本变，解析失败会**静默**返回空，那就又是"屏幕冻一整天、
# 日志一个字不写"那一类坑。
hm_to_min() {
    # 先卡格式再算数。`${#1}` 取长度这种写法在 ash 上没把握，用 case 表达同一件事。
    case "$1" in
        [01][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) ;;
        *) return 1 ;;
    esac
    h=${1%%:*}
    m=${1##*:}
    # 别用 $((10#$h))：ash 不保证认基数写法。剥前导零即可，
    # 但"00" 剥完剩 "0"、"08" 剥完剩 "8"，两种都要覆盖到。
    h=${h#0}; [ -n "$h" ] || h=0
    m=${m#0}; [ -n "$m" ] || m=0
    echo $(( h * 60 + m ))
}

now_minutes() {
    hm_to_min "$(date +%H:%M 2>/dev/null)"
}

# 距离下一个取图时刻还有多少**分钟**。规则只有一条：每隔
# FETCH_EVERY_MINUTES 分钟取一次，并且落在 (分钟数 - FETCH_ALIGN_MINUTE) 能被步长
# 整除的那些格子上（60/10 就是每小时第 10 分，和以前完全一样）。
#
# 为什么要对齐到一个固定的分钟：不对齐的话 next 是"上次成功 + 3600 秒"推出来的，
# 于是取图时刻每天往后漂（18:11、19:14、20:22……），屏上"更新 HH:MM"看着就没规律。
# 对齐到第 10 分是因为 Actions 整点才开始跑，跑完提交要两三分钟，前面还有一层
# CDN 缓存 —— 卡在整点去取必然拿到上一张。
#
# 全程只用整数加减乘和 date 的字段，不碰 `date -d`：busybox 的 date 对 `-d`
# 的支持随固件版本变，解析失败会**静默**返回空，那就又是"屏幕冻一整天、
# 日志一个字不写"那一类坑。小时数也不写 $((10#$h))，ash 不保证认 —— 剥前导零。
minutes_until_next_fetch() {
    step=${FETCH_EVERY_MINUTES:-60}
    case "$step" in ''|*[!0-9]*) step=60 ;; esac
    [ "$step" -ge 1 ] && [ "$step" -le 720 ] || step=60
    am=${FETCH_ALIGN_MINUTE:-10}
    case "$am" in ''|*[!0-9]*) am=10 ;; esac
    [ "$am" -le 59 ] || am=10

    now_min=$(now_minutes) || return 1

    # 往后一分钟一分钟找第一个"合法档位"。
    # 为什么不直接算：busybox ash 的取模对负数是往零截断的（凌晨 00:05、am=10
    # 时 (now-am) 是负数），算出来的下一档会跳过今天那一格。多跑一千多次整数
    # 运算换来"一眼就看得出对"，值。
    i=1
    while [ "$i" -le 1500 ]; do
        m=$(( now_min + i ))
        if [ $(( (m - am) % step )) -eq 0 ] && ! in_quiet_hour $(( (m / 60) % 24 )); then
            echo "$i"
            return 0
        fi
        i=$(( i + 1 ))
    done
    return 1
}

# ---------------------------------------------------------------------------
# 定好下一次拉整图的时刻。优先级是刻意的：
#   低电量 > 云端下发的时刻表 > 本机对齐时刻表
# 低电量排最前 —— 服务器不知道这台设备的电量，那种时候省电比"几点换新图"重要。
# 中间那条 X-Next-Image 分支留着不删：哪天自己再跑一个 HTTP 服务当备用出口，
# 它不用改这边一行就立刻生效；而 GitHub 不发这个头，函数直接返回空走到下一档。
plan_next_image() {
    pnow=$(now_epoch)
    if is_low_battery; then
        echo $((pnow + LOW_BATTERY_INTERVAL))
        return 0
    fi
    sched=$(next_image_from_headers)
    if [ -n "$sched" ]; then
        log "按云端时刻表，约 $(( (sched - pnow) / 60 )) 分钟后再来取"
        echo "$sched"
        return 0
    fi
    mins=$(minutes_until_next_fetch)
    if [ -n "$mins" ]; then
        log "下次取图：${mins} 分钟后（每 ${FETCH_EVERY_MINUTES:-60} 分钟、相位第 ${FETCH_ALIGN_MINUTE:-10} 分；安静期 ${QUIET_START:-无}-${QUIET_END:-无} 点）"
        echo $((pnow + mins * 60))
        return 0
    fi
    # 算不出来（配置写坏了）也要有个节奏，而且必须喊出来 —— 静默不刷新是本项目
    # 栽过最多次的那类故障。
    log "!! 算不出取图时刻，先按 3600s 兜底（查 FETCH_EVERY_MINUTES / QUIET_START / QUIET_END）"
    echo $((pnow + 3600))
}

# ---------------------------------------------------------------------------
# 拉一张整图并全刷。返回 0 = 成功，1 = 这一轮没拿到图。
# 整图右上角那块（电量，以前还有时钟）在图里是**留白**的 —— 补它这一下归主循环干：
# 每轮醒来都会跑 stamp_clock / stamp_battery，刷过整图的那一轮带 force。
# 这里不要自己贴，否则同一小块会被连贴两遍。
refresh() {
    [ "$WIFI_SLEEP" = "1" ] && wifi_on

    if ! wait_for_wifi; then
        log "WiFi 连接超时，本轮跳过：$(wifi_state)"
        consecutive_failures=$((consecutive_failures + 1))
        # WIFI_SLEEP=0 时我们不碰射频，于是"关联掉了"这件事没人负责捡起来 ——
        # 而 Kindle 只在射频重新打开的那一刻才会自动重连存过的网络。
        # 所以这里关一下再开：本轮不等它连好（等也来不及），下一轮再试。
        wifi_off; sleep 3; wifi_on
        log "已经把射频关开过一次，下一轮重连"
        [ "$WIFI_SLEEP" = "1" ] && wifi_off
        return 1
    fi

    # 按顺序试 config.sh 里那一串出口，第一个拿到合法 PNG 的就用。
    # 三家给的都是同一个仓库里的同一个文件，所以退到后面那条不会换版式 ——
    # 以前"备用地址"最坑的就是这个。
    # 每条失败都写清返回码：这台机器上"连不上"和"证书不认"和"超时"是完全不同的病，
    # 混成一行"下载失败"的话，只能靠反复插拔去猜。
    last_rc=0
    got=0
    tried=0
    for url in $DASHBOARD_URLS; do
        tried=$((tried + 1))
        if download_image "$url" "$TMP"; then
            if image_is_sane "$TMP"; then
                got=1
                host=$(printf '%s' "$url" | sed 's#^https\?://##; s#/.*##')
                log "取图成功：走 $host"
                break
            fi
            # 200 但内容不是图：多半是 404 页面、或者地址写错跳到了登录页
            log "  拿回来的不是合法 PNG（地址错了、或仓库还没这张图）：$url"
        else
            log "  这个出口不行（rc=$last_rc $(rc_meaning "$last_rc")）：$url"
        fi
    done
    [ "$got" = "0" ] && [ "$tried" = "0" ] && log "!! DASHBOARD_URLS 是空的，没地址可试"

    if [ "$got" = "1" ]; then
        sync_clock
        new_hash=$(file_hash "$TMP")
        # 取回来和屏上那张一模一样时，可以省掉那一下白闪 —— 局域网快道把取图
        # 提到每 10 分钟一次之后，这一条是必需的，不然屏幕每小时白闪 6 次。
        #
        # ⚠️ 判据必须是"屏幕现在是什么"（screen_hash，只在真的画出去之后才记），
        #   不能是"/tmp 里那份缓存"。以前拿缓存文件比过，结果把屏幕永久弄白了：
        #   framework 一停屏幕被清成白的，而缓存文件还在，于是"和上次一样"=
        #   什么都不画 = 一直白着。
        #
        # SKIP_STREAK_MAX 是兜底：万一有我们没记到的清屏事件（人为重启了 framework、
        # 别的程序盖了屏），最多连续跳过这么多次就强制画一次，白屏不会过夜。
        if [ -n "$screen_hash" ] && [ "$new_hash" = "$screen_hash" ] \
           && [ "$skip_streak" -lt "${SKIP_STREAK_MAX:-6}" ]; then
            skip_streak=$((skip_streak + 1))
            # 只在"第一次发现没变"时说一声，之后静默 —— 否则 10 分钟一档会把
            # 500 行的日志全刷成同一句话，真正要查的东西反而被挤掉。
            [ "$skip_streak" -eq 1 ] && log "取回来和屏上那张一样，跳过重绘（之后连续相同就安静）"
            rm -f "$TMP" 2>/dev/null
            consecutive_failures=0
            log "这段的休眠：真睡 ${sleep_ok} 次共 ${sleep_ok_seconds}s · 提前醒 ${sleep_short} 次 · 弹回 ${sleep_bounce} 次"
            sleep_ok=0; sleep_ok_seconds=0; sleep_short=0; sleep_bounce=0
            [ "$WIFI_SLEEP" = "1" ] && wifi_off
            return 0
        fi
        mv "$TMP" "$IMG"
        # 每拿到一张好图就另存一份进设备存储。/tmp 重启就空了，而"点启动立刻贴一张"
        # 靠的就是这个文件 —— 不同步的话重启后贴的可能是上个月的旧图，
        # 屏幕上是一个月前的日期，比空白更误导人。
        cp "$IMG" "$FALLBACK_IMAGE" 2>/dev/null
        consecutive_failures=0
        notice_shown=0
        # 休眠质量小结。以前"到底有没有真睡进去"只有进程启动后头三次有证据，
        # 之后一整天掉电快慢在日志里全是空白 —— 现在每次取图报一行，够查了。
        log "这段的休眠：真睡 ${sleep_ok} 次共 ${sleep_ok_seconds}s · 提前醒 ${sleep_short} 次 · 弹回 ${sleep_bounce} 次"
        sleep_ok=0; sleep_ok_seconds=0; sleep_short=0; sleep_bounce=0
        if is_low_battery; then
            show_low_battery_notice
            notice_shown=1
        else
            show_image
            show_status_line
        fi
        [ "$WIFI_SLEEP" = "1" ] && wifi_off
        return 0
    fi

    consecutive_failures=$((consecutive_failures + 1))
    log "所有地址都失败（第 $consecutive_failures 次）"
    # 连续失败 3 次以上才启用备用图，避免偶发抖动就切走画面
    if [ "$consecutive_failures" -ge 3 ] && [ -f "$FALLBACK_IMAGE" ]; then
        cp "$FALLBACK_IMAGE" "$IMG" 2>/dev/null && eips -g "$IMG" >/dev/null 2>&1
        # 备用图盖上去了，屏上内容不再是刚才那张 —— 作废。
        screen_hash=""
    fi
    rm -f "$TMP" 2>/dev/null
    [ "$WIFI_SLEEP" = "1" ] && wifi_off
    return 1
}

# ---------------------------------------------------------------------------
# 睡到下一个心跳点。
#
# CLOCK_MODE=local 时按 CLOCK_INTERVAL 对齐到整分钟 —— 贴钟必须踩着分钟边界，
# 否则分钟数会越走越偏。
#
# 不贴钟的时候（CLOCK_MODE=off）**心跳必须改成跟着整图的节奏走**。
# 以前这里无条件读 CLOCK_INTERVAL，于是"关掉时钟"只是不贴钟、设备照样每 60 秒
# 醒一次，一度电都省不下来 —— 而省电恰恰是关掉时钟唯一的理由。
sleep_to_next_tick() {
    if [ "$CLOCK_MODE" != "local" ]; then
        left=$(( next_image_at - $(now_epoch) ))
        # 下限 60 秒：next_image_at 还没算出来（首轮）或已经过期时，
        # 别退化成不停空转；上限交给 plan_next_image 那边管。
        [ "$left" -lt 60 ] && left=60
        # 上限 STOP_CHECK_MAX_SLEEP：循环只在醒来时才检查 .stop 文件，
        # 不封顶的话关掉时钟之后心跳是"几小时一次"，用户建完 .stop 要好几个
        # 小时才生效 —— 那等于把唯一的优雅出口废掉了。
        # 10 分钟是个折中：一天醒 144 次，仍比贴钟时的 1440 次少一个数量级。
        [ "$left" -gt "${STOP_CHECK_MAX_SLEEP:-600}" ] && left="${STOP_CHECK_MAX_SLEEP:-600}"
        secure_sleep "$left"
        return 0
    fi
    tick=$(int_of "$CLOCK_INTERVAL")
    [ "$tick" -ge 10 ] || tick=60
    left=$((tick - $(now_epoch) % tick))
    [ "$left" -lt 1 ] && left=$tick
    secure_sleep "$left"
}

# 睡一段时间。USE_RTC_SLEEP=1 时设备真正断电休眠，靠 RTC 叫醒。
secure_sleep() {
    duration="$1"
    if [ "$USE_RTC_SLEEP" = "1" ] && [ -n "$RTC" ] && [ -w "$RTC" ]; then
        # 先写 0 清掉旧闹钟，否则唤醒时刻会沿用上一次的值
        echo 0 >"$RTC" 2>/dev/null
        case "$RTC_KIND" in
            wakealarm)     echo "+$duration" >"$RTC" 2>/dev/null ;;   # 新内核：相对秒数要带 +
            wakeup_enable) echo "$duration"  >"$RTC" 2>/dev/null ;;   # 老内核：裸秒数
        esac
        t0=$(now_epoch)
        # 官方 ABI：suspend 前先把 wakeup_count 写回去，等于向内核"认领"当前这批
        # 唤醒事件。不写的话，事件还没排空就 `echo mem`，内核会立刻 abort ——
        # 实测症状就是"人为唤醒之后 echo mem 每秒弹回"（#2 #3 都是 1s），
        # 而第一次能睡 121 秒。dmesg 里那句 `otg udc vbus rising wakeup`
        # 说明 USB VBUS 本身就是唤醒源，插着线时更容易撞上。
        wc=$(cat /sys/power/wakeup_count 2>/dev/null)
        [ -n "$wc" ] && echo "$wc" >/sys/power/wakeup_count 2>/dev/null
        echo mem >/sys/power/state
        slept=$(( $(now_epoch) - t0 ))
        # 第一次必须留下证据：计划睡多久 vs 实际睡了多久。
        # slept ≈ duration = 真睡进去了；slept ≈ 0 = `echo mem` 压根没生效
        # （多半是 RTC 没被登记成唤醒源），那跟没改之前一样整夜醒着。
        # 为什么睡不长 —— 别猜，连测三次再下结论。
        # 第一次紧跟在整图刷新之后，EPD 还在忙，`echo mem` 本来就容易 1~2 秒弹回，
        # 拿它当证据会误判（就误判过一次）。所以头三次都记，第四次起只在
        # "真睡够八成时间"时再记一行，不会把日志刷爆。
        if [ "${sleep_logged:-0}" -lt 3 ]; then
            sleep_logged=$(( ${sleep_logged:-0} + 1 ))
            log "休眠实测 #$sleep_logged：计划 ${duration}s，实际睡了 ${slept}s（$RTC_KIND @ $RTC）· 下次出图 $next_image_at"
        elif [ -z "$sleep_proof_logged" ] && [ "$slept" -ge $((duration * 8 / 10)) ]; then
            sleep_proof_logged=1
            log "休眠确认：计划 ${duration}s，实际睡了 ${slept}s —— 真睡进去了"
        fi
        # 比预定时间早一大截就回来了 = 叫醒我们的不是闹钟，是有人碰屏幕或按了电源。
        # 原生界面在恢复过程中会把我们画的整图擦掉，而循环每分钟只贴那一小块钟，
        # 于是屏幕只剩一个时间、要等下一次拉图（四五小时后）才自愈 —— 必须当场重贴。
        #
        # slept 接近 0 表示压根没睡进去（设备忙），那不算人为唤醒：否则每次心跳都
        # 重贴整图，屏幕会被刷坏。
        if [ "$slept" -ge 5 ] && [ "$slept" -lt "$((duration - 10))" ]; then
            woken_early=1
        fi
        # 连着几次 `echo mem` 一秒就弹回 = 有东西压着不让睡。这时候继续重试就是
        # **纯空转**：CPU 满载、一度电也省不下来，比老实 sleep 还糟 ——
        # 而这正是现在实测在发生的事（计划 600s，实际睡 1s，然后立刻再试）。
        # 所以连蹦三次就改用普通 sleep 熬时间，并且只喊一次。
        #
        # 但**一口气熬完剩下的 600 秒是错的**：挡住休眠的东西（最常见就是插着 USB，
        # VBUS 本身是唤醒源）往往十几秒就走了，而这段代码会全程醒着熬完，
        # 一度电都不省 —— 实测有一次就是这样连续熬了十几个小时。
        # 所以这里最多熬 60 秒就交回主循环，让下一轮再试一次 `echo mem`。
        if [ "$slept" -lt 5 ]; then
            suspend_bounces=$((suspend_bounces + 1))
            sleep_bounce=$((sleep_bounce + 1))
        else
            suspend_bounces=0
            if [ "$slept" -ge $((duration * 8 / 10)) ]; then
                sleep_ok=$((sleep_ok + 1))
                sleep_ok_seconds=$((sleep_ok_seconds + slept))
                # 睡成过一次就把"只喊一次"的闸门关掉：之后要是又开始弹，日志里
                # 还能再看见一次 —— 以前这个闸门开了就永远不再报，
                # 于是"下午还好好的、晚上开始睡不着"这种事完全不留痕迹。
                bounce_warned=0
            else
                sleep_short=$((sleep_short + 1))
            fi
        fi
        if [ "$suspend_bounces" -ge 3 ]; then
            if [ "$bounce_warned" = "0" ]; then
                bounce_warned=1
                log "!! 连续 $suspend_bounces 次 suspend 立刻弹回 → 有东西拦着（插着 USB？），改用普通 sleep 熬 60 秒后再试一次"
            fi
            elapsed=$slept
            # 只熬 60 秒就交回主循环 —— 下一轮会重新算时长并**再试一次 `echo mem`**。
            # 一口气熬完 600 秒是错的：挡住休眠的东西往往十几秒就走了。
            cap=60
            [ "$duration" -lt "$cap" ] && cap=$duration
            while [ "$elapsed" -lt "$cap" ]; do
                [ -f "$STOP_FLAG" ] && return 0
                sleep 10
                elapsed=$((elapsed + 10))
            done
        fi
    else
        # 退化为普通 sleep：设备不会真正休眠，耗电大，但一定能醒
        elapsed=0
        while [ "$elapsed" -lt "$duration" ]; do
            [ -f "$STOP_FLAG" ] && return 0
            sleep 10
            elapsed=$((elapsed + 10))
        done
    fi
}

# ---------------------------------------------------------------------------
# 退出原因必须写进日志。以前三条退出路径（有人放了 .stop / 运行标记没了 /
# 被信号杀掉）打出来的是同一行「退出：恢复系统状态」，于是"程序为什么自己退了"
# 永远查不出来 —— 用户看到的只是"过一会儿屏幕被主页面盖掉了"。
cleanup() {
    log "退出：恢复系统状态（原因：${1:-未记录}）"
    rm -f "$RUN_FLAG" "$PID_FILE" "$STOP_FLAG" "$TMP" "$HDR"
    eips -c >/dev/null 2>&1
    # WiFi 一定要还回去。WIFI_SLEEP=1 时刷完图会把射频关掉，而这条退出路径
    # 以前**不打开它** —— 后果是"退出信息屏之后 Kindle 搜不到任何 WiFi"，
    # 只能重启。2026-09-22 加 WIFI_SLEEP=1 时漏了这一条，别再漏第二次。
    # 放在最前面：下面任何一步失败都不该把用户留在"射频关着"的状态里。
    wifi_on
    lipc-set-prop com.lab126.powerd preventScreenSaver 0 >/dev/null 2>&1
    if [ "$STOP_FRAMEWORK" = "1" ]; then
        # 反过来还回去。framework 起来后一般会把 pillow / statusbar 自己带起来，
        # 所以先只 start framework，等两秒，再补那些仍然没跑的任务 ——
        # 免得对着一个已经 running 的任务重复 start，多一个说不清的变量。
        initctl start framework >/dev/null 2>&1
        sleep 2
        for j in $UI_JOBS; do
            initctl status "$j" 2>/dev/null | grep -q "start/running" \
                || initctl start "$j" >/dev/null 2>&1
        done
        echo ondemand >/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null
        log "已重新启动原生界面"
    fi
    exit 0
}

init() {
    log "=== 启动 AI 信息屏 ==="
    log "出口地址（按顺序试）："
    for u in $DASHBOARD_URLS; do log "   $u"; done
    log "整图：每 ${FETCH_EVERY_MINUTES:-60} 分钟取一次、相位第 ${FETCH_ALIGN_MINUTE:-10} 分；安静期 ${QUIET_START:-无}-${QUIET_END:-无} 点（这段时间不联网）"
    # 这一行是拿来当场核表的：屏幕上的时间就是这个 date 的输出，
    # 和你对着手表看到的时刻不一样 → 就是 config.sh 的 TIMEZONE 错了，别改别的。
    log "设备时间 $(date '+%Y-%m-%d %H:%M:%S')（TZ=${TZ:-未设置}，epoch $(date +%s)）"
    # 这一行是掉电排查的第一现场。secure_sleep() 在 RTC 节点找不到/不可写时会
    # 悄悄退化成普通 sleep 循环 —— 设备全程醒着，掉电极快，而日志上完全看不出来。
    # 以前就是这样：一晚掉 60%，分不清是"醒得太勤"还是"根本没睡"。
    if [ "$USE_RTC_SLEEP" != "1" ]; then
        log "休眠：USE_RTC_SLEEP=0，设备不会真睡（很费电）"
    elif [ -z "$RTC" ]; then
        log "休眠：!! 两代唤醒节点都没有（新 wakealarm / 老 wakeup_enable 都试过）"
        log "      → 会退化成普通 sleep，设备全程醒着。这就是掉电元凶，不是时钟"
    elif [ ! -w "$RTC" ]; then
        log "休眠：!! 唤醒节点存在但不可写（$RTC）→ 同上，实际不会真睡"
    else
        log "休眠：用 $RTC_KIND（$RTC），echo mem 真休眠"
    fi
    # 系统探针，写进文件，插上 USB 就能在电脑上看。每次启动都重写（文件很小），
    # 因为固件升级后答案可能变。
    #
    # 为什么要它：`/etc/init.d/framework stop` 在这台设备上返回 **127（命令不存在）**，
    # 于是界面从来没被停过 —— "碰一下就回主界面""系统时钟盖上来"全是这个带来的，
    # 而那行「framework 已停止」是无条件打印的，谁也没发现它在撒谎。
    # "该用哪条命令停界面""该往哪个节点写闹钟"只有设备自己知道，
    # 凭印象换个路径试，多半还是静默失败。所以先问，再改。
    {
        echo "== 生成于 $(date '+%Y-%m-%d %H:%M:%S') =="
        echo "身份  = $(id)"
        echo "固件  = $(cat /etc/version 2>/dev/null | tr '\n' ' ')"
        echo "型号  = $(cat /proc/device-tree/model 2>/dev/null)"
        echo "-- /etc/init.d 到底存不存在、里面有什么 --"
        if [ -d /etc/init.d ]; then ls -l /etc/init.d/ 2>&1; else echo "（没有 /etc/init.d 这个目录）"; fi
        echo "-- initctl 管着哪些任务（全量，别截断：要找的就是那个界面任务）--"
        echo "initctl = $(command -v initctl || echo 不在 PATH)"
        initctl list 2>&1
        echo "-- 正在跑什么（全量 ps：界面进程叫什么还不知道，不能靠 grep 猜名字）--"
        ps 2>&1
        echo "-- 网络（要它的 IP：能 SSH 上去就不用再反复插拔线了）--"
        echo "wlan0 IP   = $(ifconfig wlan0 2>/dev/null | grep -o 'inet addr:[0-9.]*' | cut -d: -f2)"
        echo "WiFi 状态  = $(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)"
        echo "sshd 状态  = $(initctl status sshd 2>&1 | tr '\n' ' ')"
        echo "-- RTC 与唤醒节点 --"
        ls -d /sys/devices/platform/*rtc* 2>/dev/null || echo "（platform 下没有 rtc）"
        ls /sys/class/rtc/ 2>/dev/null || echo "（没有 /sys/class/rtc）"
        for r in /sys/class/rtc/*; do
            [ -d "$r" ] || continue
            wa=$([ -w "$r/wakealarm" ] && echo 可写 || echo 不可写)
            echo "$r  name=$(cat "$r/name" 2>/dev/null)  time=$(cat "$r/time" 2>/dev/null)  wakealarm=$wa"
        done
        ls /sys/devices/platform/*/power/wakeup 2>/dev/null | head -n 20
        ls /sys/devices/platform/*rtc*/wakeup_enable 2>/dev/null \
            || echo "（没有 wakeup_enable —— 老内核那套写法在这台机器上不适用）"
        echo "-- 休眠接口 --"
        ls /sys/power 2>/dev/null
        echo "state        = $(cat /sys/power/state 2>/dev/null)"
        echo "wakeup_count = $(cat /sys/power/wakeup_count 2>/dev/null)"
        # 为什么 `echo mem` 一秒就被顶回来 —— 别猜，让内核自己说。
        # suspend 失败时 PM 层会写下是谁拦的（某个驱动 wakelock、某个唤醒源、
        # 或者 powerd 自己压着 deferSuspend）。
        echo "-- powerd 有没有压着不休眠 --"
        echo "deferSuspend = $(lipc-get-prop com.lab126.powerd deferSuspend 2>/dev/null)"
        echo "-- 内核里 suspend / resume / wakeup 相关最后 25 行 --"
        dmesg 2>/dev/null | grep -iE "suspend|resume|wakeup|wake.*alarm|PM:|earlysuspend" | tail -n 25
        echo "-- powerd 属性 --"
        echo "sleepDuration      = $(lipc-get-prop com.lab126.powerd sleepDuration 2>/dev/null)"
        echo "powerMgrState      = $(lipc-get-prop com.lab126.powerd powerMgrState 2>/dev/null)"
        echo "preventScreenSaver = $(lipc-get-prop com.lab126.powerd preventScreenSaver 2>/dev/null)"
    } > "$DIR/system_probe.txt" 2>/dev/null
    if [ "$CLOCK_MODE" = "local" ]; then
        if [ -f "$CLOCK_CONF" ]; then
            log "时钟：本机贴图，每 ${CLOCK_INTERVAL}s 一次，坐标 ($CLOCK_X,$CLOCK_Y)"
        else
            log "时钟：本机贴图**未就绪** —— 缺 $CLOCK_CONF，右上角会留空白"
        fi
    else
        log "时钟：CLOCK_MODE=$CLOCK_MODE（不贴图，时间由出图时画死）"
    fi
    rm -f "$STOP_FLAG" "$TMP" "$HDR"
    # PID 由主循环自己写，不靠 start.sh 的 $!：setsid 需要时会 fork 一次，
    # $! 拿到的是 setsid 的进程号，那个 exec 完就没了，stop 会杀错对象。
    echo $$ >"$PID_FILE" 2>/dev/null

    if [ "$STOP_FRAMEWORK" = "1" ]; then
        # 停掉 Kindle 自己的界面。
        #
        # 以前这里写的是 `/etc/init.d/framework stop`，而实测这台 PW3 上
        # **/etc/init.d 是个空目录**（system_probe.txt 里 `total 0`），那条命令
        # 一直返回 **127 = 命令不存在** —— 也就是说界面**从来没被停过**。
        # "碰一下就回主界面""系统时钟盖在画面上"两个症状根因就在这。
        #
        # 这台机器的界面归 upstart 管，任务名是从 `initctl list` 里查出来的，
        # 不是猜的：framework（主框架）、pillow（书架/桌面，就是"主界面"本身）、
        # statusbar（顶部状态栏 —— **它是独立任务**，只停 framework 的话那个
        # 系统时钟照样刷）、webreader（浏览器）。
        n_before=$(ps 2>/dev/null | wc -l)
        for j in $UI_JOBS; do initctl stop "$j" >/dev/null 2>&1; done
        sleep 2
        n_after=$(ps 2>/dev/null | wc -l)
        still=""
        for j in $UI_JOBS; do
            initctl status "$j" 2>/dev/null | grep -q "start/running" && still="$still$j"
        done
        # 三个信息缺一不可：进程数掉了多少（真停了的旁证）、哪些任务还在跑
        # （被 respawn 拉起来的话必须换策略）、我们自己还活着没
        # （启动链是 framework → scriptlet → 我们，杀 framework 有连坐风险，
        #   靠 start.sh 的 setsid 脱离进程组；这一行第一次成为**有效**的存活证据，
        #   以前它是在一条没执行的命令后面打印的，什么都没证明）。
        log "界面任务已停（$UI_JOBS）· 本进程存活 PID $$ · 进程数 $n_before → $n_after · 仍在跑：${still:-无}"
    fi
    echo powersave >/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null
    # 阻止系统屏保把我们的画面盖掉
    lipc-set-prop com.lab126.powerd preventScreenSaver 1 >/dev/null 2>&1

    # 点下「启动信息屏」要**立刻看到画面**，不能等网络。
    #
    # 为什么以前会"卡在 PID / setsid 那页"：scriptlet 是把 stdout 当成一个文字页
    # 显示在屏幕上的，而第一次联网刷新可能要十几秒、失败时更久（服务端没起、WiFi
    # 没连上）。刷新没成功就没有任何东西可画，于是那页文字一直留着，
    # 看起来像"点了没反应"。
    #
    # 所以这里先把**手上已有的一张**贴上去 —— /tmp 的缓存图优先，
    # 没有就用设备里的 fallback.png —— 然后主循环再照常联网更新。
    # 这一步失败也无所谓：主循环会重试，而且下面那行日志会说明当时到底有没有图。
    if [ -f "$IMG" ]; then
        eips -f -g "$IMG" >/dev/null 2>&1
        log "启动即贴屏：用上次那张缓存图（联网更新随后进行）"
    elif [ -f "$FALLBACK_IMAGE" ]; then
        cp "$FALLBACK_IMAGE" "$IMG" 2>/dev/null && eips -f -g "$IMG" >/dev/null 2>&1
        log "启动即贴屏：无缓存，用 fallback.png 顶上"
    else
        log "启动即贴屏：设备上没有可贴的图，只能等首轮联网结果"
    fi
}

main_loop() {
    next_image_at=0
    while [ -f "$RUN_FLAG" ]; do
        if [ -f "$STOP_FLAG" ]; then
            cleanup "有人放了 .stop（点了「停止信息屏」，或手动建的文件）"
        fi

        redrew=0
        now=$(now_epoch)
        # 第一次进来（还没图）或者到点，就拉一张整图
        if [ ! -f "$IMG" ] || [ "$now" -ge "$next_image_at" ]; then
            if refresh; then
                # 下一次几点来才有新图：先看服务器有没有塞 X-Next-Image（只有我们自己
                # 跑的 HTTP 服务会塞），没有就按本机的 FETCH_* / QUIET_* 自己算
                next_image_at=$(plan_next_image)
                case "$next_image_at" in
                    ''|*[!0-9]*) next_image_at=0 ;;
                esac
                if [ "$next_image_at" -le "$now" ]; then
                    # **算不出未来时刻就兜底，而且必须出声。**
                    # 留着空串或过去时刻，下面那句 `[ now -ge next_image_at ]`
                    # 要么报错返回假（永远不刷新）、要么每轮都刷新（刷屏），
                    # 两种都是静默故障 —— 实际栽过一次：79 分钟屏幕没动过，
                    # 日志里只有"已全屏刷新（第 0 次）"孤零零一行。
                    next_image_at=$(( $(now_epoch) + 600 ))
                    log "!! 算不出下次出图时刻，先按 600s 兜底（查 plan_next_image）"
                fi
                redrew=1
            else
                # 失败别等一整个周期，10 分钟后再试一次
                next_image_at=$(( $(now_epoch) + 600 ))
            fi
        fi

        # 被人为唤醒过、这一轮又没有重新拉图 —— 屏幕很可能已经被原生界面在恢复
        # 过程中擦成空白了，而循环每分钟只贴那一小块钟，于是整屏就只剩一个时间，
        # 要等到下一次拉图（四五小时后）才恢复。必须当场重贴。
        #
        # 用不带 -f 的局部刷新：不闪屏。代价只是累积一点残影，而下一次整图是全刷
        # （FULL_REFRESH_EVERY=1），会顺手把它清掉。
        #
        # 这里原来还挂着"双击屏幕退出"，已撤掉。碰屏幕是使用这块屏时最常见的动作，
        # 拿它当退出键等于随机退出 —— 用户反馈的"只有重启后第一次能正常打开"
        # 就是这么来的。退出只走「停止信息屏」、.stop 文件、长按电源三条路。
        # 人为唤醒：数次数当出口，并顺手把被原生界面擦掉的画面贴回去。
        #
        # 为什么靠"提前醒"而不是读触摸屏拿坐标：框架停掉之后没人告诉我们点了
        # 哪儿，而且内核的唤醒源清单里压根没有触摸屏（见 system_probe.txt，
        # 只有 ehci/usb/rtc/hall）。所以唯一可靠的信号就是"这次不是闹钟叫醒的"。
        # 代价是**分不清位置** —— 屏幕上任何一下、甚至按电源键都算一下。
        # 图左上角那枚「点两下退出」因此只是提示，不是热区。
        #
        # 次数由 config.sh 的 EXIT_TAPS 决定（2026-09-26 起是 2；历史上 2 因为
        # "一碰就退"被提到 3，见提交 2357393 和 config.sh 第 1 条退出路径）。
        # ⚠️ 因为信号是"唤醒"，快速双击只算一下：第一下叫醒它之后它本来就醒着，
        #   第二下不产生新唤醒。有效的动作是"点一下 → 等它睡回去 → 再点一下"。
        #
        # 每一下都写日志：现在还不知道触摸到底能不能叫醒这台机器，
        # 这些行就是证据 —— 一次都没有 = 触摸不唤醒，得改用电源键那一套。
        if [ "$woken_early" = "1" ]; then
            # 带默认值取：万一设备上还是旧版 config.sh、没有这两个键，
            # `[ x -ge "" ]` 会报错返回假 —— 退出功能就**静默失效**了。
            need=${EXIT_TAPS:-3}
            gap_max=${EXIT_TAP_GAP:-8}
            tap_at=$(now_epoch)
            gap=$((tap_at - last_tap_at))
            if [ "$taps" -gt 0 ] && [ "$gap" -le "$gap_max" ]; then
                taps=$((taps + 1))
            else
                taps=1
            fi
            last_tap_at=$tap_at
            # 间隔必须照实写。以前这里把 gap 归零再打印，于是"第 1/2 下（距上次 0s）"
            # 连着出现三行 —— 看着像计数器坏了，实际是窗口太短；而真正要看的
            # "两下之间隔了多少秒"被那行假 0 藏掉了（2026-09-26 排查退出无效时踩的）。
            # 第一下没有"上次"，会算出一个巨大的 epoch 差值，所以单独说"首次"。
            if [ "$gap" -gt "$gap_max" ]; then gap_txt="首次/超窗"; else gap_txt="${gap}s"; fi
            log "人为唤醒：第 $taps/$need 下（距上次 $gap_txt，窗口 ${gap_max}s）"
            # need=0 是"关掉连点退出"（2026-09-26）。必须挡在前面：
            # 少了这个 -ge 1，`taps(=1) -ge 0` 恒真，第一下就直接退出了。
            if [ "$need" -ge 1 ] && [ "$taps" -ge "$need" ]; then
                taps=0
                log "连够 $need 下 → 退出并回到桌面"
                cleanup "连点 ${need} 下（用户主动退出）"
            fi
            if [ "$redrew" = "0" ] && [ -f "$IMG" ] && [ "$notice_shown" != "1" ]; then
                eips -g "$IMG" >/dev/null 2>&1
                redrew=1            # 告诉下面：整屏刚重画过，右上角那块又是空的了
                log "已重贴整图"
            fi
        fi
        woken_early=0

        # 整图刚刷过、或者只是醒了一次 —— 都要重新贴这两小块：
        # 前者因为整图里右上角是留白的，后者因为画面可能被原生界面擦过。
        # 电量尤其值得每次醒来看一眼：走 GitHub 之后整图一天只有四次，
        # 不补这一步，屏幕上的电量角标就跟着一天只动四次。
        # 醒的次数由 STOP_CHECK_MAX_SLEEP 封顶（默认 10 分钟），而且这两下都是
        # du 局部刷新 —— 不额外唤醒，只是顺手，档位没变时 stamp_battery 自己跳过。
        # 低电量提示占着整屏时不贴，免得把它盖掉。
        if [ "$notice_shown" != "1" ]; then
            stamp_clock
            if [ "$redrew" = "1" ]; then stamp_battery force; else stamp_battery; fi
        fi

        sleep_to_next_tick
    done
    cleanup "运行标记 .running 不在了（被外部清掉，或磁盘上文件消失）"
}

trap 'cleanup "收到 TERM/INT 信号（插 USB 进大容量模式、或系统回收进程）"' TERM INT
init
main_loop
