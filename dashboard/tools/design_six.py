#!/usr/bin/env python3
"""推倒重来一轮（2026-09-22 夜）：六版高阶审美重构。

研究底稿（每条都落到版面上，不是贴在文档里）：
- Apple HIG：clarity / deference —— 层级只靠字号字重与留白，界面退后，内容居前；
  列表用发丝线 + 右对齐数值（设置页血统）。
- Müller-Brockmann《网格系统》/ 瑞士国际主义：严格模数网格、非对称平衡、
  左对齐右不齐、尺度对比做层级、发丝线是网格的表达不是装饰。
- 原研哉 / MUJI「空」：空不是删减后的简单，是容纳内容的容器；纸白是主角，墨最少。
- Tufte《定量信息的视觉显示》：数据墨水比最大化、小倍数、直接标注、
  预报画成温度区间条（NYT 天气条血统），删掉一切非数据墨。
- Dieter Rams / Braun：少却更好；仪表面板式的分区秩序，可读性即美学。
- TDC / D&AD 获奖编辑排印：folio 刊头线、kicker 小标题、字距拉开的小标签、
  栏线、悬挂缩进、封面目录式列表。

共同决定：回到设备原生五档灰（0/70/130/195/255，现网验证过）；
显示字 = 思源宋体 Heavy，正文 = Noto Sans SC 常规/粗；电量样式 B 画进图里；
全部左对齐起步，不再用居中堆叠（上一轮被否的就是它）。

用法：python dashboard/tools/design_six.py
产物：.workbuddy/_directions/六-*.png + six.html
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

from design_directions import OUT, W, H, Fonts, Sheet          # noqa: E402
from design_bw import worst_bw, lunar_zodiac, cells_pick      # noqa: E402

INK, SOFT, GRAY, LIGHT, PAPER = 0, 70, 130, 195, 255
M = 64
X1 = W - M
CW = X1 - M
TOP = 112                      # 电量矩形 (814,40)-(1016,96) 下沿 +16
BAT = (814, 40, 1016, 96)


def rule(sh, y, x0, x1, t=2, c=LIGHT):
    sh.d.rectangle([x0, y, x1, y + t - 1], fill=c)


def vrule(sh, x, y0, y1, t=2, c=LIGHT):
    sh.d.rectangle([x, y0, x + t - 1, y1], fill=c)


def tracked(sh, x, y, s, font, fill=GRAY, tr=8):
    """字距拉开的小标签 —— 编辑排印的 kicker 血统。返回占用宽。"""
    cx = x
    for ch in s:
        sh.t((cx, y), ch, font, fill)
        cx += sh.tw(ch, font) + tr
    return cx - tr - x


def sq(sh, x, y, s=12, filled=True, c=INK):
    if filled:
        sh.d.rectangle([x, y, x + s, y + s], fill=c)
    else:
        sh.d.rectangle([x, y, x + s, y + s], outline=c, width=3)


def val_font(sh, v: str, size: int):
    """本轮不用等宽字：值统一黑体（混中文也安全）。"""
    return sh.f.sans(size)


def battery(sh, level=86):
    x1, y0, x2, y1 = BAT
    gf = sh.f.sans(38, True)
    sh.t((x2, y0 + 2), f"{level}%", gf, INK, anchor="rt")
    by = y1 - 14
    sh.d.rectangle([x1, by, x2, by + 10], fill=LIGHT)
    sh.d.rectangle([x1, by, x1 + (x2 - x1) * level // 100, by + 10], fill=INK)


def big(sh, x, top, bottom, s, size, anchor="lt", fill=INK):
    """宋体 Heavy 大数字；° 黑体小字单画（宋体没有 ° 字形）。"""
    digits = s.rstrip("°")
    deg = s[len(digits):]
    f = sh.f.serif(size)
    w = sh.ink(x, top, bottom, digits, f, fill, anchor=anchor)
    if deg:
        df = sh.f.sans(int(size * 0.42), True)
        dx = (x + w / 2 + 4) if anchor == "mt" else (x + w + 4)
        sh.ink(dx, top, top + int((bottom - top) * 0.36), deg, df, fill)
        w += 8 + sh.tw(deg, df)
    return w


def big_w(sh, s, size):
    digits = s.rstrip("°")
    deg = s[len(digits):]
    w = sh.tw(digits, sh.f.serif(size))
    if deg:
        w += 8 + sh.tw(deg, sh.f.sans(int(size * 0.42), True))
    return w


def fest_lines(sh, cal, y, fs=30, bold_fs=34, x=M):
    """节日 / 倒计时 / 调休：几行画几行，无则零高度。"""
    for name in cal["fest_today"][:2]:
        sq(sh, x, y + bold_fs // 2 - 6, 12)
        sh.ink(x + 22, y, y + bold_fs + 6, name, sh.f.sans(bold_fs, True), INK)
        y += bold_fs + 10
    for line in (cal["countdown"], cal["tiaoxiu"]):
        if line:
            sh.ink(x, y, y + fs + 6, line, sh.f.sans(fs), INK)
            y += fs + 8
    return y


def warn_lines(sh, wx, y, fs=28, x=M):
    for t in wx["warn"][:2]:
        sq(sh, x, y + fs // 2 - 6, 12)
        sh.ink(x + 22, y, y + fs + 6, sh.clip(t, sh.f.sans(fs), X1 - x - 22),
               sh.f.sans(fs), INK)
        y += fs + 10
    return y


def footer(sh, d, y):
    rule(sh, y, M, X1, 2, LIGHT)
    sh.t((M, y + 16), d["foot_l"], sh.f.sans(26), GRAY)
    sh.t((M, y + 50), d["foot_m"], sh.f.sans(26), GRAY)
    return y + 50 + 40


# ---- 一 · 瑞士网格 --------------------------------------------------------

def lay_swiss(sh, d, y, extra=0):
    cal, wx = d["cal"], d["wx"]
    g = 24
    col = (CW - 3 * g) // 4
    c = [M + i * (col + g) for i in range(4)]

    tracked(sh, M, y, "DAILY · 每日信息屏", sh.f.sans(24), GRAY, 6)
    y += 40
    rule(sh, y, M, X1, 2, INK)
    e = spread(extra, 3)
    y += 28 + e[0]
    big(sh, M, y, y + 270, cal["day"], 270)
    rx = c[2]
    sh.icons.icon(rx + 48, y + 90, 92, wx["icon"], SOFT)
    big(sh, rx + 116, y + 8, y + 8 + 160, wx["temp"], 160)
    sh.ink(rx + 116, y + 180, y + 220, wx["desc"], sh.f.sans(38), INK)
    ly = y + 234
    lunar_zodiac(sh, ly, cal, 34, x=rx)
    ly += 46
    ly = fest_lines(sh, cal, ly, fs=26, bold_fs=30, x=rx)
    y = max(y + 270, ly) + 28
    rule(sh, y, M, X1, 2, LIGHT)
    y += 20
    y = warn_lines(sh, wx, y, fs=28)
    if wx["warn"]:
        y += 12
    r1, r2, r3 = 64 + 8, 64 + 8 + 30 + 8, 64 + 8 + 30 + 8 + 30 + 8
    for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:4]):
        gx = c[i]
        sh.icons.icon(gx + 36, y + 32, 64, kind, SOFT)
        sh.ink(gx, y + r1, y + r1 + 36, label, sh.f.sans(30), GRAY)
        sh.ink(gx, y + r2, y + r2 + 36, sh.clip(desc, sh.f.sans(30), col),
               sh.f.sans(30), INK)
        sh.ink(gx, y + r3, y + r3 + 36, hi + " / " + lo, sh.f.sans(30), SOFT)
    y += r3 + 36 + 24 + e[1]
    rule(sh, y, M, X1, 2, LIGHT)
    y += 20
    for i, (k, v) in enumerate(cells_pick(wx, 6)):
        gx = c[i % 4] if i < 4 else c[i - 4]
        gy = y + (i // 4) * 60
        sh.ink(gx, gy, gy + 30, k, sh.f.sans(26), GRAY)
        sh.ink(gx + 130, gy, gy + 30, v, val_font(sh, v, 26), INK)
    y += 60 * 2 + 20 + e[2]
    rule(sh, y, M, X1, 2, LIGHT)
    y += 18
    for i, (name, price, pct) in enumerate(d["quotes"][:3]):
        gx = c[i] if i < 2 else c[2]
        gx = M + i * (CW // 3)
        sh.ink(gx, y, y + 30, name, sh.f.sans(26), GRAY)
        sh.ink(gx, y + 34, y + 34 + 52, price, sh.f.sans(52, True), INK)
        pw = sh.tw(price, sh.f.sans(52, True))
        txt = f"{pct:+.2f}%"
        pf = sh.f.sans(28)
        sq(sh, gx + pw + 16, y + 52, 14, filled=pct >= 0)
        sh.ink(gx + pw + 38, y + 40, y + 40 + 34, txt, pf, INK)
    y += 34 + 52 + 6
    return y


# ---- 二 · Apple Clarity ---------------------------------------------------

def lay_apple(sh, d, y, extra=0):
    cal, wx = d["cal"], d["wx"]
    LM = 96
    LX1 = W - LM
    sh.icons.icon(LM + 55, y + 80, 100, wx["icon"], SOFT)
    big(sh, LM + 150, y, y + 220, wx["temp"], 220)
    tx = LM + 150 + big_w(sh, wx["temp"], 220) + 28
    sh.ink(tx, y + 130, y + 130 + 44, wx["desc"], sh.f.sans(44), GRAY)
    y += 220 + 32
    sh.ink(LM, y, y + 64, f"{cal['month']} {cal['day']} 日 · {cal['week']}",
           sh.f.sans(52, True), INK)
    y += 64 + 14
    lunar_zodiac(sh, y, cal, 34, x=LM)
    y += 46
    y = fest_lines(sh, cal, y, fs=30, bold_fs=34, x=LM)
    y += 40
    cw4 = (LX1 - LM) // 4
    for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:4]):
        gx = LM + i * cw4 + cw4 // 2
        sh.ink(gx, y, y + 30, label, sh.f.sans(28), GRAY, anchor="mt")
        sh.icons.icon(gx, y + 36 + 40, 76, kind, SOFT)
        sh.ink(gx, y + 122, y + 152, sh.clip(desc, sh.f.sans(26), cw4 - 8),
               sh.f.sans(26), INK, anchor="mt")
        sh.ink(gx, y + 150, y + 182, hi + " / " + lo, sh.f.sans(30), INK, anchor="mt")
    y += 182 + 36
    if wx["warn"]:
        y = warn_lines(sh, wx, y, fs=28, x=LM)
        y += 24
    for i, (name, price, pct) in enumerate(d["quotes"][:3]):
        rule(sh, y, LM, LX1, 1, LIGHT)
        y += 10
        sh.ink(LM, y, y + 34, name, sh.f.sans(30), INK)
        txt = f"{pct:+.2f}%"
        pf = sh.f.sans(30)
        tw = sh.tw(txt, pf)
        pw = sh.tw(price, sh.f.sans(34, True))
        sh.ink(LX1, y, y + 38, txt + "  " , pf, INK if pct >= 0 else GRAY, anchor="rt")
        sh.ink(LX1 - tw - 34, y - 2, y + 36, price, sh.f.sans(34, True), INK,
               anchor="rt")
        sq(sh, LX1 - tw - 34 - pw - 30, y + 12, 14, filled=pct >= 0)
        y += 38 + 10
    rule(sh, y, LM, LX1, 1, LIGHT)
    y += 20
    sh.ink(LM, y, y + 30, " · ".join(f"{k} {v}" for k, v in cells_pick(wx, 6)),
           sh.f.sans(26), GRAY)
    return y + 30


# ---- 三 · MUJI 空 ---------------------------------------------------------

def lay_muji(sh, d, y, extra=0):
    cal, wx = d["cal"], d["wx"]
    LM = 88
    big(sh, LM, y, y + 360, cal["day"], 360)
    dw = big_w(sh, cal["day"], 360)
    big(sh, LM + dw + 60, y + 170, y + 170 + 180, wx["temp"], 180)
    twd = big_w(sh, wx["temp"], 180)
    sh.ink(LM + dw + 60 + twd + 24, y + 250, y + 250 + 40, wx["desc"],
           sh.f.sans(40), GRAY)
    y += 360 + 24
    lunar_zodiac(sh, y, cal, 40, x=LM)
    y += 40 + 64 + extra
    lh = 44
    def line(s, fill=GRAY, bold=False, marker=False):
        nonlocal y
        x = LM + (22 if marker else 0)
        if marker:
            sq(sh, LM, y + 14, 12)
        sh.ink(x, y, y + lh - 18, s, sh.f.sans(28, bold), fill)
        y += lh

    for name in cal["fest_today"][:1]:
        line(name, INK, True, marker=True)
    for s in (cal["countdown"], cal["tiaoxiu"]):
        if s:
            line(s, INK)
    for t in wx["warn"][:2]:
        line(sh.clip(t, sh.f.sans(28), X1 - LM), INK, marker=True)
    for label, kind, desc, hi, lo in wx["fc"][:4]:
        line(f"{label}　{desc}　{hi} / {lo}", GRAY)
    line(" · ".join(f"{k} {v}" for k, v in cells_pick(wx, 6)), GRAY)
    for name, price, pct in d["quotes"][:3]:
        line(f"{name}　{price}　{pct:+.2f}%", GRAY)
    return y


# ---- 四 · 编辑 Folio ------------------------------------------------------

def lay_folio(sh, d, y, extra=0):
    cal, wx = d["cal"], d["wx"]
    rule(sh, y, M, X1, 2, INK)
    y += 12
    tracked(sh, M, y, f"{cal['week']} · {cal['month']} {cal['day']} 日 · "
            f"{d['place']} · 第 268 期", sh.f.sans(26), INK, 4)
    y += 40
    rule(sh, y, M, X1, 2, INK)
    e = spread(extra, 4)
    y += 24 + e[0]
    big(sh, M, y, y + 380, cal["day"], 380)
    dw = big_w(sh, cal["day"], 380)
    vx = M + dw + 44
    rx = vx + 44
    ry = y + 4
    tracked(sh, rx, ry, "天气 WEATHER", sh.f.sans(24), GRAY, 6)
    ry += 36
    sh.icons.icon(rx + 44, ry + 75, 88, wx["icon"], SOFT)
    big(sh, rx + 100, ry, ry + 150, wx["temp"], 150)
    sh.ink(rx, ry + 154, ry + 186, wx["desc"], sh.f.sans(32), INK)
    ry += 150 + 40
    tracked(sh, rx, ry, "历法 ALMANAC", sh.f.sans(24), GRAY, 6)
    ry += 36
    lunar_zodiac(sh, ry, cal, 32, x=rx)
    ry += 32 + 14
    tracked(sh, rx, ry, "节日 FESTIVAL", sh.f.sans(24), GRAY, 6)
    ry += 36
    ry = fest_lines(sh, cal, ry, fs=26, bold_fs=30, x=rx)
    band = max(380, ry - y)
    vrule(sh, vx, y, y + band, 2, INK)
    y += band + 24 + e[1]
    rule(sh, y, M, X1, 2, LIGHT)
    y += 20 + e[3]
    y = warn_lines(sh, wx, y, fs=28)
    if wx["warn"]:
        y += 14
    for label, kind, desc, hi, lo in wx["fc"][:4]:
        rule(sh, y, M, X1, 1, LIGHT)
        y += 12
        sh.ink(M, y, y + 34, label, sh.f.sans(32), INK)
        sh.icons.icon(M + 160, y + 26, 52, kind, SOFT)
        sh.ink(M + 250, y, y + 34, desc, sh.f.sans(32), INK)
        sh.ink(X1, y, y + 34, hi + " / " + lo, sh.f.sans(32), SOFT, anchor="rt")
        y += 34 + 12
    rule(sh, y, M, X1, 1, LIGHT)
    y += 16 + e[2]
    for name, price, pct in d["quotes"][:3]:
        sh.ink(M, y, y + 32, name, sh.f.sans(28), GRAY)
        txt = f"{pct:+.2f}%"
        pf = sh.f.sans(28)
        tw = sh.tw(txt, pf)
        sh.ink(X1, y, y + 32, txt, pf, INK if pct >= 0 else GRAY, anchor="rt")
        sh.ink(X1 - tw - 32, y, y + 32, price, sh.f.sans(34, True), INK, anchor="rt")
        pw = sh.tw(price, sh.f.sans(34, True))
        sq(sh, X1 - tw - 32 - pw - 26, y + 10, 13, filled=pct >= 0)
        y += 32 + 12
    return y


# ---- 五 · Tufte 数据条 ----------------------------------------------------

def lay_tufte(sh, d, y, extra=0):
    cal, wx = d["cal"], d["wx"]
    big(sh, M, y, y + 300, cal["day"], 300)
    dw = big_w(sh, cal["day"], 300)
    lx = M + dw + 40
    lunar_zodiac(sh, y + 10, cal, 34, x=lx)
    sh.ink(lx, y + 60, y + 96, " · ".join([cal["month"], cal["week"], d["place"]]),
           sh.f.sans(28), GRAY)
    sh.icons.icon(lx + 36, y + 240, 84, wx["icon"], SOFT)
    big(sh, lx + 110, y + 180, y + 180 + 150, wx["temp"], 150)
    tx = lx + 110 + big_w(sh, wx["temp"], 150) + 20
    sh.ink(tx, y + 230, y + 266, wx["desc"], sh.f.sans(34), INK)
    e = spread(extra, 3)
    y += 300 + 18 + e[0]
    y = fest_lines(sh, cal, y, fs=30, bold_fs=34)
    y = warn_lines(sh, wx, y, fs=30)
    y += 18
    rule(sh, y, M, X1, 2, INK)
    y += 28 + e[1]
    lo_all, hi_all = 18, 36
    px0, px1 = M + 150, M + 640
    def mx(t):
        return px0 + (int(t.rstrip("°")) - lo_all) * (px1 - px0) // (hi_all - lo_all)
    for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:4]):
        today = i == 0
        sh.ink(M, y, y + 40, label, sh.f.sans(34, today), INK if today else GRAY)
        x0, x1 = mx(lo), mx(hi)
        sh.d.rectangle([x0, y + 14, x1, y + 24], fill=INK if today else LIGHT)
        sh.d.rectangle([x0 - 2, y + 7, x0 + 2, y + 31], fill=INK)
        sh.d.rectangle([x1 - 2, y + 7, x1 + 2, y + 31], fill=INK)
        sh.ink(x0 - 14, y, y + 40, lo, sh.f.sans(30), SOFT, anchor="rt")
        sh.ink(x1 + 14, y, y + 40, hi, sh.f.sans(30), INK)
        sh.ink(px1 + 60, y, y + 40, desc, sh.f.sans(30), INK if today else GRAY)
        y += 40 + 22
    rule(sh, y, M, X1, 2, INK)
    y += 18
    sh.ink(M, y, y + 30, " · ".join(f"{k} {v}" for k, v in cells_pick(wx, 6)),
           sh.f.sans(26), GRAY)
    y += 30 + 22
    for name, price, pct in d["quotes"][:3]:
        sh.ink(M, y, y + 36, name, sh.f.sans(30), GRAY)
        txt = f"{pct:+.2f}%"
        pf = sh.f.sans(30)
        tw = sh.tw(txt, pf)
        sh.ink(X1, y, y + 36, txt, pf, INK if pct >= 0 else GRAY, anchor="rt")
        sh.ink(X1 - tw - 32, y, y + 36, price, sh.f.sans(36, True), INK, anchor="rt")
        pw = sh.tw(price, sh.f.sans(36, True))
        sq(sh, X1 - tw - 32 - pw - 26, y + 12, 13, filled=pct >= 0)
        y += 36 + 14
    return y


# ---- 六 · Braun 仪表 ------------------------------------------------------

def lay_braun(sh, d, y, extra=0):
    cal, wx = d["cal"], d["wx"]
    e = spread(extra, 6)
    tracked(sh, M, y, "日期 DATE", sh.f.sans(24), GRAY, 6)
    tracked(sh, M + 400, y, "历法 ALMANAC", sh.f.sans(24), GRAY, 6)
    tracked(sh, M + 704, y, "天气 WEATHER", sh.f.sans(24), GRAY, 6)
    y += 38 + e[0]
    rule(sh, y, M, X1, 3, INK)
    y += 20 + e[1]
    big(sh, M, y, y + 300, cal["day"], 300)
    lx = M + 400
    sh.ink(lx, y + 10, y + 42, " · ".join([cal["month"], cal["week"]]),
           sh.f.sans(26), INK)
    lunar_zodiac(sh, y + 56, cal, 28, x=lx)
    fest_lines(sh, cal, y + 104, fs=26, bold_fs=30, x=lx)
    wx0 = M + 704
    sh.icons.icon(wx0 + 45, y + 45, 90, wx["icon"], INK)
    sh.ink(wx0 + 106, y + 24, y + 60, wx["desc"], sh.f.sans(30), INK)
    big(sh, wx0, y + 100, y + 280, wx["temp"], 180)
    y += 300 + 22
    rule(sh, y, M, X1, 3, INK)
    y += 16 + e[2]
    tracked(sh, M, y, "预报 FORECAST", sh.f.sans(24), GRAY, 6)
    y += 36
    cw4 = CW // 4
    for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:4]):
        gx = M + i * cw4
        if i:
            vrule(sh, gx - 12, y - 8, y + 176, 2, LIGHT)
        sh.icons.icon(gx + 42, y + 42, 84, kind, INK)
        sh.ink(gx + 100, y + 16, y + 52, label, sh.f.sans(32), INK)
        sh.ink(gx, y + 100, y + 134, sh.clip(desc, sh.f.sans(28), cw4 - 16),
               sh.f.sans(28), GRAY)
        sh.ink(gx, y + 140, y + 176, hi + " / " + lo, sh.f.sans(34, True), INK)
    y += 180 + 20
    rule(sh, y, M, X1, 3, INK)
    y += 16 + e[3]
    y = warn_lines(sh, wx, y, fs=28)
    if wx["warn"]:
        y += 10
    sh.ink(M, y, y + 30, " · ".join(f"{k} {v}" for k, v in cells_pick(wx, 6)),
           sh.f.sans(26), GRAY)
    y += 30 + 18 + e[4]
    rule(sh, y, M, X1, 3, INK)
    y += 16 + e[5]
    tracked(sh, M, y, "行情 MARKETS", sh.f.sans(24), GRAY, 6)
    y += 36
    cw3 = CW // 3
    for i, (name, price, pct) in enumerate(d["quotes"][:3]):
        gx = M + i * cw3
        if i:
            vrule(sh, gx - 12, y - 8, y + 116, 2, LIGHT)
        sh.ink(gx, y, y + 30, name, sh.f.sans(26), GRAY)
        sh.ink(gx, y + 34, y + 74, price, sh.f.sans(38, True), INK)
        txt = f"{pct:+.2f}%"
        pf = sh.f.sans(26)
        pw = sh.tw(price, sh.f.sans(38, True))
        sq(sh, gx + pw + 14, y + 48, 13, filled=pct >= 0)
        sh.ink(gx + pw + 34, y + 42, y + 72, txt, pf, INK)
    return y + 74 + 6


# ---- 数据条改进轮（2026-09-22 夜二）：日期节日 / 天气 分带，预报行加回图标 ----
# 用户反馈：数据条不错，但日期节日和天气要分开，天气图标要保留。
# 三版只差"分开"的手法：A 全发丝线分带 / B 日期与天气之间用大留白分 /
# C 日期带与天气带各自双栏（信息密度最高）。

STRIPS = {
    "A": "A · 线分带：每段之间一条发丝黑线：日期+节日 / 天气 / 预报数据条 / 行情，四段秩序分明。",
    "B": "B · 留白分带：日期+节日 与 天气 之间不用线、用 56px 留白分（MUJI 手法），其余段仍用发丝线。",
    "C": "C · 双栏分带：日期带 = 左日期右历法节日；天气带 = 左当前天气右数据格；密度最高、段最矮。",
}


def _strip_fc(sh, wx, y, pitch=46, icon_sz=44):
    """预报数据条 + 每行图标：标签 / 图标 / 区间条 / 高低 / 描述。"""
    px0, px1 = M + 150, M + 560
    lo_all, hi_all = 18, 36
    def mx(t):
        return px0 + (int(t.rstrip("°")) - lo_all) * (px1 - px0) // (hi_all - lo_all)
    for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:4]):
        today = i == 0
        sh.ink(M, y, y + pitch, label, sh.f.sans(30, today), INK if today else GRAY)
        sh.icons.icon(M + 102, y + pitch // 2, icon_sz, kind, SOFT if not today else INK)
        x0, x1 = mx(lo), mx(hi)
        sh.d.rectangle([x0, y + 17, x1, y + 26], fill=INK if today else LIGHT)
        sh.d.rectangle([x0 - 2, y + 10, x0 + 2, y + 33], fill=INK)
        sh.d.rectangle([x1 - 2, y + 10, x1 + 2, y + 33], fill=INK)
        sh.ink(x0 - 12, y, y + 44, lo, sh.f.sans(28), SOFT, anchor="rt")
        sh.ink(x1 + 12, y, y + 44, hi, sh.f.sans(28), INK)
        sh.ink(px1 + 90, y, y + pitch, desc, sh.f.sans(28), INK if today else GRAY)
        y += pitch
    return y


def _strip_markets(sh, quotes, y, price_fs=38):
    cw3 = CW // 3
    for i, (name, price, pct) in enumerate(quotes[:3]):
        gx = M + i * cw3
        if i:
            vrule(sh, gx - 12, y - 6, y + 70, 2, LIGHT)
        sh.ink(gx, y, y + 28, name, sh.f.sans(26), GRAY)
        sh.ink(gx, y + 32, y + 32 + price_fs + 4, price, sh.f.sans(price_fs, True), INK)
        txt = f"{pct:+.2f}%"
        pf = sh.f.sans(26)
        pw = sh.tw(price, sh.f.sans(price_fs, True))
        sq(sh, gx + pw + 14, y + 48, 13, filled=pct >= 0)
        sh.ink(gx + pw + 34, y + 42, y + 74, txt, pf, INK)
    return y + 30 + price_fs + 4


def lay_strip(sh, d, y, extra=0, mode="A"):
    cal, wx = d["cal"], d["wx"]
    n = 4
    e = spread(extra, n)
    pitch, icon_sz = (50, 48) if mode == "C" else (46, 44)
    price_fs = 42 if mode == "C" else 38

    tracked(sh, M, y, "日期 DATE", sh.f.sans(24), GRAY, 6)
    y += 30
    if mode == "C":
        big(sh, M, y, y + 260, cal["day"], 260)
        dw = big_w(sh, cal["day"], 260)
        rx = M + dw + 40
        vrule(sh, M + dw + 20, y, y + 260, 2, LIGHT)
        sh.ink(rx, y + 6, y + 38, " · ".join([cal["month"], cal["week"], d["place"]]),
               sh.f.sans(26), GRAY)
        lunar_zodiac(sh, y + 50, cal, 32, x=rx)
        fest_lines(sh, cal, y + 100, fs=26, bold_fs=30, x=rx)
        y += 260
    else:
        big(sh, M, y, y + 230, cal["day"], 230)
        dw = big_w(sh, cal["day"], 230)
        rx = M + dw + 40
        sh.ink(rx, y + 30, y + 62, " · ".join([cal["month"], cal["week"], d["place"]]),
               sh.f.sans(26), GRAY)
        lunar_zodiac(sh, y + 72, cal, 32, x=rx)
        y += 230 + 12
        y = fest_lines(sh, cal, y, fs=26, bold_fs=30)
    if mode == "B":
        y += 40 + e[0]
    else:
        y += 18 + e[0]
        rule(sh, y, M, X1, 2, INK)
        y += 18

    tracked(sh, M, y, "天气 WEATHER", sh.f.sans(24), GRAY, 6)
    y += 30
    if mode == "C":
        sh.icons.icon(M + 60, y + 75, 120, wx["icon"], SOFT)
        big(sh, M + 150, y, y + 150, wx["temp"], 150)
        tx = M + 150 + big_w(sh, wx["temp"], 150) + 20
        sh.ink(tx, y + 66, y + 98, wx["desc"], sh.f.sans(30), INK)
        cx0 = M + 520
        for i, (k, v) in enumerate(cells_pick(wx, 6)):
            gx = cx0 + (i % 3) * 160
            gy = y + (i // 3) * 62
            sh.ink(gx, gy, gy + 26, k, sh.f.sans(24), GRAY)
            sh.ink(gx, gy + 28, gy + 56, v, val_font(sh, v, 26), INK)
        y += 132 + 10
        y = warn_lines(sh, wx, y, fs=26)
    else:
        sh.icons.icon(M + 50, y + 66, 100, wx["icon"], SOFT)
        big(sh, M + 130, y, y + 132, wx["temp"], 132)
        tx = M + 130 + big_w(sh, wx["temp"], 132) + 20
        sh.ink(tx, y + 60, y + 92, wx["desc"], sh.f.sans(30), INK)
        y += 132 + 10
        sh.ink(M, y, y + 28, " · ".join(f"{k} {v}" for k, v in cells_pick(wx, 6)),
               sh.f.sans(26), GRAY)
        y += 28 + 10
        y = warn_lines(sh, wx, y, fs=26)
    y += 18 + e[1]
    rule(sh, y, M, X1, 2, INK)
    y += 18

    tracked(sh, M, y, "预报 FORECAST", sh.f.sans(24), GRAY, 6)
    y += 30
    y = _strip_fc(sh, wx, y, pitch, icon_sz)
    y += 12 + e[2]
    rule(sh, y, M, X1, 2, INK)
    y += 18
    tracked(sh, M, y, "行情 MARKETS", sh.f.sans(24), GRAY, 6)
    y += 30
    y = _strip_markets(sh, d["quotes"], y, price_fs)
    y += e[3]
    return y


# ---- C 改进轮（2026-09-22 夜三）：双栏分带 + 大图标预报 + 基准网格行距 --------
# 用户：C 可以，但数据条不要、预报要大图标；行距按国际获奖排印标准重排。
# 做法：4px 基准单位；行盒一律取 8 的倍数（40 / 48 / 56）≈ 字号×1.5；
# 间距三档递进（Gestalt 邻近性）：组内 gi < 块间 gb < 带间 gband。
# 三版只差节奏：C1 标准 / C2 疏朗（Kinfolk 气）/ C3 紧凑（NYT 气）。

CP = {
    "1": dict(day=240, temp=136, icon=104, fc_icon=96, gi=8, gb=20, gband=24,
              note="C1 · 标准节奏：行盒一律 8 的倍数（40/48/56），组内 8 < 块间 20 < 带间 28。"),
    "2": dict(day=176, temp=112, icon=88, fc_icon=64, gi=8, gb=24, gband=40,
              note="C2 · 疏朗节奏：带间 40、块间 24，字阶降一档，留白当结构（Kinfolk 气）。"),
    "3": dict(day=272, temp=152, icon=120, fc_icon=120, gi=4, gb=16, gband=24,
              note="C3 · 紧凑节奏：带间 24、组内 4，字阶升一档，密度优先（NYT 气）。"),
}


def lay_cgrid(sh, d, y, extra=0, P=None):
    cal, wx = d["cal"], d["wx"]
    gi, gb, gband = P["gi"], P["gb"], P["gband"]
    e = spread(extra, 3)

    # -- 带一：日期（左）+ 历法/节日（右）
    rh = 40 + 48 + 2 * gi
    if cal["fest_today"]:
        rh += 48 + gi
    for line in (cal["countdown"], cal["tiaoxiu"]):
        if line:
            rh += 40 + gi
    band1 = max(P["day"], rh - gi)
    big(sh, M, y, y + P["day"], cal["day"], P["day"])
    dw = big_w(sh, cal["day"], P["day"])
    rx = M + dw + 40
    vrule(sh, M + dw + 20, y, y + band1, 2, LIGHT)
    ry = y
    sh.ink(rx, ry, ry + 40, " · ".join([cal["month"], cal["week"], d["place"]]),
           sh.f.sans(26), SOFT)
    ry += 40 + gi
    lunar_zodiac(sh, ry, cal, 32, x=rx)
    ry += 48 + gi
    for name in cal["fest_today"][:1]:
        sq(sh, rx, ry + 18, 14)
        sh.ink(rx + 24, ry, ry + 48, name, sh.f.sans(32, True), INK)
        ry += 48 + gi
    for line in (cal["countdown"], cal["tiaoxiu"]):
        if line:
            sh.ink(rx, ry, ry + 40, line, sh.f.sans(28), INK)
            ry += 40 + gi
    y += band1 + gband // 2
    rule(sh, y, M, X1, 2, INK)
    y += gband // 2 + e[0]

    # -- 带二：当前天气（左）+ 数据格一行（右）+ 预警（通栏）
    tracked(sh, M, y, "天气 WEATHER", sh.f.sans(24), GRAY, 6)
    y += 32 + gi
    sh.icons.icon(M + P["icon"] / 2, y + P["temp"] * 0.42, P["icon"], wx["icon"], SOFT)
    tx = M + P["icon"] + 24
    tx += big(sh, tx, y, y + P["temp"], wx["temp"], P["temp"]) + 24
    sh.ink(tx, y + P["temp"] * 0.42, y + P["temp"] * 0.42 + 48, wx["desc"],
           sh.f.sans(32), INK)
    # 数据格 2 列 × 3 行：栏宽按最宽格「空气 轻度 118」(158px) 实测留出 172
    cx0 = M + 600
    pitch = (X1 - cx0) // 2
    gh = 3 * 40 + 2 * gi                    # 格区实高
    zone = max(P["temp"], gh)
    gy0 = y + (zone - gh) // 2              # 与温度块垂直居中
    vrule(sh, cx0 - 28, y, y + zone, 2, LIGHT)
    for i, (k, v) in enumerate(cells_pick(wx, 6)):
        gx = cx0 + (i % 2) * pitch
        gy = gy0 + (i // 2) * (40 + gi)
        lw = sh.ink(gx, gy, gy + 40, k, sh.f.sans(24), GRAY)
        sh.ink(gx + lw + 10, gy, gy + 40, v, val_font(sh, v, 26), INK)
    y += zone + gi
    for t in wx["warn"][:2]:
        sq(sh, M, y + 14, 13)
        sh.ink(M + 24, y, y + 40, sh.clip(t, sh.f.sans(28), X1 - M - 24),
               sh.f.sans(28), INK)
        y += 40 + gi
    y += gb - gi + gband // 2
    rule(sh, y, M, X1, 2, INK)
    y += gband // 2 + e[1]

    # -- 带三：预报四联，大图标
    tracked(sh, M, y, "预报 FORECAST", sh.f.sans(24), GRAY, 6)
    y += 32 + gi
    cw4 = CW // 4
    for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:4]):
        gx = M + i * cw4 + cw4 // 2
        today = i == 0
        sh.icons.icon(gx, y + P["fc_icon"] // 2 + 12, P["fc_icon"], kind,
                      INK if today else SOFT, align_cloud=True)
        ly = y + P["fc_icon"] + 20
        sh.ink(gx, ly, ly + 40, label,
               sh.f.sans(28, today), INK if today else GRAY, anchor="mt")
        sh.ink(gx, ly + 40 + gi, ly + 80 + gi,
               sh.clip(desc, sh.f.sans(28), cw4 - 12), sh.f.sans(28), INK,
               anchor="mt")
        sh.ink(gx, ly + 80 + 2 * gi, ly + 120 + 2 * gi,
               hi + " / " + lo, sh.f.sans(28), SOFT, anchor="mt")
    y += P["fc_icon"] + 140 + 2 * gi + gb + gband // 2
    rule(sh, y, M, X1, 2, INK)
    y += gband // 2 + e[2]

    # -- 带四：行情三栏
    tracked(sh, M, y, "行情 MARKETS", sh.f.sans(24), GRAY, 6)
    y += 32 + gi
    cw3 = CW // 3
    for i, (name, price, pct) in enumerate(d["quotes"][:3]):
        gx = M + i * cw3
        py = y + 40 + gi
        if i:
            vrule(sh, gx - 12, y - 8, py + 56, 2, LIGHT)
        sh.ink(gx, y, y + 40, name, sh.f.sans(26), GRAY)
        sh.ink(gx, py, py + 56, price, sh.f.sans(36, True), INK)
        txt = f"{pct:+.2f}%"
        pf = sh.f.sans(26)
        pw = sh.tw(price, sh.f.sans(36, True))
        sq(sh, gx + pw + 14, py + 22, 13, filled=pct >= 0)
        sh.ink(gx + pw + 34, py + 8, py + 48, txt, pf, INK)
    return py + 56


LAYS = [("一 · 瑞士网格", lay_swiss), ("二 · Apple Clarity", lay_apple),
        ("三 · MUJI 空", lay_muji), ("四 · 编辑 Folio", lay_folio),
        ("五 · Tufte 数据条", lay_tufte), ("六 · Braun 仪表", lay_braun)]

NOTES = {
    "一 · 瑞士网格": "Müller-Brockmann：4 栏模数网格（栏宽 218 / 槽 24），全部左对齐右不齐；"
        "层级只靠尺度对比（日期 300 vs 正文 26–30）；发丝线是网格的表达：粗黑线只在报头下沿一处。",
    "二 · Apple Clarity": "HIG clarity/deference：零装饰零粗线，分隔全靠 40+ 留白；温度 300 是唯一主角；"
        "指数做成设置页式列表行（发丝线 + 右对齐数值）；预报是天气 App 式四联小倍数。",
    "三 · MUJI 空": "原研哉「空」：上半页只有日期 400 + 温度 200 两组墨迹，其余全部退成 28px 灰字行、"
        "行距 56；纸白是主角。功能一行没少，只是音量调低。",
    "四 · 编辑 Folio": "TDC/D&AD 编辑排印：上下双黑线刊头 + 字距拉开的 folio 行；封面主体日期 340 + "
        "栏线 + 右栏 kicker 目录（天气/历法/节日）；预报与指数做成带发丝线的目录行、数值右对齐。",
    "五 · Tufte 数据条": "数据墨水比最大化：预报画成温度区间条（NYT 天气条血统），端点直接标数值，"
        "今日一行加粗；除两条分隔黑线外无框无底；图标只保留当前天气一枚。",
    "六 · Braun 仪表": "Rams 少却更好：仪表面板分区，3px 黑线划区 + 字距小标签（DATE/WEATHER/…），"
        "区内竖发丝线分格；数字粗黑体、几何秩序；可读性即美学。",
}


def render(name, fn, data):
    sh = Sheet(Fonts())
    battery(sh)
    foot_top = H - 44 - 86
    probe = Sheet(Fonts())
    h = fn(probe, data, 0, 0)
    extra = (foot_top - 80) - TOP - h
    y1 = fn(sh, data, TOP, extra)
    footer(sh, data, foot_top - 60)
    return sh, extra


def spread(extra, n):
    """把富余高度拆成 n 份段距 —— 空间分布掉，页面永远填到页脚。"""
    q = extra // n
    return [q + (extra - q * n) if i == 0 else q for i in range(n)]


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--strip3", action="store_true",
                    help="数据条改进轮三版（日期节日/天气分带 + 预报行图标）+ strip.html")
    ap.add_argument("--cgrid3", action="store_true",
                    help="C 改进轮三节奏（双栏分带 + 大图标 + 8px 基准网格）+ cgrid.html")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    if args.strip3:
        data = worst_bw()
        cards = []
        for key in ("A", "B", "C"):
            fn = lambda sh, d, y, extra=0, k=key: lay_strip(sh, d, y, extra, k)
            sh = Sheet(Fonts())
            battery(sh)
            foot_top = H - 44 - 86
            probe = Sheet(Fonts())
            h = fn(probe, data, 0, 0)
            extra = (foot_top - 80) - TOP - h
            if extra < 40:
                raise SystemExit(f"条-{key}: 余量 {extra}px 低于安全线 40")
            fn(sh, data, TOP, extra)
            footer(sh, data, foot_top - 60)
            png = OUT / f"条-{key}.png"
            sh.img.save(png, format="PNG", optimize=True)
            print(f"条-{key}: 余量 {extra}px -> {png.name}")
            cards.append(f"""
    <figure>
      <h2>条 · {key}</h2>
      <div class="shot"><img src="{png.name}" alt="条-{key}">
        <span class="bat" style="left:{BAT[0] / W * 100}%;top:{BAT[1] / H * 100}%;
              width:{(BAT[2] - BAT[0]) / W * 100}%;height:{(BAT[3] - BAT[1]) / H * 100}%"></span></div>
      <p class="d">{STRIPS[key]}</p>
      <figcaption>最坏情况（节日+倒计时+调休 / 2 预警 / 4 预报 / 3 指数）· 余量 <b>{extra}px</b></figcaption>
    </figure>""")
        html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>数据条改进轮 · 三版</title>
<style>
 body{{margin:0;background:#eceef0;color:#1c1f23;
      font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
 header{{padding:24px 28px 16px;border-bottom:1px solid #d6d9dd;background:#fff}}
 h1{{margin:0 0 6px;font-size:21px}} header p{{margin:0;color:#5b6470;font-size:13.5px}}
 main{{padding:24px 28px 80px;display:grid;grid-template-columns:repeat(auto-fit,
       minmax(420px,1fr));gap:22px;max-width:1700px}}
 figure{{margin:0;background:#fff;border:1px solid #d6d9dd;border-radius:12px;
         padding:14px 16px 16px}}
 h2{{margin:0 0 8px;font-size:18px}} .d{{margin:10px 0 8px;color:#3c434b;font-size:12.5px}}
 .shot{{position:relative;line-height:0}}
 img{{width:100%;height:auto;border:1px solid #cfd3d8;border-radius:4px;background:#fff}}
 .bat{{position:absolute;border:2px dashed #b42318;border-radius:3px;box-sizing:border-box}}
 figcaption{{margin-top:6px;font-size:12.5px;color:#3c434b}}
</style></head><body>
<header><h1>数据条改进轮 · 日期节日 / 天气分带，预报行图标加回</h1>
<p>三张都是 1072×1448 真图、五档灰、宋体 Heavy 显示字。预报 = 温度区间条 + 每行图标；
日期+节日 与 天气 各自成段。红虚线 = 电量精灵图位。</p></header>
<main>{''.join(cards)}</main></body></html>
"""
        (OUT / "strip.html").write_text(html, encoding="utf-8")
        print(f"ok -> {OUT / 'strip.html'}")
        return 0

    if args.cgrid3:
        data = worst_bw()
        cards = []
        for key in ("1", "2", "3"):
            P = CP[key]
            fn = lambda sh, d, y, extra=0, PP=P: lay_cgrid(sh, d, y, extra, PP)
            sh = Sheet(Fonts())
            battery(sh)
            foot_top = H - 44 - 86
            probe = Sheet(Fonts())
            h = fn(probe, data, 0, 0)
            extra = (foot_top - 80) - TOP - h
            if extra < 40:
                raise SystemExit(f"C{key}: 余量 {extra}px 低于安全线 40")
            fn(sh, data, TOP, extra)
            footer(sh, data, foot_top - 60)
            png = OUT / f"C-{key}.png"
            sh.img.save(png, format="PNG", optimize=True)
            print(f"C{key}: 余量 {extra}px -> {png.name}")
            cards.append(f"""
    <figure>
      <h2>{P['note'].split('：')[0]}</h2>
      <div class="shot"><img src="{png.name}" alt="C-{key}">
        <span class="bat" style="left:{BAT[0] / W * 100}%;top:{BAT[1] / H * 100}%;
              width:{(BAT[2] - BAT[0]) / W * 100}%;height:{(BAT[3] - BAT[1]) / H * 100}%"></span></div>
      <p class="d">{P['note']}</p>
      <figcaption>最坏情况（节日+倒计时+调休 / 2 预警 / 4 预报 / 3 指数）· 余量 <b>{extra}px</b></figcaption>
    </figure>""")
        html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>C 改进轮 · 基准网格三节奏</title>
<style>
 body{{margin:0;background:#eceef0;color:#1c1f23;
      font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
 header{{padding:24px 28px 16px;border-bottom:1px solid #d6d9dd;background:#fff}}
 h1{{margin:0 0 6px;font-size:21px}} header p{{margin:0;color:#5b6470;font-size:13.5px}}
 main{{padding:24px 28px 80px;display:grid;grid-template-columns:repeat(auto-fit,
       minmax(420px,1fr));gap:22px;max-width:1700px}}
 figure{{margin:0;background:#fff;border:1px solid #d6d9dd;border-radius:12px;
         padding:14px 16px 16px}}
 h2{{margin:0 0 8px;font-size:18px}} .d{{margin:10px 0 8px;color:#3c434b;font-size:12.5px}}
 .shot{{position:relative;line-height:0}}
 img{{width:100%;height:auto;border:1px solid #cfd3d8;border-radius:4px;background:#fff}}
 .bat{{position:absolute;border:2px dashed #b42318;border-radius:3px;box-sizing:border-box}}
 figcaption{{margin-top:6px;font-size:12.5px;color:#3c434b}}
</style></head><body>
<header><h1>C 改进轮 · 双栏分带 + 大图标预报 + 8px 基准网格行距</h1>
<p>三张都是 1072×1448 真图。行盒 = 字号×1.5 取整到 8px；组内 / 块间 / 带间三档间距递进；
预报 = 大图标四联（当天实心加粗），数据条已撤。红虚线 = 电量精灵图位。</p></header>
<main>{''.join(cards)}</main></body></html>
"""
        (OUT / "cgrid.html").write_text(html, encoding="utf-8")
        print(f"ok -> {OUT / 'cgrid.html'}")
        return 0

    data = worst_bw()
    cards = []
    for name, fn in LAYS:
        sh, slack = render(name, fn, data)
        if slack < 40:
            raise SystemExit(f"{name}: 余量 {slack}px 低于安全线 40")
        png = OUT / f"六-{name.split(' ')[0]}.png"
        sh.img.save(png, format="PNG", optimize=True)
        print(f"{name}: 余量 {slack}px -> {png.name}")
        cards.append(f"""
    <figure>
      <h2>{name}</h2>
      <div class="shot"><img src="{png.name}" alt="{name}">
        <span class="bat" style="left:{BAT[0] / W * 100}%;top:{BAT[1] / H * 100}%;
              width:{(BAT[2] - BAT[0]) / W * 100}%;height:{(BAT[3] - BAT[1]) / H * 100}%"></span></div>
      <p class="d">{NOTES[name]}</p>
      <figcaption>最坏情况（节日+倒计时+调休 / 2 预警 / 4 预报 / 3 指数）· 余量 <b>{slack}px</b></figcaption>
    </figure>""")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>推倒重来 · 六版高阶审美</title>
<style>
 body{{margin:0;background:#eceef0;color:#1c1f23;
      font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
 header{{padding:24px 28px 16px;border-bottom:1px solid #d6d9dd;background:#fff}}
 h1{{margin:0 0 6px;font-size:21px}} header p{{margin:0;color:#5b6470;font-size:13.5px}}
 main{{padding:24px 28px 80px;display:grid;grid-template-columns:repeat(auto-fit,
       minmax(420px,1fr));gap:22px;max-width:1700px}}
 figure{{margin:0;background:#fff;border:1px solid #d6d9dd;border-radius:12px;
         padding:14px 16px 16px}}
 h2{{margin:0 0 8px;font-size:18px}} .d{{margin:10px 0 8px;color:#3c434b;font-size:12.5px}}
 .shot{{position:relative;line-height:0}}
 img{{width:100%;height:auto;border:1px solid #cfd3d8;border-radius:4px;background:#fff}}
 .bat{{position:absolute;border:2px dashed #b42318;border-radius:3px;box-sizing:border-box}}
 figcaption{{margin-top:6px;font-size:12.5px;color:#3c434b}}
</style></head><body>
<header><h1>推倒重来 · 六版高阶审美（研究底稿：Apple HIG / 瑞士网格 / MUJI / Tufte / Rams / TDC）</h1>
<p>六张都是 1072×1448 真图、设备原生五档灰、显示字思源宋体 Heavy + 正文黑体。全部左对齐起步，"
"不再居中堆叠。红虚线 = 电量精灵图位（样式 B 已画进图里预览）。</p></header>
<main>{''.join(cards)}</main></body></html>
"""
    (OUT / "six.html").write_text(html, encoding="utf-8")
    print(f"ok -> {OUT / 'six.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
