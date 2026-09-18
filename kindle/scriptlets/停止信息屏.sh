#!/bin/sh
# Name: 停止信息屏
# Author: Kindle Plan
#
# 停掉主循环并恢复原生书架界面。
# 万能保底：万一这个也点不动，长按电源键 40 秒强制重启，系统完全恢复原样。

EXT=/mnt/us/extensions/aistatus
[ -d "$EXT" ] || { echo "找不到 $EXT"; exit 1; }

/bin/sh "$EXT/bin/stop.sh"
exit 0
