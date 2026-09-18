#!/bin/sh
# Name: 时钟贴图自检
# Author: Kindle Plan
#
# 全项目唯一一个只能在真机上验的环节：eips 的 -x/-y 坐标参数在 2015 年的
# 机器上到底生不生效，官方文档没说死。跑完抬头看时钟出现在哪：
#   右上角 = 正常；左上角 = 这台固件忽略坐标；什么都没有 = 精灵图没拷全

EXT=/mnt/us/extensions/aistatus
[ -d "$EXT" ] || { echo "找不到 $EXT"; echo "先把 kindle/extensions/aistatus 整个文件夹拷进 Kindle 的 extensions/ 里。"; exit 1; }

/bin/sh "$EXT/bin/clock_check.sh"
exit 0
