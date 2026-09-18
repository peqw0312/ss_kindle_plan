#!/bin/sh
# Name: 查看设备信息
# Author: Kindle Plan
#
# 查这台机器的真实分辨率、固件版本、电量。换机型时对不上分辨率就靠它确认。

EXT=/mnt/us/extensions/aistatus
[ -d "$EXT" ] || { echo "找不到 $EXT"; exit 1; }

/bin/sh "$EXT/bin/info.sh"
exit 0
