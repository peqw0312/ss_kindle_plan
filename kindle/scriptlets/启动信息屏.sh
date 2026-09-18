#!/bin/sh
# Name: 启动信息屏
# Author: Kindle Plan
#
# 放进 documents/ 就会在书库里出现，点开即运行（sh_integration 提供这个能力）。
# 真正的逻辑在 extensions/aistatus/bin/ 里，这里只负责转过去 ——
# 这样改 bin/ 下的脚本不需要重拷 documents 里这几个文件。

EXT=/mnt/us/extensions/aistatus
[ -d "$EXT" ] || { echo "找不到 $EXT"; echo "先把 kindle/extensions/aistatus 整个文件夹拷进 Kindle 的 extensions/ 里。"; exit 1; }

/bin/sh "$EXT/bin/start.sh"
exit 0
