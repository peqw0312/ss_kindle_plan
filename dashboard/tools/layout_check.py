#!/usr/bin/env python3
"""版式自检：改字号 / 内边距之前先跑这个。

墨水屏的版式是个"预算问题"：1072×1448 的屏上要正好放下日历条、天气、行情和页脚。
任何一处多花 20px，就得从别处抠回来。render.py 只会把富余高度均摊成内边距，
它不会告诉你"刚好卡在边缘"——你要的是从容的余量，不是勉强不越界。

这个脚本用假数据把每一块的高度量出来，并且刻意用"最长的那种内容"
（最长的地名、最长的农历月名、大号指数价格），所以余量才是真实余量。

    python dashboard/tools/layout_check.py
    python dashboard/tools/layout_check.py -c dashboard/config.yaml --out .workbuddy/_preview

它不联网、不改仓库里的任何文件，输出几张预览图和一份高度清单。
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
for path in (str(TOOLS), str(ROOT / "dashboard")):
    if path not in sys.path:
        sys.path.insert(0, path)

from _marks import BAD, OK, WARN, line, safe_stdout   # noqa: E402

from aiinfo import render as R                        # noqa: E402
from aiinfo.config import Config                      # noqa: E402
from aiinfo.lunar import calendar_info                # noqa: E402
from aiinfo.render import Renderer, wrap_text         # noqa: E402

# 故意用偏长的内容：地名、天气描述、温度都取"比平均水平更占地方"那种，
# 不然量出来的余量会虚高
WEATHER = {
    "source": "和风天气",
    "temp": 29, "desc": "多云转小雨", "icon": "sun_cloud",
    "feels": 32, "humidity": 71, "wind_level": 3, "wind_dir": "东南",
    "precip": 0, "pop": None,
    "pressure": 1010, "vis": 23, "uv": 7,
    "air": {"level": "轻度污染", "aqi": 118},
    "warning": [],
    "forecast": [
        {"label": "今天", "high": 30, "low": 23, "icon": "sun_cloud", "desc": "多云"},
        {"label": "明天", "high": 31, "low": 24, "icon": "rain", "desc": "小雨"},
        {"label": "后天", "high": 28, "low": 22, "icon": "cloud", "desc": "阴"},
        {"label": "周日", "high": 27, "low": 21, "icon": "sun", "desc": "晴"},
    ],
}

WARNED_WEATHER = dict(WEATHER, warning=[
    {"title": "杭州市气象台发布暴雨黄色预警", "severity": "yellow"},
    {"title": "大风蓝色预警", "severity": "blue"},
])

#: 三个指数：纳指和标普是五位数（千分位最长），上证是四位数
QUOTES = [
    {"code": "usndx", "name": "纳斯达克100", "price": 29446.98, "pct": 1.73},
    {"code": "usinx", "name": "标普500", "price": 7637.76, "pct": -0.32},
    {"code": "sh000001", "name": "上证指数", "price": 3911.87, "pct": 0.94},
]

# 三条分别对应三个栏目，摘要长度都顶到 AI 的 34 字上限
DIGEST_ITEMS = [
    {"category": "game", "category_name": "3A游戏",
     "title": "《最终幻想7 重制版》第三部公布发售窗口",
     "summary": "史克威尔艾尼克斯确认明年春季登陆 PS5，系列收官作首次加入双主角并行叙事结构。"},
    {"category": "ai", "category_name": "AI 行业",
     "title": "英伟达发布新一代推理专用芯片",
     "summary": "单卡吞吐提升两倍，主打超长上下文推理，云厂商将于年底开始供货。"},
    {"category": "cn", "category_name": "国内热点",
     "title": "国务院部署下半年稳就业若干措施",
     "summary": "重点支持高校毕业生与制造业用工，配套补贴细则将于月底前落地实施。"},
]

#: 特殊日子也各出一张图：节日徽章是实心的、交节日是空心的，文案长度还不一样，
#: 加上一条气象预警，能把日历条上所有会变的文字都覆盖到
SCENARIOS = (
    ("平时", datetime(2026, 9, 18, 8, 32), WEATHER),
    ("节日", datetime(2026, 9, 25, 8, 32), WEATHER),
    ("节气", datetime(2026, 9, 23, 8, 32), WEATHER),
    ("春节", datetime(2027, 2, 6, 8, 32), WEATHER),
    ("预警", datetime(2026, 9, 18, 8, 32), WARNED_WEATHER),
)


def build_data(cfg: Config, when: datetime, weather: dict) -> dict:
    return {
        "generated_at": when,
        "calendar": calendar_info(when),
        "weather": weather,
        "quotes": QUOTES,
        "funds": [],
        "digest": {"title": cfg.get("digest.title", "今日速览"), "ai": True,
                   "model": cfg.get("digest.model", ""), "items": DIGEST_ITEMS},
    }


def measure(renderer: Renderer, data: dict, cfg: Config) -> tuple[int, list[str]]:
    """返回 (基准总高, 每块的明细行)。

    基准总高**不含**富余高度的均摊——render() 会把余量摊进三块的内边距，
    所以只要基准总高 ≤ 屏高，实际版面就一定放得下。
    """
    digest_on = bool(cfg.get("digest.enabled", True))
    rows = [
        ("顶部", renderer.px(10)),
        ("日历条", renderer.calendar_height(data["calendar"])),
        ("天气", renderer.weather_height(data["weather"])),
        ("行情", renderer.quotes_height(data["quotes"], [])),
        ("速览", renderer.digest_height(data["digest"]["items"]) if digest_on else 0),
        ("页脚", renderer.px(58)),
    ]
    total = sum(h for _, h in rows)
    detail = [f"{name:<6} {h:>5}px" for name, h in rows if h]
    detail.append(f"{'合计':<6} {total:>5}px")
    slack = renderer.h - total
    detail.append(f"{'屏高':<6} {renderer.h:>5}px   "
                  + (f"余 {slack}px（会均摊成三块的内边距）" if slack >= 0
                     else f"超出 {-slack}px"))
    return total, detail


def check_width_budget(cfg: Config, when: datetime) -> list[str]:
    """横向预算：日历条第一行是「农历 … 时钟」，两边不能打架。

    12 小时制下「下午 11:59」比 24 小时制的「16:34」宽得多，若还按 24 小时制
    预留宽度，晚上的时钟就会压到农历文字上。这里把最宽形态摊开来看。
    """
    renderer = Renderer(cfg, build_data(cfg, when, WEATHER))
    cal = calendar_info(when)
    g = renderer.calendar_geometry(cal)
    clock_font = g["clock_font"]
    out = [
        f"右半区总宽 {g['right_w']}px（日历纸 {g['box_w']}px 之后剩下的）",
        f"农历可用宽 {g['text_w']}px",
    ]
    bad = False
    for style in ("cn12", "en12", "h24"):
        cfg.set("clock.style", style)
        renderer2 = Renderer(cfg, build_data(cfg, when, WEATHER))
        g2 = renderer2.calendar_geometry(cal)
        probe = renderer2.clock_probe()
        w = renderer2.tw(probe, g2["clock_font"])
        lunar_w = renderer2.tw(cal.lunar_text, g2["lunar_font"])
        ok = lunar_w <= g2["text_w"]
        bad = bad or not ok
        out.append(f"  {style:<5} 最宽 {probe!r} = {w}px；"
                   f"农历 {cal.lunar_text} = {lunar_w}px "
                   + ("OK" if ok else "挤到时钟了"))
    cfg.set("clock.style", "cn12")
    out.append(("宽度预算正常" if not bad else "宽度不够，要调小 FS_CLOCK 或 FS_LUNAR"))
    return out


def check_weather_grid(cfg: Config, when: datetime) -> list[str]:
    """横向预算 · 天气卡片数据格。

    这是**已经翻过车**的地方：旧版用 3 列，每格只有 161px，而「空气 轻度污染 118」
    要 308px，直接糊到隔壁格子上；同一版的左半区把温度和描述并排，
    580px 的内容塞进 445px，描述压到了数据格上。两个问题都是"看着挺合理、
    量一下才知道越界"，所以这里把它固化成自检项。

    用 renderer.weather_grid() 取内容（和绘制同一份），不在这里重抄一遍。
    """
    renderer = Renderer(cfg, build_data(cfg, when, WEATHER))
    g = renderer.weather_geometry(WEATHER)
    pad = g["pad"]
    label_font = renderer.f(R.FS_LABEL)
    value_font = renderer.f(R.FS_VALUE, True)
    icon_size = g["icon_size"]

    right_x = renderer.margin + pad + (renderer.avail_w - pad * 2) * R.WEATHER_SPLIT
    right_w = renderer.w - renderer.margin - pad - right_x
    left_w = right_x - (renderer.margin + pad)
    cell_w = right_w / g["cols"]
    gap = renderer.px(12)
    sub_gap = renderer.px(8)

    # 最坏的那一组：四字等级 + 四位数 AQI + 两字风向
    worst_weather = dict(WEATHER, wind_dir="东南",
                         air={"level": "严重污染", "aqi": 1234})
    out = [
        f"分界 {R.WEATHER_SPLIT:.2f} → 左半区 {left_w:.0f}px / 右半区 {right_w:.0f}px，"
        f"{g['cols']} 列每格 {cell_w:.0f}px",
    ]

    bad = False
    shrunk_any = False
    for tag, weather in (("示例", WEATHER), ("最坏", worst_weather)):
        grid = renderer.weather_grid(weather)
        label_w = [max(renderer.tw(c[0], label_font) for c in grid[i::g["cols"]])
                   for i in range(g["cols"])]
        out.append(f"  -- {tag} --")
        for i, (label, value, sub) in enumerate(grid):
            lw = renderer.tw(label, label_font)
            sw = renderer.tw(sub, label_font) if sub else 0
            tail = (sub_gap + sw) if sub else 0
            avail = cell_w - label_w[i % g["cols"]] - gap - tail
            # 和绘制一致：数值放不下会降号，自检也要按降号后的宽度算，否则结论是假的
            vfont = renderer._fit_font(value, R.FS_VALUE, avail)
            shrunk = renderer.tw(value, vfont) > avail or vfont.size < value_font.size
            need = label_w[i % g["cols"]] + gap + renderer.tw(value, vfont) + tail
            fits = need <= cell_w
            bad = bad or not fits
            shrunk_any = shrunk_any or shrunk
            out.append(f"     {label} {value}{' ' + sub if sub else '':<6} "
                       f"需要 {need:.0f}px / {cell_w:.0f}px "
                       + ("OK" if fits and not shrunk else
                          ("OK（降到 %dpx）" % vfont.size if fits else "溢出，会压到隔壁格")))

    # 左半区两行：第一行「图标 + 温度」，第二行天气描述
    temp_font = renderer.f(R.FS_TEMP, True)
    desc_font = renderer.f(R.FS_DESC)
    desc_w = left_w - renderer.px(28)      # 再让出和数据格之间那道间隙
    for temp, w in (("-12°", WEATHER), ("29°", dict(WEATHER, desc="阵雨转多云"))):
        line = w["desc"]
        row1 = icon_size + renderer.px(22) + renderer.tw(temp, temp_font)
        row2 = renderer.tw(line, desc_font)
        bad = bad or row1 > left_w or row2 > desc_w
        out.append(f"     「{temp}」{row1:.0f}px ·「{line}」{row2:.0f}px "
                   f"/ 上限 {desc_w:.0f}px "
                   + ("OK" if row2 <= desc_w and row1 <= left_w else
                      f"放不下，会被截成「{renderer.clip_text(line, desc_font, desc_w)}」"))

    # 预报条：今天也算一格，所以格子数 = weather.days，格宽随天数变窄。
    # 每天那一格要放「图标 + 天气文字」和「最高° 最低°」两行。
    days = max(1, int(renderer.cfg.get("weather.days", 4)))
    strip_w = (renderer.avail_w - pad * 2) / days
    day_icon = g["day_icon"]
    label_font = renderer.f(R.FS_LABEL)
    value_font = renderer.f(R.FS_VALUE)
    for desc, high, low in (("阵雨转多云", -12, -18), ("晴", 30, 23)):
        row_a = day_icon + renderer.px(16) + renderer.tw(desc[:4], label_font)
        h = f"{high}°"
        row_b = renderer.tw(h, value_font) + renderer.px(10) + renderer.tw(f"{low}°", label_font)
        fits = max(row_a, row_b) <= strip_w
        bad = bad or not fits
        out.append(f"     预报 {days} 格：「{desc[:4]}」{row_a:.0f}px · "
                   f"「{h} {low}°」{row_b:.0f}px / 每格 {strip_w:.0f}px "
                   + ("OK" if fits else "挤了，减少 weather.days 或调小 FS_VALUE"))
    out.append("天气卡片横向预算正常" if not bad
               else "天气卡片有格子放不下，调小字号或把 WEATHER_SPLIT 往右挪")
    if shrunk_any:
        out.append("（有格子靠自动降号才放下——不算错，但说明字号已经卡到边了）")
    return out


def check_clock_assets(cfg: Config, when: datetime) -> tuple[list[str], bool]:
    """时钟精灵图 ↔ 版面几何 是否同一套（只有 clock.mode=local 才需要）。

    这块留白是**跨机器**协同的：坐标在本机由渲染器算出来写进 clock.conf，
    精灵图也在本机生成，然后两样东西一起拷进 Kindle。生成脚本自己会验一遍
    对齐（见 make_clock_assets.verify），但那只覆盖"生成的那一刻"。

    之后你要是改了 FS_CLOCK、clock.style 或字体，矩形会变，而 clock.conf 和
    那 1440 张图还是旧的 —— 生成脚本不会再跑，于是没人拦得住。真机上的表现是
    时钟整体偏移或压到农历上。这里拿**现在**的配置重算一遍矩形和指纹，把这个
    断点补上。
    """
    from aiinfo.fonts import book_for
    from make_clock_assets import fingerprint

    mode = cfg.get("clock.mode", "image")
    out: list[str] = []
    # YAML 1.1 把 off/on/yes/no 解析成布尔值，裸写 `mode: off` 到这里会变成 False，
    # 于是下面按"不是 local"处理成 image 模式 —— 屏幕上画个几小时前的假时间且不报错。
    # 所以非字符串一律当场点名，别放过去。
    if not isinstance(mode, str):
        out.append(f"!! clock.mode 读出来是 {mode!r}（{type(mode).__name__}），不是字符串 —— "
                   f"YAML 把 off/on/yes/no 当布尔值了，配置里要写成带引号的 \"off\"")
        return out, False
    mode = mode.lower()
    if mode == "off":
        out.append("clock.mode=off：屏幕上不要时间，图里不画、也不用精灵图 "
                   "（Kindle 端 CLOCK_MODE 必须同为 off，否则右上角永远空白）")
        return out, True
    if mode != "local":
        out.append(f"clock.mode={mode}：时钟画进图里，不用精灵图 "
                   f"（代价是图得按点重算才准）")
        return out, True

    out.append("clock.mode=local：图里时钟区留白，Kindle 用系统时间贴精灵图")

    assets = ROOT / "kindle" / "extensions" / "aistatus" / "clock"
    conf = assets / "clock.conf"
    if not conf.exists():
        out.append(f"!! 没找到 {conf.relative_to(ROOT)} —— "
                   f"先在电脑上跑 python dashboard/tools/make_clock_assets.py")
        return out, False

    fields: dict[str, str] = {}
    for raw in conf.read_text(encoding="utf-8").splitlines():
        if "=" in raw and not raw.startswith("#"):
            key, _, value = raw.partition("=")
            fields[key.strip()] = value.strip()

    # 换行符：这个文件会被 Kindle 的 sh source，CRLF 会让 CLOCK_X 变成 "644\r"，
    # eips 拿到带回车的参数就贴错位置。生成脚本已经显式写 LF，这里守一道，
    # 免得有人手改过、或者被某个编辑器"顺手"转成 CRLF。
    raw_bytes = conf.read_bytes()
    if b"\r\n" in raw_bytes or b"\r" in raw_bytes.replace(b"\r\n", b""):
        out.append("!! clock.conf 里混进了 CR（回车）—— Windows 编辑器或者手改留下的。")
        out.append("   这个文件会被 Kindle 的 sh 直接 source，"
                   "CLOCK_X 会变成带回车的值，时钟会贴错位置。")
        out.append("   修法：重跑 make_clock_assets.py（它会写 LF），"
                   "别用记事本另存。")
        return out, False
    out.append("换行符 LF，可以安全 source")

    renderer = Renderer(cfg, build_data(cfg, when, WEATHER))
    renderer.render()                        # render() 会填 clock_box
    box = renderer.clock_box
    if box is None:
        out.append("!! 渲染器没给出时钟区，检查 Renderer.clock_region()")
        return out, False

    want = (int(fields.get("CLOCK_X", -1)), int(fields.get("CLOCK_Y", -1)),
            int(fields.get("CLOCK_W", -1)), int(fields.get("CLOCK_H", -1)))
    got = (box[0], box[1], box[2] - box[0], box[3] - box[1])
    if want == got:
        out.append(f"坐标一致：({got[0]},{got[1]}) {got[2]}×{got[3]}px")
    else:
        out.append(f"!! 坐标对不上：clock.conf 写的是 ({want[0]},{want[1]}) "
                   f"{want[2]}×{want[3]}，按现在的配置算出来是 "
                   f"({got[0]},{got[1]}) {got[2]}×{got[3]}px")
        out.append("   改过 FS_CLOCK / clock.style / 字体就会这样："
                   "重跑 make_clock_assets.py，再把整个 clock/ 目录重拷进 Kindle")

    sprites = sorted(assets.glob("*.png"))
    if len(sprites) == 1440:
        out.append("精灵图 1440 张齐全（一天里的每一分钟）")
    else:
        out.append(f"!! 精灵图只有 {len(sprites)} 张，应为 1440")

    if sprites:
        from PIL import Image

        sample = sorted({sprites[0], sprites[-1]})
        wrong = []
        for one in sample:
            with Image.open(one) as im:
                if im.size != (got[2], got[3]):
                    wrong.append(f"{one.name} 是 {im.size}")
        if wrong:
            out.append(f"!! 精灵图尺寸不对（应为 {got[2]}×{got[3]}）："
                       + "；".join(wrong))
        else:
            out.append(f"抽查 {', '.join(s.name for s in sample)} "
                       f"尺寸均为 {got[2]}×{got[3]}px")

    # 指纹：几何或字体动过却没重新生成，这一步会当场抓住
    fonts = book_for(cfg)
    tag = fingerprint(cfg, box, fonts)
    have = fields.get("CLOCK_TAG", "")
    if tag == have:
        out.append(f"指纹一致 {tag}（精灵图用的是 {fonts.bold.name}）")
    else:
        out.append(f"!! 指纹不一致：clock.conf={have or '（空）'}，当前算出 {tag}")
        out.append("   说明字体或几何变过而精灵图没重生成，"
                   "贴上去会和图上其他字不是一套")

    ok = want == got and len(sprites) == 1440 and tag == have
    return out, ok


def main() -> int:
    safe_stdout()
    parser = argparse.ArgumentParser(description="墨水屏版式自检（不联网）")
    parser.add_argument("-c", "--config", default="dashboard/config.yaml")
    parser.add_argument("--out", default=".workbuddy/_preview",
                        help="预览图输出目录（默认 .workbuddy/_preview）")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    cfg = Config.load(str(config_path))
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 66)
    print(f" 版式自检 · {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" 配置：{config_path}")
    print(f" 机型：{cfg.get('device.model')} -> {cfg.size[0]}x{cfg.size[1]}")
    print(f" 速览：{'开' if cfg.get('digest.enabled', True) else '关'}   "
          f"时钟：{cfg.get('clock.style', 'cn12')}   "
          f"天气源：{cfg.get('weather.provider', 'qweather')}")
    print("=" * 66)

    print("\n【纵向预算】")
    # poster（帖）和 c1（带）都是"直接渲染、量实际矩形"的量法；
    # 只有 bands 才需要按条带基准高去预算。c1 刚加进来时这里漏了，
    # 于是它拿 bands 的数字报了个 ✅ —— 假通过，比不查更糟。
    layout = str(cfg.get("style.layout", "bands") or "bands").lower()
    direct_measure = layout in ("poster", "c1")
    worst = 0
    worst_slack = 10 ** 9
    for tag, when, weather in SCENARIOS:
        cal = calendar_info(when)
        data = build_data(cfg, when, weather)
        renderer = Renderer(cfg, data)
        if direct_measure:
            # 帖版没有"条带基准高"这个概念：直接渲染，量每块实际占的矩形和底边余量
            image = renderer.render()
            image.save(out_dir / f"{tag}.png")
            worst_slack = min(worst_slack, renderer.slack)
            print(f"\n  -- {tag}：{cal.solar_text} {cal.lunar_text} "
                  f"徽章={cal.badge or '无'} 值日={cal.zhiri}日 --")
            for key, (_x0, y0, _x1, y1) in renderer.block_boxes.items():
                print(f"     {renderer.BLOCK_LABELS.get(key, key):<6} "
                      f"y {y0:>4}..{y1:>4}   高 {y1 - y0:>4}px")
            print(f"     总余量 {renderer.slack}px（上下留白合计；居中版式看这个数，"
                  f"底边那半截只是它的一半）")
            for note in renderer.notes:
                print(f"     {WARN} {note}")
            continue
        total, detail = measure(renderer, data, cfg)
        worst = max(worst, total)
        print(f"\n  -- {tag}：{cal.solar_text} {cal.lunar_text} "
              f"徽章={cal.badge or '无'} 值日={cal.zhiri}日 --")
        for row in detail:
            print(f"     {row}")
        image = renderer.render()
        image.save(out_dir / f"{tag}.png")
        for note in renderer.notes:
            print(f"     {WARN} {note}")

    if direct_measure:
        print("\n【横向预算】")
        print("     居中/分带版式所有长字符串都走 clip_text 兜底（干支节气行 / 预警 /"
              " 预报描述 / 速览标题），跳过条带版的两栏横向检查。")
    else:
        print("\n【横向预算 · 日历条第一行】")
        for row in check_width_budget(cfg, SCENARIOS[0][1]):
            print(f"     {row}")

        print("\n【横向预算 · 天气卡片】")
        for row in check_weather_grid(cfg, SCENARIOS[0][1]):
            print(f"     {row}")

    print("\n【时钟精灵图 · 与版面几何同步】")
    try:
        clock_rows, clock_ok = check_clock_assets(cfg, SCENARIOS[0][1])
    except Exception as exc:                             # noqa: BLE001
        clock_rows, clock_ok = [f"!! 检查本身出错了：{exc!r}"], False
    for row in clock_rows:
        print(f"     {row}")

    print("\n【电量精灵图 · 与版面几何同步】")
    battery_ok = True
    if not direct_measure:
        print("     bands 版式右上角没有电量区，跳过")
    else:
        region = Renderer(cfg, build_data(cfg, SCENARIOS[0][1], WEATHER)).battery_region()
        conf = ROOT / "kindle" / "extensions" / "aistatus" / "battery" / "battery.conf"
        if not conf.is_file():
            battery_ok = False
            print("     !! 还没有 battery.conf —— 跑 dashboard/tools/make_battery_assets.py")
        else:
            got = {}
            for line_ in conf.read_text(encoding="utf-8").splitlines():
                if "=" in line_ and not line_.startswith("#"):
                    k, v = line_.split("=", 1)
                    got[k] = v.strip()
            want = {"BATTERY_X": region[0], "BATTERY_Y": region[1],
                    "BATTERY_W": region[2] - region[0],
                    "BATTERY_H": region[3] - region[1]}
            bad = [k for k, v in want.items() if str(v) != got.get(k, "")]
            if bad:
                battery_ok = False
                print(f"     !! battery.conf 的 {bad} 和渲染器算出的 {region} 对不上 —— "
                      f"重跑 make_battery_assets.py 并重拷 battery/")
            else:
                print(f"     精灵图坐标与 geometry 一致：{region}（指纹 {got.get('BATTERY_TAG')}）")

    print("\n【结论】")
    slack = worst_slack if direct_measure else cfg.size[1] - worst
    if slack >= 40:
        tail = ("上下留白合计，居中版式从两边一起扣" if direct_measure
                else "均摊成内边距后版面很从容")
        line("余量", OK, f"最紧的一屏还剩 {slack}px，{tail}")
    elif slack >= 0:
        line("余量", WARN, f"最紧的一屏只剩 {slack}px：不会越界，但换字体会很紧，"
                           "建议再压一点字号")
    else:
        line("余量", BAD, f"最紧的一屏已经超出 {-slack}px，必须减字号或关一个区块")
    line("时钟", OK if clock_ok else BAD,
         "精灵图与几何同一套" if clock_ok else "精灵图与几何对不上，真机上会贴偏")
    if direct_measure:
        line("电量", OK if battery_ok else BAD,
             "精灵图坐标与版面几何一致" if battery_ok
             else "battery.conf 与版面几何对不上，真机上会贴偏")
    print("     安全线是 40px：图上的字统一由 fonts.book_for 解析（默认指到 Noto），")
    print("     所以本机预览和云端出图的行高一致，不会再出现本机雅黑 / 云端 Noto 那种差异。")
    print(f"     预览图：{out_dir}")

    # 速览默认已关闭，开着的时候才需要看折行——它是最容易悄悄出问题的一块
    if cfg.get("digest.enabled", True):
        print("\n【速览折行】")
        base = SCENARIOS[0][1]
        probe = Renderer(cfg, {"generated_at": base, "calendar": calendar_info(base)})
        title_font = probe.f(R.FS_ITEM_TITLE, True)
        sum_font = probe.f(R.FS_ITEM_SUM)
        chip_font = probe.f(R.FS_ITEM_CHIP, True)
        chip_w = max([probe.px(76)]
                     + [probe.tw(it["category_name"], chip_font) + probe.px(28)
                        for it in DIGEST_ITEMS])
        text_w = probe.w - 2 * probe.margin - chip_w - probe.px(18)
        for it in DIGEST_ITEMS:
            tl = wrap_text(probe.d, it["title"], title_font, text_w,
                           int(cfg.get("digest.max_title_lines", 1)))
            sl = wrap_text(probe.d, it["summary"], sum_font, text_w,
                           int(cfg.get("digest.max_summary_lines", 2)))
            truncated = tl and tl[-1].endswith("…")
            line(it["category_name"], WARN if truncated else OK,
                 f"标题 {len(tl)} 行 / 摘要 {len(sl)} 行"
                 + ("（标题被截断）" if truncated else ""))
            for row in sl:
                print(f"           {row}")
    else:
        print("\n     速览已关闭（digest.enabled: false），跳过折行检查。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                    # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
