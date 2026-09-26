#!/usr/bin/env python3
"""皮肤墙：一次取数，把每套版式各出一张**真图**。

为什么非得是工具而不是看设计稿：成品是 1072×1448 的 8 级灰度电子墨水图。
颜色一掉、尺寸一压，网页上好看的版式经常直接糊成一团 —— 所以比较只能在
真图上做。而且必须是**同一时刻的同一份数据**：拿 09:00 的行情去比 15:00 的
气温，比出来的是数据差异，不是版式差异。

    python dashboard/tools/skins.py                    # 联网取真数据
    python dashboard/tools/skins.py --offline          # 只比版式，数据是假的
    python dashboard/tools/skins.py --out docs/skins   # 云端发布时这么调

输出 `<版式>.png`（成品原尺寸）加一份 `manifest.json`。调试台那一页读
manifest.json 摆墙，所以加版式只要改 `Renderer.LAYOUTS`，这里不用动。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
for path in (str(TOOLS), str(ROOT / "dashboard")):
    if path not in sys.path:
        sys.path.insert(0, path)

from _marks import BAD, OK, WARN, line, safe_stdout   # noqa: E402

import generate                                         # noqa: E402  复用它的取数
from aiinfo.config import Config                        # noqa: E402
from aiinfo.lunar import calendar_info                  # noqa: E402
from aiinfo.render import Renderer                      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="把每套版式各出一张真图，摆成皮肤墙")
    ap.add_argument("-c", "--config", default="dashboard/config.yaml")
    ap.add_argument("--out", default="docs/skins", help="图片写到哪个目录")
    ap.add_argument("--offline", action="store_true", help="不联网，用假数据只比版式")
    args = ap.parse_args()

    safe_stdout()
    cfg_path = generate.resolve(args.config)
    cfg = Config.load(str(cfg_path) if cfg_path else None)
    current = str(cfg.get("style.layout", "bands") or "bands").lower()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 取数一次。各版式共用它，比出来的差异才只有版式这一项。
    data = generate.collect(cfg, offline=args.offline)
    data["calendar"] = calendar_info(data["generated_at"])

    # 版式名单以 Renderer.LAYOUTS 为准。这里留兜底常量是因为 render.py 正被另一个人
    # 同时改、我那半个改动还没提交 —— 等它落地就把这两行删掉。
    layouts = getattr(Renderer, "LAYOUTS", None) or ("bands", "poster", "c1")
    labels = getattr(Renderer, "LAYOUT_LABELS", None) or {}

    skins = []
    for name in layouts:
        cfg.set("style.layout", name)
        rec = {"name": name,
               "label": labels.get(name, name),
               "file": f"{name}.png",
               "current": name == current}
        try:
            renderer = Renderer(cfg, data)
            image = renderer.render()
            path = out / rec["file"]
            image.save(path)
            rec["bytes"] = path.stat().st_size
            # 版式自己抱怨的话要带出来 —— "这版余量只剩 12px"这种事，看图是看不出来的
            rec["notes"] = list(renderer.notes)
            line(name, OK, f"{rec['bytes'] // 1024} KB"
                          + ("  ← 当前生效" if rec["current"] else ""))
            for note in rec["notes"]:
                line("", WARN, note)
        except Exception as exc:                      # 一版崩不该带走整面墙
            rec["error"] = f"{type(exc).__name__}: {exc}"
            line(name, BAD, rec["error"])
        skins.append(rec)

    manifest = {"generated_at": data["generated_at"].strftime("%Y-%m-%d %H:%M:%S"),
                "current": current, "layouts": list(layouts), "skins": skins}
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    line("清单", OK, str(out / "manifest.json"))

    broken = [s["name"] for s in skins if s.get("error")]
    if broken:
        line("结论", WARN, f"这 {len(broken)} 套没出图：{' / '.join(broken)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
