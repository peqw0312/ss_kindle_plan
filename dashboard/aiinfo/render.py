"""墨水屏排版渲染。

排版原则（都是踩过坑的）：
1. **不要灰底文字**。16 级灰度的水墨屏上，浅灰小字会糊成一团。正文只用 0 / 70 / 130
   三档墨色，靠字号和字重拉开层次。
2. **不要渐变、不要阴影、不要细线**。1px 细线在水墨屏上会时隐时现，分隔线至少 3px。
3. **字号要够大**。300ppi 下 30px 字才相当于 7pt，是最小可读字号，正文从 30px 起步。
   这块屏主要是给家里老人扫一眼的，宁可比"精致"更大一号。
4. **几何必须靠测量算出来**，不能手写偏移量。所有纵向位置一律由 font.getmetrics()
   推导；数字这种没有下伸部的字形要按**实际墨迹**居中，不能按字体行盒定位
   （"大字日期偏上"就是这么来的）。
5. **横向留白不省**。左右各留 48px，宁可少放一条内容，也不要挤满。

版面结构（自上而下）：撕页日历条 → 天气卡片 → 行情 → 页脚。
富余的高度不堆在底部留白，而是均摊成三块的内边距，看起来才像"设计过的"。
"""

from __future__ import annotations

import math
import re
from datetime import datetime

from PIL import Image, ImageDraw

from .fonts import book_for
from .sources import clean_text, crypto_source_label, effective_wind_level

# 墨色梯度：只保留这几档，保证在 16 级灰度屏上有足够反差
INK = 0
INK_SOFT = 70
GRAY = 130
GRAY_LIGHT = 195

# 基准字号（按 1072px 宽设计，其他机型按比例缩放）
#
# 字号是"预算"而不是"审美"：日历条 + 天气 + 行情三块要正好吃掉一屏。
# 动任何一个数字前先跑 dashboard/tools/layout_check.py 看总高。
FS_CAL_MONTH = 34
FS_CAL_DAY = 184
FS_CAL_WEEK = 40
FS_LUNAR = 62
FS_CAL_LINE = 32
FS_CLOCK = 76
FS_BADGE = 36
FS_YIJI_MARK = 32
FS_YIJI = 32
FS_TEMP = 124
FS_DESC = 44
FS_LABEL = 30
FS_VALUE = 44
FS_SECTION = 35
FS_ITEM_CHIP = 26
FS_ITEM_TITLE = 34
FS_ITEM_SUM = 28
FS_QUOTE_NAME = 30
FS_QUOTE_PRICE = 52
FS_QUOTE_PCT = 34
FS_FOOT = 18

#: 左侧撕页方块的宽度。再宽就挤压右边的时钟与农历了
CAL_BOX_W = 256

#: 日历纸内部留白（不含均摊来的富余高度）
CAL_DAY_PAD_TOP = 12
CAL_WEEK_GAP = 10
CAL_DAY_PAD_BOTTOM = 24

#: 版面顶部留白，以及日历纸上缘距日历区块顶部的空隙。
#: 单独立成常量是因为 clock_region() 要按它们算时钟的绝对 y ——
#: 手抄一份数字迟早会和 draw_calendar() 对不上。
TOP_GAP = 10
CAL_BOX_TOP_GAP = 8

#: 时钟区向外多留几像素：精灵图的纯白底要能盖住文字边缘的抗锯齿
CLOCK_PAD = 6

#: 行情单元格内三段（名称 / 价格 / 涨跌）的间距
QUOTE_GAP_NAME_PRICE = 10
QUOTE_GAP_PRICE_PCT = 8

#: 天气卡片左右分界（占内区宽度的比例）。
#: 右半的数据格要放得下「空气 轻度 118」（220px），左半要放得下「图标 + 温度」
#: （348px），0.46 是两边都不吃亏的位置。layout_check.py 会按这个值验算。
WEATHER_SPLIT = 0.46

DEVICE_LABELS = {
    "kindle_pw1": "Paperwhite 1",
    "kindle_pw2": "Paperwhite 2",
    "kindle_pw3": "Paperwhite 3",
    "kindle_pw4": "Paperwhite 4",
    "kindle_pw5": "Paperwhite 5",
    "kindle_basic": "Kindle 基础版",
    "kindle_10": "Kindle 10",
    "kindle_11": "Kindle 11",
    "kindle_oasis2": "Oasis 2",
    "kindle_oasis3": "Oasis 3",
    "kindle_scribe": "Scribe",
    "kobo_clara": "Kobo Clara",
}

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’._:/%+\-]*")

#: 允许"悬挂"在行尾的标点。中文排版里 。，）这类收尾标点不该被挤到下一行，
#: 否则会出现一整行只有一个句号的难看情况（实测摘要经常会撞上）。
#: 开括号（《「【）故意不在此列——它们要留在行首，被挤下去才是对的。
_HANG_PUNCT = set("。，、；：？！）〕〗》」』】”’…·%")


def tokenize(text: str) -> list[tuple[str, bool]]:
    """切成排版单元：连续英文/数字算一个词，CJK 逐字切。

    返回 (单元, 是否需要在前面补空格)。
    """
    tokens: list[tuple[str, bool]] = []
    pending_space = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            pending_space = True
            i += 1
            continue
        m = _WORD_RE.match(text, i)
        if m and m.start() == i:
            tokens.append((m.group(0), pending_space and bool(tokens)))
            i = m.end()
        else:
            tokens.append((ch, False))
            i += 1
        pending_space = False
    return tokens


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int,
              max_lines: int = 99) -> list[str]:
    """按宽度折行；超出 max_lines 时在末行补省略号。"""
    text = clean_text(text)
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for token, need_space in tokenize(text):
        candidate = current + (" " if need_space and current else "") + token
        if not current or draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        elif token in _HANG_PUNCT:
            # 标点悬挂：让它在行尾探出去一点，比留一整行标点干净得多
            current = candidate
        else:
            lines.append(current)
            current = token
            if len(lines) >= max_lines:
                current = ""
                break
    if current and len(lines) < max_lines:
        lines.append(current)

    # 判断是否真的还有内容没放下，是的话末行截断加省略号
    if len(lines) >= max_lines:
        placed = len("".join(lines))
        if placed < len(text.replace(" ", "")):
            last = lines[-1]
            while last and draw.textlength(last + "…", font=font) > max_width:
                last = last[:-1]
            lines[-1] = last.rstrip() + "…"
    return lines


class Renderer:
    def __init__(self, cfg, data: dict):
        self.cfg = cfg
        self.data = data or {}
        self.w, self.h = cfg.size
        self.s = cfg.scale
        self.margin = int(round(int(cfg.get("device.margin", 48)) * self.s))
        # 字体必须和生成时钟精灵图的那套一致（font.regular/font.bold 可覆盖），
        # 否则本机 YaHei / 云端 Noto 的行高差 10px，留白区会和精灵图对不上。
        self.fonts = book_for(cfg)
        self.img = Image.new("L", (self.w, self.h), 255)
        self.d = ImageDraw.Draw(self.img)
        self.avail_w = self.w - self.margin * 2
        self.generated_at: datetime = self.data.get("generated_at") or datetime.now()
        self._notes: list[str] = []
        # 富余高度均摊出来的额外内边距，由 render() 在绘制前算好
        self._cal_extra = 0
        self._weather_extra = 0
        self._quotes_extra = 0
        # 时钟在成品图里的绝对矩形，由 render() 填 —— 生成精灵图的工具靠它取坐标
        self.clock_box: tuple[int, int, int, int] | None = None

    # ---- 基础绘制 ------------------------------------------------------

    def f(self, size: float, bold: bool = False):
        return self.fonts.get(int(round(size * self.s)), bold)

    def px(self, value: float) -> int:
        return int(round(value * self.s))

    @staticmethod
    def metrics(font) -> tuple[int, int]:
        return font.getmetrics()

    def lh(self, font) -> int:
        ascent, descent = font.getmetrics()
        return ascent + descent

    def ascent(self, font) -> int:
        return font.getmetrics()[0]

    def tw(self, s: str, font) -> int:
        return int(self.d.textlength(s, font=font)) if s else 0

    def _stroke(self, strong: bool) -> int:
        """没有独立粗体字重时用描边模拟——测量也得把这圈描边算进去。"""
        return max(1, self.px(1)) if (strong and not self.fonts.bold_is_real) else 0

    def text(self, xy, s, font, fill=INK, anchor=None, strong: bool = False):
        if not s:
            return
        kw = {}
        stroke = self._stroke(strong)
        if stroke:
            kw = {"stroke_width": stroke, "stroke_fill": fill}
        self.d.text(xy, s, font=font, fill=fill, anchor=anchor, **kw)

    def text_centered_ink(self, cx: float, top: int, bottom: int, s: str, font,
                          fill=INK, strong: bool = True) -> None:
        """按**实际墨迹**在 [top, bottom] 区间里垂直居中。

        数字没有下伸部，用字体行盒（ascent/descent）定位必然偏高——这正是
        上一版大字日期「18 偏上」的根因。这里先量出墨迹的真实高度再定位，
        以后怎么调字号都不会偏。
        """
        if not s:
            return
        stroke = self._stroke(strong)
        box = self.d.textbbox((cx, 0), s, font=font, anchor="mt", stroke_width=stroke)
        ink_h = box[3] - box[1]
        y = top + (bottom - top - ink_h) / 2 - box[1]
        self.text((cx, y), s, font, fill, anchor="mt", strong=strong)

    def clip_text(self, s: str, font, max_w: int) -> str:
        """按像素宽度裁剪，超出补省略号。比按字数截断准得多。"""
        if not s:
            return ""
        if self.tw(s, font) <= max_w:
            return s
        while s and self.tw(s + "…", font) > max_w:
            s = s[:-1]
        return (s + "…") if s else ""

    def rule(self, y: int, *, thickness: int = 3, color=INK,
             x0: int | None = None, x1: int | None = None) -> None:
        self.d.rectangle(
            [x0 if x0 is not None else self.margin, y,
             x1 if x1 is not None else self.w - self.margin, y + thickness - 1],
            fill=color,
        )

    def section_title(self, y: int, title: str, *, right_note: str = "") -> int:
        """报纸式区块标题：粗体字 + 下方通栏粗线。返回内容区起始 y。"""
        font = self.f(FS_SECTION, bold=True)
        self.text((self.margin, y), title, font, INK, strong=True)
        if right_note:
            self.text((self.w - self.margin, y + self.px(10)), right_note,
                      self.f(FS_LABEL), GRAY, anchor="rt")
        bottom = y + self.lh(font) + self.px(8)
        self.rule(bottom, thickness=self.px(3))
        return bottom + self.px(15)

    # ---- 时钟 ----------------------------------------------------------

    def clock_text(self, when: datetime | None = None) -> str:
        """按 clock.style 生成时刻文字。默认「下午 4:34」——对老人最直白。"""
        dt = when or self.generated_at
        style = str(self.cfg.get("clock.style", "cn12") or "cn12").lower()
        if style in ("h24", "24"):
            return dt.strftime("%H:%M")
        hour12 = dt.hour % 12 or 12
        body = f"{hour12}:{dt.minute:02d}"
        if style in ("en12", "en"):
            return f"{body} {'AM' if dt.hour < 12 else 'PM'}"
        return f"{self._period(dt.hour)} {body}"

    @staticmethod
    def _period(hour: int) -> str:
        """中文时段词。比笼统的「上午/下午」更接近日常说法，老人一眼能对上。"""
        if hour < 5:
            return "凌晨"
        if hour < 12:
            return "上午"
        if hour == 12:
            return "中午"
        if hour < 18:
            return "下午"
        return "晚上"

    def clock_probe(self) -> str:
        """最宽的那种时刻。

        时钟区宽度是按文字量出来的，再用它反推农历能占多宽。12 小时制下
        「晚上 11:59」比 24 小时制的「16:34」宽出一大截，**必须按最宽形态预留**，
        否则一到晚上就会压到农历文字上。
        """
        style = str(self.cfg.get("clock.style", "cn12") or "cn12").lower()
        if style in ("h24", "24"):
            return "16:34"
        if style in ("en12", "en"):
            return "11:59 PM"
        return "晚上 11:59"

    # ---- 本机时钟：出图时留白，Kindle 自己贴图 --------------------------

    def clock_in_image(self) -> bool:
        """时钟画不画进图里。

        `clock.mode: local` 时把这块留白，由 Kindle 用系统时间贴精灵图。
        好处是**图不必为了"时间准"而反复重算**：数据一天只更新几次，
        但屏幕上的时间照样每分钟都是对的。
        """
        mode = str(self.cfg.get("clock.mode", "image") or "image").lower()
        return mode != "local"

    def clock_region(self, cal=None) -> tuple[int, int, int, int]:
        """时钟在成品图里的绝对像素矩形 (左, 上, 右, 下)。

        这是**出图、生成精灵图、Kindle 贴图三方共用的唯一基准**：
        图里这块留白，精灵图的画布就是这个尺寸，`eips -x/-y` 也照这个坐标贴。
        三者用的是同一个函数，所以不可能对不上；改了字号不重新生成精灵图才会。

        注意高度取 `r1`（农历行与时钟行的较大行高）而不是墨迹高度 ——
        要覆盖所有时刻里最高的那种字形，不能只按当前这一刻算。
        """
        g = self.calendar_geometry(cal if cal is not None else self.calendar_for())
        box_top = self.px(TOP_GAP) + self.px(CAL_BOX_TOP_GAP)
        x1 = self.w - self.margin
        return (x1 - g["clock_w"] - self.px(CLOCK_PAD),
                box_top,
                x1 + self.px(CLOCK_PAD),
                box_top + g["r1"])

    def _draw_clock(self, label: str, box_top: int, ox: int = 0, oy: int = 0) -> None:
        """画时刻文字。右对齐锚点（用左对齐锚点会从右边界往外画，被画布裁掉）。

        ox/oy 是把同一段文字画到精灵图小画布上时的整体平移量 ——
        有了它，精灵图和主图是**同一行代码**画出来的，连描边宽度都一致。
        """
        font = self.f(FS_CLOCK, bold=True)
        self.text((self.w - self.margin - ox,
                   box_top + self.ascent(font) + self.px(4) - oy),
                  label, font, INK, anchor="rs", strong=True)

    def clock_sprite(self, label: str) -> Image.Image:
        """把一段时刻文字单独画到「刚好等于时钟区」的画布上。

        排版位置和主图里一模一样（见 _draw_clock 的 ox/oy），
        所以 Kindle 把它贴回去之后，接缝看不出来。
        """
        left, top, right, bottom = self.clock_region()
        canvas = Image.new("L", (right - left, bottom - top), 255)
        saved = (self.img, self.d)
        self.img, self.d = canvas, ImageDraw.Draw(canvas)
        try:
            self._draw_clock(label, top, ox=left, oy=top)
        finally:
            self.img, self.d = saved
        return canvas

    # ---- 天气图标（纯几何绘制，不依赖字体里的符号）----------------------

    def icon(self, cx: float, cy: float, size: float, kind: str, fill=INK) -> None:
        """一个图标最多一个附加符号。

        小尺寸下符号越多越糊，"晴间多云还带五条短射线"缩到 34px 就是一团黑点。
        所以只保留 6 种画法：晴 / 多云 / 阴 / 雨 / 雪 / 雷，认不出的一律按阴画。
        """
        r = size / 2.0
        lw = max(2, int(round(size * 0.10)))
        kind = str(kind or "cloud").lower()

        def cloud(ox: float, oy: float, scale: float) -> None:
            a = size * scale
            self.d.ellipse([ox - a * 0.50, oy - a * 0.30, ox + a * 0.10, oy + a * 0.28], fill=fill)
            self.d.ellipse([ox - a * 0.14, oy - a * 0.52, ox + a * 0.44, oy + a * 0.20], fill=fill)
            self.d.ellipse([ox + a * 0.12, oy - a * 0.22, ox + a * 0.62, oy + a * 0.28], fill=fill)
            self.d.rectangle([ox - a * 0.48, oy + a * 0.02, ox + a * 0.60, oy + a * 0.27], fill=fill)

        if kind == "sun":
            sun_r = r * 0.46
            self.d.ellipse([cx - sun_r, cy - sun_r, cx + sun_r, cy + sun_r], fill=fill)
            for i in range(8):
                ang = math.radians(i * 45)
                self.d.line([cx + math.cos(ang) * r * 0.68, cy + math.sin(ang) * r * 0.68,
                             cx + math.cos(ang) * r * 0.98, cy + math.sin(ang) * r * 0.98],
                            fill=fill, width=lw)
        elif kind == "sun_cloud":
            # 太阳露一角、云压住左下——比"太阳 + 短射线"好认得多
            sun_r = r * 0.34
            sx, sy = cx - r * 0.30, cy - r * 0.32
            self.d.ellipse([sx - sun_r, sy - sun_r, sx + sun_r, sy + sun_r], fill=fill)
            cloud(cx + r * 0.18, cy + r * 0.26, 0.94)
        elif kind == "cloud":
            cloud(cx, cy, 1.0)
        elif kind in ("rain", "rain_heavy", "shower", "drizzle"):
            cloud(cx, cy - r * 0.30, 0.98)
            for i in range(3):
                x = cx - r * 0.40 + i * (r * 0.40)
                self.d.line([x, cy + r * 0.20, x, cy + r * 0.84], fill=fill, width=lw)
        elif kind == "snow":
            cloud(cx, cy - r * 0.30, 0.98)
            dot = lw
            for i in range(3):
                x = cx - r * 0.40 + i * (r * 0.40)
                self.d.ellipse([x - dot, cy + r * 0.52 - dot, x + dot, cy + r * 0.52 + dot],
                               fill=fill)
        elif kind == "thunder":
            cloud(cx, cy - r * 0.34, 0.98)
            self.d.polygon([
                (cx + r * 0.06, cy + r * 0.06), (cx - r * 0.34, cy + r * 0.60),
                (cx - r * 0.02, cy + r * 0.60), (cx - r * 0.24, cy + r * 1.02),
                (cx + r * 0.36, cy + r * 0.40), (cx + r * 0.04, cy + r * 0.40),
                (cx + r * 0.34, cy + r * 0.06),
            ], fill=fill)
        else:                                                   # fog / 认不出来
            for i, dy in enumerate((-0.44, 0.0, 0.44)):
                half = r * (0.98 if i != 1 else 0.70)
                self.d.line([cx - half, cy + r * dy, cx + half, cy + r * dy],
                            fill=fill, width=lw)

    # =====================================================================
    #  顶部：撕页日历条（农历 / 节气 / 节日 / 宜忌）+ 时钟
    # =====================================================================

    def calendar_geometry(self, cal) -> dict:
        """撕页日历条的全部纵向位置。测量与绘制共用这一份几何。

        版式照传统「一天撕一张」的日历页做：左侧是一张日历纸（月份 + 大字日期 +
        星期），右侧是农历、干支、地点、时钟，底部压一条宜忌。

        左右两列的高度必须咬合：左列由大字日期撑开，右列由「农历 + 干支 + 徽章」
        撑开，box_h 取两者较大值；谁矮就把差值补进自己的上下留白，
        否则空出来的那一截会让大字日期看着贴顶。
        """
        gap = self.px(28)
        box_w = self.px(CAL_BOX_W)
        right_x = self.margin + box_w + gap
        right_w = self.w - self.margin - right_x

        bar_h = self.px(58)
        day_font = self.f(FS_CAL_DAY, bold=True)
        week_font = self.f(FS_CAL_WEEK)

        extra_top = self._cal_extra // 2
        extra_bottom = self._cal_extra - extra_top
        pad_top = self.px(CAL_DAY_PAD_TOP) + extra_top
        pad_bottom = self.px(CAL_DAY_PAD_BOTTOM) + extra_bottom
        left_h = (bar_h + pad_top + self.lh(day_font) + self.px(CAL_WEEK_GAP)
                  + self.lh(week_font) + pad_bottom)

        clock_font = self.f(FS_CLOCK, bold=True)
        clock_w = self.tw(self.clock_probe(), clock_font)

        lunar_font = self.f(FS_LUNAR, bold=True)
        r1 = max(self.lh(lunar_font), self.lh(clock_font))
        r2 = self.lh(self.f(FS_CAL_LINE))
        badge_h = max(self.px(56), self.lh(self.f(FS_BADGE, bold=True)) + self.px(20))
        # 右列三行之间的间距。这三个数不只是"好看"——富余高度会优先吃掉它们，
        # 否则文字全挤在上半截、徽章孤零零贴在底部，中间空一大块。
        g1, g2, g3 = self.px(18), self.px(10), self.px(22)
        content_h = r1 + g1 + r2 + g2 + r2 + g3 + badge_h

        box_h = max(left_h, content_h)
        if box_h > left_h:                                       # 右列更高，差值补给上下留白
            diff = box_h - left_h
            pad_top += diff // 2
            pad_bottom += diff - diff // 2
        if box_h > content_h:                                    # 左列更高，差值摊进行距
            spread = box_h - content_h
            g1 += spread // 3
            g2 += spread // 3
            g3 += spread - (spread // 3) * 2

        # 第一行的横向预算：时钟靠右占多少，剩下的才归农历
        text_w = max(self.px(180), right_w - clock_w - self.px(28))

        strip_h = self.px(3) + self.px(18) + self.lh(self.f(FS_YIJI)) + self.px(18)
        # 日历纸和宜忌条之间的那道空隙也算进总高，否则 measure 和 draw 会差一截
        band_h = self.px(8) + box_h + self.px(20) + strip_h
        return {
            "box_w": box_w, "box_h": box_h, "right_x": right_x, "right_w": right_w,
            "text_w": text_w, "bar_h": bar_h, "pad_top": pad_top,
            "pad_bottom": pad_bottom, "left_h": left_h,
            "clock_font": clock_font, "clock_w": clock_w, "day_font": day_font,
            "week_font": week_font, "lunar_font": lunar_font,
            "r1": r1, "r2": r2, "g1": g1, "g2": g2, "g3": g3, "badge_h": badge_h,
            "strip_h": strip_h, "band_h": band_h,
        }

    def calendar_height(self, cal) -> int:
        if not cal:
            return 0
        return self.calendar_geometry(cal)["band_h"] + self.px(20)

    def draw_calendar(self, top: int, cal) -> int:
        if not cal:
            return top
        g = self.calendar_geometry(cal)
        x0, x1 = self.margin, self.w - self.margin
        box_top = top + self.px(8)
        box_bottom = box_top + g["box_h"]
        box_right = self.margin + g["box_w"]
        radius = self.px(14)

        # ---------------- 左：一张日历纸 ----------------
        self.d.rounded_rectangle([x0, box_top, box_right, box_bottom], radius=radius,
                                 fill=250, outline=INK, width=max(2, self.px(4)))
        # 顶部年月反白条（先把整条填黑，再把下缘补成直角，避免出现圆角缺口）
        self.d.rounded_rectangle([x0, box_top, box_right, box_top + g["bar_h"]],
                                 radius=radius, fill=INK)
        self.d.rectangle([x0, box_top + g["bar_h"] - radius,
                          box_right, box_top + g["bar_h"]], fill=INK)
        self.text(((x0 + box_right) / 2, box_top + g["bar_h"] / 2),
                  f"{cal.solar_year}年{cal.solar_month}月", self.f(FS_CAL_MONTH, bold=True),
                  255, anchor="mm", strong=True)

        # 大字日期：撕页日历的视觉主角。按墨迹居中，别按行盒——数字没有下伸部
        day_top = box_top + g["bar_h"] + g["pad_top"]
        day_bottom = day_top + self.lh(g["day_font"])
        self.text_centered_ink((x0 + box_right) / 2, day_top, day_bottom,
                               str(cal.solar_day), g["day_font"], INK, strong=True)
        week_y = day_bottom + self.px(CAL_WEEK_GAP)
        self.text(((x0 + box_right) / 2, week_y), cal.weekday, g["week_font"], GRAY,
                  anchor="mt")

        # ---------------- 右：农历 / 干支 / 地点 / 时钟 ----------------
        y = box_top
        # 农历文字宁可缩一号也不压到时钟上（赶上闰月会多出一个字）
        lunar_font = g["lunar_font"]
        if self.tw(cal.lunar_text, lunar_font) > g["text_w"]:
            size = FS_LUNAR
            while size > FS_LUNAR * 0.5 and self.tw(
                    cal.lunar_text, self.f(size, bold=True)) > g["text_w"]:
                size -= 2
            lunar_font = self.f(size, bold=True)
        self.text((g["right_x"], y), cal.lunar_text, lunar_font, INK, anchor="lt",
                  strong=True)

        # 时钟：本机时钟模式下这块整片留白，由 Kindle 用系统时间贴精灵图。
        # 留白不影响排版——clock_w 照样参与横向预算，农历该让的位置还让。
        if self.clock_in_image():
            self._draw_clock(self.clock_text(), box_top)

        line_font = self.f(FS_CAL_LINE)
        y += g["r1"] + g["g1"]
        if self.cfg.get("calendar.show_ganzhi", True):
            gan_line = (f"{cal.ganzhi_year}年 属{cal.shengxiao} · "
                        f"{cal.ganzhi_month}月 {cal.ganzhi_day}日")
            if cal.lunar_size:
                gan_line += f" · {cal.lunar_size}月"
        else:
            gan_line = ""
        if gan_line:
            self.text((g["right_x"], y), self.clip_text(gan_line, line_font, g["right_w"]),
                      line_font, INK_SOFT, anchor="lt")
        y += g["r2"] + g["g2"]

        # 第二行只放地点：星期已经在左边的日历纸上了，重复一遍纯属浪费一行
        place = clean_text(self.cfg.get("location.name", ""), 20)
        self.text((g["right_x"], y), place or cal.weekday, line_font, GRAY, anchor="lt")

        # ---------------- 底部徽章：节日 > 交节日 > 近期节日倒计时 ----------------
        # 贴日历纸底边对齐，两列的底缘才是齐的
        badge_top = box_bottom - g["badge_h"]
        badge_h = g["badge_h"]
        badge_font = self.f(FS_BADGE, bold=True)
        term_font = line_font
        if cal.badge:
            filled = cal.badge_kind == "festival"
            bw = self.tw(cal.badge, badge_font) + self.px(40)
            if filled:
                self.d.rounded_rectangle([g["right_x"], badge_top,
                                          g["right_x"] + bw, badge_top + badge_h],
                                         radius=self.px(8), fill=INK)
                self.text((g["right_x"] + bw / 2, badge_top + badge_h / 2), cal.badge,
                          badge_font, 255, anchor="mm", strong=True)
            else:
                self.d.rounded_rectangle([g["right_x"], badge_top,
                                          g["right_x"] + bw, badge_top + badge_h],
                                         radius=self.px(8), fill=250, outline=INK,
                                         width=max(2, self.px(3)))
                self.text((g["right_x"] + bw / 2, badge_top + badge_h / 2), cal.badge,
                          badge_font, INK, anchor="mm", strong=True)
            note_x = g["right_x"] + bw + self.px(20)
        else:
            note_x = g["right_x"]

        note = self.calendar_note(cal)
        if not cal.badge:
            self.d.rounded_rectangle(
                [g["right_x"], badge_top, x1, badge_top + badge_h],
                radius=self.px(8), fill=248, outline=GRAY_LIGHT, width=max(1, self.px(2)))
        note = self.clip_text(note, term_font, x1 - note_x)
        self.text((note_x, badge_top + badge_h / 2), note, term_font,
                  INK if self.weather_warning() else INK_SOFT, anchor="lm")

        # ---------------- 底：宜 / 忌 / 冲煞 ----------------
        strip_top = box_bottom + self.px(20)
        self.rule(strip_top, thickness=self.px(3))
        y = strip_top + self.px(18)
        mark_font = self.f(FS_YIJI_MARK, bold=True)
        body_font = self.f(FS_YIJI)
        box = self.px(46)
        baseline = y + self.ascent(body_font)
        right_text = f"{cal.chong} {cal.sha} · {cal.zhiri}日"
        right_w = self.tw(right_text, body_font)
        limit = x1 - right_w - self.px(28)
        x = x0
        if self.cfg.get("calendar.show_yiji", True):
            for mark, items, filled in (("宜", cal.yi, True), ("忌", cal.ji, False)):
                if x + box > limit:
                    break
                if filled:
                    self.d.rounded_rectangle([x, y, x + box, y + box],
                                             radius=self.px(5), fill=INK)
                    self.text((x + box / 2, y + box / 2), mark, mark_font, 255,
                              anchor="mm", strong=True)
                else:
                    self.d.rounded_rectangle([x, y, x + box, y + box],
                                             radius=self.px(5), outline=INK,
                                             width=max(1, self.px(2)))
                    self.text((x + box / 2, y + box / 2), mark, mark_font, INK,
                              anchor="mm", strong=True)
                x += box + self.px(12)
                text = " ".join(items)
                while text and x + self.tw(text, body_font) > limit:
                    parts = text.split(" ")
                    if len(parts) <= 1:
                        text = text[:-1]
                        break
                    text = " ".join(parts[:-1])
                self.text((x, baseline), text, body_font, INK_SOFT, anchor="ls")
                x += self.tw(text, body_font) + self.px(40)
        self.text((x1, baseline), right_text, body_font, GRAY, anchor="rs")
        return strip_top + g["strip_h"]

    def weather_warning(self) -> list:
        """本轮的气象灾害预警。有预警时日历条上优先显示它，节气进度让位。"""
        if not self.cfg.get("weather.show_warning", True):
            return []
        return (self.data.get("weather") or {}).get("warning") or []

    def calendar_note(self, cal) -> str:
        warning = self.weather_warning()
        if warning:
            note = f"预警 {warning[0].get('title', '')}"
            if len(warning) > 1:
                note += f" 等 {len(warning)} 条"
            return note
        note = f"{cal.term_current} 第 {cal.term_current_days} 天"
        if cal.term_next_days > 0:
            note += f" · 距{cal.term_next} {cal.term_next_days} 天"
        if cal.badge and cal.upcoming:
            note += f" · {cal.upcoming}"
        return note

    # =====================================================================
    #  天气卡片
    # =====================================================================

    def weather_geometry(self, weather: dict) -> dict:
        """把卡片里所有纵向位置一次算清楚，测量和绘制共用同一份几何。

        左半是「主图标 + 温度叠描述」，右半是 2 列 × 3 行的数据格
        （体感 / 湿度 · 降水 / 风力 · 空气 / 紫外），底部是后三天的
        「日期 → 大图标 + 天气文字 → 最高最低」。

        这里有两个尺寸是被"最长内容"倒逼出来的，改之前先量：

        * 数据格必须是 **2 列**。3 列时每格只有 161px，而「空气 轻度 118」
          就要 220px——旧版的「轻度污染 118」更是要 308px，直接糊到隔壁格子上。
        * 温度和一整句描述**不能并排**。图标 124 + 「29°」203 + 「多云转小雨」220
          一共 585px，而左半区最多只有 445px，并排必然压到右边的数据格。
          改成上下两行后，横向只受"较宽的那个"约束，怎么都不会越界。
        """
        pad = self.px(24) + self._weather_extra // 2
        temp_font = self.f(FS_TEMP, bold=True)
        desc_font = self.f(FS_DESC)
        label_font = self.f(FS_LABEL)
        value_font = self.f(FS_VALUE)
        icon_size = self.px(124)
        cols, rows = 2, 3

        left_h = self.lh(temp_font) + self.px(6) + self.lh(desc_font)
        # 标签和数值共用一条基线，格高只取决于两者中较高的那个
        cell_h = max(self.lh(label_font), self.lh(value_font)) + self.px(22)
        grid_h = rows * cell_h
        body_h = max(left_h, grid_h)

        day_icon = self.px(64)
        strip_h = (self.lh(label_font) + self.px(14)
                   + max(day_icon, self.lh(label_font)) + self.px(12)
                   + self.lh(value_font) + self.px(4))

        height = pad * 2 + body_h + self.px(18) + strip_h
        return {
            "pad": pad,
            "height": height,
            "body_top": pad,
            "body_h": body_h,
            "cell_h": cell_h,
            "cols": cols,
            "rows": rows,
            "left_h": left_h,
            "icon_size": icon_size,
            "day_icon": day_icon,
            "strip_top": pad + body_h + self.px(18),
            "strip_h": strip_h,
            "fonts": (temp_font, desc_font, label_font, value_font),
        }

    def weather_height(self, weather: dict) -> int:
        if not weather:
            return 0
        return self.weather_geometry(weather)["height"] + self.px(20)

    def draw_weather(self, top: int, weather: dict) -> int:
        if not weather:
            return top
        g = self.weather_geometry(weather)
        temp_font, desc_font, label_font, value_font = g["fonts"]
        pad = g["pad"]
        box_bottom = top + g["height"]

        self.d.rounded_rectangle(
            [self.margin, top, self.w - self.margin, box_bottom],
            radius=self.px(14), fill=250, outline=GRAY_LIGHT, width=max(1, self.px(2)),
        )

        # 左右分界：右边数据格要能容下「空气 轻度 118」（220px），
        # 左边也要够放「图标 + 温度」（348px）。0.46 是两边都不吃亏的位置。
        right_x = self.margin + pad + (self.avail_w - pad * 2) * WEATHER_SPLIT
        right_w = self.w - self.margin - pad - right_x

        # --- 左半：图标 + （温度 / 描述上下两行）---
        # 描述压在温度下面，不是并排：并排时「29°」+「多云转小雨」比左半区还宽，
        # 会直接压到右边的数据格上（旧版实测溢出 140px）。
        inner_x = self.margin + pad
        body_top = top + g["body_top"]
        icon_size = g["icon_size"]
        self.icon(inner_x + icon_size / 2, body_top + g["body_h"] / 2, icon_size,
                  weather.get("icon", "cloud"))

        text_x = inner_x + icon_size + self.px(22)
        left_w = right_x - (self.margin + pad)
        text_w = max(self.px(120), left_w - icon_size - self.px(34))
        temp_txt = f"{weather.get('temp')}°" if weather.get("temp") is not None else "--°"
        desc_txt = self.clip_text(clean_text(weather.get("desc", ""), 8), desc_font, text_w)
        group_h = self.lh(temp_font) + self.px(6) + self.lh(desc_font)
        ty = body_top + (g["body_h"] - group_h) / 2
        self.text((text_x, ty), temp_txt, temp_font, INK, anchor="lt", strong=True)
        self.text((text_x, ty + self.lh(temp_font) + self.px(6)), desc_txt, desc_font,
                  GRAY, anchor="lt")

        # --- 右半：2 列 × 3 行数据格，每格「标签 + 数值( + 小字补充)」一行 ---
        grid = self.weather_grid(weather)
        cols = g["cols"]
        cell_w = right_w / cols
        gap = self.px(12)
        # 列内标签宽度取齐，数值的左边缘就自然对齐成一列——比逐个自适应整齐得多。
        # 「东南风」这种三字标签单独决定它那一列的起点，所以它放在第二列，
        # 第一列留给「体感 / 降水 / 空气」这些两字标签，「空气 轻度 118」才放得下。
        label_w = [max(self.tw(cell[0], label_font) for cell in grid[c::cols])
                   for c in range(cols)]
        for i, (label, value, sub) in enumerate(grid[:cols * g["rows"]]):
            col = i % cols
            gx = right_x + col * cell_w
            gy = top + g["body_top"] + (i // cols) * g["cell_h"]
            base = gy + self.ascent(value_font)
            self.text((gx, base), label, label_font, GRAY, anchor="ls")
            vx = gx + label_w[col] + gap
            # 兜底：万一来了超长的数值（比如四位数的 AQI），宁可缩一号也不许越界。
            # 给后面的小字补充先留出位置，否则数值刚好占满时它会被顶出格子。
            tail = (self.px(8) + self.tw(sub, label_font)) if sub else 0
            vfont = self._fit_font(value, FS_VALUE, gx + cell_w - vx - tail)
            self.text((vx, base), value, vfont, INK, anchor="ls", strong=True)
            if sub:
                self.text((vx + self.tw(value, vfont) + self.px(8), base), sub,
                          label_font, GRAY, anchor="ls")

        # --- 底部：未来三天，每天「日期 → 大图标 + 天气文字 → 最高最低」---
        forecast = weather.get("forecast") or []
        show = forecast[1:4] if len(forecast) > 1 else forecast
        if show:
            strip_y = top + g["strip_top"]
            self.rule(strip_y - self.px(12), thickness=max(1, self.px(2)), color=GRAY_LIGHT,
                      x0=self.margin + pad, x1=self.w - self.margin - pad)
            cell_w = (self.avail_w - pad * 2) / len(show)
            day_icon = g["day_icon"]
            for i, day in enumerate(show):
                cx = self.margin + pad + cell_w * i
                self.text((cx, strip_y), day.get("label", ""), label_font, GRAY)

                icon_cy = strip_y + self.lh(label_font) + self.px(14) + day_icon / 2
                self.icon(cx + day_icon / 2, icon_cy, day_icon, day.get("icon", "cloud"),
                          fill=INK_SOFT)
                # 图标右边补一行天气文字：光靠图形，老人认不出"小雨"和"阵雨"的区别
                desc = clean_text(day.get("desc", ""), 4)
                if desc:
                    self.text((cx + day_icon + self.px(16),
                               icon_cy - self.lh(label_font) / 2),
                              desc, label_font, INK_SOFT)

                vy = (strip_y + self.lh(label_font) + self.px(14) + day_icon
                      + self.px(12) + self.ascent(value_font))
                high = f"{day.get('high')}°"
                self.text((cx, vy), high, value_font, INK, anchor="ls", strong=True)
                self.text((cx + self.tw(high, value_font) + self.px(10), vy),
                          f"{day.get('low')}°", label_font, GRAY, anchor="ls")
        return box_bottom + self.px(20)

    def weather_grid(self, weather: dict) -> list[tuple[str, str, str | None]]:
        """数据格的六格内容，按「行优先」排列：(标签, 数值, 小字补充)。

        单独抽出来是为了让 layout_check.py 能拿**同一份内容**去量宽度，
        而不是在自检脚本里照着抄一遍——抄的那份迟早会和这里对不上，
        然后某天「轻度污染 118」就会悄悄糊到隔壁格子上（已经发生过一次）。

        第 2 列之所以放「风力」，是因为「东南风」是唯一的三个字标签：
        它只抬高自己那一列的数值起点，而第 1 列留给「体感 / 降水 / 空气」
        这些两字标签，「空气 轻度 118」才有地方站。
        """
        return [
            ("体感", self._num(weather.get("feels"), "°"), None),
            ("湿度", self._num(weather.get("humidity"), "%"), None),
            ("降水", self._precip_cell(weather), None),
            self._wind_cell(weather),
            ("空气", *self._air_cell(weather)),
            ("紫外", self._num(weather.get("uv"), ""), None),
        ]

    def _fit_font(self, text: str, size: int, max_w: int, floor: int = 34):
        """数值字体自适应：放不下就降一号，降到 floor 为止。

        墨水屏上「越界压到隔壁格」比「小一号」难看得多，所以宁可缩。
        floor 取 34px：再小就低于这块屏的可读下限了。
        """
        while size > floor and self.tw(text, self.f(size, bold=True)) > max_w:
            size -= 2
        return self.f(size, bold=True)

    @staticmethod
    def _num(value, suffix: str) -> str:
        return f"{value}{suffix}" if value is not None else "--"

    @staticmethod
    def _air_short(level: str) -> str:
        """空气质量等级取短说：「轻度污染」→「轻度」。

        四个字的等级在数据格里要 176px，一格放不下；取前两字信息一点不少，
        「优 / 良 / 轻度 / 中度 / 重度 / 严重」正好六级都区分得开。
        """
        text = clean_text(level or "", 4)
        return text[:-2] if text.endswith("污染") else text

    def _air_cell(self, weather: dict) -> tuple[str, str | None]:
        """空气格：大号等级 + 灰色 AQI 数字，两截信息都留着。"""
        air = weather.get("air") or {}
        level = self._air_short(air.get("level") or "")
        aqi = air.get("aqi")
        if level and aqi is not None:
            return level, str(aqi)
        if aqi is not None:
            return str(aqi), None
        return level or "--", None

    def _precip_cell(self, weather: dict) -> str:
        """降水格：有概率就显示概率，只有降水量就换成毫米。

        和风的逐天预报不给降水概率，只给降水量；Open-Meteo 正好相反。
        一格两个口径总比空着强，标签「降水」两边都说得通。
        """
        pop = weather.get("pop")
        if pop is not None:
            return f"{pop}%"
        precip = weather.get("precip")
        if precip is not None:
            return f"{precip}mm"
        return "--"

    def _wind_cell(self, weather: dict) -> tuple[str, str, None]:
        """风向塞进标签、风级当数值，一行放得下。

        风向最长取两个字（「东南」→「东南风」90px），三个字就会把这一列的
        标签宽度顶上去，连累同列其他格子的数值起点。
        风级用 sources.effective_wind_level()：和风给风级、Open-Meteo 给 km/h，
        换算收在那一个函数里，免得这里和自检工具各算各的。
        """
        direction = clean_text(weather.get("wind_dir", ""), 2)
        label = f"{direction}风" if direction else "风力"
        level = effective_wind_level(weather)
        return label, (f"{level}级" if level is not None else "--"), None

    # =====================================================================
    #  速览（默认关闭，代码保留）
    # =====================================================================

    # 条目间距常量：测量和绘制必须共用，否则"预测高度"和"实际高度"会对不上，
    # 自适应裁剪就会误判（这个坑踩过一次）
    _GAP_TITLE_TO_SUM = 4
    _GAP_BETWEEN_ITEMS = 10
    _TITLE_LINE_LEAD = 3

    def digest_height(self, items: list, extra_gap: int = 0) -> int:
        if not items:
            return 0
        title_font = self.f(FS_ITEM_TITLE, bold=True)
        sum_font = self.f(FS_ITEM_SUM)
        max_title_lines = int(self.cfg.get("digest.max_title_lines", 1))
        max_sum_lines = int(self.cfg.get("digest.max_summary_lines", 2))
        per_item = (max_title_lines * (self.lh(title_font) + self.px(self._TITLE_LINE_LEAD))
                    + self.px(self._GAP_TITLE_TO_SUM)
                    + max_sum_lines * self.lh(sum_font)
                    + self.px(self._GAP_BETWEEN_ITEMS) + extra_gap)
        return self._section_height() + len(items) * per_item

    def _section_height(self) -> int:
        """区块标题 + 通栏线的固定高度。"""
        return (self.lh(self.f(FS_SECTION, bold=True)) + self.px(8)
                + self.px(3) + self.px(15))

    def draw_digest(self, top: int, digest: dict, extra_gap: int = 0) -> int:
        items = digest.get("items") or []
        if not items:
            return top

        note = "AI 摘要" if digest.get("ai") else "标题直出"
        model = digest.get("model") or ""
        if digest.get("ai") and model:
            note += f" · {model.split('/')[-1]}"
        y = self.section_title(top, digest.get("title", "AI · 早报"), right_note=note)

        title_font = self.f(FS_ITEM_TITLE, bold=True)
        sum_font = self.f(FS_ITEM_SUM)
        chip_font = self.f(FS_ITEM_CHIP, bold=True)
        max_title_lines = int(self.cfg.get("digest.max_title_lines", 1))
        max_sum_lines = int(self.cfg.get("digest.max_summary_lines", 2))

        # 栏目标签：所有标签取同一宽度、左边缘对齐成一列，比逐个自适应更像"排过版"
        chips = [clean_text(item.get("category_name") or "", 6) for item in items]
        chip_pad = self.px(14)
        chip_w = max([self.px(76)] + [self.tw(c, chip_font) + chip_pad * 2 for c in chips])
        chip_h = self.lh(chip_font) + self.px(10)
        text_x = self.margin + chip_w + self.px(18)
        text_w = self.w - self.margin - text_x

        for index, item in enumerate(items):
            title_lines = wrap_text(self.d, item.get("title", ""), title_font, text_w,
                                    max_title_lines)
            sum_lines = wrap_text(self.d, item.get("summary", ""), sum_font, text_w,
                                  max_sum_lines)

            if chips[index]:
                chip_y = y + (self.lh(title_font) - chip_h) // 2
                self.d.rounded_rectangle(
                    [self.margin, chip_y, self.margin + chip_w, chip_y + chip_h],
                    radius=self.px(6), fill=INK)
                self.text((self.margin + chip_w / 2, chip_y + chip_h / 2),
                          chips[index], chip_font, 255, anchor="mm", strong=True)

            ty = y
            for line in title_lines:
                self.text((text_x, ty), line, title_font, INK, strong=True)
                ty += self.lh(title_font) + self.px(self._TITLE_LINE_LEAD)
            if sum_lines:
                ty += self.px(self._GAP_TITLE_TO_SUM)
                for line in sum_lines:
                    self.text((text_x, ty), line, sum_font, GRAY)
                    ty += self.lh(sum_font)
            y = ty + self.px(self._GAP_BETWEEN_ITEMS) + extra_gap
        return y

    # =====================================================================
    #  行情
    # =====================================================================

    def quote_cell_h(self) -> int:
        name_f = self.f(FS_QUOTE_NAME)
        price_f = self.f(FS_QUOTE_PRICE, bold=True)
        pct_f = self.f(FS_QUOTE_PCT, bold=True)
        return (self.lh(name_f) + self.px(QUOTE_GAP_NAME_PRICE)
                + self.lh(price_f) + self.px(QUOTE_GAP_PRICE_PCT)
                + self.lh(pct_f) + self._quotes_extra)

    def quotes_height(self, quotes: list, funds: list) -> int:
        total = len(quotes or []) + len(funds or [])
        if not total:
            return 0
        cols = min(3, total)
        rows = (total + cols - 1) // cols
        return self.px(3) + self.px(20) + rows * self.quote_cell_h() + self.px(10)

    def draw_quotes(self, top: int, quotes: list, funds: list) -> int:
        entries = list(quotes or []) + list(funds or [])
        if not entries:
            return top

        # 「行情」这个区块标题行被删掉了：涨跌方块本来就画在每一行里，
        # 标题是冗余的，省下来的 74px 让价格字号能大一档。
        self.rule(top, thickness=self.px(3))
        y = top + self.px(20)

        cols = min(3, len(entries))
        gap = self.px(28)
        cell_w = (self.avail_w - gap * (cols - 1)) / cols
        cell_h = self.quote_cell_h()
        name_font = self.f(FS_QUOTE_NAME)
        price_font = self.f(FS_QUOTE_PRICE, bold=True)
        pct_font = self.f(FS_QUOTE_PCT, bold=True)
        marker = self.px(18)

        for i, entry in enumerate(entries):
            cx = self.margin + (i % cols) * (cell_w + gap)
            cy = y + (i // cols) * cell_h
            pct = entry.get("pct")
            price = entry.get("price")

            if pct is None or price is None:
                pct_txt, pct_color, filled = "--", GRAY_LIGHT, None
            elif pct >= 0:
                pct_txt, pct_color, filled = f"+{pct:.2f}%", INK, True
            else:
                pct_txt, pct_color, filled = f"{pct:.2f}%", GRAY, False

            name = clean_text(entry.get("name", ""), 12)
            name = self.clip_text(name, name_font, cell_w)
            self.text((cx, cy), name, name_font, INK_SOFT)

            price_txt = self._fmt_price(price, entry.get("is_fund"), entry.get("is_crypto"))
            py = cy + self.lh(name_font) + self.px(QUOTE_GAP_NAME_PRICE)
            self.text((cx, py), price_txt, price_font,
                      INK if price is not None else GRAY_LIGHT, strong=price is not None)

            # 涨跌：实心方块 = 涨、空心方块 = 跌。灰度屏上必须靠形状而不是颜色区分。
            by = py + self.lh(price_font) + self.px(QUOTE_GAP_PRICE_PCT)
            sy = by + (self.lh(pct_font) - marker) // 2
            if filled is True:
                self.d.rectangle([cx, sy, cx + marker, sy + marker], fill=INK)
            elif filled is False:
                self.d.rectangle([cx, sy, cx + marker, sy + marker], outline=GRAY,
                                 width=max(1, self.px(2)))
            self.text((cx + marker + self.px(14), by + self.ascent(pct_font)), pct_txt,
                      pct_font, pct_color, anchor="ls", strong=filled is True)

        return y + ((len(entries) + cols - 1) // cols) * cell_h + self.px(10)

    @staticmethod
    def _fmt_price(price, is_fund, is_crypto=False) -> str:
        """行情价格格式。

        四位数以上一律打千分位、不留小数：同一行里出现 "21,346" 和 "6218.44"
        两种风格会很乱，而在 300ppi 的 6 寸屏上，指数的小数位没有信息价值。
        """
        if price is None:
            return "--"
        try:
            value = float(price)
        except (TypeError, ValueError):
            return "--"
        if is_fund:
            return f"{value:.4f}"
        if is_crypto or value >= 1000:
            return f"{value:,.0f}"
        return f"{value:.2f}"

    def draw_footer(self, y: int) -> None:
        self.rule(y, thickness=max(1, self.px(2)), color=GRAY_LIGHT)
        model = str(self.cfg.get("device.model", ""))
        label = DEVICE_LABELS.get(model.lower(), model)
        weather = self.data.get("weather") or {}
        bits = [f"更新 {self.generated_at.strftime('%m-%d %H:%M')}", f"Kindle {label}"]
        if weather.get("source"):
            bits.append(f"天气 {weather['source']}")
        if self.data.get("quotes"):
            bits.append("行情 腾讯")
        # 涨跌图例从行情标题挪到页脚，信息不丢，行情那块省下一整行
        bits.append("实心=涨 空心=跌")
        font = self.f(FS_FOOT)
        line = self.clip_text(" · ".join(bits), font, self.avail_w)
        self.text((self.margin, y + self.px(14)), line, font, GRAY)

    # =====================================================================
    #  主流程
    # =====================================================================

    def _draw_empty_notice(self, y: int) -> None:
        """所有区块都没数据时的一屏提示。"""
        cy = y + self.px(180)
        self.icon(self.w / 2, cy, self.px(96), "fog", fill=GRAY_LIGHT)
        title_font = self.f(FS_SECTION, bold=True)
        body_font = self.f(FS_ITEM_SUM)
        self.text((self.w / 2, cy + self.px(90)), "暂时没抓到数据", title_font, INK,
                  anchor="mt", strong=True)
        for i, line in enumerate((
                "天气和行情这一轮都失败了，多半是网络的问题",
                "不是你的 Kindle 坏了",
                "下一个刷新周期会自动重试")):
            self.text((self.w / 2, cy + self.px(160) + i * (self.lh(body_font) + self.px(6))),
                      line, body_font, GRAY, anchor="mt")
        self._notes.append("所有数据源都为空，已显示空白提示页")

    def calendar_for(self):
        """取当天的日历信息。

        数据里没有就按 generated_at 现算一份——渲染层不依赖调用方先算好，
        单独拿 Renderer 做预览也能画出日历。
        """
        if not self.cfg.get("calendar.enabled", True):
            return None
        cal = self.data.get("calendar")
        if cal is None:
            from .lunar import calendar_info
            cal = calendar_info(self.generated_at)
        return cal

    def render(self) -> Image.Image:
        # 三个开关在这里真正生效（以前写在默认配置里但从没人读，属于半成品）
        weather = self.data.get("weather") if self.cfg.get("weather.enabled", True) else None
        quotes_on = bool(self.cfg.get("quotes.enabled", True))
        quotes = list(self.data.get("quotes") or []) if quotes_on else []
        funds = list(self.data.get("funds") or []) if quotes_on else []
        digest = dict(self.data.get("digest") or {})
        if not self.cfg.get("digest.enabled", True):
            digest = dict(digest, items=[])
        cal = self.calendar_for()

        top_gap = self.px(10)
        footer_h = self.px(58)

        # 先量一遍基准高度，再把富余高度均摊成三块的内边距。
        # 堆在底部会看起来像"没排完"，摊进三块才像"设计过的"。
        self._cal_extra = self._weather_extra = self._quotes_extra = 0
        base = (self.calendar_height(cal) + self.weather_height(weather)
                + self.quotes_height(quotes, funds)
                + self.digest_height(digest.get("items") or []))
        slack = self.h - top_gap - footer_h - base
        if slack > 0:
            if digest.get("items"):
                # 速览在场时它自己会吃掉富余，不再摊给别的块
                self._cal_extra = slack // 3
                self._weather_extra = slack // 3
                self._quotes_extra = slack - (slack // 3) * 2
            else:
                self._cal_extra = slack // 2
                self._weather_extra = slack - self._cal_extra
        elif slack < -self.px(4):
            self._notes.append(f"版面超了 {-slack}px，内容可能越界，"
                               f"跑 layout_check.py 看是哪一块涨了")

        # 时钟区的绝对矩形记在这里，生成精灵图的工具直接取用，
        # 不自己再算一遍坐标（算两遍就会有两份真相）。
        self.clock_box = self.clock_region(cal)

        y = self.draw_calendar(top_gap, cal)
        y = self.draw_weather(y, weather)
        if digest.get("items"):
            usable = self.h - y - footer_h - self.px(24)
            items = list(digest.get("items") or [])
            chosen = 0
            for candidate in range(1, len(items) + 1):
                if self.digest_height(items[:candidate]) <= usable:
                    chosen = candidate
                else:
                    break
            if chosen < len(items):
                self._notes.append(
                    f"版面放不下：速览由 {len(items)} 条裁到 {chosen} 条"
                    f"（想全留就调小 digest.max_summary_lines 或关掉某个区块）")
            extra_gap = 0
            if chosen > 0:
                gap_slack = usable - self.digest_height(items[:chosen])
                extra_gap = max(0, min(self.px(26), gap_slack // chosen))
            y = self.draw_digest(y, dict(digest, items=items[:chosen]), extra_gap)
        y = self.draw_quotes(y, quotes, funds)

        # 所有数据源同时挂掉时，留一屏白板还不如明确说一句，
        # 否则你会以为是屏幕坏了而不是网络坏了
        if not any((weather, quotes, funds, digest.get("items"))):
            self._draw_empty_notice(y)
        self.draw_footer(self.h - footer_h)

        posterize = int(self.cfg.get("display.posterize", 0) or 0)
        if posterize > 1:
            levels = posterize - 1
            self.img = self.img.point(
                lambda v: int(round(v / 255.0 * levels)) * 255 // levels)
        return self.img

    @property
    def notes(self) -> list[str]:
        return self._notes
