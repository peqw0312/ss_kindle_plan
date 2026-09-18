#!/bin/sh
# Name: 测试收录
# Author: Kindle Plan
#
# 这一支脚本只回答一个问题：这台 Kindle 到底能不能运行 documents 里的 .sh。
# 留下两处可见的痕迹，哪个看到都算成功：
#   1) 屏幕左上角出现一行字，停留 4 秒
#   2) U 盘根目录多出一个 _scriptlet_test.log 文件

eips 0 0 'SH_Integration 是活的'
sleep 4
eips -c >/dev/null 2>&1

echo "$(date) 测试脚本运行成功" >>/mnt/us/_scriptlet_test.log 2>/dev/null
exit 0
