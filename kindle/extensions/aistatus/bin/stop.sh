#!/bin/sh
# 停止信息屏并恢复原生界面
DIR=/mnt/us/extensions/aistatus
[ -f "$DIR/config.sh" ] || DIR="$(cd "$(dirname "$0")/.." && pwd)"
# 要恢复哪些界面任务，答案写在 config.sh 的 UI_JOBS 里，别在这儿抄第二份
[ -f "$DIR/config.sh" ] && . "$DIR/config.sh"

# 主循环在每次醒来时会检查 .stop，这个哨兵文件比 kill 更可靠
touch "$DIR/.stop"

pid=$(cat "$DIR/.pid" 2>/dev/null)
if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null
fi
# 兜底：PID 文件丢了就直接按名字找
killall aistatus.sh 2>/dev/null

sleep 2

rm -f "$DIR/.running" "$DIR/.pid" "$DIR/.stop"
eips -c >/dev/null 2>&1
lipc-set-prop com.lab126.powerd preventScreenSaver 0 >/dev/null 2>&1

# 不管之前有没有停掉原生界面，这里都重新拉起来，保证设备能正常用。
#
# 这里**绝对不能**再写回 `/etc/init.d/framework start`：这台 PW3 上 /etc/init.d
# 是个空目录，那条命令返回 127，等于"以为恢复了其实什么都没做"。
# 界面真被停掉之后，这一行要是没生效，点「停止信息屏」就会得到一台
# 没有界面的砖头 —— 只能长按电源 40 秒重启。
for j in ${UI_JOBS:-framework pillow statusbar webreader}; do
    initctl start "$j" >/dev/null 2>&1
done
echo ondemand >/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null

echo "已停止，原生界面恢复中（如果没反应就长按电源键重启）。"
