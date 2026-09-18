#!/usr/bin/env python3
"""四版**内容编排**预览 —— 不是换字号，是换屏幕上放什么信息。

和 design_variants.py 的分工：那个改 `style.preset`（同一套内容换字号），
这个改内容本身。所以这里的图是**提案**，选中哪版之后要在 render.py 里正式实现
（走它那套 `*_height()` + 余量均摊），不能直接把这里的绘制代码搬上线 ——
这里只保证「不溢出、不压字」，不保证和 layout_check 的预算模型对齐。

复用 Renderer 的字体和量宽原语（f / px / tw / lh / clip_text / text / icon），
所有文本都按像素裁切过，所以不会出现"预览好看、真机压字"。

用法（仓库根目录执行）：
    python dashboard/tools/content_variants.py            # 真实数据
    python dashboard/tools/content_variants.py --offline   # 样例数据

产物：.workbuddy/_content/ 下四张 PNG + index.html。
"""

from __future__ import annotations

import argparse
import calendar as pycal
import copy
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(HERE))

from aiinfo import render as R                                   # noqa: E402
from aiinfo.config import Config                                 # noqa: E402
from aiinfo.lunar import calendar_info                           # noqa: E402
from aiinfo.render import GRAY, GRAY_LIGHT, INK, INK_SOFT        # noqa: E402
from design_variants import live_data, sample_data               # noqa: E402

OUT = ROOT / ".workbuddy" / "_content"
FOOT_H = 58          # 和 render.py 的 draw_footer 保持同一个高度
CLOCK_RECT = None    # 由 main 填：Kindle 每分钟贴图的那块留白


# ---------------------------------------------------------------------------
# 通用件

def dashed_clock(r, box):
    """标出时钟留白区 —— 那块由 Kindle 本机贴，图里本来就该是空的。"""
    x0, y0, x1, y1 = box
    step = r.px(10)
    x = x0
    while x < x1:
        r.d.line([x, y0, min(x + step // 2, x1), y0], fill=GRAY_LIGHT, width=2)
        r.d.line([x, y1, min(x + step // 2, x1), y1], fill=GRAY_LIGHT, width=2)
        x += step
    y = y0
    while y < y1:
        r.d.line([x0, y, x0, min(y + step // 2, y1)], fill=GRAY_LIGHT, width=2)
        r.d.line([x1, y, x1, min(y + step // 2, y1)], fill=GRAY_LIGHT, width=2)
        y += step
    r.text(((x0 + x1) / 2, (y0 + y1) / 2), "时钟由 Kindle 每分钟自贴",
           r.f(26), GRAY_LIGHT, anchor="mm")


def footer(r, data):
    w = data.get("weather") or {}
    y = r.h - r.margin - r.px(20)
    left = f"{data['generated_at']:%m-%d %H:%M} · {w.get('source') or '无天气源'}"
    right = "实心=涨 空心=跌"
    r.text((r.margin, y), r.clip_text(left, r.f(20), r.avail_w - r.tw(right, r.f(20))),
           r.f(20), GRAY)
    r.text((r.w - r.margin, y), right, r.f(20), GRAY, anchor="rt")


def one_line_quotes(r, data, y, *, label_size=26, value_size=40):
    """行情压成一行：名字 价格 涨跌，三项并排。"""
    qs = (data.get("quotes") or [])[:3]
    if not qs:
        return y
    lf, vf, sf = r.f(label_size), r.f(value_size, bold=True), r.f(label_size + 2)
    gap = r.px(26)
    cell = (r.avail_w - gap * (len(qs) - 1)) / len(qs)
    for i, q in enumerate(qs):
        x = r.margin + i * (cell + gap)
        r.text((x, y), r.clip_text(str(q.get("name") or ""), lf, int(cell)), lf, GRAY)
        price = q.get("price")
        r.text((x, y + r.px(28)), f"{price:,.0f}" if isinstance(price, (int, float)) else "—",
               vf, INK, strong=True)
        pct = q.get("pct")           # 注意：接口给的是小数（0.0128 = 1.28%）
        if isinstance(pct, (int, float)):
            r.text((x + r.tw(f"{price:,.0f}", vf) + r.px(10), y + r.px(40)),
                   f"{'■' if pct >= 0 else '□'}{abs(pct):.2f}%", sf, INK_SOFT)
    return y + r.px(28) + r.lh(vf) + r.px(10)


def check(r, name, y_used):
    """纵向兜底：预览工具自己也要报溢出，不然又是"看着挺好、真机上糊成一团"。

    判据用 render.py / layout_check 同一根线（40px 余量），不是"没越界就算过" ——
    只留 1px 的版面在真机上换个字体就崩了，那种"通过"没意义。
    """
    room = r.h - r.margin - r.px(FOOT_H) - r.px(20)
    spare = room - y_used
    if spare >= r.px(40):
        return f"余 {spare:.0f}px", True
    if spare >= 0:
        return f"只剩 {spare:.0f}px，低于 40px 安全线", False
    return f"超出 {-spare:.0f}px", False


# ---------------------------------------------------------------------------
# ① 月历版：整月网格 + 今天高亮。墙上挂历的核心诉求就是"看这个月"

def v_month(r, data):
    cal = data["calendar"]
    cx0, cy0, cx1, cy1 = CLOCK_RECT
    y = r.margin
    dashed_clock(r, CLOCK_RECT)
    # 月份标题放在时钟区左边（它窄），星期表头是通栏的，必须整个挪到时钟区下面，
    # 否则表头会从虚线框底下穿过去 —— 正式实现时那块是真的要留白的。
    r.text((r.margin, y + r.px(6)), f"{cal.solar_year}年 {cal.solar_month}月",
           r.f(46, bold=True), INK, strong=True)
    r.text((r.margin, y + r.px(66)),
           r.clip_text(f"{cal.lunar_text} · {cal.ganzhi_year}年属{cal.shengxiao}",
                       r.f(30), cx0 - r.margin - r.px(20)), r.f(30), GRAY)
    y = cy1 + r.px(26)

    today = data["generated_at"].date()
    weeks = [w for w in pycal.Calendar(firstweekday=6).monthdatescalendar(
        today.year, today.month)]
    cell_w = r.avail_w / 7
    for i, wd in enumerate("日一二三四五六"):
        r.text((r.margin + cell_w * i + cell_w / 2, y), wd, r.f(28, bold=True),
               INK_SOFT, anchor="ma", strong=True)
    y += r.px(40)
    r.rule(y, thickness=2, color=GRAY_LIGHT)
    y += r.px(8)

    # 格子高度按剩余空间算，不写死 —— 写死就会像现在这样底部空一大块
    grid_bottom = r.h - r.margin - r.px(FOOT_H) - r.px(365)
    cell_h = max(r.px(96), (grid_bottom - y) / len(weeks))
    for wk in weeks:
        for i, d in enumerate(wk):
            cx = r.margin + cell_w * i
            if d == today:
                r.d.rectangle([cx + 3, y + 3, cx + cell_w - 3, y + cell_h - 3], fill=INK)
                r.text((cx + cell_w / 2, y + cell_h / 2), str(d.day),
                       r.f(52, bold=True), 255, anchor="mm", strong=True)
            else:
                col = GRAY_LIGHT if d.month != today.month else (
                    INK_SOFT if d.weekday() >= 5 else INK)
                r.text((cx + cell_w / 2, y + cell_h / 2), str(d.day),
                       r.f(46, bold=d.weekday() >= 5), col, anchor="mm",
                       strong=d.weekday() >= 5)
        y += cell_h
    y += r.px(18)

    r.rule(y, thickness=r.px(4), color=INK)
    y += r.px(22)
    # 今天那一行的详情：农历 + 节气 + 宜
    detail = (f"今天 {cal.lunar_text} · {cal.ganzhi_day}日 · "
              f"{cal.term_current}第{cal.term_current_days}天 · 宜 {' '.join(cal.yi[:3])}")
    r.text((r.margin, y), r.clip_text(detail, r.f(32), r.avail_w), r.f(32), INK_SOFT)
    y += r.px(52)

    w = data.get("weather") or {}
    try:
        r.icon(r.margin + r.px(34), y + r.px(30), r.px(62), str(w.get("icon") or "cloud"))
    except Exception:
        pass
    temp = f"{w.get('temp', '—')}° {w.get('desc') or ''}"
    r.text((r.margin + r.px(84), y + r.px(6)), r.clip_text(temp, r.f(46, bold=True),
           int(r.avail_w * 0.42)), r.f(46, bold=True), INK, strong=True)
    fut = "  ".join(f"{d.get('label')} {d.get('high')}°/{d.get('low')}°"
                    for d in (w.get("forecast") or [])[1:4])
    r.text((r.w - r.margin, y + r.px(22)), r.clip_text(fut, r.f(30), int(r.avail_w * 0.44)),
           r.f(30), GRAY, anchor="rt")
    y += r.px(86)
    y = one_line_quotes(r, data, y)
    footer(r, data)
    return check(r, "月历", y)


# ---------------------------------------------------------------------------
# ② 倒计时版：把"还有几天"放大。黄历屏最实用的其实是这个

def v_countdown(r, data):
    cal = data["calendar"]
    y = r.margin
    dashed_clock(r, CLOCK_RECT)
    r.text((r.margin, y), f"{cal.solar_text}  {cal.weekday}", r.f(34), GRAY)
    y += r.px(56)
    r.text((r.margin, y), r.clip_text(f"今天 {cal.lunar_text} · {cal.ganzhi_year}"
           f"年属{cal.shengxiao}", r.f(44, bold=True), r.avail_w),
           r.f(44, bold=True), INK, strong=True)
    y += r.px(96)

    # 三条倒计时，按紧迫程度排：节气 → 最近的节日 → 本周末
    now = data["generated_at"]
    rows = [(f"距{cal.term_next}", cal.term_next_days, cal.term_next_date)]
    if cal.upcoming:
        # upcoming 形如「中秋 7 天后」，把数字取出来单独放大，别把整句塞进去
        parts = cal.upcoming.split()
        if len(parts) >= 2 and parts[1].isdigit():
            rows.append((f"距{parts[0]}", int(parts[1]), "最近的节日"))
    days_to_sun = (6 - now.weekday()) % 7 or 7
    sat = now + timedelta(days=days_to_sun)
    rows.append(("距周末", days_to_sun, f"{sat.month}月{sat.day}日"))

    # 行距按可用空间均摊。写死行距 + 把天气钉在底部，中间会空一大条 —— 看着像坏了
    rows = rows[:3]
    block = r.px(355)                       # 天气 + 行情 + 页脚
    area = r.h - r.margin - block - y
    row_h = area / len(rows)
    for label, n, note in rows:
        base = y + row_h / 2
        r.text((r.margin, base + r.px(4)), label, r.f(40, bold=True), INK_SOFT,
               strong=True)
        big = r.f(132, bold=True)
        num = str(n)
        r.text((r.margin + r.px(300), base - r.lh(big) / 2), num, big, INK,
               anchor="lt", strong=True)
        r.text((r.margin + r.px(300) + r.tw(num, big) + r.px(14), base - r.px(16)),
               "天", r.f(40), GRAY)
        if note:
            r.text((r.w - r.margin, base + r.px(14)), str(note), r.f(30), GRAY, anchor="rt")
        y += row_h
        r.rule(int(y) - r.px(10), thickness=2, color=GRAY_LIGHT)

    y += r.px(24)
    r.rule(y, thickness=r.px(4), color=INK)
    y += r.px(26)
    w = data.get("weather") or {}
    r.text((r.margin, y), f"{w.get('temp', '—')}° {w.get('desc') or ''}",
           r.f(46, bold=True), INK, strong=True)
    r.text((r.w - r.margin, y + r.px(14)),
           f"日出 {w.get('sunrise', '—')}  日落 {w.get('sunset', '—')}",
           r.f(30), GRAY, anchor="rt")
    y += r.px(74)
    y = one_line_quotes(r, data, y)
    footer(r, data)
    return check(r, "倒计时", y)


# ---------------------------------------------------------------------------
# ③ 天气详情版：竖排三天 + 全部气象指标。给"今天到底要不要带伞"的人看

def v_weather(r, data):
    cal = data["calendar"]
    w = data.get("weather") or {}
    y = r.margin
    dashed_clock(r, CLOCK_RECT)
    r.text((r.margin, y + r.px(8)), f"{cal.solar_day} {cal.weekday.replace('星期', '周')}"
           f" · {cal.lunar_text}", r.f(38, bold=True), INK_SOFT, strong=True)
    y += r.px(110)

    big = r.f(150, bold=True)
    temp_txt = f"{w.get('temp', '—')}°"
    r.text((r.margin, y), temp_txt, big, INK, anchor="lt", strong=True)
    # 描述跟在温度后面，图标固定放最右 —— 三个都挤在中间就会叠字（第一版就是这么糊的）
    after = r.margin + r.tw(temp_txt, big) + r.px(22)
    r.text((after, y + r.px(54)),
           r.clip_text(w.get("desc") or "", r.f(52, bold=True),
                       r.w - r.margin - after - r.px(140)),
           r.f(52, bold=True), INK_SOFT, strong=True)
    try:
        r.icon(r.w - r.margin - r.px(62), y + r.px(72), r.px(112),
               str(w.get("icon") or "cloud"))
    except Exception:
        pass
    r.text((r.margin, y + r.px(166)), r.clip_text(
        f"体感 {w.get('feels', '—')}° · 风 {w.get('wind_dir', '')}"
        f"{w.get('wind_level', '—')}级 · 气压 {w.get('pressure', '—')}hPa",
        r.f(32), r.avail_w), r.f(32), GRAY)
    y += r.px(214)

    cells = [("湿度", f"{w.get('humidity', '—')}%"), ("降水概率", f"{w.get('pop', '—')}%"),
             ("雨量", f"{w.get('precip', '—')}mm"), ("紫外线", str(w.get("uv", "—"))),
             ("空气", f"{(w.get('air') or {}).get('level', '—')} "
                     f"{(w.get('air') or {}).get('aqi', '')}"),
             ("PM2.5", str((w.get('air') or {}).get('pm25', '—')))]
    cw, ch = r.avail_w / 3, r.px(132)
    for i, (label, value) in enumerate(cells):
        cx = r.margin + (i % 3) * cw
        cy = y + (i // 3) * ch
        if i % 3:
            r.d.line([cx, cy, cx, cy + ch - r.px(16)], fill=GRAY_LIGHT, width=1)
        r.text((cx + r.px(10), cy + r.px(4)), label, r.f(26), GRAY)
        r.text((cx + r.px(10), cy + r.px(48)),
               r.clip_text(value, r.f(42, bold=True), int(cw) - r.px(24)),
               r.f(42, bold=True), INK, strong=True)
    y += ch * 2 + r.px(6)
    r.d.line([r.margin, y, r.w - r.margin, y], fill=GRAY_LIGHT, width=2)
    y += r.px(10)
    air_advice = (w.get("air") or {}).get("advice") or ""
    r.text((r.margin, y),
           f"日出 {w.get('sunrise', '—')} · 日落 {w.get('sunset', '—')}"
           + (f" · {air_advice}" if air_advice else ""), r.f(28), GRAY)
    y += r.px(54)

    r.rule(y, thickness=r.px(4), color=INK)
    y += r.px(22)
    # 逐日行距按剩余空间均摊，并且把行情钉在最上面那一行之下 —— 写死行距会让底部
    # 空出一大条，看着像没排完
    days = (w.get("forecast") or [])[:4]
    quote_h = r.px(150)                      # 行情那一行 + 它和逐日行之间的空隙
    room = r.h - r.margin - r.px(FOOT_H) - r.px(24) - quote_h
    rh = max(r.px(58), (room - y) / max(len(days), 1))
    for d in days:
        r.text((r.margin, y + rh / 2 - r.px(20)), str(d.get("label") or ""),
               r.f(34, bold=True), INK, strong=True)
        try:
            r.icon(r.margin + r.px(150), y + rh / 2 - r.px(2), r.px(38),
                   str(d.get("icon") or "cloud"))
        except Exception:
            pass
        r.text((r.margin + r.px(196), y + rh / 2 - r.px(20)),
               r.clip_text(str(d.get("desc") or ""), r.f(32), r.px(300)),
               r.f(32), INK_SOFT)
        r.text((r.w - r.margin - r.px(150), y + rh / 2 - r.px(22)),
               f"{d.get('high', '—')}° / {d.get('low', '—')}°", r.f(36, bold=True),
               INK, anchor="rt", strong=True)
        r.text((r.w - r.margin, y + rh / 2 - r.px(16)), f"雨 {d.get('pop', '—')}%",
               r.f(26), GRAY, anchor="rt")
        y += rh
    y = one_line_quotes(r, data, y, label_size=24, value_size=36)
    footer(r, data)
    return check(r, "天气详情", y)


# ---------------------------------------------------------------------------
# ④ 极简版：只剩四件事。远看和"不想被信息打扰"用

def v_minimal(r, data):
    cal = data["calendar"]
    w = data.get("weather") or {}
    y = r.margin
    dashed_clock(r, CLOCK_RECT)
    r.text((r.margin, y), f"{cal.solar_year} / {cal.solar_month:02d}", r.f(32), GRAY)
    y += r.px(70)
    big = r.f(300, bold=True)
    r.text((r.margin, y), str(cal.solar_day), big, INK, anchor="lt", strong=True)
    y += r.px(300)
    r.text((r.margin, y), cal.weekday, r.f(52, bold=True), INK_SOFT, strong=True)
    r.text((r.margin + r.tw(cal.weekday, r.f(52, bold=True)) + r.px(24), y + r.px(14)),
           cal.lunar_text, r.f(40), GRAY)
    y += r.px(120)
    r.rule(y, thickness=r.px(6), color=INK)
    y += r.px(44)
    r.text((r.margin, y), f"{w.get('temp', '—')}°  {w.get('desc') or ''}",
           r.f(84, bold=True), INK, strong=True)
    y += r.px(120)
    line = f"{cal.term_current}第{cal.term_current_days}天 · 宜 {' '.join(cal.yi[:3])}"
    r.text((r.margin, y), r.clip_text(line, r.f(34), r.avail_w), r.f(34), INK_SOFT)
    y += r.px(64)
    q = (data.get("quotes") or [])[:3]
    for i, item in enumerate(q):
        x = r.margin + i * (r.avail_w / 3)
        pct = item.get("pct")
        r.text((x, y), r.clip_text(str(item.get("name") or ""), r.f(26), int(r.avail_w / 3) - r.px(12)),
               r.f(26), GRAY)
        r.text((x, y + r.px(30)),
               f"{item.get('price', 0):,.0f}" if isinstance(item.get("price"), (int, float)) else "—",
               r.f(40, bold=True), INK, strong=True)
        if isinstance(pct, (int, float)):
            r.text((x + r.px(4), y + r.px(84)),
                   f"{'■' if pct >= 0 else '□'}{abs(pct):.2f}%",
                   r.f(26), INK_SOFT)
    y += r.px(124)
    footer(r, data)
    return check(r, "极简", y)


VARIANTS = [
    ("月历", v_month, "整月网格 + 今天高亮",
     "把「这个月」当主角。适合挂书房、办公室，抬头就能对上日程"),
    ("倒计时", v_countdown, "距节气 / 距节日 / 距周末，数字放大到 132",
     "黄历屏最实用的信息。适合关心假期和节气的人，一眼看到还剩几天"),
    ("天气详情", v_weather, "大温度 + 六项指标 + 竖排三天(带降水概率) + 日出日落",
     "解决「今天要不要带伞 / 能不能户外」。信息量最大，适合摆桌上"),
    ("极简", v_minimal, "只有日期、农历、温度、一句宜、三行行情",
     "当背景用，不抢注意力。远看最清楚，也最省电（局部刷新面积最小）"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="四版内容编排预览 + 一个对比网页")
    ap.add_argument("-c", "--config", default=str(ROOT / "dashboard" / "config.yaml"))
    ap.add_argument("--offline", action="store_true", help="不联网，用样例数据")
    args = ap.parse_args()

    global CLOCK_RECT
    base = Config.load(args.config)
    base.set("style.preset", "大字")
    data = sample_data() if args.offline else live_data(base)

    probe = R.Renderer(base, data)
    CLOCK_RECT = probe.clock_region(data["calendar"])
    print(f"数据：{'样例' if args.offline else '实时'} · 时钟留白区 {CLOCK_RECT}\n")

    OUT.mkdir(parents=True, exist_ok=True)
    shots = []
    for name, fn, note, fit in VARIANTS:
        r = R.Renderer(copy.deepcopy(base), data)
        msg, ok = fn(r, data)
        img = r.img.convert("L")
        png = OUT / f"{name}.png"
        img.save(png, format="PNG", optimize=True)
        shots.append((name, png.name, note, fit, msg, ok))
        print(f"  {'✅' if ok else '❌'} {name:5} → {png.name}   纵向 {msg}")

    cards = "".join(f"""
      <figure>
        <h2>{name}</h2>
        <p class="d">{note}</p>
        <a href="{png}"><img src="{png}" alt="{name}" loading="lazy"></a>
        <p class="c">适合：{fit}</p>
        <p class="{'ok' if ok else 'bad'}">纵向预算：{msg}（安全线 40px）</p>
      </figure>""" for name, png, note, fit, msg, ok in shots)

    (OUT / "index.html").write_text(f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>信息屏 · 内容编排四版</title>
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
 .d{{margin:0 0 12px;font-size:13px;color:#5b6470}}
 .c{{margin:10px 0 0;font-size:12.5px;color:#3c434b;background:#f5f6f7;
     border-radius:6px;padding:7px 10px}}
 img{{display:block;width:100%;height:auto;border:1px solid #cfd3d8;border-radius:4px}}
 .ok{{color:#1a7f37}} .bad{{color:#b42318;font-weight:600}}
 .tip{{margin:0 0 20px;padding:12px 16px;background:#fff;border:1px solid #d6d9dd;
       border-left:4px solid #b42318;border-radius:8px;font-size:13.5px}}
 code{{background:#f2f3f5;padding:1px 5px;border-radius:4px;font-size:12px}}
</style></head><body>
<header>
  <h1>信息屏 · 内容编排四版</h1>
  <p>这四版换的是<b>屏幕上放什么信息</b>，不是字号。都按 1072×1448、四级灰画的，
     文本全部按像素裁切过。</p>
</header>
<main>
  <p class="tip"><b>先说清楚这是提案，不是成品。</b>
     选中哪一版，我要在 <code>render.py</code> 里正式实现一遍（走它那套
     <code>*_height()</code> 预算 + 余量均摊），再过 <code>layout_check</code> 的
     40px 安全线。预览里我只保证了不压字、不越界。
     右上角虚线框是时钟留白区，Kindle 每分钟自己贴，图里本来就该空着。</p>
  <div class="grid">{cards}</div>
</main></body></html>
""", encoding="utf-8")

    print(f"\n打开对比：\n  {OUT / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
