#!/usr/bin/env python3
"""ARC Raiders 译法 · 信息终端（2026-09-26）：只出稿、不碰生产。

游戏里那套是「磨损的工业军规 UI」：危险斜纹、切角面板、DIN 模板字、分段仪表、
条形码序列号。它的橄榄/橙/锈色上不了五档灰的墨水屏，所以在这里翻译成：
黑钢板条 + 130/195 灰的仪表 + 切角发丝框 + 等宽读数。
字体分工：拉丁与数字走 Bahnschrift（DIN 可变字重）/ Impact，读数走 Consolas，
中文一律回退思源黑体 —— DIN 和 Impact 都没有 CJK 字形，混排会出豆腐块。
涨跌不用文字图例：灰条+▼ / 黑条+▲ 的图样自己说话（▼▲ 是画的多边形）。

同轮还出过 乙·任务简报 / 丙·扫描雷达 两版，用户看完放弃，代码与图样已删；
三版齐全的旧稿与被否掉的 A（冲压实心）/ C（点阵）图标，都在 .workbuddy/_archive/2026-09-26/。

用法：python dashboard/tools/design_arc.py
产物：.workbuddy/_directions/袭-*.png + arcraiders.html

图标用 B 套线稿轮廓（用户 2026-09-26 从三套里选的，见 line_icon）。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

from design_directions import OUT, W, H, Fonts, Sheet          # noqa: E402
from design_bw import worst_bw, cells_pick                     # noqa: E402

INK, SOFT, GRAY, LIGHT, PAPER = 0, 70, 130, 195, 255
M = 56
X1 = W - M
CW = X1 - M
TOP = 112
BAT = (814, 40, 1016, 96)

DIN = r"C:\Windows\Fonts\bahnschrift.ttf"
IMPACT = r"C:\Windows\Fonts\impact.ttf"
MONO_R = r"C:\Windows\Fonts\consola.ttf"
MONO_B = r"C:\Windows\Fonts\consolab.ttf"

#: DIN / Impact / Arial Black / Consolas 共有的字符。不在这个表里（中文、·、全角）
#: 就整串回退黑体，绝不混排 —— 混排必出豆腐块。
LATIN_OK = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
               "°%+-/.,:()#&'\"!?*<>= ")


class FontsArc(Fonts):
    def _getv(self, path, size, axes):
        key = (path, size, tuple(axes))
        if key not in self._cache:
            from PIL import ImageFont
            f = ImageFont.truetype(path, size)
            if axes:
                f.set_variation_by_axes(list(axes))
            self._cache[key] = f
        return self._cache[key]

    def din(self, size: int, w: int = 700, wdth: int | None = None):
        """Bahnschrift：DIN 1451 血统的工业字。w=字重轴，wdth=字宽轴（75=窄）。"""
        return self._getv(DIN, size, (w, wdth) if wdth else (w,))

    def impact(self, size: int):
        return self._get(IMPACT, size)

    def mono(self, size: int, bold: bool = False):
        return self._get(MONO_B if bold else MONO_R, size)


def tf(sh, s: str, size: int, kind: str = "din", **kw):
    """纯拉丁走指定工业字；混进中文/·就整串回退黑体粗。"""
    if all(c in LATIN_OK for c in s):
        if kind == "mono":
            return sh.f.mono(size, bool(kw.get("bold")))
        return sh.f.din(size, **kw)
    return sh.f.sans(size, True)


def mix(sh, x, top, bottom, lat, cn, lat_size, cn_size, fill=INK,
        kind="din", gap=10, anchor_r=None):
    """拉丁 + 中文一行：前半 DIN/Impact，后半黑体，各自按墨迹垂直居中。"""
    lf, cf = tf(sh, lat, lat_size, kind), sh.f.sans(cn_size, True)
    lw = sh.tw(lat, lf) if lat else 0
    cw = sh.tw(cn, cf) if cn else 0
    x0 = (anchor_r - (lw + (gap if lat and cn else 0) + cw)) if anchor_r else x
    if lat:
        sh.ink(x0, top, bottom, lat, lf, fill)
    if cn:
        sh.ink(x0 + lw + gap, top, bottom, cn, cf, fill)
    return lw + (gap if lat and cn else 0) + cw


# ---- 通用零件 --------------------------------------------------------------

def rule(sh, y, x0, x1, t=2, c=INK):
    sh.d.rectangle([x0, y, x1, y + t - 1], fill=c)


def dashed(sh, y, x0, x1, dash=16, gap=10, t=3, c=INK):
    x = x0
    while x < x1:
        sh.d.rectangle([x, y, min(x + dash, x1), y + t - 1], fill=c)
        x += dash + gap


def hazard(sh, x0, y, x1, h, step=22, c=INK):
    """45° 危险斜纹。先铺纸色再画斜条，两侧溢出的斜条用纸色盖掉。"""
    sh.d.rectangle([x0, y, x1, y + h - 1], fill=PAPER)
    x = x0 - h
    while x < x1 + h:
        sh.d.polygon([(x, y + h - 1), (x + h * 0.7, y),
                      (x + h * 0.7 + step * 0.55, y), (x + step * 0.55, y + h - 1)],
                     fill=c)
        x += step
    sh.d.rectangle([0, y - 1, x0 - 1, y + h], fill=PAPER)
    sh.d.rectangle([x1 + 1, y - 1, W - 1, y + h], fill=PAPER)


def notch(sh, x0, y0, x1, y1, cut=16, t=3, c=INK, fill=None):
    """切掉左上/右下两角的面板 —— 游戏里所有框都是这个形状。"""
    pts = [(x0 + cut, y0), (x1, y0), (x1, y1 - cut), (x1 - cut, y1),
           (x0, y1), (x0, y0 + cut)]
    if fill is not None:
        sh.d.polygon(pts, fill=fill)
    sh.d.line(pts + [pts[0]], fill=c, width=t, joint="curve")


def tag(sh, x, y, s, font, c=INK, txt=None, pad=10, h=40, cut=10):
    """切角小标签。txt 给了就反白。返回占用宽度。"""
    w = sh.tw(s, font)
    pts = [(x + cut, y), (x + w + pad * 2, y), (x + w + pad * 2, y + h - cut),
           (x + w + pad * 2 - cut, y + h), (x, y + h), (x, y + cut)]
    if txt is not None:
        sh.d.polygon(pts, fill=c)
        sh.ink(x + pad, y, y + h, s, font, txt)
    else:
        sh.d.line(pts + [pts[0]], fill=c, width=2, joint="curve")
        sh.ink(x + pad, y, y + h, s, font, c)
    return w + pad * 2


def meter(sh, x, y, w, h, frac, segs=8, c=INK, ec=GRAY):
    n = max(0, min(segs, round(frac * segs)))
    sw = (w - (segs - 1) * 4) // segs
    for i in range(segs):
        sx = x + i * (sw + 4)
        if i < n:
            sh.d.rectangle([sx, y, sx + sw, y + h - 1], fill=c)
        else:
            sh.d.rectangle([sx, y, sx + sw, y + h - 1], outline=ec, width=2)


def chgbar(sh, x, y, w, h, pct):
    """涨跌条：跌=中线往左的一条灰条，涨=中线往右的一条黑条，长度=幅度
    （满长 ≈ 3%）。中间浅灰竖刻是中立基准。接口只给当前价+涨跌幅，
    没有历史序列就不编 K 线 —— 一条诚实的涨跌条比假 K 线强。"""
    mid = x + w // 2
    sh.d.rectangle([mid, y + 2, mid, y + h - 3], fill=LIGHT)
    bl = max(8, int(min(1.0, abs(pct) / 3.0) * (w // 2 - 10)))
    by = y + h // 2 - 8
    if pct >= 0:
        sh.d.rectangle([mid + 6, by, mid + 6 + bl, by + 15], fill=INK)
    else:
        sh.d.rectangle([mid - 6 - bl, by, mid - 6, by + 15], fill=GRAY)


def frac_of(k: str, v: str) -> float:
    if "湿度" in k or "降水" in k:
        return float(v.rstrip("%")) / 100
    if "空气" in k:
        return min(1.0, float(v.split()[-1]) / 300)
    if "紫外" in k:
        return float(v) / 11
    if "风" in k:
        return float(v.split()[0]) / 12
    return 0.5


def tri(sh, x, y, s, c=INK):
    """预警三角：实心三角 + 反白感叹号。字体里没有 ⚠ 字形。"""
    sh.d.polygon([(x + s // 2, y), (x + s, y + s), (x, y + s)], fill=c)
    sh.ink(x + s // 2, y + int(s * 0.26), y + s - int(s * 0.10), "!",
           sh.f.din(int(s * 0.72), 700), PAPER, anchor="mt")


def barcode(sh, x, y, w, h, seed=7, c=INK):
    """序列号条码：固定种子，每次出图长得一样。"""
    bars = [3, 1, 2, 1, 4, 1, 1, 3, 2, 1, 2, 4, 1, 2, 1, 3, 1, 1, 2, 3,
            1, 4, 2, 1, 1, 2, 3, 1, 2, 1]
    x0 = x
    i = seed
    while x0 < x + w - 4:
        bw = bars[i % len(bars)]
        sh.d.rectangle([x0, y, x0 + bw, y + h - 1], fill=c)
        x0 += bw + bars[(i + 5) % len(bars)] + 2
        i += 1


def key_glyph(sh, xr, y, h):
    """涨跌图例：灰条+▼ / 黑条+▲，不写字 —— ▼▲ 字体里没有，画多边形。"""
    bw, bh, ts, gap = 44, 12, 16, 14
    x = xr - (bw + 8 + ts + gap + bw + 8 + ts)
    cy = y + h // 2
    sh.d.rectangle([x, cy - bh // 2, x + bw, cy + bh // 2], fill=GRAY)
    sh.d.polygon([(x + bw + 8, cy - ts // 2), (x + bw + 8 + ts, cy - ts // 2),
                  (x + bw + 8 + ts // 2, cy + ts // 2)], fill=GRAY)
    x2 = x + bw + 8 + ts + gap
    sh.d.rectangle([x2, cy - bh // 2, x2 + bw, cy + bh // 2], fill=INK)
    sh.d.polygon([(x2 + bw + 8 + ts // 2, cy - ts // 2),
                  (x2 + bw + 8, cy + ts // 2), (x2 + bw + 8 + ts, cy + ts // 2)],
                 fill=INK)


def foot(sh, d, y, fg=GRAY, legend="LEFT=DOWN RIGHT=UP"):
    dashed(sh, y, M, X1, 16, 10, 3, GRAY)
    fl = "UPLINK " + d["foot_l"]
    sh.ink(M, y + 12, y + 52, fl, tf(sh, fl, 24, "mono", bold=True), fg)
    sh.ink(X1, y + 12, y + 52, legend, sh.f.mono(24), fg, anchor="rt")
    fm = sh.clip(d["foot_m"].replace(" · 实心=涨 空心=跌", ""), sh.f.sans(24), CW)
    sh.ink(M, y + 52, y + 92, fm, sh.f.sans(24), fg)
    return y + 92


def spread(extra, n):
    q = extra // n
    return [q + (extra - q * n) if i == 0 else q for i in range(n)]


# ---- B · 线稿轮廓 ----------------------------------------------------------

LOBES = ((-0.44, 0.10, 0.30), (-0.16, -0.12, 0.42), (0.16, -0.06, 0.36),
         (0.42, 0.10, 0.26))
RAIN_N = {"drizzle": 1, "rain_light": 1, "rain": 2, "shower": 3,
          "rain_heavy": 3, "rain_storm": 4}      # 滴数=强度，暴雨数到 4


def _cloud_pts(cx, cy, R, pad=0.0):
    """四瓣并集轮廓：极坐标逐角取最远交点。"""
    pts = []
    for i in range(96):
        a = math.tau * i / 96
        ux, uy = math.cos(a), math.sin(a)
        best = 0.0
        for dx, dy, lr in LOBES:
            r = lr * R + pad
            bx, by = dx * R, dy * R
            dpar = bx * ux + by * uy
            disc = dpar * dpar - (bx * bx + by * by - r * r)
            if disc >= 0:
                best = max(best, dpar + math.sqrt(disc))
        pts.append((cx + best * ux, cy + best * uy))
    return pts


def line_icon(sh, cx, cy, size, kind, fg, bg=PAPER):
    R = size / 2.0
    w = max(2, int(round(size * 0.055)))
    kind = str(kind or "cloud").lower()
    n = RAIN_N.get(kind, 0)
    ccy = cy - R * 0.16 if (n or kind in ("snow", "thunder")) else cy
    pts = _cloud_pts(cx, ccy, R)

    def sun(x, y, r):
        sh.d.ellipse([x - r, y - r, x + r, y + r], outline=fg, width=w)
        for i in range(8):
            a = math.tau * i / 8 + math.tau / 16
            sh.d.line([x + math.cos(a) * r * 1.35, y + math.sin(a) * r * 1.35,
                       x + math.cos(a) * r * 1.72, y + math.sin(a) * r * 1.72],
                      fill=fg, width=w)

    def moon(x, y, r):
        sh.d.arc([x - r, y - r, x + r, y + r], 55, 305, fill=fg, width=w)
        rr = r * 0.86
        sh.d.arc([x + r * 0.42 - rr, y - r * 0.42 - rr, x + r * 0.42 + rr,
                  y - r * 0.42 + rr], 250, 430, fill=fg, width=w)

    if kind == "sun":
        sun(cx, cy, R * 0.42)
        return
    if kind == "moon":
        moon(cx, cy, R * 0.50)
        return
    if kind == "fog":
        for i, dy in enumerate((-0.34, -0.02, 0.30)):
            half = R * (0.50 if i % 2 else 0.76)
            sh.d.line([cx - half, cy + R * dy, cx + half, cy + R * dy],
                      fill=fg, width=w)
        return
    if kind in ("sun_cloud", "moon_cloud"):
        if kind == "sun_cloud":
            sun(cx - R * 0.42, ccy - R * 0.56, R * 0.26)
        else:
            moon(cx - R * 0.40, ccy - R * 0.54, R * 0.30)
        sh.d.polygon(pts, fill=bg)
    sh.d.line(list(pts) + [pts[0]], fill=fg, width=w, joint="curve")
    if n:
        offs = {1: (0.0,), 2: (-0.26, 0.26), 3: (-0.44, 0.0, 0.44),
                4: (-0.60, -0.22, 0.22, 0.60)}[n]
        y0 = ccy + R * 0.46
        for o in offs:
            x = cx + o * R
            sh.d.line([x + R * 0.08, y0, x - R * 0.08, y0 + R * 0.42],
                      fill=fg, width=w)
    elif kind == "snow":
        r = R * 0.17
        for o in (-0.44, 0.0, 0.44):
            x, y = cx + o * R, ccy + R * 0.66
            for i in range(3):
                a = math.tau * i / 3
                sh.d.line([x - math.cos(a) * r, y - math.sin(a) * r,
                           x + math.cos(a) * r, y + math.sin(a) * r],
                          fill=fg, width=max(2, w - 1))
    elif kind == "thunder":
        s = R * 0.9
        y0 = ccy + R * 0.34
        sh.d.line([cx + 0.14 * s, y0, cx - 0.16 * s, y0 + 0.42 * s,
                   cx + 0.10 * s, y0 + 0.42 * s, cx - 0.16 * s, y0 + 0.86 * s],
                  fill=fg, width=w, joint="curve")


# ---- 甲 · 信息终端 INFO TERMINAL ------------------------------------------

def lay_terminal(sh, d, y, extra=0):
    cal, wx = d["cal"], d["wx"]
    e = spread(extra, 5)

    # 顶部钢板状态条：左端危险斜纹 + 黑底白字
    hz = 92
    hazard(sh, M, y, M + hz, 58)
    sh.d.rectangle([M + hz + 6, y, X1, y + 57], fill=INK)
    mix(sh, M + hz + 26, y, y + 58, "INFO TERMINAL", "信息终端",
        32, 30, PAPER, "din", 14)
    rt = "NODE 07 / LINPING"
    sh.ink(X1 - 22, y, y + 58, rt, sh.f.mono(26, True), PAPER, anchor="rt")
    y += 58 + 12 + e[0]

    # 序列号行 + 条码
    sn = "SN 2026-0925-07 // DAILY CYCLE 4x"
    sh.ink(M, y, y + 30, sn, sh.f.mono(24), GRAY)
    barcode(sh, X1 - 220, y - 2, 220, 30, seed=3, c=GRAY)
    y += 30 + 14 + e[1]

    # 日期块：左大字日期，右历法栈
    day_h = 152
    sh.big(M, y, y + day_h, cal["day"], sh.f.impact(day_h), INK)
    dw = sh.big_width(cal["day"], sh.f.impact(day_h))
    lx = M + dw + 26
    sh.vrule(lx - 14, y + 6, y + day_h - 6, 3, INK)
    yy = y + 4
    mo = cal["month"].replace(" 年 ", ".").replace(" 月", "")
    mix(sh, lx, yy, yy + 40, mo, "", 36, 30, INK, "din")
    yy += 46
    wk = cal["week"]
    sh.ink(lx, yy, yy + 40, wk, sh.f.sans(34, True), INK)
    yy += 46
    lun = f"{cal['lunar']} · {cal['zodiac']}"
    sh.ink(lx, yy, yy + 34, sh.clip(lun, sh.f.sans(26), X1 - lx),
           sh.f.sans(26), SOFT)
    # 右侧：节日戳 + 倒计时/调休
    rx = lx + 430
    if rx < X1 - 200:
        fy = y + 8
        for name in cal["fest_today"][:2]:
            w = tag(sh, rx, fy, name, sh.f.sans(30, True), INK, PAPER, 12, 46)
            fy += 54
        for line in (cal["countdown"], cal["tiaoxiu"]):
            if line:
                sh.ink(rx, fy, fy + 34, sh.clip(line, sh.f.sans(26), X1 - rx),
                       sh.f.sans(26), INK)
                fy += 40
    y += day_h + 16 + e[2]

    # 大气面板：切角框，左图标+温度，右六格分段仪表
    ph = 220
    notch(sh, M, y, X1, y + ph, 18, 3, INK)
    sh.d.rectangle([M + 78, y - 1, M + 380, y + 3], fill=PAPER)
    tag(sh, M + 22, y - 20, "01", sh.f.din(26, 700), INK, PAPER, 8, 38)
    mix(sh, M + 84, y - 20, y + 18, "ATMOSPHERE", "大气状态", 26, 26, INK,
        "din", 10)
    ix, iy = M + 92, y + 30 + 58
    line_icon(sh, ix, iy, 108, wx["icon"], INK)
    tw_ = sh.big(ix + 80, y + 36, y + 140, wx["temp"], sh.f.impact(108), INK)
    sh.ink(ix + 80, y + 146, y + 184, wx["desc"], sh.f.sans(30, True), INK)
    cells = cells_pick(wx, 6)
    gx0 = M + 470
    colw = (X1 - 30 - gx0) // 2
    for i, (k, v) in enumerate(cells):
        cx0 = gx0 + (i % 2) * colw
        cy0 = y + 36 + (i // 2) * 58
        sh.ink(cx0, cy0, cy0 + 30, k, sh.f.sans(26), SOFT)
        sh.ink(cx0 + colw - 24, cy0, cy0 + 30, v, tf(sh, v, 28, "mono", bold=True)
               if all(c in "0123456789°%.," for c in v) else sh.f.sans(26, True),
               INK, anchor="rt")
        meter(sh, cx0, cy0 + 34, colw - 24, 14, frac_of(k, v))
    y += ph + 18 + e[3]

    # 预警：危险斜纹包边的盒子
    warns = wx["warn"]
    nh = (len(warns) * 44 + 34) if warns else 78
    sh.d.rectangle([M, y, M + 14, y + nh - 1], fill=INK)
    hazard(sh, M + 14, y, M + 62, nh, 16)
    sh.d.rectangle([M, y, M + 14, y + nh - 1], fill=INK)
    sh.d.rectangle([M + 62, y, X1, y + nh - 1], outline=INK, width=3)
    sh.d.rectangle([M + 63, y + 1, X1 - 1, y + nh - 2], fill=PAPER)
    if warns:
        ty = y + 16
        for i, wline in enumerate(warns):
            tri(sh, M + 84, ty + 2, 30)
            sh.ink(M + 128, ty, ty + 36, sh.clip(wline, sh.f.sans(28), X1 - M - 150),
                   sh.f.sans(28, True), INK)
            ty += 44
    else:
        mix(sh, M + 90, y, y + nh, "NO ACTIVE THREAT", "当前无预警",
            30, 28, GRAY, "din", 12)
    y += nh + 16 + e[4]

    # 预报：约定 fc[0]=昨天（灰底）、fc[1]=今天（反相），后面是明/后天
    fc = wx["fc"][:4]
    n = len(fc)
    pitch = CW // n
    mix(sh, M, y, y + 34, "FORECAST", "近日预报", 28, 26, INK, "din", 10)
    y += 40
    ch = 164
    for i, (day, ik, desc, hi, lo) in enumerate(fc):
        x0 = M + i * pitch
        x1c = x0 + pitch - 12
        if i == 0:
            notch(sh, x0, y, x1c, y + ch, 14, 3, GRAY, GRAY)
            fg, fg2, bgc = INK, INK, GRAY
        elif i == 1:
            notch(sh, x0, y, x1c, y + ch, 14, 3, INK, INK)
            fg, fg2, bgc = PAPER, LIGHT, INK
        else:
            notch(sh, x0, y, x1c, y + ch, 14, 3, INK)
            fg, fg2, bgc = INK, GRAY, PAPER
        sh.ink(x0 + 16, y + 12, y + 44, day, sh.f.sans(26, True), fg)
        line_icon(sh, (x0 + x1c) // 2, y + 78, 72, ik, fg, bg=bgc)
        sh.ink(x0 + 16, y + ch - 52, y + ch - 20, sh.clip(desc, sh.f.sans(26), x1c - x0 - 32),
               sh.f.sans(26), fg2)
        hv = f"{hi}/{lo}"
        sh.ink(x1c - 14, y + 12, y + 44, hv, tf(sh, hv, 26, "mono", bold=True),
               fg, anchor="rt")
    y += ch + 16

    # 行情：三行 + 条码分隔
    mix(sh, M, y, y + 34, "SALVAGE LEDGER", "行情", 28, 26, INK, "din", 10)
    key_glyph(sh, X1, y, 34)
    y += 40
    for name, price, pct in d["quotes"]:
        sh.ink(M, y, y + 34, sh.clip(name, sh.f.sans(26), 280), sh.f.sans(26), INK)
        sh.ink(M + 300, y, y + 34, price, tf(sh, price, 30, "mono", bold=True), INK)
        chgbar(sh, M + 560, y + 2, 200, 40, pct)
        ps = f"{pct:+.2f}%"
        sh.ink(X1, y, y + 34, ps, tf(sh, ps, 28, "mono", bold=True), INK, anchor="rt")
        y += 44
    return y


LAYS = [("甲 · 信息终端", lay_terminal)]

NOTES = {
    "甲 · 信息终端": "最贴近游戏主界面的一版：顶部黑钢板状态条 + 危险斜纹、切角面板、"
                   "六个分段仪表读湿度/降水/空气/紫外、预警盒用斜纹包边。"
                   "预报叫近日预报：昨天灰底、今天反相、明/后天白底，大后天不要了。"
                   "行情行中间是涨跌条：跌=中线往左一条灰条、涨=中线往右一条黑条，"
                   "长度=幅度（满长 ≈ 3%）；图例不写字，用灰条+▼ / 黑条+▲ 的图样。"
                   "接口不给历史数据，所以不编 K 线序列。"
                   "只有序列号行那枚条码是纯装饰。",
}


def render(name, fn, data):
    sh = Sheet(FontsArc())
    foot_top = H - 44 - 86
    probe = Sheet(FontsArc())
    h = fn(probe, data, 0, 0)
    extra = (foot_top - 80) - TOP - h
    y = fn(sh, data, TOP, extra)
    foot(sh, data, foot_top - 60)
    return sh, extra


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = worst_bw()
    # 近日预报 = 昨天 + 今天 + 明/后天：砍掉大后天，前面补昨天（ Open-Meteo 的
    # past_days=1 在生产里能给到真昨天，这里用最坏情况同款长度造一条）。
    data["wx"]["fc"] = [("昨天", "cloud", "阴", "28°", "23°")] + data["wx"]["fc"][:3]
    cards = []
    for name, fn in LAYS:
        sh, extra = render(name, fn, data)
        if extra < 40:
            raise SystemExit(f"{name}: 余量 {extra}px 低于安全线 40")
        png = OUT / f"袭-{name[0]}.png"
        sh.img.save(png, format="PNG", optimize=True)
        print(f"{name}: 余量 {extra}px -> {png.name}")
        cards.append(f"""
    <figure>
      <h2>{name}</h2>
      <div class="shot"><img src="{png.name}" alt="{name}">
        <span class="bat" style="left:{BAT[0] / W * 100}%;top:{BAT[1] / H * 100}%;
              width:{(BAT[2] - BAT[0]) / W * 100}%;height:{(BAT[3] - BAT[1]) / H * 100}%"></span></div>
      <p class="d">{NOTES[name]}</p>
      <figcaption>最坏情况（节日+倒计时+调休 / 2 预警 / 4 预报 / 3 指数）· 余量 <b>{extra}px</b></figcaption>
    </figure>""")
    calm = worst_bw()
    calm["wx"]["fc"] = data["wx"]["fc"]
    calm["wx"]["warn"] = []
    sh, extra = render("甲 · 信息终端", lay_terminal, calm)
    if extra < 40:
        raise SystemExit(f"无预警态: 余量 {extra}px 低于安全线 40")
    png = OUT / "袭-甲-无预警.png"
    sh.img.save(png, format="PNG", optimize=True)
    print(f"甲 · 无预警态: 余量 {extra}px -> {png.name}")
    cards.append(f"""
    <figure>
      <h2>甲 · 信息终端（无预警态）</h2>
      <div class="shot"><img src="{png.name}" alt="无预警态">
        <span class="bat" style="left:{BAT[0] / W * 100}%;top:{BAT[1] / H * 100}%;
              width:{(BAT[2] - BAT[0]) / W * 100}%;height:{(BAT[3] - BAT[1]) / H * 100}%"></span></div>
      <p class="d">把两条预警抽掉后的样子：预警盒收成一行灰字「NO ACTIVE THREAT 当前无预警」，
      省下的四十多像素由版面摊进各段间距，不留空洞。其余内容仍是最坏情况。</p>
      <figcaption>无预警 · 余量 <b>{extra}px</b></figcaption>
    </figure>""")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ARC Raiders 译法 · 信息终端</title>
<style>
 body{{margin:0;background:#14161a;color:#d8d5cd;
      font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
 header{{padding:24px 28px 16px;border-bottom:3px solid #3a3d42;
      background:repeating-linear-gradient(-45deg,#1b1e22 0 14px,#22262b 14px 28px)}}
 h1{{margin:0 0 6px;font-size:21px;color:#f0ede4;letter-spacing:.06em}}
 header p{{margin:0;color:#a8a49a;font-size:13.5px;max-width:900px}}
 main{{padding:24px 28px 80px;display:grid;grid-template-columns:repeat(auto-fit,
       minmax(420px,1fr));gap:22px;max-width:1700px}}
 figure{{margin:0;background:#1b1e22;border:1px solid #3a3d42;
         padding:14px 16px 16px;clip-path:polygon(18px 0,100% 0,100% calc(100% - 18px),calc(100% - 18px) 100%,0 100%,0 18px)}}
 h2{{margin:0 0 8px;font-size:18px;color:#f0ede4;letter-spacing:.04em}}
 .d{{margin:10px 0 8px;color:#b3afa5;font-size:12.5px}}
 .shot{{position:relative;line-height:0}}
 img{{width:100%;height:auto;border:1px solid #3a3d42}}
 .bat{{position:absolute;border:2px dashed #e5534b;box-sizing:border-box}}
 figcaption{{margin-top:6px;font-size:12.5px;color:#a8a49a}}
</style></head><body>
<header><h1>ARC RAIDERS 译法 · 信息终端（留用的一版）</h1>
<p>游戏那套橄榄/橙/锈的军规 UI 上不了五档灰的墨水屏，所以翻译成：黑钢板条、危险斜纹、
切角面板、DIN 模板字（Bahnschrift）+ Impact 大数字、Consolas 读数、分段仪表、条码序列号。
涨跌不写字例：灰条+▼=跌、黑条+▲=涨，条长=幅度。1072×1448 真图、最坏情况数据；
红虚线=电量精灵图位。同轮的任务简报/扫描雷达两版已放弃删除。本页只是看稿，未接生产。</p></header>
<main>{''.join(cards)}</main></body></html>
"""
    (OUT / "arcraiders.html").write_text(html, encoding="utf-8")
    print(f"ok -> {OUT / 'arcraiders.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
