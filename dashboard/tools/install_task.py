#!/usr/bin/env python3
"""把「按时刻表生成信息屏」注册成 Windows 计划任务。

用法：
    python dashboard/tools/install_task.py            # 按 config.yaml 的时刻表注册
    python dashboard/tools/install_task.py --status   # 看状态和上次运行结果
    python dashboard/tools/install_task.py --run      # 立刻跑一次
    python dashboard/tools/install_task.py --remove   # 注销

出图时刻从 `dashboard/config.yaml` 的 `cloud.refresh_at` 读
（默认一天四次：00:05 翻日期、05:10 美股收盘、12:00、15:05 A 股收盘）。
时钟已经改由 Kindle 本机画，图不必再为了"时间准"而每小时重算，
所以这里也从"每小时一次"改成了"只在需要的时候跑"。

**这条计划任务是云端方案的备份**，不是主力：主地址在云端时电脑关机也不影响
Kindle。留着它有两个用处 —— docs/ 里始终有一张新图可供局域网兜底，
以及云端或网络出问题时 Kindle 一秒就能切过去。

为什么用计划任务而不是别的：
  * 不需要常开的服务进程，也不占端口；跑完就退，电脑该省电省电。
  * 开机自动带起来（任务本身是持久的），不像手动开着的窗口，一重启就没了。
  * 失败能在「任务计划程序」里看到返回码，好排查。

为什么这个脚本是 Python 而不是 .ps1：
  Windows 上的 .ps1 默认按系统 ANSI 代码页读取，一旦路径里有中文就会乱码，
  而 Python 源文件是 UTF-8，读的是自己的路径，怎么都不会错位。
  真正调 PowerShell 的那几行在下面拼好再喂给它，内容全是 ASCII。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASH = ROOT / "dashboard"

TASK_NAME = "KindleAIScreen"

#: config.yaml 读不到时用的兜底时刻。和 dashboard/config.yaml 里的注释一一对应，
#: 真值永远以配置为准 —— 这里只是"配置坏了也别让任务注册不出来"。
FALLBACK_TIMES = ["00:05", "05:10", "12:00", "15:05"]
DEFAULT_MINUTES = 60


def read_refresh_at(config_path: Path) -> list[str]:
    """从 config.yaml 里抠出 cloud.refresh_at。

    刻意只用一个正则、不引入 PyYAML：这个脚本要能在任何环境下跑起来
    （它本身就是为了"当你还不会用 Python 生态时也能装好"而存在的）。
    认不出来就退回兜底时刻，不静默变成"每小时一次"。
    """
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return list(FALLBACK_TIMES)
    match = re.search(r"^\s*refresh_at\s*:\s*\[(.*?)\]", text, re.MULTILINE)
    if not match:
        return list(FALLBACK_TIMES)
    times = re.findall(r"\d{1,2}:\d{2}", match.group(1))
    return times or list(FALLBACK_TIMES)


def run_ps(script: str) -> tuple[int, str]:
    """跑一段 PowerShell。UTF-8 写盘 + -File 调起，避开命令行转义和代码页两个坑。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "task.ps1"
        # utf-8-sig：PowerShell 5.1 认 BOM 才会按 UTF-8 读，否则按 GBK 猜
        path.write_text(script, encoding="utf-8-sig")
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


def python_exe() -> str:
    """用当前解释器——也就是你跑这个脚本时用的那个（按文档就是项目 venv 里的）。"""
    return sys.executable


def ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def build_register_script(times: list[str], minutes: int) -> str:
    exe = python_exe()
    generate = DASH / "generate.py"
    config = DASH / "config.yaml"

    if times:
        # 每个时刻一个每日触发器。register 接受数组，一次注册全部。
        at_list = ", ".join(ps_quote(t) for t in times)
        trigger_block = (
            "# 每天在这些时刻各跑一次。用「时刻表」而不是「固定间隔」，因为出图只在\n"
            "# 数据真的变了的时刻才有意义 —— 多跑的每一次都是一轮白抓的天气和行情。\n"
            "$triggers = @()\n"
            f"foreach ($t in @({at_list})) {{\n"
            "    $triggers += New-ScheduledTaskTrigger -Daily -At $t\n"
            "}\n"
        )
    else:
        trigger_block = (
            "# 没配时刻表，退回固定间隔。\n"
            "$triggers = @(New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `\n"
            f"              -RepetitionInterval (New-TimeSpan -Minutes {minutes}))\n"
        )

    return f"""
$ErrorActionPreference = 'Stop'
$exe = {ps_quote(exe)}
$args = {ps_quote(f'"{generate}" -c "{config}"')}
$cwd  = {ps_quote(str(ROOT))}

$action  = New-ScheduledTaskAction -Execute $exe -Argument $args -WorkingDirectory $cwd
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
            -StartWhenAvailable -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

{trigger_block}
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $triggers `
    -Settings $settings -Force | Out-Null
Write-Output 'REGISTERED'
"""


STATUS_SCRIPT = f"""
$ErrorActionPreference = 'Stop'
$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue
if (-not $t) {{ Write-Output 'MISSING'; exit 0 }}
$i = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}'
Write-Output ("STATE=" + $t.State)
Write-Output ("LAST=" + $i.LastRunTime)
Write-Output ("RESULT=" + $i.LastTaskResult)
Write-Output ("NEXT=" + $i.NextRunTime)
"""

RUN_SCRIPT = f"Start-ScheduledTask -TaskName '{TASK_NAME}'; Write-Output 'STARTED'"

REMOVE_SCRIPT = (
    f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false "
    f"-ErrorAction SilentlyContinue; Write-Output 'REMOVED'"
)

#: 计划任务的返回码含义，只看最常见的几个
RESULT_CODES = {
    0: "成功",
    267009: "正在运行",
    267011: "还没跑过",
    267014: "被手动终止",
    3221225786: "被 Ctrl+C / 关机打断",
}


def print_status() -> int:
    code, out = run_ps(STATUS_SCRIPT)
    if code != 0:
        print("查询失败：", out)
        return 1
    if "MISSING" in out:
        print(f"计划任务 {TASK_NAME} 还没注册。")
        print("注册：python dashboard/tools/install_task.py")
        return 1
    info = dict(
        line.split("=", 1) for line in out.splitlines() if "=" in line
    )
    print("=" * 58)
    print(f" 计划任务 {TASK_NAME}")
    print("=" * 58)
    print(f" 状态     {info.get('STATE', '?')}")
    print(f" 上次运行 {info.get('LAST', '?')}")
    raw = info.get("RESULT", "")
    try:
        hint = RESULT_CODES.get(int(raw), "")
    except ValueError:
        hint = ""
    print(f" 返回码   {raw} {hint}")
    print(f" 下次运行 {info.get('NEXT', '?')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="注册按时刻表生成信息屏的 Windows 计划任务")
    parser.add_argument("--at", default=None,
                        help="出图时刻，逗号分隔（如 00:05,12:00）。默认读 config.yaml")
    parser.add_argument("--minutes", type=int, default=DEFAULT_MINUTES,
                        help=f"--at 为空时的兜底重复间隔（分钟），默认 {DEFAULT_MINUTES}")
    parser.add_argument("--status", action="store_true", help="查看状态")
    parser.add_argument("--remove", action="store_true", help="注销任务")
    parser.add_argument("--run", action="store_true", help="立刻触发一次")
    args = parser.parse_args()

    if sys.platform != "win32":
        times = [t.strip() for t in (args.at or "").split(",") if t.strip()] \
            or read_refresh_at(DASH / "config.yaml")
        print("这个脚本只负责 Windows 上的计划任务。")
        print("macOS / Linux 用 cron，一分钟一个字段，按时刻表写：")
        for stamp in times:
            hour, _, minute = stamp.partition(":")
            print(f"  {int(minute)} {int(hour)} * * * cd {ROOT} && {python_exe()} "
                  f"dashboard/generate.py -c dashboard/config.yaml")
        print("  （cron 用的是本机时区，不是 UTC；时区不对就整体偏）")
        return 1

    if args.status:
        return print_status()

    if args.remove:
        code, out = run_ps(REMOVE_SCRIPT)
        print("已注销。" if code == 0 else f"注销失败：{out}")
        return code

    if args.run:
        code, out = run_ps(RUN_SCRIPT)
        print("已触发，几秒后可用 --status 看结果。" if code == 0 else f"触发失败：{out}")
        return code

    if not (DASH / "generate.py").exists():
        print(f"找不到 {DASH / 'generate.py'}，这个脚本得放在项目的 dashboard/tools/ 下。")
        return 1

    if args.at is not None:
        times = [t.strip() for t in args.at.split(",") if t.strip()]
    else:
        times = read_refresh_at(DASH / "config.yaml")

    code, out = run_ps(build_register_script(times, args.minutes))
    if code != 0 or "REGISTERED" not in out:
        print("注册失败，PowerShell 说：")
        print(out)
        return 1

    print("=" * 58)
    print(" 已注册计划任务 KindleAIScreen")
    print("=" * 58)
    if times:
        print(f" 每天 {len(times)} 次出图：{'、'.join(times)}")
    else:
        print(f" 每 {args.minutes} 分钟出图一次")
    print(f" 执行     {python_exe()}")
    print(f"          {DASH / 'generate.py'}")
    print(f" 工作目录 {ROOT}")
    print("-" * 58)
    print(" 想立刻验证出图链路：")
    print("   python dashboard/tools/install_task.py --run")
    print("   python dashboard/tools/install_task.py --status")
    print("-" * 58)
    print(" 注意：任务只在**你登录着**的时候跑。")
    print(" 主地址在云端时电脑关机也不影响 Kindle；这条任务是局域网兜底，")
    print(" 顺带保证 docs/ 里始终有一张新图，云端出问题时能立刻切过去。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
