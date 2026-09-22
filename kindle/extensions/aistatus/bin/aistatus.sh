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
sync_disabled=0         # 校时失败一次就别再试，免得反复折腾
notice_shown=0          # 低电量提示占着整屏时不贴时钟
woken_early=0           # 本轮是被人为唤醒的（不是闹钟），要重贴整图

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
# 贴电量。和贴时钟同一条路：整图右上角是留白的，刷完整图补这一小块。
# 缺精灵图 / 读不到电量都只记一次日志然后跳过 —— 右上角留白是无害的，
# 不能因为电量把整轮刷新搞失败。
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
    sprite="$BATTERY_DIR/$(printf '%03d' "$lvl").png"
    if [ ! -f "$sprite" ]; then
        if [ "$battery_warned" = "0" ]; then
            battery_warned=1
            log "!! 电量精灵图缺文件：$sprite —— 大致是没拷全，重新整目录拷一次"
        fi
        return 1
    fi

    eips -g "$sprite" -w "$BATTERY_WAVE" -x "$BATTERY_X" -y "$BATTERY_Y" >/dev/null 2>&1
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
            stamp_battery
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
        echo mem >/sys/power/state
        slept=$(( $(now_epoch) - t0 ))
        # 第一次必须留下证据：计划睡多久 vs 实际睡了多久。
        # slept ≈ duration = 真睡进去了；slept ≈ 0 = `echo mem` 压根没生效
        # （多半是 RTC 没被登记成唤醒源），那跟没改之前一样整夜醒着。
        if [ -z "$first_sleep_logged" ]; then
            first_sleep_logged=1
            # 把 next_image_at 和睡前时刻一起打出来：实测出现过"计划 60s"，
            # 而按配置应该封顶在 600s。光看 duration 分不清是算错了还是被上下限
            # 夹的，这两个值一放进来就能立刻看出来是哪一头。
            log "首次休眠实测：计划 ${duration}s，实际睡了 ${slept}s（$RTC_KIND @ $RTC）· 睡前 $t0 · 下次出图 $next_image_at"
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
        #
        # 这里原来还挂着"双击屏幕退出"，已撤掉。碰屏幕是使用这块屏时最常见的动作，
        # 拿它当退出键等于随机退出 —— 用户反馈的"只有重启后第一次能正常打开"
        # 就是这么来的。退出只走「停止信息屏」、.stop 文件、长按电源三条路。
        if [ "$woken_early" = "1" ] && [ "$redrew" = "0" ] && [ -f "$IMG" ] \
           && [ "$notice_shown" != "1" ]; then
            eips -g "$IMG" >/dev/null 2>&1
            log "检测到被人为唤醒，已重贴整图"
        fi
        woken_early=0

        # 整图刚刷过、或者只是过了一分钟 —— 都要重新贴时钟：
        # 前者因为整图里那块是留白的，后者因为时间变了。
        # 低电量提示占着整屏时不贴，免得把它盖掉。
        [ "$notice_shown" = "1" ] || stamp_clock

        sleep_to_next_tick
    done
    cleanup "运行标记 .running 不在了（被外部清掉，或磁盘上文件消失）"
}

trap 'cleanup "收到 TERM/INT 信号（插 USB 进大容量模式、或系统回收进程）"' TERM INT
init
main_loop
