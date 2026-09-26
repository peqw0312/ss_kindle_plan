"""跨平台中文字体解析。

这是整个管线里最容易翻车的一环：Linux(GitHub Actions) / Windows(本地预览) /
macOS 三边的中文字体路径完全不一样，而且 Noto CJK 是 .ttc 字体集合——
直接用 index=0 有可能拿到日文字形（"直" "骨" "直" 等汉字写法与中文不同）。

策略：显式候选路径 -> 逐 index 探测并给字族名打分 -> fc-match 系统查询 ->
 递归扫描字体目录 -> 最后退回英文字体（会打警告，中文会变豆腐块）。
"""

from __future__ import annotations

import functools
import glob
import os
import subprocess
from dataclasses import dataclass

from PIL import ImageFont

_REGULAR_CANDIDATES = [
    # --- Windows ---
    r"C:\Windows\Fonts\msyh.ttc",          # 微软雅黑
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",        # 黑体
    r"C:\Windows\Fonts\simsun.ttc",        # 宋体
    r"C:\Windows\Fonts\Deng.ttf",          # 等线
    r"C:\Windows\Fonts\msyhl.ttc",
    # --- macOS ---
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # --- Debian / Ubuntu (GitHub Actions) ---
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-VF.otf.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",   # 最后的英文兜底
]

_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyhbd.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-VF.otf.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansSC-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansSC-Bold.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

_FONT_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/Library/Fonts"),
    "/Library/Fonts",
    r"C:\Windows\Fonts",
]

_SCAN_PATTERNS = ["*CJK*", "*NotoSansSC*", "*wqy*", "*YaHei*", "*PingFang*", "*SourceHanSans*"]

#: 显示字（只给日期/温度这类超大数字用）的候选。思源宋体 = Noto Serif CJK 同一套字形：
#: 本机装的是 Source Han Serif SC Heavy，Debian/Ubuntu 的 fonts-noto-cjk 自带
#: NotoSerifCJK 的 Regular/Bold。.ttc 里逐 index 挑 face 的逻辑复用 _resolve()。
_DISPLAY_CANDIDATES = [
    r"C:\Windows\Fonts\Source Han Serif SC Heavy (TrueType).ttf",
    r"C:\Windows\Fonts\Source Han Serif SC Bold (TrueType).ttf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
]


@dataclass(frozen=True)
class FontSpec:
    path: str
    index: int = 0
    name: str = ""

    @property
    def is_cjk(self) -> bool:
        """粗判是否含中文字形。用于在渲染时决定要不要警告。"""
        blob = (self.path + " " + self.name).lower()
        return not ("dejavu" in blob or "liberation" in blob or "arial" in blob and "unicode" not in blob)


def _score_family(family: str, style: str, want_bold: bool) -> int:
    fam, sty = family.lower(), style.lower()
    score = 0
    if "sc" in fam:              # 简体中文优先
        score += 20
    if "mono" in fam:            # 等宽版字形偏窄，不想要
        score -= 8
    if "ui" in fam:              # 微软雅黑的 UI 变体
        score -= 4
    if "jp" in fam or "kr" in fam or "tc" in fam:
        score -= 6
    if want_bold:
        score += 6 if ("bold" in sty or "bold" in fam or "heavy" in sty) else -6
    else:
        score += 6 if "regular" in sty or "normal" in sty or not sty else 0
        score -= 4 if "bold" in sty else 0
    return score


@functools.lru_cache(maxsize=64)
def _resolve(path: str, want_bold: bool) -> FontSpec:
    """在单个字体文件里挑出最合适的 face（.ttc 需要逐 index 探测）。"""
    best: FontSpec | None = None
    best_score = -10**9
    for index in range(0, 12):
        try:
            font = ImageFont.truetype(path, 24, index=index)
            family, style = font.getname()
        except Exception:
            break                     # index 越界即停止
        score = _score_family(family or "", style or "", want_bold)
        if score > best_score:
            best_score = score
            best = FontSpec(path, index, f"{family} {style}".strip())
    if best is None:
        return FontSpec(path, 0, os.path.basename(path))
    return best


def _fc_match(want_bold: bool) -> str | None:
    """用 fontconfig 问系统要一个中文字体路径，Linux/macOS 上很省事。"""
    query = "Noto Sans CJK SC:weight=bold" if want_bold else "Noto Sans CJK SC"
    for cmd in (
        ["fc-match", "-f", "%{file}", query],
        ["fc-match", "-f", "%{file}", "sans-serif:lang=zh-cn"],
    ):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            path = (res.stdout or "").strip()
            if path and os.path.exists(path):
                return path
        except Exception:
            continue
    return None


def _scan_dirs() -> list[str]:
    found: list[str] = []
    for root in _FONT_DIRS:
        if not os.path.isdir(root):
            continue
        for pattern in _SCAN_PATTERNS:
            found.extend(glob.glob(os.path.join(root, "**", pattern), recursive=True))
    # 去重并保持顺序
    seen: set[str] = set()
    uniq = []
    for item in found:
        key = os.path.normcase(item)
        if key not in seen and os.path.isfile(item):
            seen.add(key)
            uniq.append(item)
    return uniq


#: 「信息终端」那套版式的骨架是**等宽读数**（SN/价格/读数全是一格一格的），
#: 而这一路里以前根本没有等宽角色 —— fonts.py 甚至专门排除过等宽（字形偏窄）。
#: 所以现在是"有选择地"加两个角色：mono 和 condensed。
#: 顺序都是 Windows 真货 → Linux 开源近亲 → 退回常规字体。
#: ⚠️ 近亲不是等价物：Consolas 和 DejaVu Sans Mono 的宽度、字怀都不一样，
#:   所以同一套版式在本机和云端会看着不太一样。这不是 bug，是字体的事实 ——
#:   验收要看**云端那张**，本机那张只是快。
_MONO_CANDIDATES = [
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\cour.ttf",
    "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]

_MONO_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\consolab.ttf",
    "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]

#: DIN 1451 血统的窄体工业字。云端没有 Bahnschrift（微软授权，不能塞进公开仓库），
#: 用 Roboto Condensed / DejaVu Sans Condensed 顶：同样是"窄、规整、无衬线"。
_CONDENSED_CANDIDATES = [
    r"C:\Windows\Fonts\bahnschrift.ttf",
    "/usr/share/fonts/truetype/roboto/RobotoCondensed-Regular.ttf",
    "/usr/share/fonts/truetype/roboto/condensed/RobotoCondensed-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Narrow.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
]

_CONDENSED_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\bahnschrift.ttf",
    "/usr/share/fonts/truetype/roboto/RobotoCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/roboto/condensed/RobotoCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-NarrowBold.ttf",
]


def _first_existing(paths) -> str | None:
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def _fc_query(pattern: str) -> str | None:
    """问 fontconfig 要一个**非中文**角色（等宽 / 窄体）。

    不复用 _fc_match：那个写死了查中文（Noto Sans CJK / lang=zh-cn），
    拿它查 mono 只会把中文字体还回来 —— 那种错最难发现，因为图照样出得来。
    """
    try:
        res = subprocess.run(["fc-match", "-f", "%{file}", pattern],
                             capture_output=True, text=True, timeout=10)
        path = (res.stdout or "").strip()
        return path if path and os.path.exists(path) else None
    except Exception:
        return None


def resolve_variant(env_key: str, candidates: list[str], fc_pattern: str = ""):
    """按"环境变量 > 已知路径 > fontconfig"找一个非中文角色。

    找不到返回 **None**，让调用方决定退回哪一个 —— 等宽/窄体属于"锦上添花"，
    缺了只是这套版式看着普通一点，不该让整个出图失败。
    """
    env_path = os.environ.get(env_key)
    if env_path and os.path.exists(env_path):
        return _resolve(env_path, True)
    path = _first_existing(candidates)
    if path:
        return _resolve(path, True)
    if fc_pattern:
        fc = _fc_query(fc_pattern)
        if fc:
            return _resolve(fc, True)
    return None


def resolve(want_bold: bool = False) -> FontSpec:
    """找到可用的中文字体。优先级：环境变量 > 已知路径 > fontconfig > 目录扫描。"""
    env_key = "AIINFO_FONT_BOLD" if want_bold else "AIINFO_FONT"
    env_path = os.environ.get(env_key)
    if env_path and os.path.exists(env_path):
        return _resolve(env_path, want_bold)

    candidates = _BOLD_CANDIDATES if want_bold else _REGULAR_CANDIDATES
    for path in candidates:
        if os.path.exists(path):
            return _resolve(path, want_bold)

    fc = _fc_match(want_bold)
    if fc:
        return _resolve(fc, want_bold)

    scanned = _scan_dirs()
    if scanned:
        # 粗体优先挑名字里带 Bold 的
        if want_bold:
            scanned.sort(key=lambda p: (0 if "bold" in os.path.basename(p).lower() else 1))
        return _resolve(scanned[0], want_bold)

    # 彻底没有：用 PIL 内置位图字体，中文会缺字，但至少不崩
    return FontSpec("", 0, "PIL-default")


def resolve_display() -> FontSpec | None:
    """找显示字（宋体）。找不到返回 None，由 FontBook 退回粗体黑——
    版面几何不依赖它存在，只是书卷气差一点。"""
    env_path = os.environ.get("AIINFO_FONT_DISPLAY")
    if env_path and os.path.exists(env_path):
        return _resolve(env_path, True)
    for path in _DISPLAY_CANDIDATES:
        if os.path.exists(path):
            return _resolve(path, True)
    return None


class FontBook:
    """带缓存的字体加载器。同一尺寸只加载一次，避免渲染时反复 IO。"""

    def __init__(self, regular: FontSpec | None = None, bold: FontSpec | None = None,
                 display: FontSpec | None = None):
        self.regular = regular or resolve(False)
        self.bold = bold or resolve(True)
        # 有些环境（比如只装了微软雅黑一种）没有独立粗体，只能靠描边模拟
        self.bold_is_real = os.path.normcase(self.bold.path) != os.path.normcase(self.regular.path)
        self.display = display or resolve_display() or self.bold
        self.display_is_real = os.path.normcase(self.display.path) != os.path.normcase(self.bold.path)
        # 信息终端那套要等宽读数和窄体标题。没有就退回常规/粗体 ——
        # 版面几何不依赖它们，差的只是"工业感"。
        self.mono = resolve_variant("AIINFO_FONT_MONO", _MONO_CANDIDATES, "mono")
        self.mono_bold = resolve_variant("AIINFO_FONT_MONO_BOLD", _MONO_BOLD_CANDIDATES, "mono:bold")
        self.condensed = resolve_variant("AIINFO_FONT_CONDENSED", _CONDENSED_CANDIDATES,
                                         "sans-serif:style=Condensed")
        self.condensed_bold = resolve_variant("AIINFO_FONT_CONDENSED_BOLD",
                                              _CONDENSED_BOLD_CANDIDATES,
                                              "sans-serif:style=Condensed:weight=bold")
        self._cache: dict[tuple[int, str], ImageFont.FreeTypeFont] = {}
        self._warn_if_no_cjk()

    def _warn_if_no_cjk(self) -> None:
        if not self.regular.is_cjk:
            print(
                "[fonts] 警告：没找到中文字体，正文里的汉字会显示成方块。\n"
                "        解决办法：装一个中文字体（apt install fonts-noto-cjk\n"
                "        或下载思源黑体），再用 AIINFO_FONT 环境变量指过去。"
            )

    def get(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return self._get(size, "b" if bold else "r")

    def get_display(self, size: int) -> ImageFont.FreeTypeFont:
        return self._get(size, "d")

    def get_mono(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return self._get(size, "mb" if bold else "m")

    def get_condensed(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return self._get(size, "cb" if bold else "c")

    def _get(self, size: int, weight: str) -> ImageFont.FreeTypeFont:
        size = max(6, int(round(size)))
        key = (size, weight)
        if key in self._cache:
            return self._cache[key]
        # 等宽 / 窄体缺字体时退回常规或粗体：宁可长得普通，也不能画不出来。
        spec = {"b": self.bold, "d": self.display,
                "m": self.mono or self.regular, "mb": self.mono_bold or self.bold,
                "c": self.condensed or self.regular,
                "cb": self.condensed_bold or self.bold}.get(weight, self.regular)
        if not spec.path:
            font = ImageFont.load_default()
        else:
            try:
                font = ImageFont.truetype(spec.path, size, index=spec.index)
            except Exception:
                font = ImageFont.truetype(spec.path, size)
        self._cache[key] = font
        return font

    def describe(self) -> str:
        reg = self.regular.name or os.path.basename(self.regular.path) or "PIL-default"
        bold = self.bold.name or os.path.basename(self.bold.path) or "PIL-default"
        disp = self.display.name or os.path.basename(self.display.path) or "PIL-default"
        suffix = "" if self.bold_is_real else "（无独立粗体，用描边模拟）"
        if not self.display_is_real:
            suffix += "（无宋体，显示字退回粗体黑）"
        # 等宽/窄体是什么要报出来：信息终端那套的观感一半靠它们，而本机是
        # Consolas、云端是开源近亲 —— 不写清楚就会花时间去查"为什么两边不一样"。
        mono = (os.path.basename(self.mono.path) if self.mono else "无→退常规")
        cond = (os.path.basename(self.condensed.path) if self.condensed else "无→退常规")
        return (f"常规={reg} / 粗体={bold} / 显示={disp}"
                f" / 等宽={mono} / 窄体={cond}{suffix}")


# 认这两组键，前一组优先（`font.*` 是通用写法，`clock.*` 是历史字段名）
_FONT_KEYS = (
    ("font.regular", "clock.font", False),
    ("font.bold", "clock.font_bold", True),
)


def book_for(cfg) -> FontBook:
    """按配置造一个字体册 —— **出图和时钟精灵图都必须走这里**。

    有配置项就按配置指到的那套字，没有就走正常解析。这两个键存在的唯一意义，
    是让下面三处用上**同一种字**：

        本机预览   ·   云端出图   ·   Kindle 上的时钟精灵图

    为什么非统一不可：出图在本机跑时默认解析到微软雅黑，云端解析到 Noto Sans CJK。
    字形不同，最要命的是**行高差 10px** —— 而时钟区坐标是由这些字量出来的，
    精灵图按 Noto 的尺寸生成、却贴到雅黑量出来的留白上，于是时钟要么压着别的字，
    要么偏出去一截，看着就像"另一块拼上去的"。

    所以配置里把这套字指到 Noto，本机、云端、精灵图三边就都对上了。
    （云端那些 Windows 路径**不存在**，会自动退回那边的正常解析 —— 而那边本来
    解析出来的也是 Noto，所以结论不变。）
    """
    regular = resolve(False)
    bold = resolve(True)
    display = None
    if cfg is None:
        return FontBook(regular=regular, bold=bold)
    for key, fallback_key, want_bold in _FONT_KEYS:
        path = cfg.get(key) or cfg.get(fallback_key)
        if not path or not os.path.exists(str(path)):
            continue
        spec = _resolve(str(path), want_bold)
        if want_bold:
            bold = spec
        else:
            regular = spec
    disp_path = cfg.get("font.display")
    if disp_path and os.path.exists(str(disp_path)):
        display = _resolve(str(disp_path), True)
    return FontBook(regular=regular, bold=bold, display=display)
