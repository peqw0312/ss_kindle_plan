#!/bin/sh
# Name: 查看日志
# Author: Kindle Plan
#
# 屏幕上显示 aistatus.log 的最后几十行。出问题时第一个该点的东西。
# 关键几行：「framework 已停止，本进程存活」有没有出现，
# 以及有没有「下载或校验失败」「按云端时刻表，约 N 分钟后再来取」。

EXT=/mnt/us/extensions/aistatus
[ -f "$EXT/aistatus.log" ] || { echo "还没有日志文件，说明主循环一次都没跑起来过。"; exit 1; }

/bin/sh "$EXT/bin/log.sh"
exit 0
