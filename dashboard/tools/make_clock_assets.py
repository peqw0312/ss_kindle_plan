#!/usr/bin/env python3
"""生成 Kindle 本机时钟用的精灵图。

为什么要有这个脚本
------------------
时钟画进图里的话，「时间准」和「图多久重算一次」就绑死了：想让钟准，服务端
就得反复重算图，等于为了看一眼时间反复去拉天气和行情。改成 Kindle 自己贴图之后，
图一天出四次就够，屏幕上每一分钟却都是对的。

产物
----
    kindle/extensions/aistatus/clock/HHMM.png      1440 张（一天里的每一分钟）
    kindle/extensions/aistatus/clock/clock.conf    贴图坐标，给 Kindle 端 source

每张精灵图的画布**正好等于**图里留白的那块矩形，排版位置和主图用的是同一行
代码（Renderer.clock_sprite），所以贴回去之后接缝看不出来。

生成完会自己验一遍（见 verify()）：把精灵图贴到留白版上，和「时钟直接画进图里」
的那一版逐像素对比 —— 必须完全一致才认为通过。字型或字号一改就对不上，
这个检查能当场抓住，不会等到真机上才发现"像两块拼的"。

    python dashboard/tools/make_clock_assets.py
    python dashboard/tools/make_clock_assets.py --force
    python dashboard/tools/make_clock_assets.py --font "C:/Windows/Fonts/NotoSansSC-VF.ttf"

字体：默认和渲染器用同一套解析结果。**但云端出图用的是 Noto Sans CJK SC**，
本机若用微软雅黑生成精灵图，时钟的数字字形会和图上其他文字有细微差异。
想完全对齐就用 --font / --font-bold 指向本机的 Noto（脚本会打印实际用的是哪个）。
"""

from __future__ import annotations

import io
import argparse
import hashlib
import os
import sys
import time
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
for path in (str(TOOLS), str(ROOT / "dashboard")):
    if path not in sys.path:
        sys.path.insert(0, path)

from _marks import BAD, OK, line, safe_stdout              # noqa: E402

from aiinfo import render as R                               # noqa: E402
from aiinfo.config import Config                             # noqa: E402
from aiinfo.fonts import FontBook, book_for                    # noqa: E402
from aiinfo.lunar import calendar_info                       # noqa: E402
from aiinfo.render import Renderer                           # noqa: E402

DEFAULT_OUT = ROOT / "kindle" / "extensions" / "aistatus" / "clock"

#: 验证用的基准时刻。取晚上 —— 12 小时制下「晚上 11:59」是最宽形态，
#: 拿它对齐等于顺手验证了最坏情况
PROBE_WHEN = datetime(2026, 9, 18, 23, 59)


def pretty(path: Path) -> str:
    """能相对仓库显示就相对显示，否则给绝对路径（--out 指向仓库外时不炸）。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build_renderer(cfg: Config, when: datetime, mode: str) -> Renderer:
    """造一个只够算几何的 Renderer：不联网，天气行情都留空。

    时钟区只跟 device / clock.style / 字号有关，跟真实数据无关，
    所以这里不必抓任何东西。
    """
    cfg.set("clock.mode", mode)
    data = {
        "generated_at": when,
        "calendar": calendar_info(when),
        "weather": None,
        "quotes": [],
        "funds": [],
        "digest": {},
    }
    return Renderer(cfg, data)


def fingerprint(cfg: Config, region: tuple[int, int, int, int],
                fonts: FontBook) -> str:
    """资产指纹。任何一项变了都得重新生成，否则真机上会看出错位。"""
    parts = [
        f"{cfg.size[0]}x{cfg.size[1]}",
        f"style={cfg.get('clock.style', 'cn12')}",
        f"fs={R.FS_CLOCK}",
        f"pad={R.CLOCK_PAD}",
        f"region={region}",
        f"font={fonts.regular.path}#{fonts.regular.index}",
        f"bold={fonts.bold.path}#{fonts.bold.index}",
    ]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def verify(renderer: Renderer, cfg: Config, when: datetime,
           region: tuple[int, int, int, int], sprite) -> list[str]:
    """把精灵图贴到留白版上，要求它和「时钟画进图里」的那版完全一致。

    这是本脚本存在的意义所在。两个前提必须同时成立：
      1. 留白的那块矩形**必须是干净的纯白**，否则精灵图的实心白底会盖掉底纹；
      2. 精灵图的排版必须和图里直出的时钟逐像素重合，否则会看出"两块拼的"。
    任何一个不成立，真机上都会很难看，但**肉眼在小图上不一定看得出来**，
    所以放在这里用像素判。
    """
    from PIL import ImageChops

    blank = build_renderer(cfg, when, "local").render()
    baked = build_renderer(cfg, when, "image").render()

    out: list[str] = []

    # --- 前提 1：留白区干净吗 ---
    patch = blank.crop(region)
    lo, hi = patch.getextrema()
    area = patch.size[0] * patch.size[1]
    if lo == 255 and hi == 255:
        out.append(f"留白区 {region} 全白（{area} 像素），可以贴图")
    else:
        hist = patch.histogram()
        dirty = area - hist[255]
        out.append(f"!! 留白区有 {dirty}/{area} 个非白像素（灰度 {lo}~{hi}），"
                   f"精灵图的白底会盖掉底纹")

    # --- 前提 2：贴上去和直出的是否逐像素一致 ---
    # paste 不带 mask = 不透明覆盖，这正是 eips 贴图的行为
    composed = blank.copy()
    composed.paste(sprite, (region[0], region[1]))
    box = ImageChops.difference(composed, baked).getbbox()
    if box is None:
        out.append("精灵图贴回去与图内直出的时钟**逐像素一致**（完美对齐）")
    else:
        out.append(f"!! 对齐有偏差，差异区域 {box} —— "
                   f"精灵图和图不是同一套几何，检查 clock_region()")
    return out


def main() -> int:
    safe_stdout()
    ap = argparse.ArgumentParser(description="生成 Kindle 本机时钟精灵图")
    ap.add_argument("-c", "--config", default=str(ROOT / "dashboard" / "config.yaml"))
    ap.add_argument("-o", "--out", default=str(DEFAULT_OUT))
    ap.add_argument("--font", default=None, help="常规字体路径（覆盖自动解析）")
    ap.add_argument("--font-bold", default=None, help="粗体字体路径")
    ap.add_argument("--force", action="store_true", help="指纹没变也重新生成")
    args = ap.parse_args()

    cfg = Config.load(args.config)

    # 字体：**必须和出图用同一套**，否则时钟区坐标是按另一种字量出来的，
    # 精灵图贴回去就差一截，看着像"另一块拼上去的"。
    # 出图（Renderer.__init__）和这里都走 book_for(cfg)，所以"同一套"是
    # 结构上保证的，不靠人去手工对齐。命令行 --font/--font-bold 优先级最高。
    if args.font:
        cfg.set("clock.font", args.font)
    if args.font_bold:
        cfg.set("clock.font_bold", args.font_bold)

    # 有一处必须比 book_for 严：**本机生成精灵图时，配置指到的字体必须真的在**。
    # book_for 会静默退回本机默认字（云端需要这个行为，因为配置里的 Windows
    # 路径在那边根本不存在），但那意味着你拿到一套和云端对不上的精灵图 ——
    # 等真机上看着别扭才发现就太晚了。所以这里直接拦下。
    for key in ("font.regular", "font.bold", "clock.font", "clock.font_bold"):
        path = cfg.get(key)
        if path and not os.path.exists(str(path)):
            line(key, BAD, f"配置指向的 {path} 不存在；"
                           f"本机生成精灵图必须指到真实存在的字体")
            return 1

    fonts = book_for(cfg)

    out_dir = Path(args.out)
    conf_path = out_dir / "clock.conf"

    # 几何只跟配置有关，先拿一个渲染器把坐标定下来
    probe = build_renderer(cfg, PROBE_WHEN, "local")
    probe.render()                      # render() 会填 clock_box
    if probe.clock_box is None:
        line("时钟区", BAD, "渲染器没有给出时钟区坐标")
        return 1
    region = probe.clock_box
    left, top, right, bottom = region
    width, height = right - left, bottom - top

    tag = fingerprint(cfg, region, fonts)

    line("字体", OK, fonts.describe())
    line("时钟区", OK, f"({left},{top}) → ({right},{bottom})  "
                      f"{width}×{height}px")
    line("样式", OK, f"{cfg.get('clock.style', 'cn12')}  指纹 {tag}")

    existing = 0
    if out_dir.is_dir():
        existing = len(list(out_dir.glob("[0-9][0-9][0-9][0-9].png")))

    if not args.force and conf_path.is_file() and existing == 1440:
        if f"CLOCK_TAG={tag}" in conf_path.read_text(encoding="utf-8"):
            line("跳过", OK, f"已有 1440 张且指纹一致（{tag}），无需重生成")
            return 0

    # --- 生成 ---
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    total_bytes = 0
    worst = (0, "")
    changed = 0
    for hour in range(24):
        for minute in range(60):
            label = probe.clock_text(datetime(2026, 1, 1, hour, minute))
            sprite = probe.clock_sprite(label)
            path = out_dir / f"{hour:02d}{minute:02d}.png"
            buf = io.BytesIO()
            sprite.save(buf, format="PNG", optimize=True)
            blob = buf.getvalue()
            # 精灵图是「白底 + 几个字」，和它在整张图里的位置无关。所以只挪坐标
            # （换字号最常见的结果）时这 1440 张其实一个字节都没变 —— 这时候
            # 要拷进 Kindle 的只有 clock.conf 那一个文件，不是 4MB 的整个目录。
            # 尺寸变了才会真的重画，那种情况必须整目录重拷，少拷就贴偏。
            if not path.is_file() or path.read_bytes() != blob:
                path.write_bytes(blob)
                changed += 1
            size = path.stat().st_size
            total_bytes += size
            if size > worst[0]:
                worst = (size, path.name)
    cost = time.time() - t0
    line("生成", OK, f"1440 张，{total_bytes/1024/1024:.1f} MB，"
                     f"最大 {worst[0]/1024:.1f} KB（{worst[1]}），{cost:.0f}s")
    line("变化", OK, f"其中 {changed} 张的内容和上次不同")

    # --- 写坐标 ---
    # newline="\n" 不能省：这个文件会被 Kindle 上的 sh **source**，
    # Windows 默认会把 \n 翻成 \r\n，于是 CLOCK_X 变成 "644\r"，
    # eips 拿到带回车的参数就贴错位置 —— 而且在电脑上看这个文件完全正常。
    conf_path.write_text(
        "# 由 dashboard/tools/make_clock_assets.py 自动生成 —— 不要手改，\n"
        "# 改了字号或 clock.style 之后重跑生成脚本，这个文件会一起更新。\n"
        "#\n"
        "# 这些坐标是「图里留白的那块矩形」在图中的绝对位置，\n"
        "# Kindle 端用 eips -g HHMM.png -x $CLOCK_X -y $CLOCK_Y 贴图。\n"
        "#\n"
        "# 换行符必须是 LF（下面这些行会被 Kindle 的 sh 直接 source）——\n"
        "# 脚本写这个文件时显式指定了 newline=\"\\n\"，别改成默认值。\n"
        f"CLOCK_X={left}\n"
        f"CLOCK_Y={top}\n"
        f"CLOCK_W={width}\n"
        f"CLOCK_H={height}\n"
        f"CLOCK_TAG={tag}\n"
        f"CLOCK_FONT={fonts.bold.name or fonts.bold.path}\n",
        encoding="utf-8", newline="\n")
    line("坐标", OK, f"{pretty(conf_path)} 已写入")

    # --- 自检 ---
    print()
    print("【自检】拿最宽的时刻（晚上 11:59）验证对齐")
    sprite = probe.clock_sprite(probe.clock_text(PROBE_WHEN))
    for row in verify(probe, cfg, PROBE_WHEN, region, sprite):
        print("     " + row)

    print()
    # 到底要拷一个文件还是整个目录，取决于精灵图内容变没变，不是取决于"我重跑了没有"。
    # 说错了会让人白拷 4MB，或者更糟 —— 只拷了 conf、图却是旧的，真机上时钟贴偏。
    print("下一步：Kindle 的 config.sh 里保持 CLOCK_MODE=local。")
    if changed == 0:
        print(f"       这次 1440 张精灵图一张都没变，只需把**一个文件**拷进")
        print(f"       extensions/aistatus/clock/ ：  clock.conf（{pretty(conf_path)}）")
    else:
        print(f"       这次有 {changed} 张精灵图内容变了，必须把**整个 clock/ 目录**")
        print(f"       拷进 Kindle 的 extensions/aistatus/clock/，少拷就贴偏。")
    print("       拷完先点「时钟贴图自检」那个 scriptlet，确认时钟在右上角再长期开。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
