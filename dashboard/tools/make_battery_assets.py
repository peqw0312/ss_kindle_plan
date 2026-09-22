#!/usr/bin/env python3
"""生成 Kindle 本机电量显示用的精灵图（样式 B：百分比大字 + 进度条）。

为什么要有这个脚本
------------------
电量只有设备自己知道：云端一天出四次图，也摸不到 Kindle 的电池。所以和当初
时钟同一条路 —— 图上右上角留一块固定矩形（render.POSTER_BATTERY），Kindle 每次
刷完整图之后按当前电量贴一张对应档位的精灵图。

产物
----
    kindle/extensions/aistatus/battery/NNN.png      11 张（0/10/…/100）
    kindle/extensions/aistatus/battery/battery.conf 贴图坐标，给 Kindle 端 source

≤20% 的那几档自带灰字「请充电」，设备端不用判低电量样式。

生成完会自己验一遍（见 verify()）：把精灵图贴到留白版上，和「电量直接画进图里」
的那一版逐像素对比 —— 必须完全一致才认为通过。

    python dashboard/tools/make_battery_assets.py
    python dashboard/tools/make_battery_assets.py --force
"""

from __future__ import annotations

import argparse
import hashlib
import io
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

from aiinfo.config import Config                             # noqa: E402
from aiinfo.fonts import book_for                            # noqa: E402
from aiinfo.lunar import calendar_info                       # noqa: E402
from aiinfo.render import Renderer                           # noqa: E402

DEFAULT_OUT = ROOT / "kindle" / "extensions" / "aistatus" / "battery"
LEVELS = list(range(0, 101, 10))
PROBE_WHEN = datetime(2026, 9, 22, 12, 0)


def pretty(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build_renderer(cfg: Config) -> Renderer:
    """poster 版式、无数据的渲染器：电量区只跟版式和字号有关，跟真实数据无关。"""
    cfg.set("style.layout", "poster")
    return Renderer(cfg, {
        "generated_at": PROBE_WHEN,
        "calendar": calendar_info(PROBE_WHEN),
        "weather": None,
        "quotes": [],
        "funds": [],
        "digest": {},
    })


def fingerprint(cfg: Config, region, fonts) -> str:
    parts = [
        f"{cfg.size[0]}x{cfg.size[1]}",
        f"region={region}",
        f"font={fonts.regular.path}#{fonts.regular.index}",
        f"bold={fonts.bold.path}#{fonts.bold.index}",
        "style=B", "low=20",
    ]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def verify(renderer: Renderer, region, sprite) -> list[str]:
    """前提 1：留白区必须纯白；前提 2：贴回去和直出逐像素一致。"""
    from PIL import ImageChops

    blank = renderer.render()
    out: list[str] = []
    patch = blank.crop(region)
    lo, hi = patch.getextrema()
    area = patch.size[0] * patch.size[1]
    if lo == 255 and hi == 255:
        out.append(f"留白区 {region} 全白（{area} 像素），可以贴图")
    else:
        dirty = area - patch.histogram()[255]
        out.append(f"!! 留白区有 {dirty}/{area} 个非白像素（灰度 {lo}~{hi}），"
                   f"精灵图的白底会盖掉底纹")

    baked = renderer.render()
    renderer._draw_battery(80)
    composed = blank.copy()
    composed.paste(sprite, (region[0], region[1]))
    box = ImageChops.difference(composed, baked).getbbox()
    if box is None:
        out.append("精灵图贴回去与图内直出的电量**逐像素一致**（完美对齐）")
    else:
        out.append(f"!! 对齐有偏差，差异区域 {box} —— 精灵图和图不是同一套几何")
    return out


def main() -> int:
    safe_stdout()
    ap = argparse.ArgumentParser(description="生成 Kindle 电量精灵图")
    ap.add_argument("-c", "--config", default=str(ROOT / "dashboard" / "config.yaml"))
    ap.add_argument("-o", "--out", default=str(DEFAULT_OUT))
    ap.add_argument("--force", action="store_true", help="指纹没变也重新生成")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    for key in ("font.regular", "font.bold"):
        path = cfg.get(key)
        if path and not __import__("os").path.exists(str(path)):
            line(key, BAD, f"配置指向的 {path} 不存在；本机生成精灵图必须指到真实存在的字体")
            return 1
    fonts = book_for(cfg)
    probe = build_renderer(cfg)
    region = probe.battery_region()
    left, top, right, bottom = region
    width, height = right - left, bottom - top
    tag = fingerprint(cfg, region, fonts)

    line("字体", OK, fonts.describe())
    line("电量区", OK, f"({left},{top}) → ({right},{bottom})  {width}×{height}px")
    line("指纹", OK, tag)

    out_dir = Path(args.out)
    conf_path = out_dir / "battery.conf"
    if not args.force and conf_path.is_file() and \
            len(list(out_dir.glob("[0-9][0-9][0-9].png"))) == len(LEVELS) and \
            f"BATTERY_TAG={tag}" in conf_path.read_text(encoding="utf-8"):
        line("跳过", OK, f"已有 {len(LEVELS)} 张且指纹一致（{tag}），无需重生成")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    changed = 0
    total = 0
    for level in LEVELS:
        blob_io = io.BytesIO()
        probe.battery_sprite(level).save(blob_io, format="PNG", optimize=True)
        blob = blob_io.getvalue()
        path = out_dir / f"{level:03d}.png"
        if not path.is_file() or path.read_bytes() != blob:
            path.write_bytes(blob)
            changed += 1
        total += path.stat().st_size
    line("生成", OK, f"{len(LEVELS)} 张，共 {total/1024:.1f} KB，{time.time()-t0:.1f}s")
    line("变化", OK, f"其中 {changed} 张和上次不同")

    # newline="\n" 不能省：这个文件会被 Kindle 上的 sh 直接 source（同 clock.conf）
    conf_path.write_text(
        "# 由 dashboard/tools/make_battery_assets.py 自动生成 —— 不要手改。\n"
        "# 电量区在图中的绝对位置；Kindle 端按当前电量贴 battery/NNN.png。\n"
        "# 换行符必须是 LF（下面这些行会被 Kindle 的 sh 直接 source）。\n"
        f"BATTERY_X={left}\n"
        f"BATTERY_Y={top}\n"
        f"BATTERY_W={width}\n"
        f"BATTERY_H={height}\n"
        f"BATTERY_TAG={tag}\n",
        encoding="utf-8", newline="\n")
    line("坐标", OK, f"{pretty(conf_path)} 已写入")

    print()
    print("【自检】")
    for row in verify(probe, region, probe.battery_sprite(80)):
        print("     " + row)
    print()
    print("下一步：Kindle 的 config.sh 里保持 BATTERY_MODE=local，")
    if changed == 0:
        print(f"       这次精灵图一张都没变，只需拷一个文件：battery.conf")
    else:
        print(f"       这次有 {changed} 张变了，把整个 battery/ 目录拷进")
        print(f"       Kindle 的 extensions/aistatus/battery/。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
