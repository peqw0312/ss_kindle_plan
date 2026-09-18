#!/bin/sh
# Name: 测试刷新一次
# Author: Kindle Plan
#
# 只拉一张图、刷一次屏就退出，不停 framework、不留后台。
# 排障首选：主地址失败会自动试备用地址，并把每一步写到日志里。

EXT=/mnt/us/extensions/aistatus
[ -d "$EXT" ] || { echo "找不到 $EXT"; echo "先把 kindle/extensions/aistatus 整个文件夹拷进 Kindle 的 extensions/ 里。"; exit 1; }

/bin/sh "$EXT/bin/refresh.sh"
exit 0
