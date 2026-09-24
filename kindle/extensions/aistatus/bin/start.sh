#!/bin/sh
# 启动 AI 信息屏（从 documents 里的「启动信息屏」scriptlet 转过来执行这个）
DIR=/mnt/us/extensions/aistatus
[ -f "$DIR/config.sh" ] || DIR="$(cd "$(dirname "$0")/.." && pwd)"

# 已经在跑就不重复启动 —— 但**必须验进程真的还在**，不能只看标记文件：
# 长按电源强制关机时 cleanup() 来不及跑，`.running` 会留在盘上，
# 于是以后每次点启动都被这句拦住、主循环根本不跑，而日志一行都不会多写
# —— 这个故障看起来像"脚本没反应"，实际是上一次的非正常退出把门堵死了。
if [ -f "$DIR/.running" ]; then
    oldpid=$(cat "$DIR/.pid" 2>/dev/null)
    if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
        echo "信息屏已经在运行了（PID $oldpid）。"
        echo "要重启请先点「停止信息屏」。"
        exit 0
    fi
    echo "发现上次异常退出留下的运行标记（PID ${oldpid:-无} 已不存在），自动接手。"
    rm -f "$DIR/.running" "$DIR/.pid"
fi

# 顺手检查一下配置里最容易忘的一项
if grep -qE 'example|DASHBOARD_URLS=""' "$DIR/config.sh" 2>/dev/null; then
    echo "警告：config.sh 里的 DASHBOARD_URLS 还没填（或还是示例地址），图片会下载失败。"
fi

rm -f "$DIR/.stop"
touch "$DIR/.running"

# 必须脱离启动它的那个进程组，否则会被连坐杀掉自己 —— 这是外部项目踩实过的坑：
# 启动链是 framework →（KUAL 或 scriptlet）→ 本脚本 → aistatus.sh，
# 而 aistatus.sh 一起来就 `framework stop`（STOP_FRAMEWORK=1），
# 等于亲手杀掉自己的爷爷进程；组信号会一路打到 aistatus.sh 身上。
#
# nohup **挡不住这个**：它只忽略 SIGHUP，不改变进程组。
# setsid 才是对的 —— 新建会话、脱离原进程组，之后显式 `kill $pid` 仍然有效
# （那是按 PID 打的，不是按组），所以停止功能不受影响。
if command -v setsid >/dev/null 2>&1; then
    setsid /bin/sh "$DIR/bin/aistatus.sh" >/dev/null 2>&1 &
    way="setsid"
elif command -v nohup >/dev/null 2>&1; then
    nohup /bin/sh "$DIR/bin/aistatus.sh" >/dev/null 2>&1 &
    way="nohup（没有 setsid，退而求其次；真机上若启动后自己退出就是这个问题）"
else
    /bin/sh "$DIR/bin/aistatus.sh" >/dev/null 2>&1 &
    way="直接后台（无隔离）"
fi

# PID 由 aistatus.sh 自己写 $$ —— 这里不能用 $!：setsid 在需要时会 fork 一次，
# $! 拿到的是 setsid 的 PID，那个进程 exec 完就没了，stop 会杀错对象。
sleep 2
if [ -f "$DIR/.pid" ]; then
    echo "AI 信息屏已启动（PID $(cat "$DIR/.pid")，隔离方式 $way）。"
    echo "首次刷新要连 WiFi，大概 10~30 秒后屏幕会更新。"
else
    echo "!! 启动后 2 秒内没见到 PID —— 主循环可能已经退出。"
    echo "   看 extensions/aistatus/aistatus.log 的最后几行找原因。"
fi
