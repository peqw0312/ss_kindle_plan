#!/usr/bin/env python3
"""黑白一轮（2026-09-22 新简报）：纯 1-bit（只 0 / 255）、两种字重、节日模块上屏。

和 design_directions.py 的区别：那一轮允许五档灰，这一轮简报把灰阶整个禁了 ——
层级只能靠字号 / 字重 / 留白 / 细黑线。数字按简报用等宽（Consolas），
中文正文 Noto Sans SC，农历 / 生肖用思源宋体 Regular 添一点纸感（Regular 不算新字重）。

三个方向：
  一 · 大日期杂志封面款 —— 主日期 + 节日提示是绝对 C 位
  二 · 天气晨间款       —— 温度视觉权重最高，节日倒计时在日期下方
  三 · 均衡编辑款       —— 日期 / 天气 / 行情三条横带均分纵向，节日是独立小标题

用法（仓库根目录）：
    python dashboard/tools/design_bw.py
产物：.workbuddy/_directions/ 黑-一/二/三.png（+ 黑-一-精简.png）+ bw.html
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "dashboard"))

from design_directions import (OUT, W, H, Fonts, Sheet, build_poster2_html)  # noqa: E402

INK, PAPER = 0, 255
CONS_R = r"C:\Windows\Fonts\consola.ttf"
CONS_B = r"C:\Windows\Fonts\consolab.ttf"
MARGIN = 56
TOP = 112                      # 电量精灵图矩形 (814,40)-(1016,96) 下沿 +16
BAT_BOX = (814, 40, 1016, 96)


class FontsBW(Fonts):
    def digit(self, size: int, bold: bool = False):
        """等宽数字（简报要求）。Consolas 的数码天然是等宽表格数字。"""
        return self._get(CONS_B if bold else CONS_R, size)


# ---- 最坏情况内容（简报指定：节日 + 倒计时 + 调休三者同屏）------------------

def worst_bw() -> dict:
    return {
        "place": "杭州 · 临平区",
        "cal": {
            "month": "2026 年 9 月", "day": "25", "week": "星期五",
            "lunar": "八月十五", "zodiac": "丙午 · 马年",
            "fest_today": ["中秋节"],            # 法定节假日优先
            "countdown": "距国庆节还有 6 天",
            "tiaoxiu": "本周日调休上班",
        },
        "wx": {
            "temp": "30°", "desc": "多云转小雨", "icon": "sun_cloud",
            "cells": [("体感", "33°"), ("湿度", "78%"), ("降水", "60%"),
                      ("东南风", "3 级"), ("空气", "轻度 118"), ("紫外", "6")],
            "warn": ["杭州市气象台发布强对流天气黄色预警",
                     "杭州市气象台发布雷电黄色预警信号"],
            "fc": [("今天", "sun_cloud", "多云转小雨", "30°", "24°"),
                   ("明天", "drizzle", "小毛毛雨", "29°", "23°"),
                   ("周六", "rain", "中雨", "27°", "22°"),
                   ("周日", "cloud", "阴转多云", "30°", "23°")],
        },
        "quotes": [("纳斯达克100", "29,446.98", 1.73),
                   ("标普500", "7,765.42", -1.14),
                   ("上证指数", "3,955.10", 0.14)],
        "foot_l": "更新 09-25 13:16",
        "foot_m": "Kindle Paperwhite 3 · 天气 Open-Meteo · 行情 腾讯 · 实心=涨 空心=跌",
    }


def minimal_bw() -> dict:
    """无节日无调休、0 预警、1 天预报、行情关、数据格只剩 2 —— 证明整块收缩不塌。"""
    d = worst_bw()
    d["cal"]["fest_today"] = []
    d["cal"]["countdown"] = ""
    d["cal"]["tiaoxiu"] = ""
    d["wx"]["warn"] = []
    d["wx"]["fc"] = d["wx"]["fc"][:1]
    d["wx"]["cells"] = d["wx"]["cells"][:2]
    d["quotes"] = []
    return d


# ---- 1-bit 小件 ----------------------------------------------------------

def rule(sh, y, x0, x1, t=2):
    sh.d.rectangle([x0, y, x1, y + t - 1], fill=INK)


def sq(sh, x, y, s=14, filled=True):
    if filled:
        sh.d.rectangle([x, y, x + s, y + s], fill=INK)
    else:
        sh.d.rectangle([x, y, x + s, y + s], outline=INK, width=3)


def dig_c(sh, x, top, bottom, s, size, bold=False, anchor="lt", font=None):
    """数字按墨迹画进 [top,bottom]。anchor="mt" 时 x 是中心，否则是左边。
    font 给宋体 Heavy 时就是旧稿那套显示字；° 一律黑体小字单独画。返回总宽。"""
    digits = s.rstrip("°")
    deg = s[len(digits):]
    f = font or sh.f.digit(size, bold)
    w = sh.ink(x, top, bottom, digits, f, INK, anchor=anchor)
    if deg:
        df = sh.f.sans(int(size * 0.42), True)
        dw = sh.tw(deg, df)
        dx = (x + w / 2 + 4) if anchor == "mt" else (x + w + 4)
        sh.ink(dx, top, top + int((bottom - top) * 0.36), deg, df, INK, anchor="lt")
        w += 8 + dw
    return w


def dig_c_width(sh, s, size, bold=False, font=None):
    digits = s.rstrip("°")
    deg = s[len(digits):]
    w = sh.tw(digits, font or sh.f.digit(size, bold))
    if deg:
        w += 8 + sh.tw(deg, sh.f.sans(int(size * 0.42), True))
    return w


def lunar_zodiac(sh, y, cal, size, cx=None, x=None):
    """农历 + 生肖：宋体里没有「·」字形（会出豆腐块），所有分隔符换黑体画。返回总宽。"""
    parts = [cal["lunar"]] + (cal["zodiac"].split(" · ") if cal["zodiac"] else [])
    f = sh.f.serif(size)
    fs = sh.f.sans(max(20, size - 8))
    sep = " · "
    ws = sh.tw(sep, fs)
    widths = [sh.tw(p, f) for p in parts]
    total = sum(widths) + ws * (len(parts) - 1)
    sx = (cx - total / 2) if cx is not None else x
    for i, p in enumerate(parts):
        sh.ink(sx, y, y + size, p, f, INK)
        sx += widths[i]
        if i < len(parts) - 1:
            sh.ink(sx, y, y + size, sep, fs, INK)
            sx += ws
    return total


def cells_pick(wx, n=4):
    """数据格挤不下时按简报截断优先级砍：风向 / 紫外先走。"""
    drop = ("风", "紫外")
    out = [kv for kv in wx["cells"] if not any(k in kv[0] for k in drop)]
    if len(out) < n:
        out = list(wx["cells"])
    return out[:n]


def val_font(sh, v: str, size: int):
    """值里混了中文（3 级 / 轻度 118）就不能用等宽字 —— Consolas 没有 CJK 字形。"""
    if all(c in "0123456789°%.,+- " for c in v):
        return sh.f.digit(size)
    return sh.f.sans(size)


def fest_height(cal, fs_bold=40, fs=30, mode="all") -> int:
    """节日块会占多高（不画）。几行画几行，没有就零高度 —— 不预留死高度。"""
    h = 0
    if mode in ("all", "today") and cal["fest_today"]:
        h += (fs_bold + 8) * min(len(cal["fest_today"]), 2 if mode == "all" else 1)
    if mode in ("all", "rest"):
        h += (bool(cal["countdown"]) + bool(cal["tiaoxiu"])) * (fs + 6)
    return h


def fest_block(sh, cal, y, cx=None, x=None, fs_bold=40, fs=30, mode="all"):
    """节日块：粗体当日节日（强调区之一）+ 倒计时 + 调休，几行画几行。
    mode: all / today（只粗体节日行）/ rest（只倒计时与调休）。返回下边缘。"""
    anchor_c = cx is not None
    if mode in ("all", "today"):
        for name in cal["fest_today"][:2]:
            mw = fs_bold // 2
            line_w = mw + 12 + sh.tw(name, sh.f.sans(fs_bold, True))
            bx = (cx - line_w / 2) if anchor_c else x
            sq(sh, bx, y + fs_bold // 2 - mw // 2, mw)
            sh.ink(bx + mw + 12, y, y + fs_bold + 6, name,
                   sh.f.sans(fs_bold, True), INK)
            y += fs_bold + 8
    if mode in ("all", "rest"):
        for line in (cal["countdown"], cal["tiaoxiu"]):
            if not line:
                continue
            if anchor_c:
                sh.ink(cx, y, y + fs + 6, line, sh.f.sans(fs), INK, anchor="mt")
            else:
                sh.ink(x, y, y + fs + 6, line, sh.f.sans(fs), INK)
            y += fs + 6
    return y


def battery_bw(sh, level=86):
    """电量样式 B 的 1-bit 版：百分比粗体 + 空心轨道里填实心进度。"""
    x1, y0, x2, y1 = BAT_BOX
    gf = sh.f.sans(38, True)
    sh.t((x2, y0 + 2), f"{level}%", gf, INK, anchor="rt")
    by = y1 - 14
    sh.d.rectangle([x1, by, x2, by + 10], outline=INK, width=3)
    sh.d.rectangle([x1 + 3, by + 3,
                    x1 + 3 + (x2 - x1 - 6) * level // 100, by + 7], fill=INK)


def warn_lines(sh, wx, y, cx=None, x=None, fs=28):
    for t in wx["warn"][:2]:
        line_w = 12 + 10 + sh.tw(t, sh.f.sans(fs))
        bx = (cx - line_w / 2) if cx is not None else x
        sq(sh, bx, y + fs // 2 - 6, 12)
        sh.ink(bx + 22, y, y + fs + 6, t, sh.f.sans(fs), INK)
        y += fs + 8
    return y


def fc_row(sh, wx, y, x0, fw, n, icon=64, fs=28):
    r1 = icon + 6
    r2 = r1 + fs + 6
    r3 = r2 + fs + 6
    for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:n]):
        gx = x0 + i * fw + fw // 2
        sh.icons.icon(gx, y + icon // 2, icon, kind, INK)
        sh.ink(gx, y + r1, y + r1 + fs + 6, label, sh.f.sans(fs), INK, anchor="mt")
        sh.ink(gx, y + r2, y + r2 + fs + 6,
               sh.clip(desc, sh.f.sans(fs), fw - 8), sh.f.sans(fs), INK, anchor="mt")
        sh.ink(gx, y + r3, y + r3 + fs + 6, hi + " / " + lo,
               sh.f.sans(fs), INK, anchor="mt")
    return y + r3 + fs + 6


def quotes_row(sh, quotes, y, m, x1, name_fs=26, price_fs=48, pct_fs=28):
    cw3 = (x1 - m) // 3
    for i, (name, price, pct) in enumerate(quotes[:3]):
        gx = m + i * cw3 + cw3 // 2
        sh.ink(gx, y, y + name_fs + 6, name, sh.f.sans(name_fs), INK, anchor="mt")
        sh.ink(gx, y + name_fs + 10, y + name_fs + 10 + price_fs + 6, price,
               sh.f.sans(price_fs, True), INK, anchor="mt")
        py = y + name_fs + 10 + price_fs + 14
        txt = f"{pct:+.2f}%"
        pw = sh.tw(txt, sh.f.sans(pct_fs))
        sq(sh, gx - pw // 2 - 24, py + 8, 14, filled=pct >= 0)
        sh.ink(gx + 4, py, py + pct_fs + 6, txt, sh.f.sans(pct_fs), INK, anchor="mt")
    return y + name_fs + 10 + price_fs + 14 + pct_fs + 6


def footer_bw(sh, d, m, x1):
    y = H - 44 - 68
    rule(sh, y - 18, m, x1)
    sh.t((m, y), d["foot_l"], sh.f.sans(26), INK)
    sh.t((m, y + 34), d["foot_m"], sh.f.sans(26), INK)
    return y - 18


def cells_line(sh, wx, y, cx, fs=26, max_cells=4):
    """数据格：挤不下就按简报截断优先级砍（紫外 / 风向先走），拼成一行居中。"""
    txt = " · ".join(f"{k} {v}" for k, v in cells_pick(wx, max_cells))
    sh.ink(cx, y, y + fs + 6, txt, sh.f.sans(fs), INK, anchor="mt")
    return y + fs + 6


# ---- 方向一 · 大日期杂志封面款 --------------------------------------------

P1 = dict(day=300, lunar=38, fest_b=44, fest=30, temp=120, desc=40, icon=104,
          warn=28, fc_icon=52, fc=28, q_name=26, q_price=48, q_pct=28,
          g=dict(a=12, b=14, c=16, d=14, e=18, f=14, h=12, i=16, j=14))


def lay_cover(sh, d, y):
    M, X1 = MARGIN, W - MARGIN
    CW = X1 - M
    cx = W // 2
    cal, wx = d["cal"], d["wx"]
    g = P1["g"]

    sh.ink(cx, y, y + 40, " · ".join([cal["month"], cal["week"], d["place"]]),
           sh.f.sans(28), INK, anchor="mt")
    y += 40 + g["a"]
    dig_c(sh, cx, y, y + P1["day"], cal["day"], P1["day"], True, anchor="mt")
    y += P1["day"] + g["b"]
    lw = lunar_zodiac(sh, y, cal, P1["lunar"], cx=cx)
    y += P1["lunar"] + g["c"]
    rule(sh, y, cx - lw / 2, cx + lw / 2)
    y += g["d"]
    y = fest_block(sh, cal, y, cx=cx, fs_bold=P1["fest_b"], fs=P1["fest"])
    if cal["fest_today"] or cal["countdown"] or cal["tiaoxiu"]:
        y += g["e"]
    tf = P1["temp"]
    iw = P1["icon"]
    line_w = iw + 24 + dig_c_width(sh, wx["temp"], tf) + 24 + sh.tw(wx["desc"], sh.f.sans(P1["desc"]))
    x = cx - line_w // 2
    sh.icons.icon(x + iw / 2, y + tf * 0.40, iw, wx["icon"], INK)
    x += iw + 24
    x += dig_c(sh, x, y, y + tf, wx["temp"], tf) + 24
    sh.ink(x, y + tf * 0.40, y + tf, wx["desc"], sh.f.sans(P1["desc"]), INK)
    y += tf + g["f"]
    y = cells_line(sh, wx, y, cx) + g["h"]
    y = warn_lines(sh, wx, y, cx=cx, fs=P1["warn"])
    if wx["warn"]:
        y += g["i"]
    fw = 236
    y = fc_row(sh, wx, y, cx - fw * 2, fw, 4, icon=P1["fc_icon"], fs=P1["fc"]) + g["j"]
    rule(sh, y, M, X1)
    y += g["j"]
    if d["quotes"]:
        y = quotes_row(sh, d["quotes"], y, M, X1, P1["q_name"], P1["q_price"], P1["q_pct"])
    return y


# ---- 方向二 · 天气晨间款 --------------------------------------------------

P2 = dict(temp=200, desc=44, icon=140, day=170, lunar=34, fest_b=40, fest=30,
          cell_l=26, cell_v=32, warn=28, fc_icon=60, fc=28,
          q_name=26, q_price=44, q_pct=28,
          g=dict(a=18, b=14, c=12, d=16, e=14, f=14, h=14, i=14))


def lay_morning(sh, d, y):
    M, X1 = MARGIN, W - MARGIN
    cx = W // 2
    cal, wx = d["cal"], d["wx"]
    g = P2["g"]

    iw, tf = P2["icon"], P2["temp"]
    sh.icons.icon(M + iw / 2, y + tf * 0.42, iw, wx["icon"], INK)
    x = M + iw + 28
    x += dig_c(sh, x, y, y + tf, wx["temp"], tf) + 24
    sh.ink(x, y + tf * 0.42, y + tf, wx["desc"], sh.f.sans(P2["desc"]), INK)
    dy = y + tf + 10
    sh.ink(M + iw + 28, dy, dy + 34, d["place"], sh.f.sans(28), INK)
    y += max(tf, tf + 10 + 34) + g["a"]
    rule(sh, y, M, X1)
    y += g["b"]

    lf = sh.f.digit(P2["day"], True)
    sh.ink(M, y, y + P2["day"], cal["day"], lf, INK)
    rx = M + sh.tw(cal["day"], lf) + 32
    sh.ink(rx, y + 6, y + 40, " · ".join([cal["month"], cal["week"]]),
           sh.f.sans(30), INK)
    lunar_zodiac(sh, y + 46, cal, P2["lunar"], x=rx)
    y += P2["day"] + g["c"]
    y = fest_block(sh, cal, y, x=M, fs_bold=P2["fest_b"], fs=P2["fest"])
    if cal["fest_today"] or cal["countdown"] or cal["tiaoxiu"]:
        y += g["d"]
    cw3 = (X1 - M) // 3
    for i, (k, v) in enumerate(wx["cells"][:6]):
        gx = M + (i % 3) * cw3
        gy = y + (i // 3) * 72
        sh.ink(gx, gy, gy + P2["cell_l"] + 4, k, sh.f.sans(P2["cell_l"]), INK)
        sh.ink(gx, gy + 32, gy + 32 + P2["cell_v"] + 4, v, val_font(sh, v, P2["cell_v"]), INK)
    y += 2 * 72 + g["e"]
    y = warn_lines(sh, wx, y, x=M, fs=P2["warn"])
    if wx["warn"]:
        y += g["f"]
    fw = (X1 - M) // 4
    y = fc_row(sh, wx, y, M, fw, 4, icon=P2["fc_icon"], fs=P2["fc"]) + g["h"]
    rule(sh, y, M, X1)
    y += g["h"]
    if d["quotes"]:
        y = quotes_row(sh, d["quotes"], y, M, X1, P2["q_name"], P2["q_price"], P2["q_pct"])
    return y


# ---- 方向三 · 均衡编辑款（三条横带均分纵向，节日是独立小标题）--------------

P3 = dict(day=280, lunar=36, fest_b=40, fest=28, temp=190, desc=40, icon=120,
          warn=28, fc_icon=68, fc=28, q_name=26, q_price=56, pct=28,
          cell=26)


def lay_edit(sh, d, y):
    """均衡编辑款：日期 / 天气 / 行情三段，段间等距 + 通栏细黑线分带；整组居中。
    节日是独立小标题段，夹在日期段和天气段之间。"""
    M, X1 = MARGIN, W - MARGIN
    cal, wx = d["cal"], d["wx"]
    G = 28

    lf = sh.f.digit(P3["day"], True)
    day_w = sh.tw(cal["day"], lf)
    g1 = max(P3["day"], 40 + 8 + P3["lunar"])
    sh.ink(M, y, y + P3["day"], cal["day"], lf, INK)
    rx = M + day_w + 36
    rule_v(sh, M + day_w + 18, y + 8, y + g1 - 8)
    sh.ink(rx, y + 6, y + 46, " · ".join([cal["month"], cal["week"], d["place"]]),
           sh.f.sans(28), INK)
    lunar_zodiac(sh, y + 54, cal, P3["lunar"], x=rx)
    y += g1 + 12
    y = fest_block(sh, cal, y, x=M, fs_bold=P3["fest_b"], fs=P3["fest"])
    if cal["fest_today"] or cal["countdown"] or cal["tiaoxiu"]:
        y += G
    rule(sh, y, M, X1)
    y += 14

    iw, tf = P3["icon"], P3["temp"]
    sh.icons.icon(M + iw / 2, y + tf * 0.40, iw, wx["icon"], INK)
    x = M + iw + 26
    x += dig_c(sh, x, y, y + tf, wx["temp"], tf) + 24
    sh.ink(x, y + tf * 0.40, y + tf, wx["desc"], sh.f.sans(P3["desc"]), INK)
    y += tf + 10
    sh.ink(M + iw + 26, y, y + 34,
           " · ".join(f"{k} {v}" for k, v in cells_pick(wx, 4)),
           sh.f.sans(P3["cell"]), INK)
    y += 34 + 10
    y = warn_lines(sh, wx, y, x=M, fs=P3["warn"])
    if wx["warn"]:
        y += 10
    fw = (X1 - M) // 4
    y = fc_row(sh, wx, y, M, fw, 4, icon=P3["fc_icon"], fs=P3["fc"]) + G
    rule(sh, y, M, X1)
    y += 14
    if d["quotes"]:
        y = quotes_row(sh, d["quotes"], y, M, X1, P3["q_name"], P3["q_price"], P3["pct"])
    return y


def rule_v(sh, x, y0, y1, t=2):
    sh.d.rectangle([x, y0, x + t - 1, y1], fill=INK)


# ---- 出图 ----------------------------------------------------------------

# ---- 方向三优化轮（2026-09-22 晚）：气温搬进日期带右栏，填掉农历下面的空 -----
# 字体回到旧稿那套：主数字 / 温度 = 思源宋体 Heavy，正文 Noto Sans SC。
# 电量样式 B 画进图里（1-bit 版）。五版只调配平：日期 / 温度 / 预报 / 指数谁更重。

VARIANTS = {
    "甲 · 均衡": dict(day=380, lunar=40, temp=210, icon=120, desc=34,
                      fest_b=46, fest=30, cells=32, warn=34, fc=38, fc_icon=120,
                      q_name=28, q_price=62, pct=32, fest_pos="band"),
    "乙 · 日期更重": dict(day=430, lunar=40, temp=180, icon=104, desc=32,
                         fest_b=44, fest=30, cells=30, warn=32, fc=34, fc_icon=96,
                         q_name=28, q_price=58, pct=32, fest_pos="band"),
    "丙 · 温度更重": dict(day=330, lunar=40, temp=270, icon=165, desc=36,
                         fest_b=44, fest=30, cells=30, warn=32, fc=34, fc_icon=96,
                         q_name=28, q_price=58, pct=32, fest_pos="band"),
    "丁 · 节日分两栏": dict(day=380, lunar=44, temp=260, icon=120, desc=34,
                           fest_b=48, fest=32, cells=34, warn=36, fc=40, fc_icon=132,
                           q_name=30, q_price=62, pct=34, fest_pos="split"),
    "戊 · 下半更重": dict(day=340, lunar=40, temp=190, icon=120, desc=34,
                         fest_b=46, fest=30, cells=34, warn=36, fc=42, fc_icon=136,
                         q_name=28, q_price=62, pct=36, fest_pos="band"),
}


def lay_edit2(sh, d, y, P):
    """均衡编辑款优化版：日期带右栏 = 月周地 / 农历生肖 / 图标+温度+天气词，
    农历下面不再空；节日默认仍是日期带与天气带之间的独立小标题段
    （fest_pos="split" 时粗体节日行贴日期下方、倒计时调休贴温度下方）。"""
    M, X1 = MARGIN, W - MARGIN
    cal, wx = d["cal"], d["wx"]
    G = 28
    split = P["fest_pos"] == "split"

    lf = sh.f.serif(P["day"])
    day_w = sh.tw(cal["day"], lf)
    rx = M + day_w + 36
    rw = X1 - rx
    tf = P["temp"]
    iw = P["icon"]
    tf_w = dig_c_width(sh, wx["temp"], tf, font=sh.f.serif(tf))
    desc_w = sh.tw(wx["desc"], sh.f.sans(P["desc"]))
    inline = iw + 20 + tf_w + 20 + desc_w <= rw
    left = P["day"] + (12 + fest_height(cal, P["fest_b"], P["fest"], "today")
                       if split else 0)
    right = (40 + 8 + P["lunar"] + 12 + tf
             + (0 if inline else 8 + P["desc"])
             + (12 + fest_height(cal, P["fest_b"], P["fest"], "rest") if split else 0))
    band = max(left, right)

    sh.ink(M, y, y + P["day"], cal["day"], lf, INK)
    rule_v(sh, M + day_w + 18, y + 8, y + band - 8)
    sh.ink(rx, y + 6, y + 46, " · ".join([cal["month"], cal["week"], d["place"]]),
           sh.f.sans(28), INK)
    lunar_zodiac(sh, y + 54, cal, P["lunar"], x=rx)
    ty = y + 54 + P["lunar"] + 12
    sh.icons.icon(rx + iw / 2, ty + tf * 0.40, iw, wx["icon"], INK)
    tx = rx + iw + 20
    tx += dig_c(sh, tx, ty, ty + tf, wx["temp"], tf, font=sh.f.serif(tf)) + 20
    if inline:
        sh.ink(tx, ty + tf * 0.40, ty + tf, wx["desc"], sh.f.sans(P["desc"]), INK)
        ry = ty + tf + 12
    else:
        sh.ink(rx, ty + tf + 8, ty + tf + 8 + P["desc"], wx["desc"],
               sh.f.sans(P["desc"]), INK)
        ry = ty + tf + 8 + P["desc"] + 8
    if split:
        fest_block(sh, cal, y + P["day"] + 12, x=M, fs_bold=P["fest_b"],
                   fs=P["fest"], mode="today")
        fest_block(sh, cal, ry, x=rx, fs_bold=P["fest_b"],
                   fs=P["fest"], mode="rest")
    y += band + 12
    if not split:
        y = fest_block(sh, cal, y, x=M, fs_bold=P["fest_b"], fs=P["fest"])
        if cal["fest_today"] or cal["countdown"] or cal["tiaoxiu"]:
            y += G
    rule(sh, y, M, X1)
    y += 14

    sh.ink(M, y, y + P["cells"] + 6,
           " · ".join(f"{k} {v}" for k, v in cells_pick(wx, 4)),
           sh.f.sans(P["cells"]), INK)
    y += P["cells"] + 6 + 10
    y = warn_lines(sh, wx, y, x=M, fs=P["warn"])
    if wx["warn"]:
        y += 10
    fw = (X1 - M) // 4
    y = fc_row(sh, wx, y, M, fw, 4, icon=P["fc_icon"], fs=P["fc"]) + G
    rule(sh, y, M, X1)
    y += 14
    if d["quotes"]:
        y = quotes_row(sh, d["quotes"], y, M, X1, P["q_name"], P["q_price"], P["pct"])
    return y


SPEC = {
    "一 · 大日期杂志封面款": (
        "栅格：左右边距 56，单栏居中；块距 a14 b14 c18 d14 e18 f14 h12 i16 j14（写死不伸缩）。<br>"
        "字阶：月周地 28 常 / 主日期 300 等宽粗 / 农历生肖 38 宋常 / 节日名 44 粗（强调区 1）/ "
        "倒计时·调休 30 常 / 温度 120 等宽常 / 天气词 40 常 / 数据格 26 常 / 预警 28 常 / "
        "预报 28 常 · 图标 52 / 指数名 26 常 · 点位 48 等宽常 · 涨跌 28 等宽常 / 页脚 26 常。<br>"
        "强调区共 2：主日期粗体 + 节日名粗体；其余全靠字号与留白分层。<br>"
        "重排 / 回收：模块独立成块按 config 顺序堆叠，开关只平移不缩放；节日块几行画几行、"
        "无节日零高度；数据格挤不下按截断优先级先砍紫外 / 风向（本最坏案砍到 4 格一行）。"),
    "二 · 天气晨间款": (
        "栅格：边距 56；顶部天气通栏，其下日期左对齐 + 节日块贴日期下方；块距 a18 b14 c12 d16 e14 f14 h14。<br>"
        "字阶：温度 200 等宽常（全屏视觉最重，靠字号不靠字重）/ 天气词 44 常 / 地名 28 常 / "
        "主日期 170 等宽粗（强调区 1）/ 月周 30 常 / 农历生肖 34 宋常 / 节日名 40 粗（强调区 2）/ "
        "倒计时·调休 30 常 / 数据格 26 常 + 值 32 等宽常 / 预警 28 常 / 预报 28 常 · 图标 60 / "
        "指数 26 · 44 等宽 · 28 等宽 / 页脚 26。<br>"
        "重排 / 回收：同方向一；行情关 → 底部分隔线与其上块距一起消失，整组重居中。"),
    "三 · 均衡编辑款": (
        "栅格：边距 56；日期 / 天气 / 行情三段，段间等距 28 + 通栏细黑线分带，整组垂直居中 —— "
        "三段各占约三分之一纵向，节日是日期段与天气段之间的独立小标题段。<br>"
        "字阶：主日期 280 等宽粗（强调区 1）/ 月周地 28 常 / 农历生肖 36 宋常 / 节日小标题 40 粗（强调区 2）/ "
        "倒计时·调休 28 常 / 温度 190 等宽常 / 天气词 40 常 / 数据格 26 常 / 预警 28 常 / 预报 28 常 / "
        "指数 26 · 56 等宽 · 28 等宽 / 页脚 26。<br>"
        "重排 / 回收：三段即三个独立块，开关只整块消失 + 整组重居中；节日段无内容时零高度，"
        "两条分带线随之少一条，版面不塌。"),
}


def render_dir(name, fn, data):
    sh = Sheet(FontsBW())
    foot_top = footer_bw(sh, data, MARGIN, W - MARGIN)
    probe = Sheet(FontsBW())
    h = fn(probe, data, 0)
    extra = foot_top - TOP - h
    y = TOP + max(0, extra // 2)
    y1 = fn(sh, data, y)
    sh.block("主体", y, y1)
    return sh, extra


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--edit5", action="store_true",
                    help="方向三优化轮五版配平（气温进日期带右栏）+ bw3.html")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    if args.edit5:
        shots = []
        for name, P in VARIANTS.items():
            data = worst_bw()
            sh = Sheet(FontsBW())
            foot_top = footer_bw(sh, data, MARGIN, W - MARGIN)
            battery_bw(sh)
            probe = Sheet(FontsBW())
            h = lay_edit2(probe, data, 0, P)
            extra = foot_top - TOP - h
            if extra < 40:
                raise SystemExit(f"{name}: 余量 {extra}px 低于安全线 40")
            y = TOP + max(0, extra // 2)
            lay_edit2(sh, data, y, P)
            png = OUT / f"黑3-{name.split(' ')[0]}.png"
            sh.img.save(png, format="PNG", optimize=True)
            note = (f"日期 {P['day']} 宋体 Heavy / 温度 {P['temp']} 宋体 Heavy / 主图标 {P['icon']} / "
                    f"预报图标 {P['fc_icon']} · 预报字 {P['fc']} / 指数点位 {P['q_price']} / "
                    f"节日块 {'粗体行贴日期下 + 倒计时调休贴温度下' if P['fest_pos'] == 'split' else '独立小标题段'}。"
                    f"日期带右栏 = 月周地 / 农历生肖 / 图标+温度+天气词，农历下不空。")
            shots.append((name, png.name, extra, note))
            print(f"{name}: 余量 {extra}px -> {png.name}")
        cards = []
        for name, png, slack, note in shots:
            cards.append(f"""
    <figure>
      <h2>{name}</h2>
      <div class="shot"><img src="{png}" alt="{name}">
        <span class="clock" style="left:{BAT_BOX[0] / W * 100}%;top:{BAT_BOX[1] / H * 100}%;
              width:{(BAT_BOX[2] - BAT_BOX[0]) / W * 100}%;height:{(BAT_BOX[3] - BAT_BOX[1]) / H * 100}%"></span></div>
      <p class="d">{note}</p>
      <figcaption>最坏情况（节日+倒计时+调休同屏 / 2 预警 / 4 预报 / 3 指数）· 余量 <b>{slack}px</b></figcaption>
    </figure>""")
        html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>方向三优化轮 · 五版配平</title>
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
 .clock{{position:absolute;border:2px dashed #b42318;border-radius:3px;box-sizing:border-box}}
 figcaption{{margin-top:6px;font-size:12.5px;color:#3c434b}}
</style></head><body>
<header><h1>方向三优化轮 · 五版配平（气温进日期带右栏 + 旧字体 + 电量画回）</h1>
<p>五张都是 1072×1448 真图、纯 0/255、两字重；主数字与温度回到思源宋体 Heavy。
右上角电量样式 B 已画进图里（真机上仍由 Kindle 贴精灵图，矩形不变）。红虚线 = 精灵图位置。</p></header>
<main>{''.join(cards)}</main></body></html>
"""
        (OUT / "bw3.html").write_text(html, encoding="utf-8")
        print(f"ok -> {OUT / 'bw3.html'}")
        return 0

    shots = []
    for name, fn in (("一 · 大日期杂志封面款", lay_cover),
                     ("二 · 天气晨间款", lay_morning),
                     ("三 · 均衡编辑款", lay_edit)):
        sh, slack = render_dir(name, fn, worst_bw())
        if slack < 40:
            raise SystemExit(f"{name}: 余量 {slack}px 低于安全线 40")
        png = OUT / f"黑-{name.split(' ')[0]}.png"
        sh.img.save(png, format="PNG", optimize=True)
        shots.append((name, png.name, slack, SPEC[name]))
        print(f"{name}: 余量 {slack}px -> {png.name}")

    sh, slack = render_dir("一 · 大日期杂志封面款", lay_cover, minimal_bw())
    png = OUT / "黑-一-精简.png"
    sh.img.save(png, format="PNG", optimize=True)
    print(f"精简态: 余量 {slack}px -> {png.name}")

    cards = []
    for name, png, slack, note in shots:
        cards.append(f"""
    <figure>
      <h2>{name}</h2>
      <div class="shot"><img src="{png}" alt="{name}">
        <span class="clock" style="left:{BAT_BOX[0] / W * 100}%;top:{BAT_BOX[1] / H * 100}%;
              width:{(BAT_BOX[2] - BAT_BOX[0]) / W * 100}%;height:{(BAT_BOX[3] - BAT_BOX[1]) / H * 100}%"></span></div>
      <p class="d">{note}</p>
      <figcaption>最坏情况（节日+倒计时+调休同屏 / 2 预警 / 4 预报 / 3 指数）· 余量 <b>{slack}px</b></figcaption>
    </figure>""")
    cards.append(f"""
    <figure>
      <h2>方向一 · 精简态（证明收缩不塌）</h2>
      <div class="shot"><img src="黑-一-精简.png" alt="精简态"></div>
      <p class="d">无节日无调休、0 预警、1 天预报、行情关、数据格 2 个：节日块零高度、
         预警区消失、预报区只剩一格，整组重居中，版面不塌。</p>
    </figure>""")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>黑白一轮 · 三方向</title>
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
 .clock{{position:absolute;border:2px dashed #b42318;border-radius:3px;box-sizing:border-box}}
 figcaption{{margin-top:6px;font-size:12.5px;color:#3c434b}}
</style></head><body>
<header><h1>黑白一轮 · 纯 1-bit 三方向（节日 / 法定倒计时 / 调休上屏）</h1>
<p>四张都是 1072×1448 真图、只 0 与 255 两色、两种字重。红虚线 = 电量精灵图位置（时钟已按简报移除）。
数字一律等宽（Consolas），中文 Noto Sans SC，农历 / 生肖思源宋体 Regular。</p></header>
<main>{''.join(cards)}</main></body></html>
"""
    (OUT / "bw.html").write_text(html, encoding="utf-8")
    print(f"ok -> {OUT / 'bw.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
