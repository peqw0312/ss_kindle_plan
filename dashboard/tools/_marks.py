"""tools 下的脚本共用的一点控制台输出工具。

为什么单独抽出来：Windows 控制台默认编码是 GBK，直接 print ✅ 会抛
UnicodeEncodeError **把整个脚本打断**（不是打不出来，是直接崩），而在
GitHub Actions 上又是 UTF-8 一切正常。所以这里按当前控制台挑一套能打的标记。
"""

from __future__ import annotations

import sys


def _stdout_is_utf8() -> bool:
    enc = (sys.stdout.encoding or "utf-8").lower().replace("-", "")
    return enc in ("utf8", "utf8mb4", "cp65001")


def pick_marks() -> tuple[str, str, str]:
    return ("✅", "❌", "⚠️ ") if _stdout_is_utf8() else ("[OK]", "[NG]", "[!!]")


OK, BAD, WARN = pick_marks()


def line(label: str, status: str, detail: str = "") -> None:
    print(f"  {status} {label:<18} {detail}", flush=True)


def safe_stdout() -> None:
    """让 print 永不抛异常。

    新闻标题里偶尔夹着生僻字或零宽字符，GBK 控制台会因此中断整个自检，
    而在 Actions 上根本复现不出来。改成 replace 就只是显示成 ?，不影响结论。
    """
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
