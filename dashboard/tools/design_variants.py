#!/usr/bin/env python3
"""把 `config.yaml` 的 style.preset 各出一张图，摆成一页网页供你对比挑选。

不另画布局、也不在这里存一份字号表 —— 预设住在 `aiinfo/render.py` 的
`STYLE_PRESETS` 里，这里只是换个 preset 调真渲染器。这样网页上看到的和真机上
显示的是同一份代码算出来的，不存在"预览好看、落地两样"。

（曾经在这里另写一套手摆坐标的布局，后果是天气描述压到数据格上、大数字被屏幕
边缘切掉 —— 绕过了渲染器的量宽和垂直预算。别再把布局抄进这个文件。）

用法（仓库根目录执行）：
    python dashboard/tools/design_variants.py              # 抓真实数据
    python dashboard/tools/design_variants.py --offline     # 样例数据，不联网，最快

产物：.workbuddy/_design/ 下每个 preset 一张 PNG + index.html。
"""

from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))

from aiinfo import render as R                                   # noqa: E402
from aiinfo.config import Config                                 # noqa: E402
from aiinfo.lunar import calendar_info                           # noqa: E402

OUT = ROOT / ".workbuddy" / "_design"

#: 每个预设一句说明 + 适合摆在哪。纯文案，不参与出图；
#: 新增 preset 忘了补这里也不报错，只是网页上少一句解释。
CAPTIONS = {
    "经典": ("当前线上那版，作对照", "不改任何东西"),
    "大字": ("日期和温度更突出，小字全部提级", "挂墙上、三米外看"),
    "紧凑": ("字号收回、密度提上去", "摆桌上凑近看；将来开「今日速览」也塞得下"),
    "中式": ("农历和节气抬头，行情退到次要位置", "重历法、轻数字"),
}


def sample_data() -> dict:
    """离线样例，故意挑最坏情况：最宽的时刻、最长的天气描述、负涨幅。"""
    now = datetime(2026, 9, 18, 23, 59)      # 「晚上 11:59」是 12 小时制最宽的形态
    return {
        "generated_at": now,
        "calendar": calendar_info(now),
        "weather": {
            "source": "Open-Meteo", "icon": "cloud", "temp": 26, "feels": 29,
            "desc": "晴间多云转局部小雨", "humidity": 68, "pop": 13, "precip": 0.4,
            "wind_dir": "北偏东", "wind_level": 2, "uv": 7,
            "air": {"level": "轻度污染", "aqi": 118},
            "forecast": [
                {"label": "明天", "high": 30, "low": 22, "desc": "毛毛雨"},
                {"label": "周日", "high": 30, "low": 22, "desc": "晴间多云"},
                {"label": "周一", "high": 28, "low": 19, "desc": "晴间多云"},
            ],
        },
        "quotes": [
            {"name": "纳指100", "price": 29447.32, "change_pct": 1.73},
            {"name": "标普500", "price": 7638.10, "change_pct": -1.14},
            {"name": "上证指数", "price": 3912.55, "change_pct": 0.94},
        ],
        "funds": [],
        "digest": {"title": "今日速览", "items": []},
    }


def live_data(cfg: Config) -> dict:
    import generate
    data = generate.collect(cfg)
    data["generated_at"] = datetime.now()
    data["calendar"] = calendar_info(data["generated_at"])
    return data


def render_preset(base: Config, name: str, data: dict, png: Path):
    cfg = copy.deepcopy(base)
    cfg.set("style.preset", name)
    r = R.Renderer(cfg, data)
    r.render().convert("L").save(png, format="PNG", optimize=True)
    return r


def build_html(shots, current: str, boxes) -> str:
    same = len(boxes) == 1
    cards = []
    for name, png, box, notes in shots:
        note, fit = CAPTIONS.get(name, ("", ""))
        badge = '<span class="cur">当前选中</span>' if name == current else ""
        lines = ('<li class="ok">自检无告警</li>' if not notes else
                 "".join(f"<li class='w'>{n}</li>" for n in notes))
        cards.append(f"""
      <figure>
        <h2>{name}{badge}</h2>
        <p class="d">{note}</p>
        <a href="{png}"><img src="{png}" alt="{name}" loading="lazy"></a>
        <p class="c">适合：{fit}</p>
        <ul>{lines}</ul>
        <p class="z">时钟留白区 {list(box) if box else '—'}</p>
      </figure>""")

    clock_note = (
        f"各版留白区一致（{sorted(boxes)[0]}），换版不用重拷精灵图。"
        if same else
        "<b class='r'>各版留白区不一样</b> —— 换任何一版都要重跑 "
        "<code>make_clock_assets.py --force</code>，并把整个 <code>clock/</code> "
        "重新拷进 Kindle。"
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>信息屏 · 版面预设对比</title>
<style>
 body{{margin:0;background:#eceef0;color:#1c1f23;
      font:15px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
 header{{padding:22px 26px 14px;border-bottom:1px solid #d6d9dd;background:#fff}}
 h1{{margin:0 0 6px;font-size:20px}}
 header p{{margin:0;color:#5b6470;font-size:13px}}
 main{{padding:22px 26px 60px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:22px}}
 figure{{margin:0;background:#fff;border:1px solid #d6d9dd;border-radius:10px;
         padding:14px 16px 16px}}
 figure h2{{margin:0 0 4px;font-size:17px}}
 .cur{{margin-left:8px;font-size:11px;font-weight:400;color:#fff;background:#1c1f23;
       border-radius:999px;padding:2px 9px;vertical-align:2px}}
 .d{{margin:0 0 12px;font-size:13px;color:#5b6470}}
 .c{{margin:10px 0 6px;font-size:12.5px;color:#3c434b;background:#f5f6f7;
     border-radius:6px;padding:7px 10px}}
 img{{display:block;width:100%;height:auto;border:1px solid #cfd3d8;border-radius:4px}}
 ul{{margin:8px 0 0;padding-left:19px;font-size:12.5px}}
 li{{margin:2px 0}}
 li.ok{{list-style:none;color:#1a7f37;padding-left:0}}
 li.w{{list-style:none;color:#b42318;padding-left:0;font-weight:600}}
 .z{{margin:6px 0 0;font-size:11.5px;color:#8b939d}}
 .r{{color:#b42318}}
 .tip{{margin:0 0 20px;padding:12px 16px;background:#fff;border:1px solid #d6d9dd;
       border-left:4px solid #1c1f23;border-radius:8px;font-size:13.5px}}
 code{{background:#f2f3f5;padding:1px 5px;border-radius:4px;font-size:12px}}
</style></head><body>
<header>
  <h1>信息屏 · 版面预设对比</h1>
  <p>每张都是 <code>aiinfo/render.py</code> 用对应 preset 当场画的，和真机上同一份代码。
     切换只改 <code>dashboard/config.yaml</code> 的 <code>style.preset</code>。</p>
</header>
<main>
  <p class="tip"><b>看之前三件事：</b>
     ① 右上角那块空白是<b>故意的</b>，Kindle 每分钟用自己的系统时间贴一张钟上去；
     ② 墨水屏没有彩色，涨跌只能靠 <b>实心=涨 / 空心=跌</b>；
     ③ {clock_note}
     ④ 红字是渲染器自检告警（版面挤了、字被裁了），<b>全是绿勾这版才算干净</b>。</p>
  <div class="grid">{''.join(cards)}</div>
</main></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="各 preset 出一张图 + 一个挑稿网页")
    ap.add_argument("-c", "--config", default=str(ROOT / "dashboard" / "config.yaml"))
    ap.add_argument("--offline", action="store_true", help="不联网，用样例数据")
    args = ap.parse_args()

    base = Config.load(args.config)
    current = str(base.get("style.preset", "经典") or "经典")
    data = sample_data() if args.offline else live_data(base)

    print(f"数据：{'样例' if args.offline else '实时'} · "
          f"{(data.get('weather') or {}).get('desc', '无')} · "
          f"时刻 {data['generated_at']:%H:%M}")
    print(f"config.yaml 当前选的是「{current}」\n")

    OUT.mkdir(parents=True, exist_ok=True)
    shots, boxes = [], set()
    for name in R.STYLE_PRESETS:
        png = OUT / f"{name}.png"
        r = render_preset(base, name, data, png)
        shots.append((name, png.name, r.clock_box, list(r.notes)))
        boxes.add(r.clock_box)
        mark = "  ← 当前" if name == current else ""
        bad = f"\n     !! {'; '.join(r.notes)}" if r.notes else ""
        print(f"  ✅ {name:4} 时钟留白区 {r.clock_box}{mark}{bad}")

    (OUT / "index.html").write_text(build_html(shots, current, boxes), encoding="utf-8")
    print(f"\n打开这个文件对比：\n  {OUT / 'index.html'}")
    if len(boxes) > 1:
        print("  !! 各版时钟留白区不同 —— 换版后必须重新生成并拷贝 clock/ 目录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
