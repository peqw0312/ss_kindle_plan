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

import copy
import math
import re
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter

from .fonts import book_for
from .sources import clean_text, crypto_source_label, effective_wind_level

# 墨色梯度：只保留这几档，保证在 16 级灰度屏上有足够反差
INK = 0
INK_SOFT = 70
GRAY = 130
GRAY_LIGHT = 195
PAPER = 255

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

#: 上面这批 FS_* 就是「经典」版的基准值，同时充当预设里没写到的项的兜底 ——
#: 所以新增一个字号常量时，不必在四个预设里各补一行。
_BASE_SIZES: dict[str, int] = {
    k: v for k, v in list(globals().items()) if k.startswith("FS_") and isinstance(v, int)
}

#: 设计预设。`config.yaml` 的 `style.preset` 选哪一个。
#:
#: 每一项只写**要改的**：裸名字（FS_CAL_DAY）是字号，带点号的是配置覆盖
#: （device.margin、layout.order 这种），没写的沿用「经典」。
#: margin 归到预设里是因为边距和字号是一起配平的，分开调会得到第三种没验证过的版面。
#:
#: 为什么是预设而不是把二十几个字号一个个摆进 config：那二十几个值是互相制约的
#: （日历条变高 → 天气区就得变矮 → 数据格字号要跟着收），单独放开一个就能把版面
#: 配崩，而且崩了才知道。预设是配平过、跑过 layout_check 的整组。
STYLE_PRESETS: dict[str, dict] = {
    "经典": {},
    "大字": {
        # 远看要认的就是日期和温度这两个数，所以它们提级；代价从**小字**里出。
        # 一版只提大字号、小字一律不动的写法试过，余量只剩 21px（安全线 40）：
        # 吃掉高度的正是宜忌、数据格标签、页脚这些行，它们每行都在抢纵向预算。
        # 把这几个收一档，日期和温度就能保持 194/136，余量回到 44px。
        "device.margin": 44,
        "FS_CAL_DAY": 194, "FS_TEMP": 136,
        "FS_YIJI": 26, "FS_YIJI_MARK": 26,
        "FS_LABEL": 26, "FS_CAL_LINE": 28, "FS_QUOTE_NAME": 24, "FS_FOOT": 16,
    },
    "紧凑": {
        "device.margin": 56,
        "FS_CAL_DAY": 150, "FS_LUNAR": 52, "FS_CAL_MONTH": 30, "FS_CAL_WEEK": 34,
        "FS_TEMP": 104, "FS_DESC": 38,
        "FS_LABEL": 26, "FS_VALUE": 38, "FS_QUOTE_NAME": 26,
        "FS_QUOTE_PRICE": 44, "FS_QUOTE_PCT": 30,
        "FS_SECTION": 30, "FS_YIJI": 28, "FS_YIJI_MARK": 28, "FS_FOOT": 17,
    },
    "中式": {
        "FS_LUNAR": 92, "FS_CAL_DAY": 168, "FS_CAL_MONTH": 34, "FS_CAL_WEEK": 40,
        "FS_YIJI": 40, "FS_YIJI_MARK": 40, "FS_BADGE": 44,
        "FS_TEMP": 112, "FS_DESC": 44,
        "FS_QUOTE_NAME": 26, "FS_QUOTE_PRICE": 42, "FS_QUOTE_PCT": 28,
        "FS_SECTION": 34,
    },
    # ---- 下面三版换的是「第一眼看到什么」，不是字号 ----
    "天气优先": {
        "layout.order": ["weather", "calendar", "digest", "quotes"],
        "FS_TEMP": 124,
    },
    "行情优先": {
        # 换的是第一眼看到什么：指数挪到最上面，价格提级。
        # 提级要从日历条扣 —— 这一版日历不是主角，日期收一档换来的高度刚好够，
        # 两头都想要的话余量会掉到 14px（安全线 40），那是迟早要越界的写法。
        "layout.order": ["quotes", "calendar", "weather", "digest"],
        "FS_CAL_DAY": 150, "FS_QUOTE_PRICE": 64, "FS_QUOTE_PCT": 38,
    },
    "极简": {
        # 宜忌和干支是"看一眼就过"的信息，占的却是日历条右列两行高度。
        # 关掉它们省下来的高度不硬塞新内容，让均摊逻辑把它变成留白 ——
        # 留白是这一版要卖的东西。
        "calendar.show_yiji": False,
        "calendar.show_ganzhi": False,
        "weather.show_air": False,
        "digest.enabled": False,
        "device.margin": 56,
        "FS_CAL_DAY": 186, "FS_LUNAR": 78, "FS_TEMP": 128,
    },
}

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

#: 「帖」版式右上角留给**电量精灵图**的矩形（设计稿 B 款，2026-09-22 定稿）。
#: 电量只有设备自己知道，云端画不了 —— 和当初时钟同一条路：图上留白，
#: Kindle 按档位贴精灵图。矩形固定不变，换电量样式不用改版面。
POSTER_BATTERY = (814, 40, 1016, 96)

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
    #: 有哪些版式，以及给人看的名字。皮肤墙、云端下拉、报错提示都以这里为准。
    LAYOUTS = ("bands", "poster", "c1", "arc")
    LAYOUT_LABELS = {"bands": "条带（最早那版）", "poster": "帖（居中宋体大字）",
                     "c1": "带（四条分带 + 大图标预报）",
                     "arc": "信息终端（工业军规 · 线稿图标）"}

    def __init__(self, cfg, data: dict):
        self.data = data or {}
        self._notes: list[str] = []

        # 字号落成**实例属性**，不是临时改模块全局：改全局会让同一进程里
        # 先后两次渲染互相串版（layout_check 一次跑五个场景就是这种情况），
        # 而且看概率 —— 一台设备上出现两种字号，最难查。
        self.preset = str(cfg.get("style.preset", "经典") or "经典")
        if self.preset not in STYLE_PRESETS:
            self._notes.append(f"style.preset「{self.preset}」不存在，已退回「经典」。"
                               f"可选：{'、'.join(STYLE_PRESETS)}")
            self.preset = "经典"
        chosen = STYLE_PRESETS[self.preset]
        for name, base in _BASE_SIZES.items():
            setattr(self, name, chosen.get(name, base))

        # 版式引擎的清单只有一个来源：下面这个类属性。皮肤墙和云端那个下拉都从
        # 它取，别再在第二处抄一遍名单 —— 抄两遍迟早对不上。
        # （唯一抄第二遍的地方：build.yml 里 workflow_dispatch 的 options，那是
        #  GitHub 要求写死的静态列表，加版式时记得同步。）
        self.layout = str(cfg.get("style.layout", "bands") or "bands").lower()
        if self.layout not in self.LAYOUTS:
            self._notes.append(f"style.layout「{self.layout}」不存在，已退回 bands。"
                               f"可选：{' / '.join(self.LAYOUTS)}")
            self.layout = "bands"

        # 带点号的键是配置覆盖（device.margin、weather.days 这种）。
        # cfg 在多个请求之间共用同一个对象，直接改会把这一版的设置漏给别的请求，
        # 所以有覆盖就先深拷一份自己用。
        overrides = {k: v for k, v in chosen.items() if "." in k}
        if overrides:
            cfg = copy.deepcopy(cfg)
            for key, value in overrides.items():
                cfg.set(key, value)
        self.cfg = cfg
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
        # 富余高度均摊出来的额外内边距，由 render() 在绘制前算好
        self._cal_extra = 0
        self._weather_extra = 0
        self._quotes_extra = 0
        # 时钟在成品图里的绝对矩形，由 render() 填 —— 生成精灵图的工具靠它取坐标
        self.clock_box: tuple[int, int, int, int] | None = None
        # 每个区块实际占到的矩形，由 render() 按 layout.order 填。
        # 调参台靠它在预览图上画出可拖动的框，所以只能画完才知道。
        self.block_boxes: dict[str, tuple[int, int, int, int]] = {}
        #: 纵向余量，由 render() 填。负数就是版面已经超了。
        self.slack: int = 0

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
        font = self.f(self.FS_SECTION, bold=True)
        self.text((self.margin, y), title, font, INK, strong=True)
        if right_note:
            self.text((self.w - self.margin, y + self.px(10)), right_note,
                      self.f(self.FS_LABEL), GRAY, anchor="rt")
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

        `clock.mode: off` 是彻底不要时间（为了省电，Kindle 端 CLOCK_MODE=off）。
        这时**绝对不能画进图里** —— 图一天只重算四次，画进去的就是一个最多
        六小时前的假时间，比没有更糟。这里以前写的是 `mode != "local"`，
        任何别的值都会画进图里，加 off 这个选项时必须一起改，不然就是个错值。
        """
        return str(self.cfg.get("clock.mode", "image") or "image").lower() == "image"

    def clock_region(self, cal=None, top: int | None = None) -> tuple[int, int, int, int]:
        """时钟在成品图里的绝对像素矩形 (左, 上, 右, 下)。

        这是**出图、生成精灵图、Kindle 贴图三方共用的唯一基准**：
        图里这块留白，精灵图的画布就是这个尺寸，`eips -x/-y` 也照这个坐标贴。
        三者用的是同一个函数，所以不可能对不上；改了字号不重新生成精灵图才会。

        注意高度取 `r1`（农历行与时钟行的较大行高）而不是墨迹高度 ——
        要覆盖所有时刻里最高的那种字形，不能只按当前这一刻算。

        `top` 是日历条的顶部。时钟住在日历条里，区块一旦允许重排，
        日历就不一定排在第一块，这个 top 就不能再假设是 TOP_GAP。
        不传则退回"日历在最上面"的老默认，让单独调这个函数的工具照样能算。
        """
        g = self.calendar_geometry(cal if cal is not None else self.calendar_for())
        box_top = (top if top is not None else self.px(TOP_GAP)) + self.px(CAL_BOX_TOP_GAP)
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
        font = self.f(self.FS_CLOCK, bold=True)
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

    #: 这一套 = 选型页六套里的 **E「柔雾 · 灰实心」**（见 tools/icon_sets.py）。
    #: 整块深灰而不是黑：大面积实心黑在墨水屏上刷新时残影明显，
    #: 而 70 这一档远看照样成一个实心块面。
    #: 所有系数都以 R = size/2 或 size 为单位，所以任何尺寸下比例都一样。
    ICON_LOBES = ((-0.46, 0.21), (-0.18, 0.33), (0.14, 0.35), (0.44, 0.20))
    ICON_CLOUD_A = 1.10          # 云半径基准，单位 = R
    ICON_CLOUD_WIDE = 1.02       # 云横向再拉宽一点
    ICON_HALO = 0.028            # 云和天体之间那道白缝，单位 = size
    ICON_AUX_W = 0.065           # 雨滴的笔画宽，单位 = size
    ICON_FOG_W = 0.075           # 雾条的笔画宽，单位 = size
    ICON_DISC_R = 0.31           # 太阳盘半径，单位 = R
    ICON_MOON_R = 0.46
    ICON_RAY = (1.55, 2.05)      # 光芒点心到盘心的距离，单位 = 盘半径
    ICON_DROP_Y0 = 0.44          # 降水符号起点，单位 = R（相对图标中心）
    ICON_DROP_SPAN = 0.62        # 三个雨滴的横向排布宽，单位 = R

    #: 滴数本身就是强度。以前小/中/大雨都画三滴、只改粗细，挂墙上远看
    #: 中雨和暴雨一个样 —— 而"要不要带伞"恰恰是这一格要回答的问题。
    #: 暴雨不靠把滴画大来表达（放大后反而糊成一坨），而是数到第四滴。
    RAIN_KINDS = ("drizzle", "rain_light", "rain", "shower", "rain_heavy",
                  "rain_storm")
    ICON_DROPS = {"drizzle": 1, "rain_light": 1, "rain": 2, "shower": 3,
                  "rain_heavy": 3, "rain_storm": 4}
    ICON_DROP_OFF = {1: (0.0,), 2: (-0.19, 0.19), 3: (-0.31, 0.0, 0.31),
                     4: (-0.48, -0.16, 0.16, 0.48)}
    ICON_DROP_LONG = {"drizzle": 0.22, "rain_light": 0.30, "rain": 0.34,
                      "shower": 0.46, "rain_heavy": 0.40, "rain_storm": 0.40}
    ICON_DROP_K = {"drizzle": 0.70, "rain_light": 0.95, "rain": 1.0,
                   "shower": 1.15, "rain_heavy": 1.20, "rain_storm": 1.05}

    def _cap_line(self, d, p0, p1, w: float, level: int) -> None:
        """Pillow 的 line 没有圆头，方头在小尺寸下显脏，两端自己补圆。"""
        d.line([p0, p1], fill=level, width=w)
        for x, y in (p0, p1):
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=level)

    def _stamp_solid(self, cx: float, cy: float, size: float, sil,
                     level: int, bg: int = PAPER) -> None:
        """把剪影整块填成一个色阶贴回主图。sil(dd, side) 在掩膜上画 255。

        先贴一圈底色（把云剪影向外胀一道），再贴云体：躲在云后面的太阳/月亮
        会被切出一道均匀的底色缝，看着是"藏在后面"，而不是"和云粘在一起"。
        bg 必须是图标所在地的底色：反相格上若还贴 255，云会镶一圈白边。
        """
        side = int(max(12, round(size * 1.7)))
        x0, y0 = int(round(cx - side / 2)), int(round(cy - side / 2))
        mask = Image.new("L", (side, side), 0)
        sil(ImageDraw.Draw(mask), side)
        halo = int(round(size * self.ICON_HALO))
        if halo:
            self.img.paste(bg, (x0, y0),
                           mask.filter(ImageFilter.MaxFilter(halo * 2 + 1)))
        self.img.paste(level, (x0, y0), mask)

    #: 夜里不能画太阳。数据里一直有 is_day，只是没人读它。
    NIGHT_ICON = {"sun": "moon", "sun_cloud": "moon_cloud"}

    def _cloud(self, d, a: float, ox: float, oy: float) -> None:
        """云的剪影：四个圆 + 一条把它们底边连起来的**下公切线**。

        上一版是「三个椭圆 + 一块矩形垫底」。矩形是硬塞进去的：它的直边和圆
        相交处全是直角，两个端头还露在圆外面 —— 就成了"云底下压着一根横杠"，
        被指着骂的就是那条线。

        这里每个圆心都落在底边上方正好一个半径处，也就是**每个圆都与底边相切**。
        这时外公切线就是底边本身，不用再解切线：把圆心依次连起来、再沿底边
        收口，就是整朵云的填充域。直线和圆相切的地方没有拐角，出来是一整条
        连续的曲线，不需要再"藏"一条线。
        """
        cs = [(ox + dx * a * self.ICON_CLOUD_WIDE, oy - r * a, r * a)
              for dx, r in self.ICON_LOBES]
        d.polygon([(cx, cy) for cx, cy, _ in cs] + [(cs[-1][0], oy), (cs[0][0], oy)],
                  fill=255)
        for cx, cy, rr in cs:
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=255)

    def _cloud_stamp(self, cx: float, cy: float, size: float, dx: float,
                     dy: float, scale: float, level: int,
                     bg: int = PAPER) -> None:
        """画一朵云，云心落在 (cx + dx·R, cy + dy·R)，R = size/2。"""
        R = size / 2.0
        a = R * self.ICON_CLOUD_A * scale
        rmax = max(r for _, r in self.ICON_LOBES)

        def sil(d, side):
            # 云的视觉中心在底边上方 rmax·a 处，所以底边要往下补这一截
            self._cloud(d, a, side / 2 + dx * R,
                        side / 2 + dy * R + (rmax - 0.26) * a)
        self._stamp_solid(cx, cy, size, sil, level, bg)

    def _sun_rays(self, cx: float, cy: float, sr: float, w: float, level: int,
                  n: int, a0: float, step: float) -> None:
        """光芒是一圈离开盘心的圆点，不是射线 —— 灰实心里点比线更稳。"""
        r0, r1 = self.ICON_RAY
        rr = max(1.5, w * 0.62)
        mid = sr * (r0 + r1) / 2
        for i in range(n):
            ang = math.radians(a0 + i * step)
            x, y = cx + math.cos(ang) * mid, cy + math.sin(ang) * mid
            self.d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=level)

    def _crescent(self, cx: float, cy: float, mr: float, level: int,
                  bg: int = PAPER) -> None:
        """实心盘再挖掉一个偏心圆，剩下的就是月牙。挖的那刀要用底色 bg，
        否则在黑底格上月牙会被一坨白圆咬掉。"""
        self.d.ellipse([cx - mr, cy - mr, cx + mr, cy + mr], fill=level)
        cut = mr * 0.94
        ox, oy = cx + mr * 0.52, cy - mr * 0.46
        self.d.ellipse([ox - cut, oy - cut, ox + cut, oy + cut], fill=bg)

    def _drops(self, cx: float, cy: float, R: float, w: float, level: int,
               kind: str) -> None:
        """云底下的水滴，上尖下圆。滴数=强度（ICON_DROPS：1/2/3 滴，
        暴雨是 3 大滴），滴形大小与下落长度也跟着强度走。"""
        n = self.ICON_DROPS.get(kind, 2)
        long = self.ICON_DROP_LONG.get(kind, 0.34)
        k = self.ICON_DROP_K.get(kind, 1.0)
        y0 = cy + R * self.ICON_DROP_Y0
        rr = max(2.5, w * 1.05) * k
        for off in self.ICON_DROP_OFF[n]:
            x = cx + off * R
            y = y0 + R * long * 0.55
            self.d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=level)
            self.d.polygon([(x, y0 - R * long * 0.35), (x - rr * 0.92, y),
                            (x + rr * 0.92, y)], fill=level)

    def night_kind(self, kind, weather: dict):
        """把白天的图标换成对应夜间的。

        只在 `is_day` 明确是 0 时换 —— 拿不到这个字段（比如某些源没给）就当白天，
        别猜。猜错的代价是晚上画个月亮出来，比白天画个月亮好认。
        """
        if (weather or {}).get("is_day") in (0, "0", False):
            return self.NIGHT_ICON.get(str(kind or "").lower(), kind)
        return kind

    def icon(self, cx: float, cy: float, size: float, kind: str,
             fill: int = INK_SOFT, align_cloud: bool = False,
             bg: int = PAPER) -> None:
        """天气图标。

        旧版所有部件都是实心黑：云 = 三个实心椭圆 + 一个矩形，在主图里就是
        一坨黑疙瘩；雨是三条粗竖线，缩到小尺寸看着像"≡"。现在整块按 E 套
        「柔雾 · 灰实心」画：深灰实心 + 云和天体之间一道白缝。

        一个图标最多一个附加符号 —— 小尺寸下符号越多越糊。
        认不出来的 kind 一律按雾画。

        align_cloud：一排图标并列时（预报行）云体本身要对齐。雨类的云往上
        让了 0.30R 给雨滴、多云类的云往下让了 0.30R 给天体，不补就会一高一低。
        """
        kind = str(kind or "cloud").lower()
        R = size / 2.0
        if align_cloud:
            dy = {"sun_cloud": 0.30, "moon_cloud": 0.30, "thunder": -0.34}.get(kind)
            if dy is None and kind in self.RAIN_KINDS + ("snow",):
                dy = -0.30
            cy -= (dy or 0.0) * R
        aw = max(2, int(round(size * self.ICON_AUX_W)))
        d = self.d

        if kind == "sun":
            sr = R * self.ICON_DISC_R
            d.ellipse([cx - sr, cy - sr, cx + sr, cy + sr], fill=fill)
            self._sun_rays(cx, cy, sr, aw, fill, n=8, a0=0.0, step=45.0)

        elif kind == "sun_cloud":
            # 躲在云后面的太阳：盘要小、只露左上五道芒，芒宽再收一档，
            # 否则五道芒挤在一起并成一坨。
            sr = R * self.ICON_DISC_R * 0.80
            sx, sy = cx - R * 0.34, cy - R * 0.36
            d.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=fill)
            self._sun_rays(sx, sy, sr, max(2, int(aw * 0.72)), fill,
                           n=5, a0=180.0, step=22.5)
            self._cloud_stamp(cx, cy, size, 0.14, 0.30, 0.92, fill, bg)

        elif kind == "moon":
            self._crescent(cx - R * 0.04, cy + R * 0.02,
                           R * self.ICON_MOON_R, fill, bg)

        elif kind == "moon_cloud":
            self._crescent(cx - R * 0.34, cy - R * 0.36,
                           R * self.ICON_DISC_R * 1.02, fill, bg)
            self._cloud_stamp(cx, cy, size, 0.14, 0.30, 0.92, fill, bg)

        elif kind == "cloud":
            self._cloud_stamp(cx, cy, size, 0.0, 0.0, 1.0, fill, bg)

        elif kind in self.RAIN_KINDS + ("snow", "thunder"):
            up = 0.34 if kind == "thunder" else 0.30
            self._cloud_stamp(cx, cy, size, 0.0, -up, 0.98, fill, bg)
            if kind == "snow":
                rr = max(2.5, R * 0.085)
                span = self.ICON_DROP_SPAN
                for i in range(3):
                    x = cx + (-span / 2 + span / 2 * i) * R
                    y = cy + R * (0.46 - up * 0.35)
                    d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=fill)
            elif kind == "thunder":
                by = cy - R * up * 0.45
                d.polygon([(cx + R * 0.10, by + R * 0.16),
                           (cx - R * 0.24, by + R * 0.60),
                           (cx + R * 0.00, by + R * 0.60),
                           (cx - R * 0.12, by + R * 0.98),
                           (cx + R * 0.30, by + R * 0.46),
                           (cx + R * 0.06, by + R * 0.46),
                           (cx + R * 0.32, by + R * 0.16)], fill=fill)
            else:
                self._drops(cx, cy - R * up * 0.55, R, aw, fill, kind)

        else:                                                 # fog / 认不出来
            w = max(2, int(round(size * self.ICON_FOG_W)))
            for i, dy in enumerate((-0.34, -0.02, 0.30)):
                half = R * (0.52 if i % 2 else 0.74)
                self._cap_line(d, (cx - half, cy + R * dy),
                               (cx + half, cy + R * dy), w, fill)

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
        day_font = self.f(self.FS_CAL_DAY, bold=True)
        week_font = self.f(self.FS_CAL_WEEK)

        extra_top = self._cal_extra // 2
        extra_bottom = self._cal_extra - extra_top
        pad_top = self.px(CAL_DAY_PAD_TOP) + extra_top
        pad_bottom = self.px(CAL_DAY_PAD_BOTTOM) + extra_bottom
        left_h = (bar_h + pad_top + self.lh(day_font) + self.px(CAL_WEEK_GAP)
                  + self.lh(week_font) + pad_bottom)

        clock_font = self.f(self.FS_CLOCK, bold=True)
        clock_w = self.tw(self.clock_probe(), clock_font)

        lunar_font = self.f(self.FS_LUNAR, bold=True)
        r1 = max(self.lh(lunar_font), self.lh(clock_font))
        r2 = self.lh(self.f(self.FS_CAL_LINE))
        badge_h = max(self.px(56), self.lh(self.f(self.FS_BADGE, bold=True)) + self.px(20))
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

        strip_h = self.px(3) + self.px(18) + self.lh(self.f(self.FS_YIJI)) + self.px(18)
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
        # 时钟区跟着日历条走：区块允许重排之后日历不一定在第一块，
        # 所以这个矩形只能在真正开画的那一刻、拿到实际 top 之后才定得准。
        self.clock_box = self.clock_region(cal, top)
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
                  f"{cal.solar_year}年{cal.solar_month}月", self.f(self.FS_CAL_MONTH, bold=True),
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
            size = self.FS_LUNAR
            while size > self.FS_LUNAR * 0.5 and self.tw(
                    cal.lunar_text, self.f(size, bold=True)) > g["text_w"]:
                size -= 2
            lunar_font = self.f(size, bold=True)
        self.text((g["right_x"], y), cal.lunar_text, lunar_font, INK, anchor="lt",
                  strong=True)

        # 时钟：本机时钟模式下这块整片留白，由 Kindle 用系统时间贴精灵图。
        # 留白不影响排版——clock_w 照样参与横向预算，农历该让的位置还让。
        if self.clock_in_image():
            self._draw_clock(self.clock_text(), box_top)

        line_font = self.f(self.FS_CAL_LINE)
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
        badge_font = self.f(self.FS_BADGE, bold=True)
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
        mark_font = self.f(self.FS_YIJI_MARK, bold=True)
        body_font = self.f(self.FS_YIJI)
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

        左半是「主图标 + 温度」一行、天气描述一行，右半是 2 列 × 3 行的数据格
        （体感 / 湿度 · 降水 / 风力 · 空气 / 紫外），底部是含今天在内的预报条，
        每天「日期 → 大图标 + 天气文字 → 最高最低」。

        这里有两个尺寸是被"最长内容"倒逼出来的，改之前先量：

        * 数据格必须是 **2 列**。3 列时每格只有 161px，而「空气 轻度 118」
          就要 220px——旧版的「轻度污染 118」更是要 308px，直接糊到隔壁格子上。
        * 温度和一整句描述**不能并排**。图标 124 + 「29°」203 + 「多云转小雨」220
          一共 585px，而左半区最多只有 445px，并排必然压到右边的数据格。
          改成上下两行后，横向只受"较宽的那个"约束，怎么都不会越界。
        """
        pad = self.px(24) + self._weather_extra // 2
        temp_font = self.f(self.FS_TEMP, bold=True)
        desc_font = self.f(self.FS_DESC)
        label_font = self.f(self.FS_LABEL)
        value_font = self.f(self.FS_VALUE)
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

        # --- 左半：图标 + 温度同一行，描述压在两者下面通栏排 ---
        # 以前图标对着整个左半区居中、温度和描述各占一行，三样东西正好错开成
        # 一条斜线：太阳在中间、30° 在右上、晴在右下，看着像没排完。
        # 描述也不许和温度并排：并排时「29°」+「多云转小雨」比左半区还宽，
        # 会直接压到右边的数据格上（旧版实测溢出 140px）。
        inner_x = self.margin + pad
        body_top = top + g["body_top"]
        icon_size = g["icon_size"]
        group_h = self.lh(temp_font) + self.px(6) + self.lh(desc_font)
        group_top = body_top + (g["body_h"] - group_h) / 2
        text_x = inner_x + icon_size + self.px(22)
        temp_txt = f"{weather.get('temp')}°" if weather.get("temp") is not None else "--°"
        # 描述只能占到左半区，还要再让出一个间隙：这一行和数据格的第 3 行正好
        # 在同一条水平带上，一路顶到 right_x 就会和「空气」贴在一起。
        # 今天的高低不放在这里 —— 5 字描述 + 高低要 510px，左半区只有 403px，
        # 硬塞的结果是被截成「多云转小雨 · 高3…」。它改住在下面的预报条里。
        desc_txt = self.clip_text(clean_text(weather.get("desc", ""), 8), desc_font,
                                  right_x - inner_x - self.px(28))
        self.icon(inner_x + icon_size / 2, group_top + self.lh(temp_font) / 2,
                  icon_size, self.night_kind(weather.get("icon", "cloud"), weather))
        self.text((text_x, group_top), temp_txt, temp_font, INK, anchor="lt", strong=True)
        self.text((inner_x, group_top + self.lh(temp_font) + self.px(6)),
                  desc_txt, desc_font, GRAY, anchor="lt")

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
            vfont = self._fit_font(value, self.FS_VALUE, gx + cell_w - vx - tail)
            self.text((vx, base), value, vfont, INK, anchor="ls", strong=True)
            if sub:
                self.text((vx + self.tw(value, vfont) + self.px(8), base), sub,
                          label_font, GRAY, anchor="ls")

        # --- 底部：预报，每天「日期 → 大图标 + 天气文字 → 最高最低」---
        # 天数看 weather.days（**含今天在内**），不是写死三天。这里以前是
        # forecast[1:4]，于是今天从来没上过屏 —— 屏幕上写着现在 30°，
        # 却看不出今天到底是 30/23 还是 30/28；config.yaml 里那个
        # 「含今天在内展示几天」也从来没生效过。
        forecast = weather.get("forecast") or []
        days = max(1, int(self.cfg.get("weather.days", 4)))
        show = forecast[:days]
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
        title_font = self.f(self.FS_ITEM_TITLE, bold=True)
        sum_font = self.f(self.FS_ITEM_SUM)
        max_title_lines = int(self.cfg.get("digest.max_title_lines", 1))
        max_sum_lines = int(self.cfg.get("digest.max_summary_lines", 2))
        per_item = (max_title_lines * (self.lh(title_font) + self.px(self._TITLE_LINE_LEAD))
                    + self.px(self._GAP_TITLE_TO_SUM)
                    + max_sum_lines * self.lh(sum_font)
                    + self.px(self._GAP_BETWEEN_ITEMS) + extra_gap)
        return self._section_height() + len(items) * per_item

    def _section_height(self) -> int:
        """区块标题 + 通栏线的固定高度。"""
        return (self.lh(self.f(self.FS_SECTION, bold=True)) + self.px(8)
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

        title_font = self.f(self.FS_ITEM_TITLE, bold=True)
        sum_font = self.f(self.FS_ITEM_SUM)
        chip_font = self.f(self.FS_ITEM_CHIP, bold=True)
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
        name_f = self.f(self.FS_QUOTE_NAME)
        price_f = self.f(self.FS_QUOTE_PRICE, bold=True)
        pct_f = self.f(self.FS_QUOTE_PCT, bold=True)
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
        name_font = self.f(self.FS_QUOTE_NAME)
        price_font = self.f(self.FS_QUOTE_PRICE, bold=True)
        pct_font = self.f(self.FS_QUOTE_PCT, bold=True)
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
        font = self.f(self.FS_FOOT)
        line = self.clip_text(" · ".join(bits), font, self.avail_w)
        self.text((self.margin, y + self.px(14)), line, font, GRAY)

    # =====================================================================
    #  「帖」版式（style.layout: poster）：居中、宋体大字、留白优先
    #
    #  和条带版是两套栅格，不共用 FS_*：条带版的字号是"四块配平一屏"配出来的，
    #  帖版是"一张海报"配出来的，混在一张表里改一个就会把另一套带崩。
    #  回收的空间一律变底部留白，不摊进内边距 —— 一天重画四次之间，
    #  挂在墙上的那张"地图"不能跳。
    # =====================================================================

    # 字阶与块距 = 设计稿「巨 · 乙 双栏报头」配平过的整组（帖 v4，2026-09-22 定稿）：
    # 左上角挂农历月 + 公历月周；左栏日期 420 + 农历日 104；右栏图标 + 温度 + 天气词 + 地名。
    # 块距写死不随内容伸缩：开关模块只平移整组、不缩放，墙上的"地图"不跳。
    P_MARGIN = 64
    P_FS_LINE = 30
    P_FS_META = 28
    P_FS_TAG = 52
    P_FS_DAY = 420
    P_FS_LUNAR_DAY = 104
    P_FS_TEMP = 160
    P_FS_DESC = 46
    P_FS_PLACE = 32
    P_FS_WARN = 32
    P_FS_FC = 38
    P_FS_FC_ICON = 112
    P_FS_Q_NAME = 30
    P_FS_Q_PRICE = 62
    P_FS_Q_PCT = 36
    P_FS_FOOT = 26
    P_ICON_RATIO = 0.95    # 主图标高度 = 温度数字的 95%
    PG = dict(d1=18, d2=10, d3=10, d4=0, d5=20, d6=12, d7=16, d8=18, d9=16)

    # 「带」版式（style.layout: c1）定稿参数，按 1072 宽设计、px() 缩放。
    # 行盒 = 字号×1.5 取整到 8（_c1_box），间距三档：组内 gi < 块间 gb < 带间 gband。
    # fs 是字号档：整体放大/缩小只改这里（见 .workbuddy/_directions/字号三档.html）。
    C1 = dict(margin=64, day=240, temp=136, icon=104, fc_icon=96,
              gi=8, gb=20, gband=24,
              fs=dict(kick=24, meta=26, lunar=32, fest=32, note=28, desc=32,
                      cell=24, cellv=26, warn=28, fc=28,
                      m_name=26, m_price=36, m_pct=26))

    def fd(self, size: float):
        """显示字（宋体）。没有宋体的环境自动是粗体黑，几何不变。"""
        return self.fonts.get_display(int(round(size * self.s)))

    def _ink_lt(self, x, top, bottom, s, font, fill=INK) -> int:
        """左对齐 + 按墨迹在 [top,bottom] 里垂直居中。返回墨迹宽。"""
        if not s:
            return 0
        box = self.d.textbbox((x, 0), s, font=font, anchor="lt")
        h = box[3] - box[1]
        self.text((x, top + (bottom - top - h) / 2 - box[1]), s, font, fill, anchor="lt")
        return box[2] - box[0]

    def _big_num(self, x, top, bottom, s, font, fill=INK) -> int:
        """超大数字。° 单独用黑体画：宋体字面里没有 °，照排会出豆腐块。"""
        digits = s.rstrip("°")
        deg = s[len(digits):]
        w = self._ink_lt(x, top, bottom, digits, font, fill)
        if deg:
            df = self.fonts.get(int(font.size * 0.46), True)
            self._ink_lt(x + w + self.px(4), top,
                         top + int((bottom - top) * 0.38), deg, df, fill)
            w += self.px(4) + self.tw(deg, df)
        return w

    def _big_num_width(self, s: str, font) -> int:
        """_big_num 会占多宽（不画）。报头右栏要靠它决定图标要不要缩。"""
        digits = s.rstrip("°")
        deg = s[len(digits):]
        w = self.tw(digits, font)
        if deg:
            w += self.px(4) + self.tw(deg, self.fonts.get(int(font.size * 0.46), True))
        return w

    def _centered_weather(self, y: int, weather, cx: float, g: dict) -> int:
        """日历关掉时的退路：天气行居中单栏（报头没有左栏可以挂靠）。"""
        desc = clean_text(weather.get("desc", ""), 12)
        tf = self.fd(self.P_FS_TEMP)
        df = self.f(self.P_FS_DESC)
        temp = f"{weather.get('temp', '')}°"
        icon_w = int(self.px(self.P_FS_TEMP) * self.P_ICON_RATIO)
        line_w = (icon_w + self.px(26)
                  + self.tw(temp.rstrip("°"), tf) + self.px(4)
                  + self.tw("°", self.fonts.get(int(tf.size * 0.46), True))
                  + self.px(26) + self.tw(desc, df))
        x = cx - line_w / 2
        self.icon(x + icon_w / 2, y + self.px(self.P_FS_TEMP) * 0.40, icon_w,
                  self.night_kind(weather.get("icon"), weather), fill=INK_SOFT)
        x += icon_w + self.px(26)
        x += self._big_num(x, y, y + self.px(self.P_FS_TEMP), temp, tf) + self.px(26)
        self._ink_lt(x, y + self.px(self.P_FS_TEMP) * 0.36,
                     y + self.px(self.P_FS_TEMP), desc, df, INK_SOFT)
        return y + self.px(self.P_FS_TEMP) + g["d6"]

    def _stamp(self, x, y, s, font, box=INK, txt=255) -> int:
        """反白强调块。全屏最多两个，多了就廉价。返回占用宽。"""
        pad = self.px(12)
        w = self.tw(s, font)
        h = self.lh(font)
        self.d.rectangle([x, y, x + w + pad * 2, y + h + pad], fill=box)
        self.text((x + pad, y + pad / 2), s, font, txt)
        return w + pad * 2

    def _poster_body_height(self, cal, weather, quotes, funds) -> int:
        """在 8×8 草稿上空画一遍量高度（同 clock_sprite 的换画布手法）。
        文字量宽和画布大小无关，所以量出来的就是真高度；比解析式堆 px 可靠。"""
        saved = (self.img, self.d)
        scratch = Image.new("L", (8, 8), 255)
        self.img, self.d = scratch, ImageDraw.Draw(scratch)
        try:
            return self._poster_body(0, cal, weather, quotes, funds)
        finally:
            self.img, self.d = saved

    def _poster_body(self, y: int, cal, weather, quotes, funds) -> int:
        M = self.px(self.P_MARGIN)
        X1 = self.w - M
        CW = X1 - M
        cx = self.w / 2
        g = self.PG

        if cal:
            place = clean_text(self.cfg.get("location.name", ""), 20)
            line = " · ".join(t for t in (
                f"{cal.solar_year} 年 {cal.solar_month} 月", cal.weekday) if t)
            lm = re.match(r"^(.*月)(.+)$", cal.lunar_text or "")
            tag = lm.group(1) if lm else ""
            lday = (lm.group(2) + "日") if lm else (cal.lunar_text or "")
            if tag:
                twd = self._ink_lt(M, y, y + self.px(self.P_FS_TAG), tag,
                                   self.fd(self.P_FS_TAG), INK)
                self._ink_lt(M + twd + self.px(20), y + self.px(4),
                             y + self.px(self.P_FS_TAG), line,
                             self.f(self.P_FS_LINE), GRAY)
            else:
                self._ink_lt(M, y, y + self.px(self.P_FS_TAG), line,
                             self.f(self.P_FS_LINE), GRAY)
            y += self.px(self.P_FS_TAG) + self.px(10)

            lf, lf2 = self.fd(self.P_FS_DAY), self.fd(self.P_FS_LUNAR_DAY)
            day_s = str(cal.solar_day)
            lw = max(self.tw(day_s, lf), self.tw(lday, lf2))
            self._ink_lt(M, y, y + self.px(self.P_FS_DAY), day_s, lf, INK)
            ly = y + self.px(self.P_FS_DAY) + g["d2"]
            self._ink_lt(M, ly, ly + self.px(self.P_FS_LUNAR_DAY), lday, lf2, INK)
            lh = self.px(self.P_FS_DAY) + g["d2"] + self.px(self.P_FS_LUNAR_DAY)

            x0 = M + lw + self.px(40)
            vx = M + lw + self.px(20)
            vt = max(1, self.px(3))
            self.d.rectangle([vx, y + self.px(8), vx + vt - 1, y + lh - self.px(8)],
                             fill=GRAY_LIGHT)
            if weather:
                rw = X1 - x0
                tf = self.fd(self.P_FS_TEMP)
                temp = f"{weather.get('temp', '')}°"
                iw = int(self.px(self.P_FS_TEMP) * self.P_ICON_RATIO)
                tw_temp = self._big_num_width(temp, tf)
                if iw + self.px(24) + tw_temp > rw:
                    iw = max(self.px(60), rw - self.px(24) - tw_temp)
                stack = (self.px(self.P_FS_TEMP) + self.px(10)
                         + self.px(self.P_FS_DESC) + self.px(8)
                         + self.px(self.P_FS_PLACE) + self.px(8))
                cy = y + (lh - stack) // 2
                self.icon(x0 + iw / 2, cy + self.px(self.P_FS_TEMP) * 0.40, iw,
                          self.night_kind(weather.get("icon"), weather),
                          fill=INK_SOFT)
                self._big_num(x0 + iw + self.px(24), cy,
                              cy + self.px(self.P_FS_TEMP), temp, tf)
                dy = cy + self.px(self.P_FS_TEMP) + self.px(10)
                self._ink_lt(x0, dy, dy + self.px(self.P_FS_DESC) + self.px(8),
                             clean_text(weather.get("desc", ""), 12),
                             self.f(self.P_FS_DESC), INK_SOFT)
                py = dy + self.px(self.P_FS_DESC) + self.px(8)
                self._ink_lt(x0, py, py + self.px(self.P_FS_PLACE) + self.px(8),
                             place, self.f(self.P_FS_PLACE), GRAY)
            y += lh + self.px(8)
            bits = []
            if self.cfg.get("calendar.show_ganzhi", True):
                gan = (f"{cal.ganzhi_year}年 属{cal.shengxiao} · "
                       f"{cal.ganzhi_month}月 {cal.ganzhi_day}日")
                if cal.lunar_size:
                    gan += f" · {cal.lunar_size}月"
                bits.append(gan)
            bits.append(f"{cal.term_current} 第 {cal.term_current_days} 天"
                        + (f" · 距{cal.term_next} {cal.term_next_days} 天"
                           if cal.term_next_days > 0 else ""))
            self._ink_lt(M, y, y + self.px(36),
                         self.clip_text(" · ".join(bits), self.f(self.P_FS_META), CW),
                         self.f(self.P_FS_META), GRAY)
            y += self.px(36) + g["d5"]
        elif weather:
            y = self._centered_weather(y, weather, cx, g)

        if weather:
            warns = self.weather_warning()
            if warns:
                font = self.f(self.P_FS_WARN)
                widest = max(self.tw(w.get("title", ""), font) for w in warns[:2])
                bw = self.tw("预警", self.f(24)) + self.px(24)
                x0 = cx - (bw + self.px(14) + widest) / 2
                for i, w in enumerate(warns[:2]):
                    used = (self._stamp(x0, y - self.px(5), "预警", self.f(24))
                            if i == 0 else bw)
                    tx = x0 + used + self.px(14)
                    self.text((tx, y), self.clip_text(w.get("title", ""), font,
                                                      X1 - tx), font, INK)
                    y += self.px(self.P_FS_WARN) + self.px(12)
                y += g["d7"]
            days = int(self.cfg.get("weather.days", 4) or 4)
            fc = list(weather.get("forecast") or [])[:days]
            if fc:
                fw = self.px(236)
                x0 = cx - fw * len(fc) / 2
                r1 = self.px(self.P_FS_FC_ICON) + self.px(8)
                r2 = r1 + self.px(self.P_FS_FC) + self.px(12)
                r3 = r2 + self.px(self.P_FS_FC) + self.px(12)
                ffont = self.f(self.P_FS_FC)
                for i, day in enumerate(fc):
                    gx = x0 + i * fw + fw / 2
                    self.icon(gx, y + self.px(self.P_FS_FC_ICON) / 2,
                              self.px(self.P_FS_FC_ICON),
                              self.night_kind(day.get("icon"), weather), fill=INK_SOFT)
                    self.text_centered_ink(gx, y + r1,
                                           y + r1 + self.px(self.P_FS_FC) + self.px(8),
                                           clean_text(day.get("label", ""), 4),
                                           ffont, GRAY)
                    self.text_centered_ink(gx, y + r2,
                                           y + r2 + self.px(self.P_FS_FC) + self.px(8),
                                           self.clip_text(clean_text(day.get("desc", ""), 8),
                                                          ffont, fw - self.px(8)),
                                           ffont, INK)
                    self.text_centered_ink(gx, y + r3,
                                           y + r3 + self.px(self.P_FS_FC) + self.px(8),
                                           f"{day.get('high', '')}° / {day.get('low', '')}°",
                                           ffont, INK_SOFT)
                y += r3 + self.px(self.P_FS_FC) + self.px(8) + g["d8"]

        entries = list(quotes or []) + list(funds or [])
        if entries:
            self.rule(y, thickness=self.px(3), color=GRAY_LIGHT, x0=M, x1=X1)
            y += g["d9"]
            cw3 = CW / 3
            name_font = self.f(self.P_FS_Q_NAME)
            price_font = self.f(self.P_FS_Q_PRICE, bold=True)
            pct_font = self.f(self.P_FS_Q_PCT)
            marker = self.px(14)
            for i, entry in enumerate(entries[:3]):
                qx = M + i * cw3 + cw3 / 2
                self.text_centered_ink(qx, y,
                                       y + self.px(self.P_FS_Q_NAME) + self.px(8),
                                       self.clip_text(clean_text(entry.get("name", ""), 12),
                                                      name_font, cw3 - self.px(8)),
                                       name_font, INK_SOFT)
                price = entry.get("price")
                pct = entry.get("pct")
                py = (y + self.px(self.P_FS_Q_NAME) + self.px(12)
                      + self.px(self.P_FS_Q_PRICE) + self.px(16))
                self.text_centered_ink(qx, y + self.px(self.P_FS_Q_NAME) + self.px(12),
                                       y + self.px(self.P_FS_Q_NAME) + self.px(12)
                                       + self.px(self.P_FS_Q_PRICE) + self.px(6),
                                       self._fmt_price(price, entry.get("is_fund"),
                                                       entry.get("is_crypto")),
                                       price_font,
                                       INK if price is not None else GRAY_LIGHT,
                                       strong=price is not None)
                if pct is None:
                    pct_txt, pct_color, filled = "--", GRAY_LIGHT, None
                elif pct >= 0:
                    pct_txt, pct_color, filled = f"+{pct:.2f}%", INK, True
                else:
                    pct_txt, pct_color, filled = f"{pct:.2f}%", GRAY, False
                pw = self.tw(pct_txt, pct_font)
                if filled is True:
                    self.d.rectangle([qx - pw / 2 - marker - self.px(10),
                                      py + self.px(8),
                                      qx - pw / 2 - self.px(10),
                                      py + self.px(8) + marker], fill=INK)
                elif filled is False:
                    self.d.rectangle([qx - pw / 2 - marker - self.px(10),
                                      py + self.px(8),
                                      qx - pw / 2 - self.px(10),
                                      py + self.px(8) + marker],
                                     outline=GRAY, width=max(1, self.px(2)))
                self.text_centered_ink(qx + self.px(6), py,
                                       py + self.px(self.P_FS_Q_PCT) + self.px(8),
                                       pct_txt, pct_font, pct_color,
                                       strong=filled is True)
            y += (self.px(self.P_FS_Q_NAME) + self.px(12) + self.px(self.P_FS_Q_PRICE)
                  + self.px(16) + self.px(self.P_FS_Q_PCT) + self.px(8))
        return y

    def _poster_footer(self, y: int, M: int, X1: int) -> None:
        self.rule(y, thickness=max(1, self.px(2)), color=GRAY_LIGHT, x0=M, x1=X1)
        font = self.f(self.P_FS_FOOT)
        model = str(self.cfg.get("device.model", ""))
        label = DEVICE_LABELS.get(model.lower(), model)
        weather = self.data.get("weather") or {}
        mid = [f"Kindle {label}"]
        if weather.get("source"):
            mid.append(f"天气 {weather['source']}")
        if self.data.get("quotes") and self.cfg.get("quotes.enabled", True):
            mid.append("行情 腾讯")
        self.text((M, y + self.px(14)),
                  f"更新 {self.generated_at.strftime('%m-%d %H:%M')}", font, GRAY)
        self.text((X1, y + self.px(14)), "实心=涨 空心=跌", font, GRAY, anchor="ra")
        self.text((M, y + self.px(14) + self.lh(font)),
                  self.clip_text(" · ".join(mid), font, X1 - M), font, GRAY)

    def battery_region(self) -> tuple[int, int, int, int]:
        """电量精灵图在成品图里的绝对矩形。和时钟区同一个道理：
        出图、生成精灵图、Kindle 贴图三方共用这一个函数。"""
        l, t, r, b = POSTER_BATTERY
        return (self.px(l), self.px(t), self.px(r), self.px(b))

    def _draw_battery(self, level: int, ox: int = 0, oy: int = 0) -> None:
        """电量样式 B：百分比大字 + 一根进度条（设计稿 poster3.html 定稿）。

        云端**不会**调它（云端摸不到电池）—— 成品图里这块永远留白，
        由 Kindle 贴精灵图。它存在的唯一用途是生成精灵图和像素级自检
        （贴回去 vs 直出必须逐像素一致，同 make_clock_assets 的判据）。
        ≤20% 时数字左边多一个灰字「请充电」，精灵图按档位自带，设备端不用判。
        """
        l, t, _r, b = self.battery_region()
        x1, y0, x2, y1 = l - ox, t - oy, self.px(POSTER_BATTERY[2]) - ox, b - oy
        level = max(0, min(100, int(level)))
        gf = self.f(38, bold=True)
        txt = f"{level}%"
        self.text((x2, y0 + self.px(2)), txt, gf, INK, anchor="rt")
        if level <= 20:
            hf = self.f(24)
            hint = "请充电"
            hx = x2 - self.tw(txt, gf) - self.px(10) - self.tw(hint, hf)
            self.text((hx, y0 + self.px(10)), hint, hf, GRAY, anchor="lt")
        by = y1 - self.px(14)
        self.d.rectangle([x1, by, x2, by + self.px(10) - 1], fill=GRAY_LIGHT)
        self.d.rectangle([x1, by, x1 + (x2 - x1) * level // 100, by + self.px(10) - 1],
                         fill=INK)

    def battery_sprite(self, level: int) -> Image.Image:
        """把某一档电量画到「刚好等于电量区」的画布上（同 clock_sprite）。"""
        l, t, r, b = self.battery_region()
        canvas = Image.new("L", (r - l, b - t), 255)
        saved = (self.img, self.d)
        self.img, self.d = canvas, ImageDraw.Draw(canvas)
        try:
            self._draw_battery(level, ox=l, oy=t)
        finally:
            self.img, self.d = saved
        return canvas

    def _render_poster(self, weather, quotes, funds, cal) -> None:
        # 时钟功能已砍（2026-09）：poster 不再给时钟留白，也不画时钟。
        # 右上角这块矩形留给**电量精灵图**——电量只有设备自己知道，云端画不了，
        # 和当初时钟同一条路：Kindle 按档位贴图。矩形固定，换电量样式不用改版面。
        M = self.px(self.P_MARGIN)
        X1 = self.w - M
        foot_top = self.h - self.px(44) - self.px(86)
        # 角标和电量同一行起画，真正贴到左上角；角标在左半页，结构上碰不到电量矩形
        top = self.px(POSTER_BATTERY[1])
        extra = foot_top - top - self._poster_body_height(cal, weather, quotes, funds)
        y = top + max(0, extra // 2)
        self.block_boxes = {}
        y0 = y
        y = self._poster_body(y, cal, weather, quotes, funds)
        if y > y0:
            self.block_boxes["calendar"] = (M, y0, X1, y)
        self.slack = int(extra)   # 居中版式看总余量：字再涨也是从上下留白里扣，
        #                          # 真正越界的条件是 extra < 0，不是底边那半截小
        if self.slack < -self.px(4):
            self._notes.append(f"「帖」版超了 {-self.slack}px：内容比设计稿长，"
                               f"先关一块或调小 P_FS_*")
        if not any((weather, quotes, funds)):
            self._draw_empty_notice(y)
        self._poster_footer(foot_top, M, X1)

    # =====================================================================
    #  「带」版式（style.layout: c1）：四条发丝黑线分带，2026-09 定稿 C1
    #  带一 日期+历法节日 / 带二 当前天气+数据格+预警 / 带三 预报四联大图标
    #  / 带四 行情三栏。富余高度摊进三个带间，页面永远填到页脚。
    # =====================================================================

    @staticmethod
    def _spread(extra: int, n: int) -> list:
        q = extra // n
        return [q + (extra - q * n) if i == 0 else q for i in range(n)]

    def _c1_kicker(self, x: int, y: int, s: str, gi: int) -> int:
        """字距拉开的小标签（编辑排印的 kicker 血统）。返回下一行起点。"""
        f = self.f(self.C1["fs"]["kick"])
        cx = x
        for ch in s:
            self.text((cx, y), ch, f, GRAY)
            cx += self.tw(ch, f) + self.px(6)
        return y + self.px(32) + gi

    @staticmethod
    def _c1_box(fs_size: int) -> int:
        """文字行盒 = 字号×1.5 再取整到 8（基准网格）。"""
        return (int(fs_size * 1.5) + 4) // 8 * 8

    def _c1_body(self, y: int, cal, weather, quotes, funds, extra: int = 0,
                 plan: dict | None = None) -> int:
        px = self.px
        P = self.C1
        fs = P["fs"]
        plan = plan or {}
        gi, gb, gband = px(P["gi"]), px(P["gb"]), px(P["gband"])
        M, X1 = px(P["margin"]), self.w - px(P["margin"])
        CW = X1 - M
        e = self._spread(extra, 3)
        weather = weather or {}

        def box(key: str) -> int:
            return px(self._c1_box(fs[key]))

        # -- 带一：日期（左）+ 历法/节日（右）
        meta = " · ".join(t for t in (f"{cal.solar_year} 年 {cal.solar_month} 月",
                                      cal.weekday,
                                      clean_text(self.cfg.get("location.name", ""), 20)) if t)
        lunar_line = " · ".join(t for t in (f"{cal.lunar_month}{cal.lunar_day}",
                                            cal.ganzhi_year,
                                            f"{cal.shengxiao}年") if t)
        fest = list(cal.festivals or ())[:1]
        notes = [t for t in (cal.statutory_countdown if plan.get("countdown", True) else "",
                             cal.tiaoxiu if plan.get("tiaoxiu", True) else "") if t]
        rh = box("meta") + box("lunar") + 2 * gi \
            + (box("fest") + gi if fest else 0) + (box("note") + gi) * len(notes)
        band1 = max(px(P["day"]), rh - gi)
        day_f = self.fd(P["day"])
        self._big_num(M, y, y + px(P["day"]), str(cal.solar_day), day_f)
        dw = self._big_num_width(str(cal.solar_day), day_f)
        rx = M + dw + px(40)
        self.d.rectangle([M + dw + px(20), y, M + dw + px(21), y + band1 - 1],
                         fill=GRAY_LIGHT)
        ry = y
        # 这行是日期带里唯一的"小字说明"，130 灰在墨色屏上偏淡看不清（用户反馈），
        # 用 70 灰：比正文黑浅一档，但远看也清楚。
        self._ink_lt(rx, ry, ry + box("meta"), meta, self.f(fs["meta"]), INK_SOFT)
        ry += box("meta") + gi
        self._ink_lt(rx, ry, ry + box("lunar"), lunar_line,
                     self.f(fs["lunar"], True), INK)
        ry += box("lunar") + gi
        for name in fest:
            my = ry + box("fest") // 2
            self.d.rectangle([rx, my - px(7), rx + px(13), my + px(6)], fill=INK)
            self._ink_lt(rx + px(24), ry, ry + box("fest"), name,
                         self.f(fs["fest"], True), INK)
            ry += box("fest") + gi
        for line in notes:
            self._ink_lt(rx, ry, ry + box("note"), line, self.f(fs["note"]), INK)
            ry += box("note") + gi
        y += band1 + gband // 2
        self.rule(y, thickness=max(1, px(2)), color=INK, x0=M, x1=X1)
        y += gband // 2 + e[0]

        # -- 带二：当前天气（左）+ 数据格 2×3（右）+ 预警（通栏）
        y = self._c1_kicker(M, y, "天气 WEATHER", gi)
        icon_sz, temp_sz = px(P["icon"]), px(P["temp"])
        self.icon(M + icon_sz / 2, y + temp_sz * 0.42, icon_sz,
                  self.night_kind(weather.get("icon"), weather), fill=INK_SOFT)
        tx = M + icon_sz + px(24)
        temp = f"{weather.get('temp')}°" if weather.get("temp") not in (None, "") else "--"
        tx += self._big_num(tx, y, y + temp_sz, temp, self.fd(P["temp"])) + px(24)
        show_cells = plan.get("cells", True)
        cx0 = M + px(600)
        pitch = (X1 - cx0) // 2
        self._ink_lt(tx, y + temp_sz * 0.42, y + temp_sz * 0.42 + box("desc"),
                     self.clip_text(clean_text(weather.get("desc", ""), 12),
                                    self.f(fs["desc"]), max(px(60), cx0 - px(24) - tx)),
                     self.f(fs["desc"]), INK)
        gh = 3 * box("cell") + 2 * gi
        zone = max(temp_sz, gh) if show_cells else temp_sz
        gy0 = y + (zone - gh) // 2
        if show_cells:
            self.d.rectangle([cx0 - px(28), y, cx0 - px(27), y + zone - 1],
                             fill=GRAY_LIGHT)
            lf = self.f(fs["cell"])
            for i, (label, value, sub) in enumerate(self.weather_grid(weather)[:6]):
                gx = cx0 + (i % 2) * pitch
                gy = gy0 + (i // 2) * (box("cell") + gi)
                lw = self._ink_lt(gx, gy, gy + box("cell"), label, lf, GRAY)
                v = value + (f" {sub}" if sub else "")
                # 字号档放大后「空气 中度 154」这种最宽格会顶到邻列：数值按需缩一号
                vf = self._fit_font(v, fs["cellv"], pitch - lw - px(14),
                                    floor=int(fs["cellv"] * 0.8))
                self._ink_lt(gx + lw + px(10), gy, gy + box("cell"),
                             self.clip_text(v, vf, pitch - lw - px(10)), vf, INK)
        y += zone + gi
        wf = self.f(fs["warn"])
        wbox = box("warn")
        # 预警条目是 dict（title/level/…），以前直接把 dict 送进 clip_text：
        # 没预警时这行不执行所以一直"正常"，2026-09-26 杭州出了高温预警，云端
        # 每次出图都在 TypeError 上炸掉 —— 一有预警屏幕就停在旧图上，正好是最反着来。
        for t in [w.get("title", "") for w in self.weather_warning()
                  [:2 if plan.get("warn2", True) else 1]]:
            self.d.rectangle([M, y + wbox // 2 - px(6), M + px(12),
                              y + wbox // 2 + px(6)], fill=INK)
            self._ink_lt(M + px(24), y, y + wbox,
                         self.clip_text(t, wf, X1 - M - px(24)), wf, INK)
            y += wbox + gi
        y += gb - gi + gband // 2
        self.rule(y, thickness=max(1, px(2)), color=INK, x0=M, x1=X1)
        y += gband // 2 + e[1]

        # -- 带三：预报四联，大图标（云体对齐）
        y = self._c1_kicker(M, y, "预报 FORECAST", gi)
        cw4 = CW // 4
        ff = self.f(fs["fc"])
        fb = box("fc")
        fc_sz = px(P["fc_icon"])
        show_desc = plan.get("fc_desc", True)
        # 云体对齐会把多云/雷阵雨的云往上抬 0.3R，太阳芒因此伸出图标盒顶：
        # 整排图标下让 12px，抬头字和图标之间才留得住安全距（用户 2026-09-26 反馈）。
        icy = y + fc_sz // 2 + px(12)
        for i, day in enumerate((weather.get("forecast") or [])[:4]):
            gx = M + i * cw4 + cw4 // 2
            today = i == 0
            self.icon(gx, icy, fc_sz, day.get("icon", "cloud"),
                      fill=INK if today else INK_SOFT, align_cloud=True)
            ly = y + fc_sz + px(20)
            self.text_centered_ink(gx, ly, ly + fb, day.get("label", ""),
                                   self.f(fs["fc"], today), INK if today else GRAY,
                                   strong=False)
            hy = ly + fb + gi
            if show_desc:
                self.text_centered_ink(gx, hy, hy + fb,
                                       self.clip_text(clean_text(day.get("desc", ""), 6),
                                                      ff, cw4 - px(12)), ff, INK,
                                       strong=False)
                hy += fb + gi
            self.text_centered_ink(gx, hy, hy + fb,
                                   f"{day.get('high')}° / {day.get('low')}°", ff, GRAY,
                                   strong=False)
        y += fc_sz + px(20) + (3 * fb + 2 * gi if show_desc else 2 * fb + gi) \
            + gb + gband // 2
        self.rule(y, thickness=max(1, px(2)), color=INK, x0=M, x1=X1)
        y += gband // 2 + e[2]

        # -- 带四：行情三栏
        y = self._c1_kicker(M, y, "行情 MARKETS", gi)
        cw3 = CW // 3
        pf = self.f(fs["m_price"], True)
        nbox, pbox = box("m_name"), box("m_price")
        entries = (list(quotes or []) + list(funds or []))[:3 if plan.get("q3", True) else 2]
        py = y
        for i, entry in enumerate(entries):
            gx = M + i * cw3
            py = y + nbox + gi
            if i:
                self.d.rectangle([gx - px(12), y - px(8), gx - px(11), py + pbox - 1],
                                 fill=GRAY_LIGHT)
            self._ink_lt(gx, y, y + nbox,
                         self.clip_text(clean_text(entry.get("name", ""), 12),
                                        self.f(fs["m_name"]), cw3 - px(16)),
                         self.f(fs["m_name"]), GRAY)
            price = self._fmt_price(entry.get("price"), entry.get("is_fund"),
                                    entry.get("is_crypto"))
            self._ink_lt(gx, py, py + pbox, price, pf, INK)
            pct = entry.get("pct")
            txt = "--" if pct is None else f"{pct:+.2f}%"
            pw = self.tw(price, pf)
            sy = py + pbox // 2 - px(6)
            if pct is None:
                pass
            elif pct >= 0:
                self.d.rectangle([gx + pw + px(14), sy, gx + pw + px(26), sy + px(12)],
                                 fill=INK)
            else:
                self.d.rectangle([gx + pw + px(14), sy, gx + pw + px(26), sy + px(12)],
                                 outline=GRAY, width=max(1, px(2)))
            self._ink_lt(gx + pw + px(34), py + (pbox - box("m_pct")) // 2,
                         py + (pbox + box("m_pct")) // 2, txt, self.f(fs["m_pct"]), INK)
        return py + pbox

    def _c1_body_height(self, cal, weather, quotes, funds, plan=None) -> int:
        """在 8×8 草稿上空画一遍量高度（同 _poster_body_height 的换画布手法）。"""
        saved = (self.img, self.d)
        scratch = Image.new("L", (8, 8), 255)
        self.img, self.d = scratch, ImageDraw.Draw(scratch)
        try:
            return self._c1_body(0, cal, weather, quotes, funds, plan=plan)
        finally:
            self.img, self.d = saved

    #: 内容太长时的收行阶梯，按简报的截断优先级一级一级让位：
    #: 调休 → 倒计时 → 第二条预警 → 数据格 → 预报天气词 → 第三个指数。
    #: 主日期 / 气温 / 当天节日永远不删。
    _C1_PLANS = [
        {},
        {"tiaoxiu": False},
        {"tiaoxiu": False, "countdown": False},
        {"tiaoxiu": False, "countdown": False, "warn2": False},
        {"tiaoxiu": False, "countdown": False, "warn2": False, "cells": False},
        {"tiaoxiu": False, "countdown": False, "warn2": False, "cells": False,
         "fc_desc": False},
        {"tiaoxiu": False, "countdown": False, "warn2": False, "cells": False,
         "fc_desc": False, "q3": False},
    ]

    _C1_DROPPED_LABELS = {"tiaoxiu": "调休行", "countdown": "倒计时行",
                          "warn2": "第二条预警", "cells": "数据格",
                          "fc_desc": "预报天气词", "q3": "第三个指数"}

    def _render_c1(self, weather, quotes, funds, cal) -> None:
        px = self.px
        M, X1 = px(self.C1["margin"]), self.w - px(self.C1["margin"])
        foot_top = self.h - px(44) - px(86)
        # 电量精灵图占右上角 (814,40)-(1016,96)：正文从它下沿再留 16 起画，
        # 否则第一带的 meta 行会伸进精灵图矩形里。
        top = px(POSTER_BATTERY[3]) + px(16)
        budget = (foot_top - px(80)) - top
        plan = self._C1_PLANS[-1]
        for cand in self._C1_PLANS:
            if budget - self._c1_body_height(cal, weather, quotes, funds, cand) >= px(40):
                plan = cand
                break
        extra = budget - self._c1_body_height(cal, weather, quotes, funds, plan)
        self.slack = int(extra)
        if self.slack < px(40):
            self._notes.append(f"「带」版收行到最后仍只有 {self.slack}px 余量："
                               f"内容比设计稿长，调小 C1 字号档")
        if plan:
            self._notes.append("「带」版内容偏长，已按优先级收起："
                               + "、".join(self._C1_DROPPED_LABELS[k]
                                           for k, v in plan.items() if v is False))
        self.block_boxes = {}
        y = self._c1_body(top, cal, weather, quotes, funds, extra, plan)
        self.block_boxes["calendar"] = (M, top, X1, y)
        if not any((weather, quotes, funds)):
            self._draw_empty_notice(y)
        self._poster_footer(foot_top, M, X1)
    # =====================================================================
    #  主流程
    # =====================================================================

    #: 可以重排的区块，元组顺序就是默认顺序
    BLOCKS = ("calendar", "weather", "digest", "quotes")

    #: 区块给人看的名字
    BLOCK_LABELS = {"calendar": "日历", "weather": "天气",
                    "digest": "速览", "quotes": "行情"}

    def layout_order(self) -> list[str]:
        """区块从上到下的顺序，来自 `layout.order`。

        写错名、漏块、重复都在这里就地修好，别把错误留到绘制时才炸 ——
        漏掉一块比顺序排丑严重得多，所以缺的补到末尾、多的丢掉。
        """
        raw = self.cfg.get("layout.order", None) or list(self.BLOCKS)
        if isinstance(raw, str):
            raw = [raw]
        order = [k for k in raw if k in self.BLOCKS]
        order += [k for k in self.BLOCKS if k not in order]
        return order

    def _digest_block(self, top: int, digest: dict, footer_h: int) -> int:
        """速览：先按剩余空间裁条数，再画。

        裁条数必须在**知道上面已经吃掉多少高度之后**才能做，所以它不能像
        别的块一样直接进主循环 —— 区块一旦允许重排，速览排第几会改变它能用
        的高度，写死在 render 里就成了"换顺序后速览莫名少两条"的怪事。
        """
        items = list(digest.get("items") or [])
        if not items:
            return top
        usable = self.h - top - footer_h - self.px(24)
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
        return self.draw_digest(top, dict(digest, items=items[:chosen]), extra_gap)

    def _draw_empty_notice(self, y: int) -> None:
        """所有区块都没数据时的一屏提示。"""
        cy = y + self.px(180)
        self.icon(self.w / 2, cy, self.px(96), "fog", fill=GRAY_LIGHT)
        title_font = self.f(self.FS_SECTION, bold=True)
        body_font = self.f(self.FS_ITEM_SUM)
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

        if self.layout == "poster":
            # 帖版不摊富余：回收的空间就是底部留白
            self._cal_extra = self._weather_extra = self._quotes_extra = 0
            self._render_poster(weather, quotes, funds, cal)
        elif self.layout == "c1":
            self._cal_extra = self._weather_extra = self._quotes_extra = 0
            self._render_c1(weather, quotes, funds, cal)
        elif self.layout == "arc":
            # 信息终端：整版由 layout_arc 自己画（它带自己的图标和页脚），
            # 这里只交给它正文起点和页脚线，好让它和别的版式共用同一套留白。
            from .layout_arc import render_arc
            self._cal_extra = self._weather_extra = self._quotes_extra = 0
            render_arc(self, self.px(POSTER_BATTERY[3]) + self.px(16),
                       self.h - self.px(44) - self.px(86))
        else:
            top_gap = self.px(TOP_GAP)
            footer_h = self.px(58)
            order = self.layout_order()

            # 每块先按"不摊富余"量一次基准高度，再把富余摊进各块的内边距：
            # 堆在底部会看起来像"没排完"，摊进三块才像"设计过的"。
            self._cal_extra = self._weather_extra = self._quotes_extra = 0
            heights = {
                "calendar": self.calendar_height(cal),
                "weather": self.weather_height(weather),
                "digest": self.digest_height(digest.get("items") or []),
                "quotes": self.quotes_height(quotes, funds),
            }
            slack = self.h - top_gap - footer_h - sum(heights[k] for k in order)
            # 报给外面：调参台和自检工具都要这个数。它们自己算不出来 —— 一旦 render()
            # 把富余摊进各块的内边距，"画完之后再量一遍"得到的永远是同一个零头。
            self.slack = int(slack)
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

            # 从上往下按 layout.order 走。每块画完返回自己的下边缘，交给下一块当 top。
            y = top_gap
            self.block_boxes = {}
            for key in order:
                y0 = y
                if key == "calendar":
                    y = self.draw_calendar(y, cal)
                elif key == "weather":
                    y = self.draw_weather(y, weather)
                elif key == "digest":
                    y = self._digest_block(y, digest, footer_h)
                else:
                    y = self.draw_quotes(y, quotes, funds)
                if y > y0:
                    self.block_boxes[key] = (self.margin, y0, self.w - self.margin, y)

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
