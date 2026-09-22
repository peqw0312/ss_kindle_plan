#!/usr/bin/env python3
"""天气图标选型页 —— 一次出 6 套完整图标，你在浏览器里挑一套。

为什么是"6 套一起出"而不是"我改一版你看看"：
    图标好不好看只有对着真图、真尺寸才知道。一版一版地猜，就变成了
    我猜、你说丑、我再猜。这里每套都铺满 11 种天气 × 3 个真实尺寸
    （248 = 主图标、128 = 预报列、80 = 再小一档试极限），顶上还有一张
    六套横向对照，扫一眼就能比出来。

    全部按 Kindle 上的真实像素画（1072×1448 那张图的同一套比例），
    页面里统一缩到 50% 显示 —— 那个大小就是它在墨水屏上的物理大小。
    描边粗细、云体灰阶都是真值，不是示意图。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe dashboard\\tools\\icon_sets.py
    然后打开它印出来的 .workbuddy\\_studio\\icon_sets.html

挑中了告诉我字母（A~F）就行，我把那一套搬进 render.py 的 icon()。
下面的绘制函数就是照着"能直接搬进 render.py"的形状写的，不是另画一套。
"""

from __future__ import annotations

import base64
import io
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))

from PIL import Image, ImageChops, ImageDraw, ImageFilter        # noqa: E402
from aiinfo.config import Config                                 # noqa: E402
from aiinfo.render import Renderer                               # noqa: E402

INK = 0
INK_SOFT = 70
GRAY = 130
GRAY_LIGHT = 195
CLOUD_BODY = 210

#: 11 种天气。顺序就是页面上的列顺序，和 render.Renderer.icon() 认的 kind 对齐。
KINDS = [("sun", "晴"), ("sun_cloud", "多云"), ("cloud", "阴"),
         ("moon", "晴·夜"), ("moon_cloud", "多云·夜"),
         ("rain", "中雨"), ("shower", "阵雨"), ("drizzle", "小雨"),
         ("snow", "雪"), ("thunder", "雷阵雨"), ("fog", "雾")]

#: 真实尺寸（设备像素）：主图标 px(124)、预报列 px(64)，第三档是往下试探用的
SIZES = [(248, 330), (128, 200), (80, 150)]

# 云的三个版本：每格的 (x 偏移, 半径)，单位是 a；所有圆都与底边相切，
# 所以底边只剩切点之间那一小段公切线，直线和圆相接处没有拐角。
# 两瓣那版凹口不能太深，否则画出来是牛角包不是云。
LOBES2B = ((-0.18, 0.36), (0.26, 0.30))
LOBES3 = ((-0.40, 0.26), (-0.05, 0.38), (0.34, 0.24))
LOBES4 = ((-0.46, 0.21), (-0.18, 0.33), (0.14, 0.35), (0.44, 0.20))


# =====================================================================
#  基础几何
# =====================================================================

def _cloud_mask(d, a: float, ox: float, oy: float, lobes, wide: float) -> None:
    """云的剪影。oy 是底边所在的 y，所有圆心都落在底边上方正好一个半径处。"""
    cs = [(ox + dx * a * wide, oy - r * a, r * a) for dx, r in lobes]
    d.polygon([(cx, cy) for cx, cy, _ in cs] + [(cs[-1][0], oy), (cs[0][0], oy)],
              fill=255)
    for cx, cy, rr in cs:
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=255)


def _cap_line(d, p0, p1, w: int, level: int) -> None:
    """Pillow 的 line 没有圆头，方头在小尺寸下显脏，两端自己补圆。"""
    d.line([p0, p1], fill=level, width=w)
    for x, y in (p0, p1):
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=level)


def _stamp(img, cx: float, cy: float, size: float, lw: int, body, sil,
           halo: int = 0) -> None:
    """把剪影变成「恒定宽度轮廓 + 云体填充」再贴上去。lw=0 就是整块实心。

    轮廓靠腐蚀得到（剪影 - 腐蚀后的内部），因为**并集没法直接描边** ——
    逐个圆画 outline 会在云内部留下一圈圈弧线，比实心黑还难看。
    """
    side = int(max(12, round(size * 1.7)))
    x0, y0 = int(round(cx - side / 2)), int(round(cy - side / 2))
    mask = Image.new("L", (side, side), 0)
    sil(ImageDraw.Draw(mask), side)
    if halo:
        img.paste(255, (x0, y0), mask.filter(ImageFilter.MaxFilter(halo * 2 + 1)))
    if lw <= 0:
        img.paste(body, (x0, y0), mask)
        return
    inner = mask.filter(ImageFilter.MinFilter(lw * 2 + 1))
    edge = ImageChops.subtract(mask, inner)
    layer = Image.new("L", (side, side), 255)
    if body is not None:
        layer.paste(body, (0, 0), inner)
    layer.paste(INK, (0, 0), edge)
    img.paste(layer, (x0, y0), mask)


# =====================================================================
#  部件
# =====================================================================

def _rays(d, cx, cy, sr, st, w, level, n=8, a0=0.0, step=45.0):
    r0, r1 = st["ray_len"]
    for i in range(n):
        ang = math.radians(a0 + i * step)
        ca, sa = math.cos(ang), math.sin(ang)
        if st["ray"] == "dot":
            rr = max(1.5, w * 0.62)
            mx, my = cx + ca * sr * (r0 + r1) / 2, cy + sa * sr * (r0 + r1) / 2
            d.ellipse([mx - rr, my - rr, mx + rr, my + rr], fill=level)
            continue
        p0 = (cx + ca * sr * r0, cy + sa * sr * r0)
        p1 = (cx + ca * sr * r1, cy + sa * sr * r1)
        if st["ray"] == "wedge":                 # 收尖的三角芒，比等宽线更有精神
            # w 是"笔宽"不是半宽：八道芒在 r0 处的周向间距只有 ~2.4w，
            # 半宽一旦超过 w*0.7 相邻两道就并成一圈齿轮。
            px, py = -sa, ca
            w0, w1 = w * 0.62, w * 0.18
            d.polygon([(p0[0] + px * w0, p0[1] + py * w0),
                       (p1[0] + px * w1, p1[1] + py * w1),
                       (p1[0] - px * w1, p1[1] - py * w1),
                       (p0[0] - px * w0, p0[1] - py * w0)], fill=level)
        else:
            _cap_line(d, p0, p1, w, level)


def _luminary(d, cx, cy, sr, st, w, level, n=8, a0=0.0, step=45.0, disc=None):
    """太阳盘 / 月牙用的那层。实心风格画盘，细线风格可以只画环。"""
    if (disc or st["disc"]) == "ring":
        d.ellipse([cx - sr, cy - sr, cx + sr, cy + sr], outline=level,
                  width=max(1, w))
    else:
        d.ellipse([cx - sr, cy - sr, cx + sr, cy + sr], fill=level)
    if n:
        _rays(d, cx, cy, sr, st, w, level, n=n, a0=a0, step=step)


def _crescent(d, cx, cy, mr, level, cut=0.94, dx=0.52, dy=-0.46):
    """实心盘再挖掉一个偏心圆。"""
    d.ellipse([cx - mr, cy - mr, cx + mr, cy + mr], fill=level)
    c = mr * cut
    ox, oy = cx + mr * dx, cy + mr * dy
    d.ellipse([ox - c, oy - c, ox + c, oy + c], fill=255)


def _drops(d, cx, cy, R, st, aw, level, n, long, k=1.0, slant=None):
    """云底下那一排降水符号。k 是雨量系数：阵雨放大、小雨缩小。"""
    slant = st["drop_slant"] if slant is None else slant
    y0 = cy + R * st["drop_y0"]
    span = st["drop_span"]
    for i in range(n):
        x = cx + (-span / 2 + (span / (n - 1) if n > 1 else 0) * i) * R
        shape = st["drop"]
        if shape == "dot":
            rr = max(2.0, aw * 0.95) * k
            y = y0 + R * long * 0.5
            d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=level)
        elif shape == "tear":                    # 水滴：上尖下圆
            rr = max(2.5, aw * 1.05) * k
            y = y0 + R * long * 0.55
            d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=level)
            d.polygon([(x, y0 - R * long * 0.35), (x - rr * 0.92, y),
                       (x + rr * 0.92, y)], fill=level)
        else:
            sx = slant if shape == "slant" else 0.0
            y1 = y0 + R * long
            _cap_line(d, (x, y0), (x + sx * R, y1), max(2, int(round(aw * k))), level)


def _flakes(d, cx, cy, R, st, aw, level, n=3, y=0.46):
    span = st["drop_span"]
    for i in range(n):
        x = cx + (-span / 2 + (span / (n - 1) if n > 1 else 0) * i) * R
        yy = cy + R * y
        if st["snow"] == "dots":
            rr = max(2.5, R * 0.085)
            d.ellipse([x - rr, yy - rr, x + rr, yy + rr], fill=level)
        else:                                    # 六芒小雪片
            rr = R * 0.145
            w = max(1, int(round(R * 0.055)))
            for t in range(3):
                ang = math.radians(t * 60)
                _cap_line(d, (x - math.cos(ang) * rr, yy - math.sin(ang) * rr),
                          (x + math.cos(ang) * rr, yy + math.sin(ang) * rr),
                          w, level)


def _bolt(d, cx, cy, R, level, k=1.0):
    d.polygon([(cx + R * 0.10 * k, cy + R * 0.16 * k),
               (cx - R * 0.24 * k, cy + R * 0.60 * k),
               (cx + R * 0.00 * k, cy + R * 0.60 * k),
               (cx - R * 0.12 * k, cy + R * 0.98 * k),
               (cx + R * 0.30 * k, cy + R * 0.46 * k),
               (cx + R * 0.06 * k, cy + R * 0.46 * k),
               (cx + R * 0.32 * k, cy + R * 0.16 * k)], fill=level)


def _fog(d, cx, cy, R, st, level, w):
    for i, dy in enumerate((-0.34, -0.02, 0.30)):
        half = R * (0.52 if i % 2 else 0.74)
        _cap_line(d, (cx - half, cy + R * dy), (cx + half, cy + R * dy), w, level)


# =====================================================================
#  一个图标
# =====================================================================

def paint(img, cx: float, cy: float, size: float, st: dict, kind: str) -> None:
    """按某一套风格画一个天气图标。所有偏移量都以 R = size/2 为单位。"""
    kind = str(kind or "cloud").lower()
    R = size / 2.0
    lw = max(1, int(round(size * st["lw"]))) if st["lw"] else 0
    aw = max(2, int(round(size * st["aux_w"])))
    # 实心风格 lw=0，但光芒、圆点这些"笔画"不能跟着变成 1px 发丝 ——
    # 取两者里粗的那个当实际笔宽。
    sw = max(lw, aw)
    fw = max(2, int(round(size * st["fog_w"])))
    body, lum, halo = st["body"], st["lum"], int(round(size * st["halo"]))
    d = ImageDraw.Draw(img)
    a = R * st["cloud_mult"]
    lobes, rmax = st["lobes"], max(r for _, r in st["lobes"])

    def cloud(tx, ty, scale=1.0):
        def sil(dd, side):
            aa = a * scale
            c = side / 2
            _cloud_mask(dd, aa, c + (tx - cx), c + (ty - cy) + (rmax - 0.26) * aa,
                        lobes, st["wide"])
        _stamp(img, cx, cy, size, lw, body, sil, halo)

    def moon(mx, my, mr):
        if st.get("moon_outline"):
            def sil(dd, side):
                c = side / 2
                _crescent(dd, c + (mx - cx), c + (my - cy), mr, 255)
            _stamp(img, cx, cy, size, lw, body, sil, halo)
        else:
            _crescent(d, mx, my, mr, lum)

    if kind == "sun":
        _luminary(d, cx, cy, R * st["disc_r"], st, sw, lum,
                  n=st["ray_n"], a0=0, step=360 / st["ray_n"])

    elif kind == "sun_cloud":
        sr = R * st["disc_r"] * 0.80
        # 只露左上五道芒，间距比整圈密一倍，笔宽要跟着收，否则并成一坨。
        # 躲在云后面的太阳必须是实心盘：环被云咬掉一半之后只剩一根铁丝。
        _luminary(d, cx - R * 0.34, cy - R * 0.36, sr, st, max(2, int(sw * 0.72)),
                  lum, n=5, a0=180, step=22.5, disc="solid")
        cloud(cx + R * 0.14, cy + R * 0.30, scale=0.92)

    elif kind == "moon":
        moon(cx - R * 0.04, cy + R * 0.02, R * st["moon_r"])

    elif kind == "moon_cloud":
        moon(cx - R * 0.34, cy - R * 0.36, R * st["disc_r"] * 1.02)
        cloud(cx + R * 0.14, cy + R * 0.30, scale=0.92)

    elif kind == "cloud":
        cloud(cx, cy)

    elif kind in ("rain", "shower", "drizzle", "snow", "thunder"):
        long = {"shower": 0.46, "drizzle": 0.22, "rain": 0.34}.get(kind, 0.30)
        k = {"shower": 1.30, "drizzle": 0.70}.get(kind, 1.0)
        up = R * (0.34 if kind == "thunder" else 0.30)
        cloud(cx, cy - up, scale=0.98)
        if kind == "snow":
            _flakes(d, cx, cy - up * 0.35, R, st, aw, lum, n=3)
        elif kind == "thunder":
            _bolt(d, cx, cy - up * 0.45, R, lum)
        else:
            _drops(d, cx, cy - up * 0.55, R, st, aw, lum,
                   n=st["drop_n"], long=long, k=k)

    else:                                        # fog / 认不出来的一律按雾画
        _fog(d, cx, cy, R, st, lum if st["fog_solid"] else GRAY, fw)


# =====================================================================
#  六套候选
# =====================================================================

STYLES = [
    dict(key="A", name="细线 · 云岭", body=CLOUD_BODY, lum=INK,
         tagline="Lucide 那一脉的精修版：细描边 + 浅灰云体，三个圆弧",
         note="最稳的一版。云体是浅灰不是实心黑，小尺寸不糊，大面积也不压版面。",
         lw=0.055, aux_w=0.050, fog_w=0.055, halo=0.0,
         lobes=LOBES3, cloud_mult=1.16, wide=1.00,
         disc="solid", disc_r=0.30, moon_r=0.44, ray="line", ray_len=(1.50, 2.25),
         ray_n=8, drop="slant", drop_slant=0.16, drop_n=3, drop_y0=0.42,
         drop_span=0.62, snow="dots", fog_solid=False),

    dict(key="B", name="粗线 · 墨骨", body=None, lum=INK,
         tagline="只留轮廓、不填灰：笔画粗一倍，云更宽更扁",
         note="最「大气」的线框版。中空所以版面很透气，缺点是 40px 那一档笔画偏挤。",
         lw=0.085, aux_w=0.075, fog_w=0.080, halo=0.0,
         lobes=LOBES3, cloud_mult=1.20, wide=1.06,
         disc="solid", disc_r=0.28, moon_r=0.46, ray="line", ray_len=(1.45, 2.05),
         ray_n=8, drop="dash", drop_slant=0.0, drop_n=3, drop_y0=0.44,
         drop_span=0.66, snow="dots", fog_solid=False),

    dict(key="C", name="实心 · 剪纸", body=INK, lum=INK,
         tagline="整块实心黑，云和天体之间留一道白缝（剪纸那种挖空）",
         note="对比度最高、最抢眼，隔两米也看得清。代价是墨量大，刷新时残影明显一点。",
         lw=0.0, aux_w=0.070, fog_w=0.085, halo=0.030,
         lobes=LOBES3, cloud_mult=1.18, wide=1.04,
         disc="solid", disc_r=0.30, moon_r=0.46, ray="wedge", ray_len=(1.40, 2.10),
         ray_n=8, drop="tear", drop_slant=0.0, drop_n=3, drop_y0=0.44,
         drop_span=0.62, snow="dots", fog_solid=True),

    dict(key="D", name="双调 · 灰体墨边", body=GRAY, lum=INK,
         tagline="云体中灰 + 墨色描边，降水纯黑 —— 三层深浅拉开层次",
         note="最有「设计过」的感觉，层次最丰富。灰体比 A 深，远看更成一个块面。",
         lw=0.070, aux_w=0.060, fog_w=0.065, halo=0.022,
         lobes=LOBES3, cloud_mult=1.16, wide=1.02,
         disc="solid", disc_r=0.29, moon_r=0.45, ray="line", ray_len=(1.48, 2.18),
         ray_n=8, drop="slant", drop_slant=0.18, drop_n=3, drop_y0=0.42,
         drop_span=0.62, snow="dots", fog_solid=True),

    dict(key="E", name="柔雾 · 灰实心", body=INK_SOFT, lum=INK_SOFT,
         tagline="实心但不黑：整块深灰，雨点、月牙、闪电也都是灰的",
         note="最柔和、最不像「图标」的一版，适合你想让天气区安静一点。",
         lw=0.0, aux_w=0.065, fog_w=0.075, halo=0.028,
         lobes=LOBES4, cloud_mult=1.10, wide=1.02,
         disc="solid", disc_r=0.31, moon_r=0.46, ray="dot", ray_len=(1.55, 2.05),
         ray_n=8, drop="tear", drop_slant=0.0, drop_n=3, drop_y0=0.44,
         drop_span=0.62, snow="dots", fog_solid=True),

    dict(key="F", name="点线 · 极简", body=None, lum=INK,
         tagline="太阳是一个环 + 八颗点，雨是三颗点，笔画最细",
         note="最轻盈、最现代。但它是六套里最怕缩小的 —— 看 80px 那一档再决定。",
         lw=0.045, aux_w=0.045, fog_w=0.050, halo=0.0,
         lobes=LOBES2B, cloud_mult=1.36, wide=1.06,
         disc="ring", disc_r=0.32, moon_r=0.44, moon_outline=True,
         ray="dot", ray_len=(1.55, 2.10),
         ray_n=8, drop="dot", drop_slant=0.0, drop_n=3, drop_y0=0.46,
         drop_span=0.60, snow="flake", fog_solid=False),
]
BY_KEY = {s["key"]: s for s in STYLES}


# =====================================================================
#  出图
# =====================================================================

def make_matrix(r) -> Image.Image:
    """六套横向对照：行 = 风格，列 = 最常用的四种天气，统一 248px。"""
    cols = ["sun", "sun_cloud", "cloud", "rain", "thunder"]
    cell, gutter, top = 300, 360, 86
    img = Image.new("L", (gutter + cell * len(cols), top + cell * len(STYLES)), 255)
    d = ImageDraw.Draw(img)
    for j, kind in enumerate(cols):
        label = dict(KINDS)[kind]
        r_on(img, r, (gutter + cell * j + cell // 2, 44), label, 24, GRAY, "mm")
    for i, st in enumerate(STYLES):
        y0 = top + cell * i
        d.line([(gutter - 30, y0), (img.width - 20, y0)], fill=GRAY_LIGHT, width=2)
        r_on(img, r, (30, y0 + cell // 2 - 16), f"{st['key']} · {st['name']}", 26,
             INK, "lm", bold=True)
        r_on(img, r, (30, y0 + cell // 2 + 22), st["tagline"][:16], 20, GRAY, "lm")
        for j, kind in enumerate(cols):
            paint(img, gutter + cell * j + cell // 2, y0 + cell // 2, 248, st, kind)
    return img


def make_sheet(st) -> Image.Image:
    """一套风格的全家福：列 = 11 种天气，行 = 三个真实尺寸。"""
    cell_w, gutter, top = 268, 150, 84
    img = Image.new("L", (gutter + cell_w * len(KINDS),
                          top + sum(h for _, h in SIZES)), 255)
    for j, (kind, label) in enumerate(KINDS):
        r_on(img, _R, (gutter + cell_w * j + cell_w // 2, 40), label, 24, GRAY, "mm")
    y = top
    for size, cell_h in SIZES:
        r_on(img, _R, (34, y + cell_h // 2), f"{size // 2}px", 22, GRAY_LIGHT, "lm")
        for j, (kind, _label) in enumerate(KINDS):
            paint(img, gutter + cell_w * j + cell_w // 2, y + cell_h // 2,
                  float(size), st, kind)
        y += cell_h
    return img


def make_lockup(st, r) -> Image.Image:
    """把图标放回真实语境：主图标 + 温度 + 描述 + 四天预报，全用真字号。"""
    W, H = 976, 792
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    x0 = 40

    paint(img, x0 + 124, 210, 248.0, st, "sun_cloud")
    temp_f = r.f(124, bold=True)
    desc_f = r.f(44)
    lab_f = r.f(30)
    tx = x0 + 268 + 22
    r_on(img, r, (tx, 210), "30°", 124, INK, "lm", bold=True)
    r_on(img, r, (tx, 210 + r.ascent(temp_f) // 2 + 26), "多云转小雨", 44, INK, "lm")
    r_on(img, r, (tx, 210 + r.ascent(temp_f) // 2 + 26 + r.lh(desc_f) + 6),
         "东南风 3 级 · 湿度 71% · 体感 32°", 30, GRAY, "lm")

    d.line([(x0, 430), (W - x0, 430)], fill=GRAY_LIGHT, width=2)
    r_on(img, r, (x0, 466), "未来四天", 35, GRAY, "lm")

    days = [("今天", "sun_cloud", "30°", "23°"), ("明天", "rain", "31°", "24°"),
            ("后天", "cloud", "28°", "22°"), ("周日", "sun", "27°", "21°")]
    strip_top, cell = 540, (W - 2 * x0) // len(days)
    for i, (lab, kind, hi, lo) in enumerate(days):
        cx = x0 + cell * i + cell // 2 - 30
        r_on(img, r, (cx, strip_top), lab, 30, GRAY, "lm")
        paint(img, cx + 64, strip_top + 70 + 64, 128.0, st, kind)
        r_on(img, r, (cx, strip_top + 70 + 128 + 24), f"{hi} / {lo}", 30, INK, "lm")
    return img


_R: Renderer | None = None


def r_on(img, r: Renderer, xy, s: str, size: float, fill, anchor,
         bold: bool = False) -> None:
    """借 Renderer 的字体和 text()，但画到给定的画布上。"""
    saved = (r.img, r.d)
    r.img, r.d = img, ImageDraw.Draw(img)
    try:
        r.text(xy, s, r.f(size, bold=bold), fill, anchor=anchor, strong=bold)
    finally:
        r.img, r.d = saved


def b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>天气图标选型 · 六套对照</title>
<style>
 body{margin:0;background:#eceef0;color:#1c1f23;
      font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
 .wrap{max-width:1620px;margin:0 auto;padding:26px 22px 80px}
 h1{font-size:23px;margin:0 0 6px}
 .lead{color:#4b5460;max-width:66em}
 .lead b{color:#1c1f23}
 section{background:#fff;border:1px solid #d6d9dd;border-radius:12px;
         padding:18px 20px;margin:18px 0}
 section>h2{font-size:17px;margin:0 0 4px}
 .sub{color:#5b6470;font-size:13.5px;margin:0 0 12px}
 img{display:block;max-width:100%;height:auto}
 .card{border:1px solid #dfe2e6;border-radius:12px;padding:16px 18px;margin:16px 0;
       background:#fff}
 .card h3{margin:0;font-size:18px;display:flex;gap:10px;align-items:baseline;
          flex-wrap:wrap}
 .badge{display:inline-block;min-width:26px;text-align:center;background:#1c1f23;
        color:#fff;border-radius:6px;font-size:14px;padding:1px 8px;font-weight:700}
 .tag{color:#5b6470;font-size:13.5px;font-weight:400}
 .note{margin:6px 0 12px;color:#3c434b;font-size:13.5px;
       border-left:3px solid #e3e5e8;padding-left:10px}
 .pick{margin-top:12px;font-size:14px;background:#f6f7f8;border:1px dashed #cfd3d8;
       border-radius:8px;padding:8px 12px}
 code{background:#f2f3f5;border-radius:4px;padding:1px 5px;
      font:13px ui-monospace,Menlo,Consolas,monospace}
</style></head><body><div class="wrap">
<h1>天气图标 · 六套候选</h1>
<p class="lead">
下面每一套都是<b>完整的一套</b>（11 种天气），全部用真渲染管线画在
<b>Kindle 的真实像素</b>上，页面里统一缩到 50% 显示 ——
所以你在页面上看到的物理大小，就是它在那块墨水屏上的物理大小，
描边粗细和灰阶也都是真值，不是示意图。<br>
每套下面给了三档尺寸：<b>248px</b> 是天气卡里的主图标，
<b>128px</b> 是未来四天预报里的那一排，<b>80px</b> 是再往下压一档试探极限
（现在用不到，但能看出哪套经不起缩小）。<br>
<b>怎么挑：</b>先看顶上那张六套横向对照定个大方向，再往下翻到那套的
「放回真实语境」看它和温度、预报排在一起的样子。
挑中了跟我说字母（A~F）就行。
</p>

<section>
  <h2>① 六套横向对照</h2>
  <p class="sub">行 = 风格，列 = 最常用的五种天气，全部 248px（主图标尺寸）</p>
  <img src="data:image/png;base64,__MATRIX__" style="width:__MW__px" alt="六套对照">
</section>

__CARDS__

<section>
  <h2>挑好了怎么告诉我</h2>
  <p class="sub">
  直接说一句「用 X」就行（X 是字母）。也可以混搭，比如
  <code>云用 D、太阳用 A 的光芒、雨点用 C 的水滴</code> ——
  这六套是同一套代码按参数生成的，混搭不用重写。<br>
  如果整套都不满意，告诉我具体是哪里不对（云太扁 / 笔画太细 / 雨点太密 /
  太阳光芒太扎眼…），我按你说的方向再出六套。
  </p>
</section>
</div></body></html>
"""

CARD = """<div class="card">
  <h3><span class="badge">{key}</span>{name}
      <span class="tag">{tagline}</span></h3>
  <div class="note">{note}</div>
  <img src="data:image/png;base64,{sheet}" style="width:{sw}px" alt="{name} 全家福">
  <p class="sub" style="margin:14px 0 6px">放回真实语境（主图标 + 温度 + 四天预报）</p>
  <img src="data:image/png;base64,{lockup}" style="width:{lw}px" alt="{name} 语境">
  <div class="pick">要这套就说：<b>用 {key}</b></div>
</div>
"""


def main() -> int:
    global _R
    cfg = Config.load(str(ROOT / "dashboard" / "config.yaml"))
    r = Renderer(cfg, {"generated_at": None})
    _R = r

    matrix = make_matrix(r)
    cards = []
    for st in STYLES:
        sheet, lockup = make_sheet(st), make_lockup(st, r)
        cards.append(CARD.format(key=st["key"], name=st["name"],
                                 tagline=st["tagline"], note=st["note"],
                                 sheet=b64(sheet), sw=sheet.width // 2,
                                 lockup=b64(lockup), lw=lockup.width // 2))
    html = (HTML.replace("__MATRIX__", b64(matrix))
                .replace("__MW__", str(matrix.width // 2))
                .replace("__CARDS__", "\n".join(cards)))

    out_dir = ROOT / ".workbuddy" / "_studio"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "icon_sets.html"
    out.write_text(html, encoding="utf-8", newline="\n")
    print(f"已写出：{out}")
    print("双击打开它，挑一套告诉我字母（A~F）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
