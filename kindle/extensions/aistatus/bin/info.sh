#!/bin/sh
# 显示设备信息。配置前先跑这个，把「屏幕宽度/高度」记下来填进 config.yaml
DIR=/mnt/us/extensions/aistatus
[ -f "$DIR/config.sh" ] || DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== 设备信息 ==="
echo ""
echo "--- 屏幕参数（eips -i）---"
eips -i 2>/dev/null
echo ""
echo "  ↑ 上面 xres × yres 就是要填进 dashboard/config.yaml 的分辨率。"
echo "    Paperwhite 3 应该是 1072 x 1448，对应 model: kindle_pw3"
echo ""
echo "--- 系统 ---"
echo "固件版本 : $(cat /etc/version 2>/dev/null || echo 未知)"
echo "内核     : $(uname -a 2>/dev/null)"
echo "设备型号 : $(cat /proc/device-tree/model 2>/dev/null || echo 未知)"
echo ""
echo "--- 电量 / 网络 ---"
echo "电量     : $(gasgauge-info -c 2>/dev/null)"
echo "WiFi     : $(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)"
echo "IP       : $(ifconfig 2>/dev/null | grep -A1 wlan0 | grep 'inet addr' | cut -d: -f2 | cut -d' ' -f1)"
echo ""
echo "--- 省电相关 ---"
echo "RTC 节点 : $(ls /sys/devices/platform/*rtc*/wakeup_enable 2>/dev/null || echo 未找到)"
echo "电源状态 : $(lipc-get-prop com.lab126.powerd status 2>/dev/null | head -n 3 | tr '\n' ' ')"
echo ""
echo "--- 可用下载工具 ---"
for t in curl wget ht busybox; do
    p=$(command -v $t 2>/dev/null)
    [ -n "$p" ] && echo "  $t -> $p"
done
echo ""
echo "--- 越狱环境 ---"
[ -d /mnt/us/extensions ] && echo "  /mnt/us/extensions 存在（KUAL 扩展目录正常）"
[ -f /var/local/system/mntus.params ] && echo "  越狱标记文件存在"
ls /mnt/us/extensions/ 2>/dev/null | sed 's/^/  已装扩展: /'
