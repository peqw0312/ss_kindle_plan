#!/bin/sh
# 在屏幕上显示最近日志（KUAL 里会把输出显示成一个可滚动的文本页）
DIR=/mnt/us/extensions/aistatus
[ -f "$DIR/config.sh" ] || DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -f "$DIR/aistatus.log" ]; then
    echo "还没有日志。请先启动信息屏，或者跑一次 bin/refresh.sh。"
    exit 0
fi

echo "=== 最近 60 行日志 ==="
echo ""
tail -n 60 "$DIR/aistatus.log"
echo ""
echo "=== 状态 ==="
# 和 start.sh 一样：标记文件存在 ≠ 真的在跑。强制关机会留下过期的 .running，
# 只看文件会让这个诊断工具在最需要它的时候给出错误答案。
lpid=$(cat "$DIR/.pid" 2>/dev/null)
if [ -f "$DIR/.running" ] && [ -n "$lpid" ] && kill -0 "$lpid" 2>/dev/null; then
    echo "运行中，PID $lpid"
elif [ -f "$DIR/.running" ]; then
    echo "!! 有 .running 标记但 PID ${lpid:-无} 已不存在 —— 上次是异常退出，点「启动信息屏」会自动接手"
else
    echo "未运行"
fi
echo "电量: $(gasgauge-info -c 2>/dev/null)"
echo ""
echo "日志文件完整路径：$DIR/aistatus.log"
