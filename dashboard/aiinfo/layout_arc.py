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

from .sources import clean_text

# 注：DEVICE_LABELS 在 render.py 里，而 render.py 会 import 本模块 —— 那里改成
# 函数内延迟导入，绕开循环。别把它挪进来当参数传，那等于把设备名硬编码两遍。

#: 这块画布是设备尺寸，和生产端 cfg.size 是同一回事。
#: 版式里的 M / X1 / CW 全是模块级常量，所以这里必须钉死；
#: render_arc() 开头会核对真实画布尺寸，对不上直接报错而不是画歪。
W, H = 1072, 1448

INK, SOFT, GRAY, LIGHT, PAPER = 0, 70, 130, 195, 255
M = 56
X1 = W - M
CW = X1 - M
TOP = 112
BAT = (814, 40, 1016, 96)

#: DIN / Impact / Arial Black / Consolas 共有的字符。不在这个表里（中文、·、全角）
#: 就整串回退黑体，绝不混排 —— 混排必出豆腐块。
LATIN_OK = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
               "°%+-/.,:()#&'\"!?*<>= ")


class ArcFonts:
    """把生产的 FontBook 包成这套版式习惯的调用方式。

    出稿阶段它是直接写死 Windows 字体路径的（Bahnschrift / Impact / Consolas）。
    生产端不能那么干：云端是 Linux，那三个都不存在。所以这里一律走角色
    （mono / condensed），由 fonts.py 按平台挑真货或开源近亲，挑不到就退常规。
    """

    def __init__(self, book):
        self.book = book

    def sans(self, size: int, bold: bool = False):
        return self.book.get(size, bold)

    def serif(self, size: int):
        return self.book.get_display(size)

    def din(self, size: int, w: int = 700, wdth=None):
        # 出稿用的是 Bahnschrift 的可变字重/字宽轴；开源近亲没有那两组轴，
        # 所以轴参数直接忽略 —— 留着签名只是为了版面代码不用改。
        return self.book.get_condensed(size, w >= 600)

    def impact(self, size: int):
        return self.book.get_condensed(size, True)

    def mono(self, size: int, bold: bool = False):
        return self.book.get_mono(size, bold)

    def digit(self, size: int):
        return self.book.get_mono(size, True)


class ArcSheet:
    """生产 Renderer 之上的薄画布：只补这套版式要用的那几个动作。

    为什么不直接把 lay_terminal 重写成 Renderer 的写法：那 130 行是逐像素调出来的，
    重写一遍必然引入第二批差异，而我们要的是"和定稿那张一样"。
    """

    def __init__(self, r):
        self.r = r
        self.img = r.img
        self.d = r.d
        self.f = ArcFonts(r.fonts)

    def tw(self, s, font) -> int:
        return self.r.tw(s, font)

    def lh(self, font) -> int:
        return self.r.lh(font)

    def t(self, xy, s, font, fill=INK, anchor=None):
        self.r.text(xy, s, font, fill, anchor)

    def ink(self, x, top, bottom, s, font, fill=INK, anchor="lt"):
        """按实际墨迹在 [top,bottom] 里垂直居中（数字没有下伸部，按行盒定位会偏上）。"""
        if not s:
            return 0
        box = self.d.textbbox((x, 0), s, font=font, anchor=anchor)
        h = box[3] - box[1]
        self.r.text((x, top + (bottom - top - h) / 2 - box[1]), s, font, fill, anchor)
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

    def vrule(self, x, y0, y1, t=3, c=LIGHT):
        self.d.rectangle([x, y0, x + t - 1, y1], fill=c)

    def clip(self, s, font, max_w):
        return self.r.clip_text(s, font, max_w)


def cells_pick(wx, n=4):
    """数据格挤不下时按简报截断优先级砍：风向 / 紫外先走。"""
    drop = ("风", "紫外")
    out = [kv for kv in wx["cells"] if not any(k in kv[0] for k in drop)]
    if len(out) < n:
        out = list(wx["cells"])
    return out[:n]


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
    if n <= 0:
        return []
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
    # 出稿这行是写死的 "SN 2026-0925-07 // DAILY CYCLE 4x"。生产屏上挂一个假日期
    # 是说不过去的（这块屏唯一的作用就是"此刻是真的"），所以改成从出图时间算，
    # 后面的数字就是实际画了几格预报 —— 不编装饰性数字。
    sn = d.get("sn") or "SN ------ // DAILY CYCLE 0x"
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

    # 预报：反相那格 = 今天，它前一格（昨天）用 130 灰底，其余白底。
    # ⚠️ 出稿那版是按位置写死的（第 0 格=昨天、第 1 格=今天），因为它自己塞了一条
    #   假昨天。生产里没有假数据，第一格常常就是今天 —— 再按位置认会把"明天"
    #   高亮成今天，所以这里改成按标签找。
    fc = wx["fc"][:4]
    n = len(fc)
    if n == 0:                       # 天气整个没拿到：不画空行，也别除零
        return y
    today = next((i for i, c in enumerate(fc) if str(c[0]).startswith("今天")), None)
    pitch = CW // n
    mix(sh, M, y, y + 34, "FORECAST", "近日天气", 28, 26, INK, "din", 10)
    y += 40
    ch = 164
    for i, (day, ik, desc, hi, lo) in enumerate(fc):
        x0 = M + i * pitch
        x1c = x0 + pitch - 12
        if i == today:
            notch(sh, x0, y, x1c, y + ch, 14, 3, INK, INK)
            fg, fg2, bgc = PAPER, LIGHT, INK
        elif today is not None and i == today - 1:
            notch(sh, x0, y, x1c, y + ch, 14, 3, GRAY, GRAY)
            fg, fg2, bgc = INK, INK, GRAY
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


# ---------------------------------------------------------------------------
#  生产入口
# ---------------------------------------------------------------------------

def _cells(wx: dict) -> list[tuple[str, str]]:
    """右侧那六个读数格。顺序即优先级：格子挤不下时 cells_pick 从尾巴上砍。"""
    out = []
    if wx.get("feels") is not None:
        out.append(("体感", f'{wx["feels"]}°'))
    if wx.get("humidity") is not None:
        out.append(("湿度", f'{wx["humidity"]}%'))
    if wx.get("pop") is not None:
        out.append(("降水", f'{wx["pop"]}%'))
    if wx.get("wind_dir") and wx.get("wind_level") is not None:
        out.append((f'{wx["wind_dir"]}风', f'{wx["wind_level"]} 级'))
    air = wx.get("air") or {}
    if air.get("level"):
        out.append(("空气", f'{air["level"]} {air.get("aqi", "")}'.strip()))
    if wx.get("uv") is not None:
        out.append(("紫外", str(wx["uv"])))
    return out


def _forecast(wx: dict) -> list[tuple[str, str, str, str, str]]:
    """近日天气：(标签, 图标, 描述, 高, 低)。

    顶上那格「昨天」来自 `sources._fetch_yesterday()`（Open-Meteo 的真历史），
    和风格式给不了 —— 它的时光机接口我们调不通。拿不到就没有这一格，
    **绝不拿今天的数凑**，也绝不补一条假的（出稿那版塞的就是假昨天）。

    有昨天时总数仍是四格：昨天 / 今天 / 明天 / 后天，和定稿那张一致。
    """
    out = []
    y = wx.get("yesterday") or {}
    if y.get("high") is not None:
        out.append(("昨天", str(y.get("icon", "cloud")), clean_text(y.get("desc", ""), 8),
                    f'{y["high"]}°', f'{y.get("low", "--")}°'))
    room = 4 - len(out)
    for f in (wx.get("forecast") or [])[:room]:
        out.append((str(f.get("label", "")), str(f.get("icon", "cloud")),
                    clean_text(f.get("desc", ""), 8),
                    f'{f.get("high", "--")}°', f'{f.get("low", "--")}°'))
    return out


def arc_data(cfg, data: dict) -> dict:
    """生产那份数据 → 这套版式要的 d。字段对不上就在这里补齐，别去改版面。"""
    cal = data["calendar"]
    wx = data.get("weather") or {}
    quotes = []
    for q in (data.get("quotes") or []):
        try:
            pct = float(q.get("pct") or 0.0)
        except (TypeError, ValueError):
            pct = 0.0
        quotes.append((clean_text(q.get("name", ""), 12), f'{q.get("price", "--")}', pct))

    # 预警必须是字符串 —— 出稿版就是字符串列表，生产给的是 dict。
    # 这个区别在别的版式上炸过一次（一有预警整轮出图失败），别再炸第二次。
    warn = [clean_text(w.get("title", "") if isinstance(w, dict) else str(w), 26)
            for w in (wx.get("warning") or [])]
    warn = [w for w in warn if w]

    from .render import DEVICE_LABELS        # 延迟导入：render.py 会 import 本模块
    label = DEVICE_LABELS.get(str(cfg.get("device.model", "")).lower(),
                              str(cfg.get("device.model", "")))
    mid = [f"Kindle {label}"]
    if wx.get("source"):
        mid.append(f"天气 {wx['source']}")
    if quotes and cfg.get("quotes.enabled", True):
        mid.append("行情 腾讯")
    # 昨天那格是 Open-Meteo 的历史，其余天气是和风 —— 两个模型的最高温能差 1°C，
    # 混在一行不写清楚就是拿口径差异当误差。只在真的画了这一格时才标。
    if (wx.get("yesterday") or {}).get("high") is not None:
        mid.append("昨天 Open-Meteo")

    fc = _forecast(wx)
    return {
        "place": clean_text(cfg.get("location.name", ""), 20),
        "sn": (f'SN {data["generated_at"].strftime("%Y-%m%d")}-07'
               f' // DAILY CYCLE {len(fc)}x'),
        "cal": {
            "month": f"{cal.solar_year} 年 {cal.solar_month} 月",
            "day": str(cal.solar_day),
            "week": cal.weekday,
            "lunar": clean_text(cal.lunar_text, 12),
            "zodiac": f"{cal.ganzhi_year} · {cal.shengxiao}年",
            "fest_today": [cal.badge] if cal.badge and cal.badge_kind == "festival" else [],
            "countdown": (f"距{cal.term_next}还有 {cal.term_next_days} 天"
                          if cal.term_next_days else ""),
            "tiaoxiu": "",
        },
        "wx": {
            "temp": f'{wx.get("temp", "--")}°' if wx.get("temp") is not None else "--°",
            "desc": clean_text(wx.get("desc", ""), 8),
            "icon": wx.get("icon", "cloud"),
            "cells": _cells(wx),
            "warn": warn,
            "fc": fc,
        },
        "quotes": quotes,
        "foot_l": f"更新 {data['generated_at'].strftime('%m-%d %H:%M')}",
        "foot_m": " · ".join(mid),
    }


class _Scratch:
    """一张草稿纸：只为量出版式要多高，不能往真图上画第二遍。"""

    def __init__(self, fonts):
        from PIL import Image, ImageDraw
        self.img = Image.new("L", (W, H), PAPER)
        self.d = ImageDraw.Draw(self.img)
        self.fonts = fonts

    def text(self, xy, s, font, fill=INK, anchor=None, strong=False):
        self.d.text(xy, s, font=font, fill=fill, anchor=anchor)

    def tw(self, s, font) -> int:
        return int(self.d.textlength(s, font=font)) if s else 0

    def lh(self, font) -> int:
        ascent, descent = font.getmetrics()
        return ascent + descent

    def clip_text(self, s, font, max_w):
        """和 Renderer.clip_text 同义：超宽就截，加省略号。量高度必须走同一条路，
        否则探出来的高度和真画出来的不一样，富余算错。"""
        if not s or self.tw(s, font) <= max_w:
            return s
        while s and self.tw(s + "…", font) > max_w:
            s = s[:-1]
        return (s + "…") if s else ""


def render_arc(r, top: int, foot_top: int) -> int:
    """画一版信息终端。返回正文画完时的 y，和 _render_c1 的约定一致。"""
    if (r.w, r.h) != (W, H):
        raise RuntimeError(f"信息终端的坐标是按 {W}x{H} 钉死的，当前画布 {r.w}x{r.h}")
    d = arc_data(r.cfg, r.data)
    probe = ArcSheet(_Scratch(r.fonts))
    height = lay_terminal(probe, d, 0, 0)
    extra = max(0, (foot_top - 80) - top - height)
    sh = ArcSheet(r)
    y = lay_terminal(sh, d, top, extra)
    foot(sh, d, foot_top - 60)
    r.slack = extra
    return y
