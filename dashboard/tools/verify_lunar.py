#!/usr/bin/env python3
"""农历模块对拍：拿第三方权威实现来验 dashboard/aiinfo/lunar.py。

为什么值得留着这个脚本：`aiinfo/lunar.py` 里的农历表、节气时刻、干支建除
全部是手写推算，没有依赖任何库（这是刻意的——sxtwl 在 GitHub Actions 上
编译不过）。手写推算法最怕的就是"某几年悄悄错了一位"，而屏幕上不会报错，
只会安静地显示错的农历。所以改成这个脚本，随时能重新验一遍：

    pip install lunar_python          # 只在对拍时需要，不是运行时依赖
    python dashboard/tools/verify_lunar.py                # 全量对拍
    python dashboard/tools/verify_lunar.py --quick        # 只跑抽样和边界
    python dashboard/tools/verify_lunar.py --list 2026-10-01 2026-12-22

对拍项目：农历月日（含闰月）、年/月/日干支、生肖、建除值日、24 节气日期。
"""

from __future__ import annotations

import argparse
import random
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
for path in (str(TOOLS), str(ROOT / "dashboard")):
    if path not in sys.path:
        sys.path.insert(0, path)

from _marks import BAD, OK, WARN, line, safe_stdout   # noqa: E402

from aiinfo import lunar as L                         # noqa: E402


def load_reference():
    """载入对拍基准。没装 lunar_python 就明确说一声，而不是假装通过。"""
    try:
        from lunar_python import Solar                    # noqa: PLC0415
    except ImportError:
        print(f"  {WARN} 没有安装 lunar_python，无法对拍。")
        print("     装一下再跑：pip install lunar_python")
        print("     （它只是验证用的工具，运行时不依赖它）")
        return None
    return Solar


def check_one(Solar, d: date) -> list[tuple[str, str, str]]:
    """比一天，返回所有对不上的项目。"""
    ref = Solar.fromYmd(d.year, d.month, d.day).getLunar()
    cal = L.calendar_info(d)
    want_lunar = (("闰" if ref.getMonth() < 0 else "")
                  + L.MONTH_NAMES[abs(ref.getMonth()) - 1]
                  + L.DAY_NAMES[ref.getDay() - 1])
    pairs = [
        ("农历", cal.lunar_text, want_lunar),
        ("年干支", cal.ganzhi_year, ref.getYearInGanZhi()),
        ("月干支", cal.ganzhi_month, ref.getMonthInGanZhi()),
        ("日干支", cal.ganzhi_day, ref.getDayInGanZhi()),
        ("生肖", cal.shengxiao, ref.getYearShengXiao()),
        ("建除值日", cal.zhiri, ref.getZhiXing()),
        ("节气", cal.term_today or "", ref.getJieQi() or ""),
    ]
    return [(name, mine, ref_) for name, mine, ref_ in pairs if mine != ref_]


def check_new_year() -> list[tuple[str, str, str]]:
    """春节是整张农历表的锚点：正月初一错一天，整年的农历就全错。

    所以 218 年逐个对——这是当初发现"表里某年差了 1 天"的那一关。
    """
    from lunar_python import Lunar                        # noqa: PLC0415

    bad = []
    for year in range(L.LUNAR_TABLE_YEAR0, L.LUNAR_TABLE_YEAR0 + 218):
        try:
            solar = Lunar.fromYmd(year, 1, 1).getSolar()
            want = date(solar.getYear(), solar.getMonth(), solar.getDay())
        except Exception:                                  # noqa: BLE001
            continue
        mine = L.lunar_new_year(year)
        if mine != want:
            bad.append((str(year), str(mine), str(want)))
    return bad


def main() -> int:
    safe_stdout()
    parser = argparse.ArgumentParser(description="农历模块对拍（需要 lunar_python）")
    parser.add_argument("--quick", action="store_true", help="跳过逐日节气全量对拍")
    parser.add_argument("--samples", type=int, default=3000,
                        help="随机对拍的天数（默认 3000）")
    parser.add_argument("--seed", type=int, default=20260918, help="随机种子，便于复现")
    parser.add_argument("--list", nargs="+", metavar="YYYY-MM-DD",
                        help="只把这几天的完整信息打出来看看")
    args = parser.parse_args()

    print("=" * 66)
    print(" 农历模块对拍 · aiinfo/lunar.py")
    print(f" 农历表覆盖 {L.LUNAR_MIN_DATE} ~ {L.LUNAR_MAX_DATE}")
    print("=" * 66)

    Solar = load_reference()
    if Solar is None:
        return 1

    if args.list:
        for text in args.list:
            d = date.fromisoformat(text)
            cal = L.calendar_info(d)
            print(f"\n {cal.solar_text} {cal.weekday} · {cal.lunar_text}"
                  f"（{cal.lunar_size}月）")
            print(f"   {cal.ganzhi_year}年 属{cal.shengxiao} · "
                  f"{cal.ganzhi_month}月 {cal.ganzhi_day}日 · {cal.zhiri}日")
            print(f"   节气：{cal.term_current} 第 {cal.term_current_days} 天 · "
                  f"距 {cal.term_next} {cal.term_next_days} 天（{cal.term_next_date}）")
            print(f"   徽章：{cal.badge or '（无）'}（{cal.badge_kind or '-'}）· "
                  f"{cal.upcoming or '近期没有节日'}")
            print(f"   宜 {' '.join(cal.yi)}｜忌 {' '.join(cal.ji)}｜{cal.chong} {cal.sha}")
        return 0

    failures = 0

    # ---- 1. 随机对拍 ----
    print(f"\n【随机对拍 {args.samples} 天（1890-2099，种子 {args.seed}）】")
    random.seed(args.seed)
    tally: dict[str, int] = {}
    examples: dict[str, list] = {}
    for _ in range(args.samples):
        y = random.randint(1890, 2099)
        d = date(y, random.randint(1, 12), random.randint(1, 28))
        for name, mine, ref in check_one(Solar, d):
            tally[name] = tally.get(name, 0) + 1
            examples.setdefault(name, []).append((d.isoformat(), mine, ref))
    if not tally:
        line("全部一致", OK, f"{args.samples} 天，6 项字段零差异")
    else:
        for name, count in sorted(tally.items(), key=lambda x: -x[1]):
            line(name, BAD, f"{count} 处不一致")
            for row in examples[name][:5]:
                print(f"           {row}")
        failures += len(tally)

    # ---- 2. 春节（农历表的锚点）----
    print("\n【春节 218 年逐一对拍】")
    bad_new_year = check_new_year()
    if bad_new_year:
        line("春节", BAD, f"{len(bad_new_year)} 年对不上")
        for row in bad_new_year[:10]:
            print(f"           {row}")
        failures += len(bad_new_year)
    else:
        line("春节", OK, "全表零差异")

    # ---- 3. 逐日节气 ----
    if args.quick:
        print("\n【逐日节气】已跳过（--quick）")
    else:
        print("\n【逐日节气 2020-2035】")
        miss = []
        total = 0
        d = date(2020, 1, 1)
        while d <= date(2035, 12, 31):
            ref = Solar.fromYmd(d.year, d.month, d.day).getLunar().getJieQi()
            mine = L.calendar_info(d).term_today
            total += 1
            if (mine or "") != (ref or ""):
                miss.append((d.isoformat(), mine, ref))
            d += timedelta(days=1)
        if miss:
            line("节气", BAD, f"{len(miss)}/{total} 天对不上")
            for row in miss[:10]:
                print(f"           {row}")
            failures += len(miss)
        else:
            line("节气", OK, f"{total} 天逐日零差异")

    # ---- 4. 边界 ----
    print("\n【表边界（这里不该崩，也不该给出离谱结果）】")
    for d in (date(1884, 1, 31), date(1885, 1, 1), date(2100, 12, 31),
              date(2101, 6, 1), date(2101, 12, 31)):
        cal = L.calendar_info(d)
        inside = L.LUNAR_MIN_DATE <= d <= L.LUNAR_MAX_DATE
        flag = OK if cal.lunar_text != "—" or not inside else WARN
        line(d.isoformat(), flag,
             f"{'表内' if inside else '表外'} → {cal.lunar_text} · "
             f"{cal.zhiri}日 · 下一节气 {cal.term_next}")

    print("\n【结论】")
    if failures:
        line("对拍", BAD, f"共 {failures} 处不一致，去看上面的样例")
        return 1
    line("对拍", OK, "全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                    # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
