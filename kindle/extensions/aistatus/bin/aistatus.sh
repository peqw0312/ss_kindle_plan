#!/bin/sh
# =============================================================================
#  AI 信息屏 · Kindle 端主循环
#
#  省电逻辑说明（这是整个项目最关键的一段，别随手改）：
#    心跳是**一分钟一次**。每次醒来只干两件事：看看该不该拉整图，然后把时钟
#    贴上去。贴完立刻 `echo mem` 让设备睡死，等 RTC 闹钟叫下一分钟。
#    设备睡着时 CPU 基本不耗电，墨水屏保持画面本身是零功耗的。
#
#    必须用 `echo mem > /sys/power/state` 强制休眠，而不是靠 lipc 的
#    rtcWakeup —— 后者在多个机型上被实测证明"叫不醒"，这是别人的坑，别再踩。
#
#  为什么心跳要这么密：
#    时钟是 Kindle 用**自己的系统时间**画的（CLOCK_MODE=local），
#    一分钟贴一次才能保证屏幕上显示的就是当前时间。整图里时钟那块是留白的，
#    服务端因此一天只要出四次图，不必为了"时间准"反复拉天气和行情。
#    代价是唤醒次数从每天几十次涨到 1440 次 —— 这是本方案唯一的真实开销，
#    嫌费电就把 CLOCK_INTERVAL 调大。
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
RTC=$(ls /sys/devices/platform/*rtc*/wakeup_enable 2>/dev/null | head -n 1)

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

refresh_count=0
consecutive_failures=0
clock_warned=0          # 精灵图缺失只报一次，不然日志会被刷爆
sync_disabled=0         # 校时失败一次就别再试，免得反复折腾
notice_shown=0          # 低电量提示占着整屏时不贴时钟
woken_early=0           # 本轮是被人为唤醒的（不是闹钟），要重贴整图
last_tap_at=0           # 上一次人为唤醒的时刻，用来判"双击退出"

# ---------------------------------------------------------------------------
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"
    # 日志无限增长会撑爆分区，定期裁掉
    lines=$(wc -l <"$LOG" 2>/dev/null || echo 0)
    if [ "$lines" -gt "$LOG_MAX_LINES" ]; then
        tail -n "$LOG_MAX_LINES" "$LOG" >"$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
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
download_image() {
    url="$1"
    out="$2"
    rm -f "$HDR" 2>/dev/null
    if command -v curl >/dev/null 2>&1; then
        curl -L --silent --show-error --max-time "$HTTP_TIMEOUT" \
             -D "$HDR" --output "$out" "$url" && [ -s "$out" ]
        return $?
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -q -T "$HTTP_TIMEOUT" --server-response -O "$out" "$url" 2>"$HDR" \
             && [ -s "$out" ]
        return $?
    fi
    log "错误：curl 和 wget 都没有，无法下载"
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
# 设备时间校准。时钟由本机画之后，设备时间错了整个功能就没意义了，
# 所以每个响应都带上 X-Epoch，这里拿它对表。
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
# 没有 X-Next-Image 这个头的话，本机只能按 UPDATE_INTERVAL 瞎轮询：一天连 24 次
# WiFi、下 24 张 61KB 的图，而云端其实只换 4 次 —— 其中 20 趟拿回来的是完全相同
# 的字节。云端现在把时刻表算好发下来，下载次数就降到一天四五次。
#
# 局域网那台 serve.py 不发这个头（它只是静态发文件，不参与调度），
# 所以走备用地址时自动退回原来的轮询节奏，不用为它单独配什么。
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
battery_level() {
    gasgauge-info -c 2>/dev/null | tr -cd '0-9'
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

current_interval() {
    hour=$(date +%H)
    hour=$((10#$hour))
    if is_low_battery; then
        echo "$LOW_BATTERY_INTERVAL"
    elif [ "$hour" -lt "$ACTIVE_START" ] || [ "$hour" -ge "$ACTIVE_END" ]; then
        echo "$NIGHT_INTERVAL"
    else
        echo "$UPDATE_INTERVAL"
    fi
}

# ---------------------------------------------------------------------------
# 定好下一次拉整图的时刻。优先级是刻意的：
#   低电量 > 云端时刻表 > 本机固定间隔
# 低电量必须排在最前面 —— 云端不知道这台设备的电量，那种时候省电比"几点换新图"重要。
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
    echo $((pnow + $(current_interval)))
}

# ---------------------------------------------------------------------------
# 拉一张整图并全刷。返回 0 = 成功，1 = 这一轮没拿到图。
# 注意整图里时钟那块是留白的，调用方刷完必须紧跟一次 stamp_clock，
# 否则屏幕右上角会空一块。
refresh() {
    [ "$WIFI_SLEEP" = "1" ] && wifi_on

    if ! wait_for_wifi; then
        log "WiFi 连接超时，本轮跳过"
        consecutive_failures=$((consecutive_failures + 1))
        [ "$WIFI_SLEEP" = "1" ] && wifi_off
        return 1
    fi

    # 主地址失败就退到备用地址。两个都失败才算这一轮失败。
    # 顺序很重要：主地址是云端（电脑关机也能出图），备用是局域网。
    got=0
    for url in "$DASHBOARD_URL" "$DASHBOARD_FALLBACK_URL"; do
        [ -n "$url" ] || continue
        if download_image "$url" "$TMP" && image_is_sane "$TMP"; then
            got=1
            [ "$url" = "$DASHBOARD_URL" ] || log "主地址失败，已改用备用地址：$url"
            break
        fi
        log "下载或校验失败：$url"
    done

    if [ "$got" = "1" ]; then
        sync_clock
        # 别想着"图没变就跳过刷新"省掉那一下白闪 —— 这么干过，然后把屏幕永久弄白了：
        # $IMG 只是 /tmp 里的缓存文件，不代表屏幕上现在是什么。framework 一停屏幕就
        # 被清成白的，而缓存文件还在，于是"和上次一样"= 什么都不画 = 一直白着。
        # 要省白闪得拿屏幕的真实状态当依据，不是拿文件。
        mv "$TMP" "$IMG"
        consecutive_failures=0
        notice_shown=0
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
    fi
    rm -f "$TMP" 2>/dev/null
    [ "$WIFI_SLEEP" = "1" ] && wifi_off
    return 1
}

# ---------------------------------------------------------------------------
# 睡到下一个心跳点。按 epoch 取模对齐，所以时间不会逐次漂移累积 ——
# 每次醒来贴的都是"整分钟"那一张，屏幕上的分钟数不会越走越偏。
sleep_to_next_tick() {
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
        # 如果 RTC 里还挂着旧闹钟就先清掉，否则唤醒时刻会不对
        [ "$(cat "$RTC" 2>/dev/null || echo 0)" -ne 0 ] && echo -n 0 >"$RTC" 2>/dev/null
        echo -n "$duration" >"$RTC" 2>/dev/null
        t0=$(now_epoch)
        echo mem >/sys/power/state
        slept=$(( $(now_epoch) - t0 ))
        # 比预定时间早一大截就回来了 = 叫醒我们的不是闹钟，是有人碰屏幕或按了电源。
        # 原生界面在恢复过程中会把我们画的整图擦掉，而循环每分钟只贴那一小块钟，
        # 于是屏幕只剩一个时间、要等下一次拉图（四五小时后）才自愈 —— 必须当场重贴。
        #
        # slept 接近 0 表示压根没睡进去（设备忙），那不算人为唤醒：否则每次心跳都
        # 重贴整图，屏幕会被刷坏。
        if [ "$slept" -ge 5 ] && [ "$slept" -lt "$((duration - 10))" ]; then
            woken_early=1
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
cleanup() {
    log "退出：恢复系统状态"
    rm -f "$RUN_FLAG" "$PID_FILE" "$STOP_FLAG" "$TMP" "$HDR"
    eips -c >/dev/null 2>&1
    lipc-set-prop com.lab126.powerd preventScreenSaver 0 >/dev/null 2>&1
    if [ "$STOP_FRAMEWORK" = "1" ]; then
        /etc/init.d/framework start >/dev/null 2>&1
        initctl start webreader >/dev/null 2>&1
        echo ondemand >/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null
        log "已重新启动原生界面"
    fi
    exit 0
}

init() {
    log "=== 启动 AI 信息屏 ==="
    log "URL: $DASHBOARD_URL"
    [ -n "$DASHBOARD_FALLBACK_URL" ] && log "备用: $DASHBOARD_FALLBACK_URL"
    log "整图：优先听云端时刻表（X-Next-Image），拿不到才按 白天 ${UPDATE_INTERVAL}s / 夜间 ${NIGHT_INTERVAL}s 轮询"
    # 这一行是拿来当场核表的：屏幕上的时间就是这个 date 的输出，
    # 和你对着手表看到的时刻不一样 → 就是 config.sh 的 TIMEZONE 错了，别改别的。
    log "设备时间 $(date '+%Y-%m-%d %H:%M:%S')（TZ=${TZ:-未设置}，epoch $(date +%s)）"
    # 这一行是掉电排查的第一现场。secure_sleep() 在 RTC 节点找不到/不可写时会
    # 悄悄退化成普通 sleep 循环 —— 设备全程醒着，掉电极快，而日志上完全看不出来。
    # 以前就是这样：一晚掉 60%，分不清是"醒得太勤"还是"根本没睡"。
    if [ "$USE_RTC_SLEEP" != "1" ]; then
        log "休眠：USE_RTC_SLEEP=0，设备不会真睡（很费电）"
    elif [ -z "$RTC" ]; then
        log "休眠：!! 找不到 RTC 唤醒节点（/sys/devices/platform/*rtc*/wakeup_enable）"
        log "      → 会退化成普通 sleep，设备全程醒着。这就是掉电元凶，不是时钟"
    elif [ ! -w "$RTC" ]; then
        log "休眠：!! RTC 节点存在但不可写（$RTC）→ 同上，实际不会真睡"
    else
        log "休眠：RTC 可用（$RTC），echo mem 真休眠"
    fi
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
        # 停掉书架界面 + 浏览器进程，省内存也省电
        /etc/init.d/framework stop >/dev/null 2>&1
        initctl stop webreader >/dev/null 2>&1
        sleep 2
        # 这一行是刻意留的存活证据：启动链是 framework →（KUAL / scriptlet）→ 我们，
        # 杀 framework 有连坐把自己杀掉的风险（见 start.sh 的 setsid 说明）。
        # 日志里有这行 = 我们扛过了那一下；没有 = 就是死在这儿，别往网络方向查。
        log "framework 已停止，本进程存活（PID $$）"
    fi
    echo powersave >/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null
    # 阻止系统屏保把我们的画面盖掉
    lipc-set-prop com.lab126.powerd preventScreenSaver 1 >/dev/null 2>&1
}

main_loop() {
    next_image_at=0
    while [ -f "$RUN_FLAG" ]; do
        if [ -f "$STOP_FLAG" ]; then
            cleanup
        fi

        redrew=0
        now=$(now_epoch)
        # 第一次进来（还没图）或者到点，就拉一张整图
        if [ ! -f "$IMG" ] || [ "$now" -ge "$next_image_at" ]; then
            if refresh; then
                # 下次什么时候再来，问云端（它才知道时刻表），问不到才自己按间隔算
                next_image_at=$(plan_next_image)
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
        if [ "$woken_early" = "1" ]; then
            tap_at=$(now_epoch)
            # 双击退出：原生界面会盖住我们的画面，而 STOP_FRAMEWORK=1 又让书架界面
            # 打不开 —— 设备等于没有出口。这里把"两次人为唤醒挨得足够近"当作双击，
            # 直接 cleanup() 回到桌面。不用去读触摸屏设备，纯靠时间差就够判。
            if [ "${EXIT_TAP_GAP:-0}" -gt 0 ] && [ "$last_tap_at" -gt 0 ] \
               && [ $((tap_at - last_tap_at)) -le "$EXIT_TAP_GAP" ]; then
                log "连续两次触摸（间隔 $((tap_at - last_tap_at))s），退出并回到桌面"
                cleanup
            fi
            last_tap_at=$tap_at
            if [ "$redrew" = "0" ] && [ -f "$IMG" ] && [ "$notice_shown" != "1" ]; then
                eips -g "$IMG" >/dev/null 2>&1
                log "检测到被人为唤醒，已重贴整图"
            fi
        fi
        woken_early=0

        # 整图刚刷过、或者只是过了一分钟 —— 都要重新贴时钟：
        # 前者因为整图里那块是留白的，后者因为时间变了。
        # 低电量提示占着整屏时不贴，免得把它盖掉。
        [ "$notice_shown" = "1" ] || stamp_clock

        sleep_to_next_tick
    done
    cleanup
}

trap 'cleanup' TERM INT
init
main_loop
