#!/usr/bin/env python3
"""三个版面方向的真实成品图（1072×1448 灰度）+ 纵向预算报告 + 挑稿网页。

这里换的是**栅格结构本身**（上分栏下通栏 / 全宽报 / 居中帖），所以布局代码长在本文件里。
以前还有几个同类的提案脚本（各自 fork 一份布局代码），已经合并掉删了 ——
**新提案改这个文件，不要再开新文件**，否则每轮多一个上千行的副本。
但有两样东西故意复用真渲染器，不另画：

- 天气图标：`aiinfo.render.Renderer.icon()`（E 套「柔雾·灰实心」，真机上就是它）
- 中文折行：`aiinfo.render.wrap_text`（标点悬挂、中西文空格都在里面）

每条硬性约束都落在代码里，不是写在文档里：
只有 0/70/130/195/255 五档灰；正文最小 26px；每屏反白强调块 ≤2；
右上角 (644,18)-(1030,129) 永远留白给本机时钟，三个方向共用这一个矩形
（换方向不用重拷 clock/ 精灵图）——出图后逐像素自检，画脏了直接报错。

用法（仓库根目录执行）：
    python dashboard/tools/design_directions.py            # 最坏情况内容
    python dashboard/tools/design_directions.py --minimal  # 精简态（证明版面不塌）

产物：.workbuddy/_directions/ 下每方向一张 PNG（+ 精简态一张）+ report.txt + index.html。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))

from aiinfo.render import Renderer, wrap_text          # noqa: E402

OUT = ROOT / ".workbuddy" / "_directions"

# ---- 画布与墨色 ----------------------------------------------------------
W, H = 1072, 1448
INK, SOFT, GRAY, LIGHT, PAPER = 0, 70, 130, 195, 255

#: 时钟留白区。三个方向共用同一个矩形 = 现网精灵图的矩形，
#: 所以"换方向"不需要重新生成 / 重新拷贝 clock/ 目录。
CLOCK = (644, 18, 1030, 129)
TOP = CLOCK[3] + 19          # 148：通栏内容必须从这条线以下开始

SANS_R = r"C:\Windows\Fonts\Noto Sans SC (TrueType).otf"
SANS_B = r"C:\Windows\Fonts\Noto Sans SC Bold (TrueType).otf"
SERIF_H = r"C:\Windows\Fonts\Source Han Serif SC Heavy (TrueType).ttf"

FOOT_LEGEND = "实心=涨 空心=跌"


class Fonts:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, int], object] = {}

    def _get(self, path: str, size: int):
        key = (path, size)
        if key not in self._cache:
            from PIL import ImageFont
            self._cache[key] = ImageFont.truetype(path, size)
        return self._cache[key]

    def sans(self, size: int, bold: bool = False):
        return self._get(SANS_B if bold else SANS_R, size)

    def serif(self, size: int):
        """思源宋体 Heavy：只给日期/温度这类超大数字用，全屏就这一个显示字重。"""
        return self._get(SERIF_H, size)


class IconKit(Renderer):
    """只借 Renderer 的图标绘制，不要它的布局。"""

    def __init__(self, img: Image.Image):
        self.img = img
        self.d = ImageDraw.Draw(img)


class Sheet:
    """一张 1072×1448 的纸 + 量尺。所有纵向位置都从这里报出去做预算。"""

    def __init__(self, fonts: Fonts):
        self.img = Image.new("L", (W, H), PAPER)
        self.d = ImageDraw.Draw(self.img)
        self.f = fonts
        self.icons = IconKit(self.img)
        self.blocks: list[tuple[str, int, int]] = []

    # -- 量 --
    def tw(self, s: str, font) -> int:
        return int(self.d.textlength(s, font=font)) if s else 0

    def lh(self, font) -> int:
        a, d = font.getmetrics()
        return a + d

    # -- 画 --
    def t(self, xy, s, font, fill=INK, anchor=None):
        if s:
            self.d.text(xy, s, font=font, fill=fill, anchor=anchor)

    def ink(self, x, top, bottom, s, font, fill=INK, anchor="lt"):
        """按实际墨迹在 [top,bottom] 里垂直居中（数字没有下伸部，按行盒定位会偏上）。"""
        if not s:
            return 0
        box = self.d.textbbox((x, 0), s, font=font, anchor=anchor)
        h = box[3] - box[1]
        self.t((x, top + (bottom - top - h) / 2 - box[1]), s, font, fill, anchor)
        return box[2] - box[0]

    def big(self, x, top, bottom, s, font, fill=INK, anchor="lt") -> int:
        """超大数字。° 单独用黑体画：宋体 Heavy 没有 ° 字形，会出豆腐块。"""
        digits = s.rstrip("°")
        deg = s[len(digits):]
        w = self.ink(x, top, bottom, digits, font, fill, anchor)
        if deg:
            df = self.f.sans(int(font.size * 0.46), True)
            self.ink(x + w + 4, top, top + int((bottom - top) * 0.38), deg, df, fill)
            w += 4 + self.tw(deg, df)
        return w

    def big_width(self, s: str, font) -> int:
        """big() 会占多宽（不画）。双栏报头要靠它决定右栏起点。"""
        digits = s.rstrip("°")
        deg = s[len(digits):]
        w = self.tw(digits, font)
        if deg:
            w += 4 + self.tw(deg, self.f.sans(int(font.size * 0.46), True))
        return w

    def wrap(self, s, font, max_w, max_lines=9):
        return wrap_text(self.d, s, font, max_w, max_lines)

    def clip(self, s, font, max_w):
        if self.tw(s, font) <= max_w:
            return s
        while s and self.tw(s + "…", font) > max_w:
            s = s[:-1]
        return (s + "…") if s else ""

    def rule(self, y, x0, x1, t=3, c=LIGHT):
        self.d.rectangle([x0, y, x1, y + t - 1], fill=c)

    def vrule(self, x, y0, y1, t=3, c=LIGHT):
        self.d.rectangle([x, y0, x + t - 1, y1], fill=c)

    def stamp(self, x, y, s, font, box=INK, txt=PAPER, pad=12):
        """反白强调块。全屏最多用两个，多了就廉价。返回占用宽度。"""
        w = self.tw(s, font)
        h = self.lh(font)
        self.d.rectangle([x, y, x + w + pad * 2, y + h + pad], fill=box)
        self.t((x + pad, y + pad // 2), s, font, txt)
        return w + pad * 2

    def trend(self, x, y, up: bool, size=16, c=INK):
        """实心=涨 / 空心=跌。没有颜色，只能靠形状。"""
        if up:
            self.d.rectangle([x, y, x + size, y + size], fill=c)
        else:
            self.d.rectangle([x, y, x + size, y + size], outline=c, width=4)

    def block(self, name, y0, y1):
        self.blocks.append((name, int(y0), int(y1)))

    def footer(self, d: dict, m: int, x1: int) -> int:
        y = H - 44 - 68
        self.rule(y - 18, m, x1, 3, LIGHT)
        self.t((m, y), d["foot_l"], self.f.sans(26), GRAY)
        self.t((x1, y), FOOT_LEGEND, self.f.sans(26), GRAY, anchor="ra")
        self.t((m, y + 34), d["foot_m"], self.f.sans(26), GRAY)
        self.block("页脚", y - 18, y + 68)
        return y - 18


# ---- 最坏情况内容 --------------------------------------------------------
# 全部按需求里"故意偏长"的那组：2 条预警（第一条 17 字）、4 天预报、
# 6 个数据格、3 个指数（纳斯达克100 29,446.98 +1.73%）、4 条速览
# （标题 22 字 + 摘要 34 字上下）、农历/干支/节气同时出现。

def worst_data() -> dict:
    return {
        "place": "杭州 · 临平区",
        "cal": {
            "month": "2026 年 9 月", "day": "22", "week": "星期二",
            "lunar": "八月十二",
            "ganzhi": ["丙午年 属马", "丁酉月 己亥日 · 小月"],
            "term": ["白露 第 16 天", "距秋分 1 天"],
            "yi": "祭祀 祈福 开市", "ji": "服药 栽种",
            "chong": "冲蛇 煞西 · 满日", "festival": "",
        },
        "wx": {
            "temp": "30°", "desc": "多云转小雨", "icon": "sun_cloud",
            "cells": [("体感", "33°"), ("湿度", "78%"), ("降水", "60%"),
                      ("东南风", "3 级"), ("空气", "轻度 118"), ("紫外", "6")],
            "warn": ["杭州市气象台发布强对流天气黄色预警",
                     "杭州市气象台发布雷电黄色预警信号"],
            "fc": [("今天", "sun_cloud", "多云转小雨", "30°", "24°"),
                   ("明天", "drizzle", "小毛毛雨", "29°", "23°"),
                   ("周四", "rain", "中雨", "27°", "22°"),
                   ("周五", "cloud", "阴转多云", "30°", "23°")],
        },
        "quotes": [("纳斯达克100", "29,446.98", 1.73),
                   ("标普500", "7,765.42", -1.14),
                   ("上证指数", "3,955.10", 0.14)],
        "digest": [
            ("游戏", "史克威尔艾尼克斯确认明年春季登陆PS5",
             "系列收官作首次加入双主角并行叙事结构，并同步公开实机演示。"),
            ("本地", "临平区三条断头路年内打通通勤时间减半",
             "涉及乔司与翁梅两个片区，施工期间公交 492 路将临时改道行驶。"),
            ("财经", "央行宣布下调存款准备金率零点五个百分点",
             "释放长期资金约一万亿元，机构预计年内仍有进一步降准空间。"),
            ("科技", "国产大模型发布全新版本推理与记忆能力大提升",
             "数学与代码两项基准刷新纪录，API 价格同步下调至原来的三分之一。"),
        ],
        "foot_l": "更新 09-22 13:16",
        "foot_m": "Kindle Paperwhite 3 · 天气 Open-Meteo · 行情 腾讯",
    }


def minimal_data() -> dict:
    """精简态：速览关、行情关、只剩 2 格、0 预警、1 天预报、无干支节气宜忌。"""
    d = worst_data()
    d["wx"]["cells"] = d["wx"]["cells"][:2]
    d["wx"]["warn"] = []
    d["wx"]["fc"] = d["wx"]["fc"][:1]
    d["quotes"] = []
    d["digest"] = []
    d["cal"]["ganzhi"] = []
    d["cal"]["term"] = []
    d["cal"]["yi"] = d["cal"]["ji"] = d["cal"]["chong"] = ""
    return d


# ---- 共用小件 ------------------------------------------------------------

def warn_lines(sh: Sheet, wx: dict, x: int, y: int, max_w: int) -> int:
    """预警 0~2 条。第一条带反白「预警」块（全屏两个强调块名额之一）。"""
    for i, wtext in enumerate(wx["warn"][:2]):
        if i == 0:
            bw = sh.stamp(x, y - 5, "预警", sh.f.sans(24))
        else:
            bw = sh.tw("预警", sh.f.sans(24)) + 24
        sh.t((x + bw + 14, y), sh.clip(wtext, sh.f.sans(26), max_w - bw - 14),
             sh.f.sans(26), INK)
        y += 38
    return y


def fc_vertical(sh: Sheet, wx: dict, x: int, y: int, cell_w: int, n: int) -> int:
    """预报竖格：图标 / 星期 / 描述 / 高低温，四行。"""
    for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:n]):
        cx = x + i * cell_w + cell_w // 2
        sh.icons.icon(cx, y + 26, 52, kind, SOFT)
        sh.ink(cx, y + 62, y + 92, label, sh.f.sans(26), GRAY, anchor="mt")
        sh.ink(cx, y + 96, y + 126, sh.clip(desc, sh.f.sans(26), cell_w - 10),
               sh.f.sans(26), INK, anchor="mt")
        sh.ink(cx, y + 130, y + 164, hi + " / " + lo, sh.f.sans(28), SOFT, anchor="mt")
    return y + 168


def quotes_row(sh: Sheet, quotes, x: int, y: int, col_w: int, center: bool = False):
    """三列指数：名称 / 点位 / 涨跌。center=True 时每列居中（方向三用）。"""
    for i, (name, price, pct) in enumerate(quotes[:3]):
        cx0 = x + i * col_w
        ax = cx0 + col_w // 2 if center else cx0
        an = "mt" if center else "lt"
        sh.ink(ax, y, y + 30, name, sh.f.sans(26), GRAY, anchor=an)
        sh.ink(ax, y + 34, y + 82, price, sh.f.sans(44, True), INK, anchor=an)
        up = pct >= 0
        pf = sh.f.sans(28)
        pw = sh.tw(f"{pct:+.2f}%", pf)
        if center:
            sh.trend(ax - pw // 2 - 24, y + 96, up, 14)
            sh.ink(ax + 6, y + 90, y + 122, f"{pct:+.2f}%", pf, INK if up else SOFT,
                   anchor="mt")
        else:
            sh.trend(ax, y + 96, up, 14)
            sh.ink(ax + 24, y + 90, y + 122, f"{pct:+.2f}%", pf, INK if up else SOFT)
    return y + 128


# =====================================================================
#  方向一「轴」：上段左右分栏（左轴=两米外要认的），下段通栏速览
# =====================================================================

def layout_rail(sh: Sheet, d: dict) -> None:
    M = 48
    RAIL_X1 = 348
    RULE_X = 376
    CX0, CX1 = 404, W - M          # 右栏 404..1024，宽 620
    CW = CX1 - CX0
    cal, wx = d["cal"], d["wx"]

    # ---- 左轴 ----
    y = 56
    sh.t((M, y), d["place"], sh.f.sans(26), GRAY)
    y += 40
    day_top = y
    sh.ink(M, day_top, day_top + 210, cal["day"], sh.f.serif(210))
    y = day_top + 222
    sh.t((M, y), cal["month"], sh.f.sans(30), INK)
    y += 42
    sh.t((M, y), cal["week"], sh.f.sans(38, True), INK)
    y += 56
    sh.rule(y, M, RAIL_X1, 3, LIGHT)
    y += 20
    sh.t((M, y), cal["lunar"], sh.f.serif(44), INK)
    y += 62
    for line in cal["ganzhi"]:
        sh.t((M, y), line, sh.f.sans(26), GRAY)
        y += 36
    for line in cal["term"]:
        sh.t((M, y), line, sh.f.sans(26), GRAY)
        y += 36
    if cal["yi"]:
        y += 4
        sh.t((M, y), "宜", sh.f.sans(28, True), INK)
        sh.t((M + 40, y), cal["yi"], sh.f.sans(26), SOFT)
        y += 38
    if cal["ji"]:
        sh.t((M, y), "忌", sh.f.sans(28, True), GRAY)
        sh.t((M + 40, y), cal["ji"], sh.f.sans(26), GRAY)
        y += 38
    if cal["chong"]:
        sh.t((M, y), cal["chong"], sh.f.sans(26), GRAY)
        y += 36
    rail_end = y
    sh.block("日历轴", 56, rail_end)

    # 行情住进轴的下段（轴比右栏长，别浪费这段空白）
    if d["quotes"]:
        y += 22
        sh.rule(y, M, RAIL_X1, 3, LIGHT)
        y += 22
        q0 = y
        for name, price, pct in d["quotes"]:
            sh.t((M, y), name, sh.f.sans(26), GRAY)
            sh.t((M, y + 32), price, sh.f.sans(40, True), INK)
            up = pct >= 0
            sh.trend(M, y + 92, up)
            sh.t((M + 26, y + 82), f"{pct:+.2f}%", sh.f.sans(28), INK if up else SOFT)
            y += 138
        sh.block("行情", q0, y)
        rail_end = y

    # ---- 右栏：天气 ----
    y = TOP
    sh.icons.icon(CX0 + 44, y + 66, 88, wx["icon"], SOFT)
    tw_ = sh.big(CX0 + 112, y, y + 140, wx["temp"], sh.f.serif(140))
    sh.ink(CX0 + 112 + tw_ + 26, y + 56, y + 140, wx["desc"], sh.f.sans(38), SOFT)
    y += 160
    if wx["warn"]:
        y += 6
        w0 = y
        y = warn_lines(sh, wx, CX0, y, CW)
        sh.block("预警", w0, y)
        y += 10
    g0 = y
    colw = CW // 2
    for i, (label, value) in enumerate(wx["cells"][:6]):
        cx = CX0 + (i % 2) * colw
        cy = y + (i // 2) * 58
        lw = sh.tw(label, sh.f.sans(24))
        sh.t((cx, cy + 6), label, sh.f.sans(24), GRAY)
        sh.t((cx + lw + 12, cy), sh.clip(value, sh.f.sans(32), colw - lw - 20),
             sh.f.sans(32, True), INK)
    y += 3 * 58 + 4
    sh.block("数据格", g0, y)
    y += 16
    f0 = y
    y = fc_vertical(sh, wx, CX0, y, CW // max(1, len(wx["fc"])), len(wx["fc"]))
    sh.block("预报", f0, y)
    y += 20

    # 速览住右栏：标签一行 / 标题一行 / 摘要一行（摘要按栏宽截断，规则见规格）
    if d["digest"]:
        d0 = y
        sh.t((CX0, y), "今日速览", sh.f.sans(30, True), INK)
        sh.rule(y + 42, CX0, CX1, 3, LIGHT)
        y += 58
        for tag, title, summ in d["digest"][:4]:
            sh.t((CX0, y), tag, sh.f.sans(24), GRAY)
            y += 28
            sh.t((CX0, y), sh.clip(title, sh.f.sans(28, True), CW), sh.f.sans(28, True), INK)
            y += 38
            sh.t((CX0, y), sh.clip(summ, sh.f.sans(26), CW), sh.f.sans(26), SOFT)
            y += 42
        sh.block("速览", d0, y)
    right_end = y
    sh.vrule(RULE_X, TOP, max(right_end, rail_end), 3, LIGHT)
    sh.block("右栏(天气+速览)", TOP, right_end)

    sh.footer(d, M, W - M)


# =====================================================================
#  方向二「报」：全宽横向条带，报头 = 图标+日期+温度同基线 + 一行日期线
# =====================================================================

def layout_masthead(sh: Sheet, d: dict) -> None:
    M = 44
    X1 = W - M
    CW = X1 - M
    cal, wx = d["cal"], d["wx"]

    # ---- 报头：全部挤在时钟留白区左边（x < 644）----
    y0 = 52
    bot = y0 + 148
    x = M
    sh.icons.icon(x + 42, y0 + 74, 84, wx["icon"], SOFT)
    x += 84 + 26
    dw = sh.ink(x, y0, bot, cal["day"], sh.f.serif(148))
    x += dw + 30
    sh.vrule(x, y0 + 18, bot - 18, 3, LIGHT)     # 两个巨数之间必须有东西，否则读成"22 30"
    x += 30
    tw_ = sh.big(x, y0 + 24, bot, wx["temp"], sh.f.serif(124))
    x += tw_ + 26
    sh.ink(x, y0 + 62, bot, wx["desc"], sh.f.sans(36), SOFT)
    y = bot + 12
    dateline = " · ".join([cal["month"], cal["week"], cal["lunar"], d["place"]])
    sh.t((M, y), sh.clip(dateline, sh.f.sans(28), CW), sh.f.sans(28), GRAY)
    y += 42
    sh.rule(y, M, X1, 4, INK)          # 报头下那道通栏粗线 = 报纸的脊
    y += 22
    sh.block("报头", y0, y)

    # ---- 历法两行 ----
    c0 = y
    meta = " · ".join([s for s in cal["ganzhi"] + cal["term"] if s])
    sh.t((M, y), sh.clip(meta, sh.f.sans(26), CW), sh.f.sans(26), GRAY)
    y += 36
    if cal["yi"]:
        line = ""
        sh.t((M, y), "宜", sh.f.sans(28, True), INK)
        lx = M + 40
        sh.t((lx, y), cal["yi"], sh.f.sans(26), SOFT)
        lx += sh.tw(cal["yi"], sh.f.sans(26)) + 44
        sh.t((lx, y), "忌", sh.f.sans(28, True), GRAY)
        lx += 40
        rest = X1 - lx
        jw = sh.tw(cal["ji"], sh.f.sans(26))
        ch = ("  ·  " + cal["chong"]) if cal["chong"] else ""
        if jw + sh.tw(ch, sh.f.sans(26)) <= rest:
            sh.t((lx, y), cal["ji"] + ch, sh.f.sans(26), GRAY)
        else:
            sh.t((lx, y), sh.clip(cal["ji"], sh.f.sans(26), rest), sh.f.sans(26), GRAY)
            sh.t((X1, y), cal["chong"], sh.f.sans(26), GRAY, anchor="ra")
        y += 38
    sh.block("历法", c0, y)
    y += 12
    sh.rule(y, M, X1, 3, LIGHT)
    y += 18

    # ---- 预警 / 数据格 / 预报 / 行情 / 速览 ----
    if wx["warn"]:
        w0 = y
        y = warn_lines(sh, wx, M, y, CW)
        sh.block("预警", w0, y)
        y += 10
    g0 = y
    gw = CW // 6
    for i, (label, value) in enumerate(wx["cells"][:6]):
        gx = M + i * gw
        sh.t((gx, y), label, sh.f.sans(24), GRAY)
        sh.t((gx, y + 30), sh.clip(value, sh.f.sans(32), gw - 6), sh.f.sans(32, True), INK)
    y += 74
    sh.block("数据格", g0, y)
    y += 14
    sh.rule(y, M, X1, 3, LIGHT)
    y += 18
    f0 = y
    fw = CW // max(1, len(wx["fc"]))
    for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:4]):
        cx = M + i * fw
        sh.icons.icon(cx + 30, y + 28, 56, kind, SOFT)
        sh.t((cx + 72, y + 6), label, sh.f.sans(26), GRAY)
        sh.t((cx + 72, y + 40), sh.clip(desc, sh.f.sans(26), fw - 76), sh.f.sans(26), INK)
        sh.t((cx, y + 88), hi, sh.f.sans(34, True), INK)
        sh.t((cx + sh.tw(hi, sh.f.sans(34, True)) + 12, y + 94), lo, sh.f.sans(26), GRAY)
    y += 134
    sh.block("预报", f0, y)
    if d["quotes"]:
        y += 14
        sh.rule(y, M, X1, 3, LIGHT)
        y += 18
        q0 = y
        y = quotes_row(sh, d["quotes"], M, y, CW // 3)
        sh.block("行情", q0, y)
    if d["digest"]:
        y += 14
        sh.rule(y, M, X1, 3, LIGHT)
        y += 18
        d0 = y
        sh.t((M, y), "今日速览", sh.f.sans(30, True), INK)
        y += 40
        for tag, title, summ in d["digest"][:4]:
            bw = sh.tw(tag, sh.f.sans(24)) + 16
            sh.t((M, y + 6), tag, sh.f.sans(24), GRAY)
            sh.t((M + bw, y), sh.clip(title, sh.f.sans(28, True), CW - bw),
                 sh.f.sans(28, True), INK)
            y += 38
            sh.t((M + bw, y), sh.clip(summ, sh.f.sans(26), CW - bw), sh.f.sans(26), SOFT)
            y += 38
        sh.block("速览", d0, y)

    sh.footer(d, M, X1)


# =====================================================================
#  方向三「帖」：宋体大字、居中、留白优先；速览只排标题目录
# =====================================================================

def layout_poster(sh: Sheet, d: dict) -> None:
    M = 56
    X1 = W - M
    CW = X1 - M
    cx = W // 2
    cal, wx = d["cal"], d["wx"]

    y = TOP + 8
    sh.ink(cx, y, y + 34, " · ".join([cal["month"], cal["week"], d["place"]]),
           sh.f.sans(28), GRAY, anchor="mt")
    y += 44
    day_top = y
    sh.ink(cx, day_top, day_top + 178, cal["day"], sh.f.serif(178), anchor="mt")
    y = day_top + 190
    sh.ink(cx, y, y + 50, cal["lunar"], sh.f.serif(50), anchor="mt")
    y += 66
    meta = " · ".join([s for s in cal["ganzhi"] + cal["term"] if s])
    sh.ink(cx, y, y + 34, sh.clip(meta, sh.f.sans(26), CW), sh.f.sans(26), GRAY,
           anchor="mt")
    y += 48
    sh.rule(y, cx - 110, cx + 110, 3, LIGHT)
    y += 22
    sh.block("日历帖", TOP, y)

    # 天气：一行「图标 温度 描述」，居中
    w0 = y
    temp_f = sh.f.serif(96)
    desc_f = sh.f.sans(34)
    digits = wx["temp"].rstrip("°")
    line_w = (96 + 26 + sh.tw(digits, temp_f) + 8
              + sh.tw("°", sh.f.sans(40, True)) + 26 + sh.tw(wx["desc"], desc_f))
    x = cx - line_w // 2
    sh.icons.icon(x + 48, y + 50, 96, wx["icon"], SOFT)
    x += 96 + 26
    x += sh.big(x, y, y + 100, wx["temp"], temp_f) + 26
    sh.ink(x, y + 36, y + 100, wx["desc"], desc_f, SOFT)
    y += 112
    if wx["warn"]:
        y += 8
        ww = max(sh.tw(t, sh.f.sans(26)) for t in wx["warn"][:2])
        bw = sh.tw("预警", sh.f.sans(24)) + 24
        x0 = cx - (bw + 14 + ww) // 2
        y = warn_lines(sh, wx, x0, y, X1 - x0)
        y += 6
    y += 12
    f0 = y
    fw = 236
    x0 = cx - fw * len(wx["fc"][:4]) // 2
    for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:4]):
        gx = x0 + i * fw + fw // 2
        sh.icons.icon(gx, y + 22, 44, kind, SOFT)
        sh.ink(gx, y + 52, y + 78, label, sh.f.sans(26), GRAY, anchor="mt")
        sh.ink(gx, y + 82, y + 108, sh.clip(desc, sh.f.sans(26), fw - 8),
               sh.f.sans(26), INK, anchor="mt")
        sh.ink(gx, y + 112, y + 140, hi + " / " + lo, sh.f.sans(26), SOFT, anchor="mt")
    y += 146
    sh.block("天气帖", w0, y)

    if d["quotes"]:
        y += 12
        sh.rule(y, M, X1, 3, LIGHT)
        y += 16
        q0 = y
        y = quotes_row(sh, d["quotes"], M, y, CW // 3, center=True)
        sh.block("行情", q0, y)

    if d["digest"]:
        y += 12
        sh.rule(y, M, X1, 3, LIGHT)
        y += 16
        d0 = y
        sh.ink(cx, y, y + 34, "今 日 速 览", sh.f.sans(30, True), INK, anchor="mt")
        y += 40
        for tag, title, _summ in d["digest"][:4]:
            bw = sh.tw(tag, sh.f.sans(24)) + 16
            sh.t((M, y + 4), tag, sh.f.sans(24), GRAY)
            sh.t((M + bw, y), sh.clip(title, sh.f.sans(28), CW - bw), sh.f.sans(28), INK)
            y += 40
        sh.block("速览目录", d0, y)

    sh.footer(d, M, X1)


DIRECTIONS = [
    ("一 · 轴", layout_rail,
     "上段左右分栏：左 300px 竖轴装两米外要认的（日期/农历/节气/宜忌），"
     "右栏装天气；行情住进轴的下段。下段通栏两栏速览，摘要不截。"),
    ("二 · 报", layout_masthead,
     "全宽横向条带。报头是图标+日期+温度同基线（全部让开时钟区），加一行日期线，"
     "下面一道通栏粗线当报纸的脊；速览每条压成「标题行 + 摘要行」。"),
    ("三 · 帖", layout_poster,
     "宋体 Heavy 大字、居中、留白优先。速览只排标题目录，摘要默认不上屏"
     "（开启代价见规格）。"),
]

SPEC = {
    "一 · 轴": dict(
        grid="左右边距 48。左轴 x 48–348（300 宽）；竖细线 x=376（3px／195）；"
             "右栏 x 404–1024（620 宽）。页脚通栏。竖线长度 = 两栏中较长者，自动收放。",
        type=[
            ("日期", "210", "宋 Heavy", "0"), ("月年 / 星期", "30 / 38", "黑 / 黑 B", "0"),
            ("农历", "44", "宋 Heavy", "0"), ("干支·节气·忌·冲煞", "26", "黑", "130"),
            ("宜（宜目）", "28 B（26）", "黑", "0（70）"),
            ("温度 / 描述", "140 / 38", "宋 Heavy / 黑", "0 / 70"),
            ("预警正文", "26", "黑", "0"), ("数据格 标签／值", "24 / 32", "黑 / 黑 B", "130 / 0"),
            ("预报 星期／描述／温度", "26 / 26 / 28", "黑", "130 / 0 / 70"),
            ("速览 标签／标题／摘要", "24 / 28 B / 26", "黑", "130 / 0 / 70"),
            ("行情 名／点位／涨跌", "26 / 40 B / 28", "黑", "130 / 0 / 0|70"),
            ("页脚", "26", "黑", "130"),
        ],
        lead="行高不另设：一律按字体实测行盒（Noto Sans SC 26px ≈ 34px，28px ≈ 37px），"
             "块间距 10–26px 靠留白拉开。",
        modules="日历固定左轴、天气+速览固定右栏（想换主次 = 换方向二，不是换顺序）。"
                "行情住轴下段：关掉后轴底变留白，竖线不变。速览关掉：右栏底变留白，"
                "竖线缩短到天气底。预警 0 条：右栏整体上移 86px，余量进底部。",
        clip="速览摘要 620 栏宽 × 26px = 一行 23 字，超出砍尾补 …（34 字摘要会砍掉末 11 字）；"
             "标题 22 字 × 28 = 616 ≤ 620 不截；预警 17 字 = 442 ≤ 520 不截；"
             "预报描述格宽 155，5 字不截、6 字起截。",
    ),
    "二 · 报": dict(
        grid="边距 44，全宽通栏条带。报头高 148（两个巨数同基线，中间 3px 竖细线）；"
             "报头下 4px／0 通栏粗线当脊；其余条带间 3px／195 细线。",
        type=[
            ("日期 / 温度", "148 / 124", "宋 Heavy", "0"),
            ("日期线", "28", "黑", "130"), ("历法行", "26（宜忌头 28 B）", "黑", "130"),
            ("预警正文", "26", "黑", "0"), ("数据格 标签／值", "24 / 32", "黑 / 黑 B", "130 / 0"),
            ("预报 星期／描述／温度", "26 / 26 / 34 B+26", "黑", "130 / 0 / 0+130"),
            ("行情 名／点位／涨跌", "26 / 44 B / 28", "黑", "130 / 0 / 0|70"),
            ("速览 标签／标题／摘要", "24 / 28 B / 26", "黑", "130 / 0 / 70"),
            ("页脚", "26", "黑", "130"),
        ],
        lead="同上，行高取字体实测行盒；条带之间另加 12–18px 纯间距。",
        modules="五块全是通栏条带，顺序就是堆叠顺序，任意换序只改 y 的累加起点。"
                "任一块关掉：它的条带和上下细线一起消失，下方整体上移，"
                "回收的高度进底部留白（不摊进各块，避免换开关就换版面）。",
        clip="速览标题 22 字 × 28 + 标签 66 = 682 ≤ 984 不截；摘要 34 字 × 26 + 66 = 950 ≤ 984 "
             "不截（余 34px）；日期线 33 字 × 28 = 924 ≤ 984 不截；历法合并行 34 字 × 26 = 884 不截。"
             "本方向在最坏情况下没有任何一处触发截断。",
    ),
    "三 · 帖": dict(
        grid="边距 56，单轴居中。日历区下方一条 220 宽居中短细线；天气／行情／速览之间通栏细线。",
        type=[
            ("日期 / 农历", "178 / 50", "宋 Heavy", "0"),
            ("顶部日期线 / 干支节气行", "28 / 26", "黑", "130"),
            ("温度 / 描述", "96 / 34", "宋 Heavy / 黑", "0 / 70"),
            ("预警正文", "26", "黑", "0"), ("预报 三行", "26", "黑", "130 / 0 / 70"),
            ("行情 名／点位／涨跌", "26 / 44 B / 28", "黑（居中）", "130 / 0 / 0|70"),
            ("速览目录 标签／标题", "24 / 28", "黑", "130 / 0"),
            ("页脚", "26", "黑", "130"),
        ],
        lead="行高取实测行盒；块间 12–16px，其余全靠大留白（顶部 148 起、块间 30–40）。",
        modules="速览默认只排标题目录（4 行）。开启摘要 = 每条 +74px（34 字 × 26 = 884 ≤ 960，"
                "一行放得下、不截），4 条共 +296px &gt; 52px 余量 → 降级规则：先把日期 178→140、"
                "预报改横排（回收约 150px），仍不够则速览整块退到方向一右栏的两段式排法。"
                "行情关掉回收 144px，直接变留白（这一版卖的就是留白）。",
        clip="目录标题 22 字 × 28 + 标签 66 = 682 ≤ 960 不截；干支+节气合并行 34 字 × 26 = 884 ≤ 960 "
             "不截；预警 17 字居中排不截。",
    ),
}

COMMON_SPEC = f"""
画布 1072×1448，灰阶只用 0 / 70 / 130 / 195 / 255 五档；无渐变、无阴影、无背景图。
正文最小 26px；24px 只给「标签」（数据格名、速览栏目名），不算正文 —— 若你要求标签也 ≥26，
三版各自再扣约 10px 余量，仍都成立。每屏反白强调块 ≤ 2：现在每屏只用 1 个（「预警」），
节日徽章启用时用第 2 个。字重两种：黑体 Regular / Bold；宋体 Heavy 只作显示字（日期、温度数字）。
<br><br>
<b>时钟区</b>：三个方向共用同一个留白矩形 {CLOCK}（= 现网精灵图矩形，换方向不用重拷 clock/）。
模式 a（默认）= 矩形留白；模式 b = 把时间画进这个矩形，字号位置与精灵图一致，不需要重排；
模式 c = 矩形就是纯留白。没有任何元素依赖它「被填上」，所以拿掉时钟版面不塌。
出图后对这块矩形做逐像素自检，画脏了脚本直接报错。
<br><br>
<b>字体落地</b>：正文 Noto Sans SC Regular/Bold（本机与云端现用）；显示字本机用
Source Han Serif SC Heavy（思源宋体 = Noto Serif CJK 的同一套字形）。云端若装的是
Debian/Ubuntu fonts-noto-cjk，用 <code>fc-list | grep -i "serif cjk"</code> 确认带 Noto Serif CJK；
确认不到就退回 Noto Sans SC Bold 当显示字 —— 数字宽度差 &lt;3%，版面几何不用改。
<br><br>
<b>空间回收总规则</b>：关掉任何一块，回收的高度一律变成底部留白 —— 不重排、不放大、
不摊进各块内边距。理由：这块屏一天只重画四次，若开一个开关整页字就跳一档，
挂在墙上的人每天看到的"地图"都不一样。精简态三张图（余量 628 / 736 / 522px）就是这个
规则的样子：上半页锚定不动，下半页变留白。
"""

DICT_DESC = {name: desc for name, _fn, desc in DIRECTIONS}


def build_html(shots, minimal_shots, reports) -> str:
    cl, ct = CLOCK[0] / W * 100, CLOCK[1] / H * 100
    cw, ch = (CLOCK[2] - CLOCK[0]) / W * 100, (CLOCK[3] - CLOCK[1]) / H * 100
    cards = []
    for (name, png, slack), (_mn, mpng, mslack) in zip(shots, minimal_shots):
        sp = SPEC[name]
        rows = "".join(
            f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>"
            for a, b, c, d in sp["type"])
        cards.append(f"""
    <section>
      <h2>方向{name}</h2>
      <p class="d">{DICT_DESC[name]}</p>
      <div class="pair">
        <figure><div class="shot"><img src="{png}" alt="方向{name} 最坏情况">
          <span class="clock" style="left:{cl}%;top:{ct}%;width:{cw}%;height:{ch}%"></span></div>
          <figcaption>最坏情况 · 底边余量 <b>{slack}px</b></figcaption></figure>
        <figure><div class="shot"><img src="{mpng}" alt="方向{name} 精简态">
          <span class="clock" style="left:{cl}%;top:{ct}%;width:{cw}%;height:{ch}%"></span></div>
          <figcaption>精简态（速览关·行情关·2 格·0 预警·1 天预报）· 余量 <b>{mslack}px</b>
          </figcaption></figure>
      </div>
      <h3>栅格与间距</h3><p>{sp['grid']}</p>
      <h3>字阶 / 字重 / 灰阶</h3>
      <table><tr><th>元素</th><th>字号 px</th><th>字族字重</th><th>灰阶</th></tr>{rows}</table>
      <p class="s">{sp['lead']}</p>
      <h3>模块取舍与空间回收</h3><p>{sp['modules']}</p>
      <h3>截断规则（最坏情况下谁被砍）</h3><p>{sp['clip']}</p>
    </section>""")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>信息屏 · 三个版面方向</title>
<style>
 body{{margin:0;background:#eceef0;color:#1c1f23;
      font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
 header{{padding:24px 28px 16px;border-bottom:1px solid #d6d9dd;background:#fff}}
 h1{{margin:0 0 6px;font-size:21px}} header p{{margin:0;color:#5b6470;font-size:13.5px}}
 main{{padding:24px 28px 80px;max-width:1500px}}
 section{{background:#fff;border:1px solid #d6d9dd;border-radius:12px;
          padding:18px 22px 24px;margin:0 0 26px}}
 h2{{margin:0 0 4px;font-size:19px}} h3{{margin:18px 0 6px;font-size:14.5px}}
 .d{{margin:0 0 14px;color:#5b6470;font-size:13.5px}}
 .pair{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
 figure{{margin:0}} .shot{{position:relative;line-height:0}}
 img{{width:100%;height:auto;border:1px solid #cfd3d8;border-radius:4px;background:#fff}}
 .clock{{position:absolute;border:2px dashed #b42318;border-radius:3px;
         box-sizing:border-box}}
 figcaption{{margin-top:6px;font-size:12.5px;color:#3c434b}}
 table{{border-collapse:collapse;font-size:13px;margin:4px 0}}
 td,th{{border:1px solid #e2e5e9;padding:4px 10px;text-align:left}}
 th{{background:#f5f6f7}}
 .s{{font-size:12.5px;color:#5b6470}}
 .common{{background:#fff;border:1px solid #d6d9dd;border-left:4px solid #1c1f23;
          border-radius:10px;padding:14px 18px;font-size:13.5px;margin:0 0 26px}}
 code{{background:#f2f3f5;padding:1px 5px;border-radius:4px;font-size:12px}}
 pre{{background:#f7f8f9;border:1px solid #e2e5e9;border-radius:8px;padding:10px 14px;
      font-size:12px;overflow:auto}}
</style></head><body>
<header><h1>信息屏 · 三个版面方向（1072×1448 灰度真图）</h1>
<p>每张都是脚本当场画的真成品图，不是示意图。红虚线框 = 右上角留给本机时钟的留白区
（图上不画时间）。精简态证明关掉模块后版面不塌。</p></header>
<main>
  <div class="common">{COMMON_SPEC}</div>
  {''.join(cards)}
  <section><h2>纵向预算实测（最坏情况 + 精简态）</h2>
  <pre>{reports}</pre></section>
</main></body></html>
"""


# =====================================================================
#  帖 v2：看完真机反馈（"字有点小、下半页空"）之后的三个配平方案。
#  共同规则：整组内容在 [时钟区下沿, 页脚上沿] 之间**垂直居中**；
#  块距写死不随内容伸缩 —— 开关模块只平移整组，不缩放，墙上的"地图"不跳。
#  尺寸按最坏情况（2 条预警）配平，余量 ≥40px。
# =====================================================================

POSTER2 = {
    "甲 · 大字": dict(
        margin=56, line=30, day=232, lunar=62, meta=28, temp=118, desc=38,
        warn=28, fc=28, fc_icon=58, q_name=28, q_price=52, q_pct=32,
        gaps=dict(d1=26, d2=14, d3=18, d4=30, d5=30, d6=16, d7=22, d8=22, d9=18),
        cells=False,
        note="纯放大：日期 232、温度 118、预报 28，块距一起拉开；不加任何新信息。",
    ),
    "乙 · 大字+数据格": dict(
        margin=56, line=28, day=188, lunar=52, meta=26, temp=100, desc=34,
        warn=26, fc=26, fc_icon=48, q_name=26, q_price=46, q_pct=30,
        gaps=dict(d1=20, d2=12, d3=14, d4=24, d5=22, d6=14, d7=16, d8=18, d9=16),
        cells=True,
        note="放大 + 把体感/湿度/降水/风/空气/紫外六格请回来（居中 3 列 2 行），用信息填空间。",
    ),
    "丙 · 疏朗": dict(
        margin=64, line=28, day=206, lunar=58, meta=26, temp=108, desc=36,
        warn=26, fc=28, fc_icon=56, q_name=26, q_price=50, q_pct=30,
        gaps=dict(d1=34, d2=18, d3=24, d4=40, d5=40, d6=20, d7=32, d8=32, d9=24),
        cells=False,
        note="字中等偏大、块距最松：靠呼吸感占满，上下留白对称。",
    ),
}


def _poster2_body(sh: Sheet, d: dict, P: dict, y: int) -> int:
    """画帖 v2 主体，返回下边缘。位置全部相对 y，所以可以先在草稿上量一遍再真画。"""
    M = P["margin"]
    X1 = W - M
    CW = X1 - M
    cx = W // 2
    g = P["gaps"]
    cal, wx = d["cal"], d["wx"]

    sh.ink(cx, y, y + 40, " · ".join([cal["month"], cal["week"], d["place"]]),
           sh.f.sans(P["line"]), GRAY, anchor="mt")
    y += 40 + g["d1"]
    sh.ink(cx, y, y + P["day"], cal["day"], sh.f.serif(P["day"]), anchor="mt")
    y += P["day"] + g["d2"]
    sh.ink(cx, y, y + P["lunar"], cal["lunar"], sh.f.serif(P["lunar"]), anchor="mt")
    y += P["lunar"] + g["d3"]
    meta = " · ".join(cal["ganzhi"] + cal["term"])
    sh.ink(cx, y, y + 36, sh.clip(meta, sh.f.sans(P["meta"]), CW),
           sh.f.sans(P["meta"]), GRAY, anchor="mt")
    y += 36 + g["d4"]
    sh.rule(y, cx - 120, cx + 120, 3, LIGHT)
    y += g["d5"]

    tf = sh.f.serif(P["temp"])
    df = sh.f.sans(P["desc"])
    icon_w = int(P["temp"] * P.get("icon_ratio", 0.78))
    line_w = (icon_w + 26 + sh.tw(wx["temp"].rstrip("°"), tf) + 4
              + sh.tw("°", sh.f.sans(int(P["temp"] * 0.46), True)) + 26
              + sh.tw(wx["desc"], df))
    x = cx - line_w // 2
    sh.icons.icon(x + icon_w / 2, y + P["temp"] * 0.40, icon_w, wx["icon"], SOFT)
    x += icon_w + 26
    x += sh.big(x, y, y + P["temp"], wx["temp"], tf) + 26
    sh.ink(x, y + P["temp"] * 0.36, y + P["temp"], wx["desc"], df, SOFT)
    y += P["temp"] + g["d6"]

    if P["cells"]:
        cw = 300
        x0 = cx - cw * 3 // 2
        for i, (label, value) in enumerate(wx["cells"][:6]):
            gx = x0 + (i % 3) * cw + cw // 2
            gy = y + (i // 3) * 78
            sh.ink(gx, gy, gy + 30, label, sh.f.sans(24), GRAY, anchor="mt")
            sh.ink(gx, gy + 34, gy + 74, value, sh.f.sans(34, True), INK, anchor="mt")
        y += 2 * 78 + g["d6"]

    if wx["warn"]:
        ww = max(sh.tw(t, sh.f.sans(P["warn"])) for t in wx["warn"][:2])
        bw = sh.tw("预警", sh.f.sans(24)) + 24
        x0 = cx - (bw + 14 + ww) // 2
        for i, t in enumerate(wx["warn"][:2]):
            used = sh.stamp(x0, y - 5, "预警", sh.f.sans(24)) if i == 0 else bw
            sh.t((x0 + used + 14, y),
                 sh.clip(t, sh.f.sans(P["warn"]), X1 - x0 - used - 14),
                 sh.f.sans(P["warn"]), INK)
            y += P["warn"] + 12
        y += g["d7"]

    fw = 236
    x0 = cx - fw * len(wx["fc"][:4]) // 2
    r1 = P["fc_icon"] + 8
    r2 = r1 + P["fc"] + 12
    r3 = r2 + P["fc"] + 12
    for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:4]):
        gx = x0 + i * fw + fw // 2
        sh.icons.icon(gx, y + P["fc_icon"] // 2, P["fc_icon"], kind, SOFT)
        sh.ink(gx, y + r1, y + r1 + P["fc"] + 8, label, sh.f.sans(P["fc"]), GRAY,
               anchor="mt")
        sh.ink(gx, y + r2, y + r2 + P["fc"] + 8,
               sh.clip(desc, sh.f.sans(P["fc"]), fw - 8), sh.f.sans(P["fc"]), INK,
               anchor="mt")
        sh.ink(gx, y + r3, y + r3 + P["fc"] + 8, hi + " / " + lo, sh.f.sans(P["fc"]),
               SOFT, anchor="mt")
    y += r3 + P["fc"] + 8 + g["d8"]

    sh.rule(y, M, X1, 3, LIGHT)
    y += g["d9"]
    cw3 = CW // 3
    for i, (name, price, pct) in enumerate(d["quotes"][:3]):
        gx = M + i * cw3 + cw3 // 2
        sh.ink(gx, y, y + P["q_name"] + 8, name, sh.f.sans(P["q_name"]), GRAY,
               anchor="mt")
        sh.ink(gx, y + P["q_name"] + 12, y + P["q_name"] + 12 + P["q_price"] + 6,
               price, sh.f.sans(P["q_price"], True), INK, anchor="mt")
        py = y + P["q_name"] + 12 + P["q_price"] + 16
        up = pct >= 0
        pf = sh.f.sans(P["q_pct"])
        pw = sh.tw(f"{pct:+.2f}%", pf)
        sh.trend(gx - pw // 2 - 22, py + 8, up, 14)
        sh.ink(gx + 6, py, py + P["q_pct"] + 8, f"{pct:+.2f}%", pf,
               INK if up else SOFT, anchor="mt")
    return y + P["q_name"] + 12 + P["q_price"] + 16 + P["q_pct"] + 8


def layout_poster2(sh: Sheet, d: dict, P: dict) -> int:
    foot_top = sh.footer(d, P["margin"], W - P["margin"])
    top = P.get("top", TOP)
    if P.get("bat"):
        BAT_RECT[P["bat"]](sh, d.get("battery", 86))
    probe = Sheet(sh.f)
    height = _poster2_body(probe, d, P, 0)
    extra = foot_top - top - height
    y = top + max(0, extra // 2)
    y1 = _poster2_body(sh, d, P, y)
    sh.block("主体", y, y1)
    return extra


# ---- 电量样式（右上角固定矩形，将来做成精灵图由 Kindle 贴）------------
# 电量只有设备自己知道，云端摸不到电池 —— 所以这块和当初的时钟同一条路：
# 图上留白，Kindle 按档位贴精灵图。矩形固定 (814,40)-(1016,96)，四版共用，
# 换样式不用改版面。
BAT_BOX = (W - 56 - 202, 40, W - 56, 96)


def _bat_level_segments(sh, x, y, w, h, level, n=5):
    """电池壳 + n 格电量。空格用 195 当轨道，不用空心框（小尺寸下框线会糊）。"""
    sh.d.rectangle([x, y, x + w, y + h], outline=INK, width=4)
    sh.d.rectangle([x + w + 4, y + h // 2 - 8, x + w + 12, y + h // 2 + 8], fill=INK)
    filled = round(level / 100 * n)
    gap = 6
    sw = (w - 12 - gap * (n - 1)) / n
    for i in range(n):
        sx = x + 6 + i * (sw + gap)
        sh.d.rectangle([sx, y + 7, sx + sw, y + h - 7],
                       fill=INK if i < filled else LIGHT)


def bat_a(sh, level):
    """A · 五格电池 + 百分比数字"""
    x1, y0, x2, y1 = BAT_BOX
    gf = sh.f.sans(34, True)
    gw = sh.tw(f"{level}%", gf)
    bw = 96
    bx = x2 - gw - 14 - bw
    cy = (y0 + y1) // 2
    _bat_level_segments(sh, bx, cy - 22, bw, 44, level)
    sh.ink(bx + bw + 14, y0, y1, f"{level}%", gf, INK)


def bat_b(sh, level):
    """B · 百分比大字 + 一根进度条"""
    x1, y0, x2, y1 = BAT_BOX
    gf = sh.f.sans(38, True)
    sh.t((x2, y0 + 2), f"{level}%", gf, INK, anchor="rt")
    by = y1 - 14
    sh.d.rectangle([x1, by, x2, by + 10], fill=LIGHT)
    sh.d.rectangle([x1, by, x1 + (x2 - x1) * level // 100, by + 10], fill=INK)


def bat_c(sh, level):
    """C · 圆环量表 + 百分比"""
    x1, y0, x2, y1 = BAT_BOX
    r = 27
    cx, cy = x1 + r + 4, (y0 + y1) // 2
    sh.d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=LIGHT, width=9)
    sh.d.arc([cx - r, cy - r, cx + r, cy + r], start=-90,
             end=-90 + 360 * level // 100, fill=INK, width=9)
    gf = sh.f.sans(32, True)
    sh.ink(cx + r + 16, y0, y1, f"{level}%", gf, INK)


def bat_d(sh, level):
    """D · 极简：小电池轮廓 + 一行灰字"""
    x1, y0, x2, y1 = BAT_BOX
    gf = sh.f.sans(26)
    txt = f"电量 {level}%"
    tw = sh.tw(txt, gf)
    bw = 46
    bx = x2 - tw - 12 - bw
    cy = (y0 + y1) // 2
    _bat_level_segments(sh, bx, cy - 12, bw, 24, level, n=4)
    sh.ink(bx + bw + 12, y0, y1, txt, gf, GRAY)


BAT_RECT = {"A": bat_a, "B": bat_b, "C": bat_c, "D": bat_d}

# ---- 字阶放大三案（2026-09-22 第二轮反馈："字体再大几号、天气图标也大几号"）----
# 底座都是丙 + 电量 B。放大必须从块距里扣：最坏情况（2 预警）的可用高度是定死的。
POSTER3 = {
    "甲 · 大一号": dict(
        margin=64, line=30, day=224, lunar=62, meta=26, temp=118, desc=38,
        warn=28, fc=30, fc_icon=64, q_name=26, q_price=54, q_pct=32,
        gaps=dict(d1=30, d2=16, d3=22, d4=36, d5=36, d6=18, d7=28, d8=28, d9=22),
        cells=False, icon_ratio=0.82,
        note="字整体 +9% 左右：日期 224、温度 118、预报 30；主图标 96、预报图标 64。",
    ),
    "乙 · 大两号": dict(
        margin=64, line=32, day=244, lunar=66, meta=28, temp=128, desc=40,
        warn=28, fc=32, fc_icon=72, q_name=28, q_price=58, q_pct=32,
        gaps=dict(d1=26, d2=14, d3=18, d4=32, d5=32, d6=16, d7=24, d8=26, d9=20),
        cells=False, icon_ratio=0.84,
        note="字 +18%：日期 244、温度 128、预报 32；主图标 108、预报图标 72。块距收到最紧。",
    ),
    "丙 · 大图标": dict(
        margin=64, line=30, day=224, lunar=62, meta=26, temp=118, desc=38,
        warn=28, fc=30, fc_icon=84, q_name=26, q_price=54, q_pct=32,
        gaps=dict(d1=28, d2=14, d3=20, d4=34, d5=34, d6=16, d7=26, d8=26, d9=20),
        cells=False, icon_ratio=1.0,
        note="字同甲，但图标当第二主角：主图标和温度数字一样高（118），预报图标 84。",
    ),
    "丁 · 乙+大图标": dict(
        margin=64, line=32, day=244, lunar=66, meta=28, temp=128, desc=40,
        warn=28, fc=32, fc_icon=88, q_name=28, q_price=58, q_pct=32,
        gaps=dict(d1=22, d2=12, d3=16, d4=26, d5=26, d6=14, d7=20, d8=22, d9=18),
        cells=False, icon_ratio=1.0,
        note="乙的字（日期 244 / 温度 128）+ 丙的图标比例：主图标 128、预报图标 88。"
             "块距收到最紧给图标让地方。",
    ),
}
BAT_NOTE = {
    "A": "五格电池 + 数字。最像电池，远看格子数就是电量。",
    "B": "百分比大字 + 一根进度条。数字最大最好读，条只是辅助。",
    "C": "圆环量表。最设计感，但墨水屏上细圆环远看容易断线。",
    "D": "极简灰字 + 小电池。最安静，把右上角彻底让给留白。",
}

# ---- 巨大四案（2026-09-22 第三轮反馈："还是小、还是空，要巨大的"）----------
# "空"的根源不只是字号：居中单栏每行墨迹只占中间一条，两侧整条留白。
# 所以这一轮除了放大还动结构 —— 乙用双栏报头吃宽度，丙用横带把整页填死。
# 甲/丁仍是居中单栏，靠砍行（干支节气行、指数行）换字号。
POSTER4 = {
    "甲 · 单栏砍干支": dict(
        mode="stack", meta=False, quotes=True,
        margin=64, line=34, day=288, lunar=72, meta_fs=28, temp=168, desc=44,
        warn=28, fc=34, fc_icon=100, q_name=28, q_price=62, q_pct=32,
        gaps=dict(d1=16, d2=8, d3=14, d4=0, d5=18, d6=10, d7=14, d8=16, d9=14),
        icon_ratio=1.0,
        note="结构不变，只砍干支节气那一行换字号：日期 288 / 温度 168 / 主图标 168。"
             "代价：黄历信息全没（农历还在）。",
    ),
    "乙 · 双栏报头": dict(
        mode="mast", meta=True, quotes=True,
        margin=64, line=30, day=420, lunar=84, lunar_day=104, tag=52,
        place_fs=32, meta_fs=28, temp=160, desc=46,
        warn=32, fc=38, fc_icon=112, q_name=30, q_price=62, q_pct=36,
        gaps=dict(d1=18, d2=10, d3=10, d4=0, d5=20, d6=12, d7=16, d8=18, d9=16),
        icon_ratio=0.95,
        note="双栏报头：左上角挂农历月 + 公历月周，左栏日期 420 + 农历日（十二日，104），"
             "右栏图标 + 温度 + 天气词 + 地名。一行不砍。",
    ),
    "丙 · 横带满版": dict(
        mode="bands", meta=True, quotes=True,
        margin=64, line=36, day=240, lunar=64, meta_fs=28, temp=232, desc=56,
        warn=28, fc=36, fc_icon=110, q_name=32, q_price=60, q_pct=36,
        gaps=dict(d1=18, d2=10, d3=10, d4=0, d5=20, d6=12, d7=16, d8=18, d9=16),
        icon_ratio=0.85,
        note="整页切成四条横带（日历 / 天气 / 预报 / 指数），发丝线分隔，带内左对齐。"
             "高度按带分死，页面永远填满，没有一处整块留白；温度 240 全屏最大。",
    ),
    "丁 · 海报极限": dict(
        mode="stack", meta=False, quotes=False,
        margin=64, line=36, day=340, lunar=88, meta_fs=28, temp=200, desc=50,
        warn=32, fc=38, fc_icon=120, q_name=30, q_price=64, q_pct=34,
        gaps=dict(d1=16, d2=10, d3=18, d4=0, d5=22, d6=14, d7=16, d8=20, d9=0),
        icon_ratio=1.0,
        note="纯海报：砍干支行 + 砍指数行，只留日历 / 天气 / 预报。日期 340 / 温度 200 /"
             "主图标 200 / 预报图标 120。信息最少、字最大。",
    ),
}


def _p4_weather(sh: Sheet, d: dict, P: dict, y: int, cx: int) -> int:
    wx = d["wx"]
    tf = sh.f.serif(P["temp"])
    df = sh.f.sans(P["desc"])
    icon_w = int(P["temp"] * P.get("icon_ratio", 1.0))
    line_w = (icon_w + 26 + sh.tw(wx["temp"].rstrip("°"), tf) + 4
              + sh.tw("°", sh.f.sans(int(P["temp"] * 0.46), True)) + 26
              + sh.tw(wx["desc"], df))
    x = cx - line_w // 2
    sh.icons.icon(x + icon_w / 2, y + P["temp"] * 0.40, icon_w, wx["icon"], SOFT)
    x += icon_w + 26
    x += sh.big(x, y, y + P["temp"], wx["temp"], tf) + 26
    sh.ink(x, y + P["temp"] * 0.36, y + P["temp"], wx["desc"], df, SOFT)
    return y + P["temp"]


def _p4_warn(sh: Sheet, d: dict, P: dict, y: int, cx: int, x1: int) -> int:
    wx = d["wx"]
    if not wx["warn"]:
        return y
    ww = max(sh.tw(t, sh.f.sans(P["warn"])) for t in wx["warn"][:2])
    bw = sh.tw("预警", sh.f.sans(24)) + 24
    x0 = cx - (bw + 14 + ww) // 2
    for i, t in enumerate(wx["warn"][:2]):
        used = sh.stamp(x0, y - 5, "预警", sh.f.sans(24)) if i == 0 else bw
        sh.t((x0 + used + 14, y),
             sh.clip(t, sh.f.sans(P["warn"]), x1 - x0 - used - 14),
             sh.f.sans(P["warn"]), INK)
        y += P["warn"] + 12
    return y


def _p4_fc(sh: Sheet, d: dict, P: dict, y: int, cx: int) -> int:
    wx = d["wx"]
    fw = 236
    x0 = cx - fw * len(wx["fc"][:4]) // 2
    r1 = P["fc_icon"] + 8
    r2 = r1 + P["fc"] + 12
    r3 = r2 + P["fc"] + 12
    for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:4]):
        gx = x0 + i * fw + fw // 2
        sh.icons.icon(gx, y + P["fc_icon"] // 2, P["fc_icon"], kind, SOFT)
        sh.ink(gx, y + r1, y + r1 + P["fc"] + 8, label, sh.f.sans(P["fc"]), GRAY,
               anchor="mt")
        sh.ink(gx, y + r2, y + r2 + P["fc"] + 8,
               sh.clip(desc, sh.f.sans(P["fc"]), fw - 8), sh.f.sans(P["fc"]), INK,
               anchor="mt")
        sh.ink(gx, y + r3, y + r3 + P["fc"] + 8, hi + " / " + lo, sh.f.sans(P["fc"]),
               SOFT, anchor="mt")
    return y + r3 + P["fc"] + 8


def _p4_quotes(sh: Sheet, d: dict, P: dict, y: int, m: int, x1: int) -> int:
    cw3 = (x1 - m) // 3
    for i, (name, price, pct) in enumerate(d["quotes"][:3]):
        gx = m + i * cw3 + cw3 // 2
        sh.ink(gx, y, y + P["q_name"] + 8, name, sh.f.sans(P["q_name"]), GRAY,
               anchor="mt")
        sh.ink(gx, y + P["q_name"] + 12, y + P["q_name"] + 12 + P["q_price"] + 6,
               price, sh.f.sans(P["q_price"], True), INK, anchor="mt")
        py = y + P["q_name"] + 12 + P["q_price"] + 16
        up = pct >= 0
        pf = sh.f.sans(P["q_pct"])
        pw = sh.tw(f"{pct:+.2f}%", pf)
        sh.trend(gx - pw // 2 - 22, py + 8, up, 14)
        sh.ink(gx + 6, py, py + P["q_pct"] + 8, f"{pct:+.2f}%", pf,
               INK if up else SOFT, anchor="mt")
    return y + P["q_name"] + 12 + P["q_price"] + 16 + P["q_pct"] + 8


def _poster4_stack(sh: Sheet, d: dict, P: dict, y: int) -> int:
    """居中单栏（甲/丁）。和帖 v2 同结构，只是行可选、字号巨大。"""
    M, X1 = P["margin"], W - P["margin"]
    CW = X1 - M
    cx = W // 2
    g = P["gaps"]
    cal = d["cal"]

    sh.ink(cx, y, y + 44, " · ".join([cal["month"], cal["week"], d["place"]]),
           sh.f.sans(P["line"]), GRAY, anchor="mt")
    y += 44 + g["d1"]
    sh.ink(cx, y, y + P["day"], cal["day"], sh.f.serif(P["day"]), anchor="mt")
    y += P["day"] + g["d2"]
    sh.ink(cx, y, y + P["lunar"], cal["lunar"], sh.f.serif(P["lunar"]), anchor="mt")
    y += P["lunar"] + g["d3"]
    if P["meta"]:
        meta = " · ".join(cal["ganzhi"] + cal["term"])
        sh.ink(cx, y, y + 36, sh.clip(meta, sh.f.sans(P["meta_fs"]), CW),
               sh.f.sans(P["meta_fs"]), GRAY, anchor="mt")
        y += 36 + g["d4"]
    sh.rule(y, cx - 140, cx + 140, 3, LIGHT)
    y += g["d5"]
    y = _p4_weather(sh, d, P, y, cx) + g["d6"]
    y = _p4_warn(sh, d, P, y, cx, X1) + g["d7"]
    y = _p4_fc(sh, d, P, y, cx) + g["d8"]
    if P["quotes"]:
        sh.rule(y, M, X1, 3, LIGHT)
        y += g["d9"]
        y = _p4_quotes(sh, d, P, y, M, X1)
    return y


def _poster4_mast(sh: Sheet, d: dict, P: dict, y: int) -> int:
    """双栏报头（乙）：左上角挂农历月 + 公历月周；左栏日期 + 农历日，右栏图标温度天气词地名；
    报头以下回到通栏。"""
    M, X1 = P["margin"], W - P["margin"]
    CW = X1 - M
    g = P["gaps"]
    cal, wx = d["cal"], d["wx"]

    lm = re.match(r"^(.*月)(.+)$", cal["lunar"])
    tag, lday = (lm.group(1), lm.group(2) + "日") if lm else ("", cal["lunar"])
    if tag:
        twd = sh.ink(M, y, y + P["tag"], tag, sh.f.serif(P["tag"]))
        sh.ink(M + twd + 20, y + 4, y + P["tag"],
               " · ".join([cal["month"], cal["week"]]), sh.f.sans(P["line"]), GRAY)
    y += P["tag"] + 10

    lf, lf2 = sh.f.serif(P["day"]), sh.f.serif(P["lunar_day"])
    meta = " · ".join(cal["ganzhi"] + cal["term"])
    lw = max(sh.tw(cal["day"], lf), sh.tw(lday, lf2))
    sh.ink(M, y, y + P["day"], cal["day"], lf)
    sh.ink(M, y + P["day"] + g["d2"], y + P["day"] + g["d2"] + P["lunar_day"],
           lday, lf2)
    lh = P["day"] + g["d2"] + P["lunar_day"]

    x0 = M + lw + 40
    sh.vrule(M + lw + 20, y + 8, y + lh - 8, 3, LIGHT)
    rw = X1 - x0
    tf = sh.f.serif(P["temp"])
    iw = int(P["temp"] * P.get("icon_ratio", 0.95))
    tw_temp = sh.big_width(wx["temp"], tf)
    if iw + 24 + tw_temp > rw:
        iw = max(60, rw - 24 - tw_temp)
    stack = P["temp"] + 10 + P["desc"] + 8 + P["place_fs"] + 8
    cy = y + (lh - stack) // 2
    sh.icons.icon(x0 + iw / 2, cy + P["temp"] * 0.40, iw, wx["icon"], SOFT)
    sh.big(x0 + iw + 24, cy, cy + P["temp"], wx["temp"], tf)
    dy = cy + P["temp"] + 10
    sh.ink(x0, dy, dy + P["desc"] + 8, wx["desc"], sh.f.sans(P["desc"]), SOFT)
    py = dy + P["desc"] + 8
    sh.ink(x0, py, py + P["place_fs"] + 8, d["place"], sh.f.sans(P["place_fs"]), GRAY)
    y += lh + 8
    sh.ink(M, y, y + 36, sh.clip(meta, sh.f.sans(P["meta_fs"]), CW),
           sh.f.sans(P["meta_fs"]), GRAY)
    y += 36 + g["d5"]

    cx = W // 2
    y = _p4_warn(sh, d, P, y, cx, X1) + g["d7"]
    y = _p4_fc(sh, d, P, y, cx) + g["d8"]
    sh.rule(y, M, X1, 3, LIGHT)
    y += g["d9"]
    return _p4_quotes(sh, d, P, y, M, X1)


def _poster4_bands(sh: Sheet, d: dict, P: dict, top: int, foot_top: int) -> int:
    """横带满版（丙）：四条带按权重分死整页高度，带内垂直居中、左对齐。
    返回最紧那条带的剩余高度（= 这版的"余量"）。"""
    M, X1 = P["margin"], W - P["margin"]
    CW = X1 - M
    cal, wx = d["cal"], d["wx"]
    total = foot_top - top
    weights = (0.24, 0.31, 0.25, 0.20)
    hs = [int(total * w) for w in weights]
    hs[-1] = total - sum(hs[:-1])
    slack = 10 ** 6
    y = top
    for bi, h in enumerate(hs):
        if bi:
            sh.rule(y - 12, M, X1, 3, LIGHT)
        y0, y1 = y, y + h
        if bi == 0:
            lf = sh.f.serif(P["day"])
            day_w = sh.tw(cal["day"], lf)
            cy = y0 + (h - P["day"]) // 2
            sh.ink(M, cy, cy + P["day"], cal["day"], lf)
            rx = M + day_w + 36
            sh.vrule(M + day_w + 18, y0 + 24, y1 - 24, 3, LIGHT)
            stack = 44 + 8 + P["lunar"] + 8 + 36
            sy = y0 + (h - stack) // 2
            sh.ink(rx, sy, sy + 44,
                   " · ".join([cal["month"], cal["week"], d["place"]]),
                   sh.f.sans(P["line"]), GRAY)
            sy += 44 + 8
            sh.ink(rx, sy, sy + P["lunar"], cal["lunar"], sh.f.serif(P["lunar"]))
            sy += P["lunar"] + 8
            meta = " · ".join(cal["ganzhi"] + cal["term"])
            sh.ink(rx, sy, sy + 36, sh.clip(meta, sh.f.sans(P["meta_fs"]), X1 - rx),
                   sh.f.sans(P["meta_fs"]), GRAY)
            used = max(P["day"], stack)
        elif bi == 1:
            iw = int(P["temp"] * P.get("icon_ratio", 0.85))
            tf = sh.f.serif(P["temp"])
            row = P["temp"]
            nwarn = len(wx["warn"][:2])
            stack = row + (12 + nwarn * (P["warn"] + 12) if nwarn else 0)
            cy = y0 + (h - stack) // 2
            sh.icons.icon(M + iw / 2, cy + P["temp"] * 0.40, iw, wx["icon"], SOFT)
            x = M + iw + 28
            x += sh.big(x, cy, cy + P["temp"], wx["temp"], tf) + 28
            sh.ink(x, cy + P["temp"] * 0.36, cy + P["temp"], wx["desc"],
                   sh.f.sans(P["desc"]), SOFT)
            wy = cy + row + 12
            if nwarn:
                bw = sh.tw("预警", sh.f.sans(24)) + 24
                for i, t in enumerate(wx["warn"][:2]):
                    used_w = sh.stamp(M, wy - 5, "预警", sh.f.sans(24)) if i == 0 else bw
                    sh.t((M + used_w + 14, wy),
                         sh.clip(t, sh.f.sans(P["warn"]), X1 - M - used_w - 14),
                         sh.f.sans(P["warn"]), INK)
                    wy += P["warn"] + 12
            used = stack
        elif bi == 2:
            cw4 = CW // 4
            r1 = P["fc_icon"] + 8
            r2 = r1 + P["fc"] + 12
            r3 = r2 + P["fc"] + 12
            stack = r3 + P["fc"] + 8
            cy = y0 + (h - stack) // 2
            for i, (label, kind, desc, hi, lo) in enumerate(wx["fc"][:4]):
                gx = M + i * cw4 + cw4 // 2
                sh.icons.icon(gx, cy + P["fc_icon"] // 2, P["fc_icon"], kind, SOFT)
                sh.ink(gx, cy + r1, cy + r1 + P["fc"] + 8, label,
                       sh.f.sans(P["fc"]), GRAY, anchor="mt")
                sh.ink(gx, cy + r2, cy + r2 + P["fc"] + 8,
                       sh.clip(desc, sh.f.sans(P["fc"]), cw4 - 12),
                       sh.f.sans(P["fc"]), INK, anchor="mt")
                sh.ink(gx, cy + r3, cy + r3 + P["fc"] + 8, hi + " / " + lo,
                       sh.f.sans(P["fc"]), SOFT, anchor="mt")
            used = stack
        else:
            cw3 = CW // 3
            stack = P["q_name"] + 12 + P["q_price"] + 16 + P["q_pct"] + 8
            cy = y0 + (h - stack) // 2
            for i, (name, price, pct) in enumerate(d["quotes"][:3]):
                gx = M + i * cw3 + cw3 // 2
                sh.ink(gx, cy, cy + P["q_name"] + 8, name, sh.f.sans(P["q_name"]),
                       GRAY, anchor="mt")
                sh.ink(gx, cy + P["q_name"] + 12,
                       cy + P["q_name"] + 12 + P["q_price"] + 6, price,
                       sh.f.sans(P["q_price"], True), INK, anchor="mt")
                py = cy + P["q_name"] + 12 + P["q_price"] + 16
                up = pct >= 0
                pf = sh.f.sans(P["q_pct"])
                pw = sh.tw(f"{pct:+.2f}%", pf)
                sh.trend(gx - pw // 2 - 22, py + 8, up, 14)
                sh.ink(gx + 6, py, py + P["q_pct"] + 8, f"{pct:+.2f}%", pf,
                       INK if up else SOFT, anchor="mt")
            used = stack
        slack = min(slack, h - used)
        y = y1
    return slack


def layout_poster4(sh: Sheet, d: dict, P: dict) -> int:
    foot_top = sh.footer(d, P["margin"], W - P["margin"])
    # 乙的左上角角标和电量同一行起画，真正贴到角上；其余版式仍让开电量矩形
    top = BAT_BOX[1] if P["mode"] == "mast" else BAT_BOX[3] + 16
    if P.get("bat"):
        BAT_RECT[P["bat"]](sh, d.get("battery", 86))
    if P["mode"] == "bands":
        return _poster4_bands(sh, d, P, top, foot_top)
    body = _poster4_mast if P["mode"] == "mast" else _poster4_stack
    probe = Sheet(sh.f)
    height = body(probe, d, P, 0)
    extra = foot_top - top - height
    y = top + max(0, extra // 2)
    y1 = body(sh, d, P, y)
    sh.block("主体", y, y1)
    return extra


def build_poster2_html(shots, rect=CLOCK,
                       title="帖 v2 · 空间配平三案（字放大 + 整组垂直居中）",
                       sub="三张都是 1072×1448 真图、最坏情况内容。红虚线 = 时钟留白区。"
                           "块距写死不随内容伸缩：哪天没有预警，整组只是往下平移一点，字不会变小变大。",
                       cap="最坏情况（2 预警 / 4 预报 / 3 指数 / 农历干支节气全开）·"
                           "上下留白各约 <b>{slack}px</b>", half=True) -> str:
    cl, ct = rect[0] / W * 100, rect[1] / H * 100
    cw, ch = (rect[2] - rect[0]) / W * 100, (rect[3] - rect[1]) / H * 100
    cards = []
    for name, png, slack, note in shots:
        cards.append(f"""
    <figure>
      <h2>{name}</h2>
      <p class="d">{note}</p>
      <div class="shot"><img src="{png}" alt="{name}">
        <span class="clock" style="left:{cl}%;top:{ct}%;width:{cw}%;height:{ch}%"></span></div>
      <figcaption>{cap.format(slack=slack // 2 if half else slack)}</figcaption>
    </figure>""")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>帖 v2 · 空间配平三案</title>
<style>
 body{{margin:0;background:#eceef0;color:#1c1f23;
      font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
 header{{padding:24px 28px 16px;border-bottom:1px solid #d6d9dd;background:#fff}}
 h1{{margin:0 0 6px;font-size:21px}} header p{{margin:0;color:#5b6470;font-size:13.5px}}
 main{{padding:24px 28px 80px;display:grid;grid-template-columns:repeat(auto-fit,
       minmax(420px,1fr));gap:22px;max-width:1700px}}
 figure{{margin:0;background:#fff;border:1px solid #d6d9dd;border-radius:12px;
         padding:14px 16px 16px}}
 h2{{margin:0 0 4px;font-size:18px}} .d{{margin:0 0 12px;color:#5b6470;font-size:13px}}
 .shot{{position:relative;line-height:0}}
 img{{width:100%;height:auto;border:1px solid #cfd3d8;border-radius:4px;background:#fff}}
 .clock{{position:absolute;border:2px dashed #b42318;border-radius:3px;box-sizing:border-box}}
 figcaption{{margin-top:8px;font-size:12.5px;color:#3c434b}}
</style></head><body>
<header><h1>{title}</h1>
<p>{sub}</p></header>
<main>{''.join(cards)}</main></body></html>
"""


def render_one(name, fn, data) -> tuple[Sheet, int]:
    sh = Sheet(Fonts())
    fn(sh, data)
    box = sh.img.crop(CLOCK)
    if box.getextrema() != (255, 255):
        raise SystemExit(f"方向{name}: 时钟留白区被画脏了 extrema={box.getextrema()}")
    body = [b for b in sh.blocks if b[0] != "页脚"]
    foot = [b for b in sh.blocks if b[0] == "页脚"]
    last = max(b[2] for b in body)
    slack = (foot[0][1] if foot else H - 44) - last
    return sh, slack


def report(name, sh: Sheet, slack: int, sink: list) -> None:
    sink.append(f"=== 方向{name} ===")
    for bname, y0, y1 in sh.blocks:
        sink.append(f"    {bname:14} y {y0:4}..{y1:4}   高 {y1 - y0:4}")
    over = [b for b in sh.blocks if b[2] > H - 44]
    sink.append(f"    底边余量 {slack}px"
                + ("   !! 有块越界: " + str(over) if over else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--poster2", action="store_true",
                    help="只出「帖 v2」空间配平三案 + poster2.html")
    ap.add_argument("--poster3", action="store_true",
                    help="丙 + 砍时钟留白 + 四版电量样式 + poster3.html")
    ap.add_argument("--bigtype", action="store_true",
                    help="字阶放大三案（丙 + 电量 B）+ bigtype.html")
    ap.add_argument("--huge", action="store_true",
                    help="巨大四案（含双栏报头 / 横带满版两种新结构）+ huge.html")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    if args.huge:
        shots = []
        for name, P in POSTER4.items():
            P = dict(P, bat="B")
            sh = Sheet(Fonts())
            slack = layout_poster4(sh, worst_data(), P)
            floor = 28 if P["mode"] == "bands" else 40
            if slack < floor:
                raise SystemExit(f"{name}: 余量 {slack}px 低于安全线 {floor}")
            png = OUT / f"巨-{name.split(' ')[0]}.png"
            sh.img.save(png, format="PNG", optimize=True)
            shots.append((name, png.name, slack, P["note"]))
            print(f"{name}: 余量 {slack}px -> {png.name}")
        (OUT / "huge.html").write_text(
            build_poster2_html(
                shots, rect=BAT_BOX, half=False,
                title="巨大四案（字再大一号，结构也动）",
                sub="四张都是 1072×1448 真图、最坏情况内容。红虚线 = 电量精灵图位置。"
                    "甲/丁 = 居中单栏靠砍行换字号；乙 = 双栏报头吃掉两侧留白；"
                    "丙 = 四条横带把整页填死。余量含义见每张图下方。",
                cap="最坏情况（2 预警 / 4 预报 / 3 指数）· 余量 <b>{slack}px</b>"
                    "（居中版 = 上下合计；横带版 = 最紧一条带的剩余）"),
            encoding="utf-8")
        print(f"ok -> {OUT / 'huge.html'}")
        return 0

    if args.bigtype:
        shots = []
        for name, P in POSTER3.items():
            P = dict(P, bat="B", top=BAT_BOX[3] + 16)
            sh = Sheet(Fonts())
            slack = layout_poster2(sh, worst_data(), P)
            # 时钟已砍，旧时钟矩形里除了电量矩形都是自由留白；
            # 首行墨迹从 top+6 起（top = 电量矩形下沿 +16），结构上不会碰电量矩形
            if slack < 40:
                raise SystemExit(f"{name}: 余量 {slack}px 低于安全线 40")
            png = OUT / f"大字-{name.split(' ')[0]}.png"
            sh.img.save(png, format="PNG", optimize=True)
            shots.append((name, png.name, slack, P["note"]))
            print(f"{name}: 余量 {slack}px -> {png.name}")
        (OUT / "bigtype.html").write_text(
            build_poster2_html(shots, rect=BAT_BOX,
                               title="字阶放大三案（丙 + 电量 B）",
                               sub="三张都是 1072×1448 真图、最坏情况内容（2 预警 / 4 预报 / 3 指数）。"
                                   "红虚线 = 电量精灵图位置。放大从块距里扣，所以三版的留白比上一轮紧；"
                                   "余量都按有 2 条预警那天算，仍 ≥40px 安全线。"),
            encoding="utf-8")
        print(f"ok -> {OUT / 'bigtype.html'}")
        return 0

    if args.poster3:
        base = dict(POSTER2["丙 · 疏朗"])
        base["top"] = BAT_BOX[3] + 16      # 时钟留白砍掉后，顶部只让开电量矩形
        shots = []
        for key in ("A", "B", "C", "D"):
            P = dict(base, bat=key)
            sh = Sheet(Fonts())
            data = worst_data()
            slack = layout_poster2(sh, data, P)
            if slack < 40:
                raise SystemExit(f"电量{key}: 余量 {slack}px 低于安全线 40")
            png = OUT / f"丙电-{key}.png"
            sh.img.save(png, format="PNG", optimize=True)
            shots.append((f"电量 {key}", png.name, slack, BAT_NOTE[key]))
            print(f"电量 {key}: 余量 {slack}px -> {png.name}")
        (OUT / "poster3.html").write_text(
            build_poster2_html(shots, rect=BAT_BOX,
                               title="丙 · 电量样式四案（时钟留白已砍）",
                               sub="时钟留白去掉后顶部只让开电量矩形 (814,40)-(1016,96)。"
                                   "红虚线 = 电量精灵图将来贴的位置；四版共用同一个矩形，换样式不用改版面。"
                                   "电量只有 Kindle 自己知道，云端画不了 —— 和当初时钟同一条路：按档位贴精灵图。"),
            encoding="utf-8")
        print(f"ok -> {OUT / 'poster3.html'}")
        return 0

    if args.poster2:
        shots = []
        for name, P in POSTER2.items():
            sh = Sheet(Fonts())
            slack = layout_poster2(sh, worst_data(), P)
            if sh.img.crop(CLOCK).getextrema() != (255, 255):
                raise SystemExit(f"{name}: 时钟留白区被画脏了")
            if slack < 40:
                raise SystemExit(f"{name}: 余量 {slack}px 低于安全线 40")
            png = OUT / f"帖2-{name.split(' ')[0]}.png"
            sh.img.save(png, format="PNG", optimize=True)
            shots.append((name, png.name, slack, P["note"]))
            print(f"{name}: 余量 {slack}px -> {png.name}")
        (OUT / "poster2.html").write_text(build_poster2_html(shots), encoding="utf-8")
        print(f"ok -> {OUT / 'poster2.html'}")
        return 0

    reports: list[str] = []
    shots, minimal_shots = [], []
    for kind, data in (("满版", worst_data()), ("精简", minimal_data())):
        reports.append(f"===== 内容：{'最坏情况' if kind == '满版' else '精简态'} =====")
        for name, fn, _desc in DIRECTIONS:
            sh, slack = render_one(name, fn, data)
            png = OUT / f"{name.split(' ')[0]}-{kind}.png"
            sh.img.save(png, format="PNG", optimize=True)
            report(name, sh, slack, reports)
            if kind == "满版":
                shots.append((name, png.name, slack))
            else:
                minimal_shots.append((name, png.name, slack))
    reports_txt = "\n".join(reports)
    (OUT / "report.txt").write_text(reports_txt, encoding="utf-8")
    (OUT / "index.html").write_text(
        build_html(shots, minimal_shots, reports_txt), encoding="utf-8")
    print(f"ok -> {OUT / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
