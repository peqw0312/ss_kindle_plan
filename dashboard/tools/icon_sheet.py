#!/usr/bin/env python3
"""把天气图标单独铺成一张对照图，用真渲染器画，不改任何配置。

为什么要这个：图标是"画"出来的，改一笔剪影只有肉眼看图才知道好不好。
之前每次都要重出一整张 1072×1448 的信息屏才能看到它，太大看不清，
也分不清是图标丑还是版面丑。这里只放大图标本身。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe dashboard\\tools\\icon_sheet.py [输出路径.png]
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))

from PIL import Image, ImageDraw                                # noqa: E402
from aiinfo import render as R                                   # noqa: E402
from aiinfo.config import Config                                 # noqa: E402

#: 和 render.Renderer.icon() 里认的那些 kind 对齐；认不出来的一律按 fog 画，
#: 所以表里写的顺序就是页面上的顺序，别漏。
KINDS = ["sun", "sun_cloud", "cloud", "moon", "moon_cloud",
         "rain", "shower", "drizzle", "snow", "thunder", "fog"]
GUTTER = 120
SIZES = [124, 64, 40]        # 主图标 / 预报列 / 更小一档（试边界用）
CELL = 150


def main() -> int:
    cfg = Config.load(str(ROOT / "dashboard" / "config.yaml"))
    canvas = Image.new("L", (GUTTER + CELL * len(SIZES) + 20,
                             CELL * len(KINDS) + 40), 255)
    # 借 Renderer 的绘制方法，但画布换成这张对照图。
    r = R.Renderer(cfg, {"generated_at": None})
    r.img = canvas
    r.d = ImageDraw.Draw(canvas)

    for row, kind in enumerate(KINDS):
        for col, size in enumerate(SIZES):
            cx = GUTTER + CELL * col + CELL // 2
            cy = 30 + CELL * row + CELL // 2
            r.icon(cx, cy, float(size), kind)
            r.text((cx, 30 + CELL * row + CELL - 22), f"{size}px", r.f(22),
                   R.GRAY_LIGHT, anchor="mm")
        r.text((GUTTER - 16, 30 + CELL * row + CELL // 2), kind, r.f(28), R.GRAY,
               anchor="rm")

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".workbuddy" / "_studio" / "icons.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print(f"{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
