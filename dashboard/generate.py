#!/usr/bin/env python3
"""aiinfo 主程序：抓数据 -> AI 摘要 -> 排版 -> 输出墨水屏 PNG。

用法：
    python dashboard/generate.py                       # 用默认配置跑一次
    python dashboard/generate.py -c dashboard/config.yaml
    python dashboard/generate.py --no-ai               # 跳过 AI 摘要（本地调试快）
    python dashboard/generate.py --from-cache cache.json   # 用抓好的数据重排，不联网

配置里所有相对路径都以仓库根目录为基准，这样不管从哪个目录调用结果都一样。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiinfo import __version__, sources                     # noqa: E402
from aiinfo.config import Config                            # noqa: E402
from aiinfo.digest import build_digest                      # noqa: E402
from aiinfo.lunar import calendar_info                      # noqa: E402
from aiinfo.render import Renderer                          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def resolve(path_str: str | None) -> Path | None:
    if not path_str:
        return None
    p = Path(path_str)
    return p if p.is_absolute() else (ROOT / p)


def collect(cfg: Config, *, offline: bool = False) -> dict:
    data: dict = {"version": __version__, "generated_at": datetime.now()}
    if offline:
        return data

    # 本机常常挂着代理，而剩下的数据源国内都能直连——不该被绕到境外去
    sources.configure_network(cfg)

    t0 = time.time()
    log("抓取天气…")
    try:
        data["weather"] = sources.fetch_weather(cfg)
    except Exception as exc:                                # noqa: BLE001
        log(f"天气抓取异常（已忽略）：{exc}")
        data["weather"] = None
    weather = data.get("weather") or {}
    tmp = weather.get("temp")
    log(f"  天气：{weather.get('source') or '无数据'}"
        + (f"，{tmp}°" if tmp is not None else "")
        + f"（{time.time() - t0:.1f}s）")

    t0 = time.time()
    log("抓取行情…")
    try:
        data["quotes"] = sources.fetch_quotes(cfg)
        data["funds"] = sources.fetch_funds(cfg)
    except Exception as exc:                                # noqa: BLE001
        log(f"行情抓取异常（已忽略）：{exc}")
        data["quotes"], data["funds"] = [], []
    ok = sum(1 for q in data.get("quotes", []) if q.get("price") is not None)
    crypto = sources.crypto_source_label(data.get("quotes", []))
    log(f"  行情：{ok}/{len(data.get('quotes', []))} 条有效"
        + (f"，合约来源 {crypto}" if crypto else "")
        + f"（{time.time() - t0:.1f}s）")

    if not cfg.get("digest.enabled", True):
        # 速览关掉之后，25 个 RSS 源和模型调用整条链路都不跑，省时也少一类失败点
        log("速览已关闭（digest.enabled: false），跳过 RSS 与模型调用。")
        data["digest"] = {"title": cfg.get("digest.title", "今日速览"), "items": []}
        data["_news_raw"] = {}
        return data

    t0 = time.time()
    log("抓取新闻源…")
    try:
        news = sources.fetch_news(cfg)
    except Exception as exc:                                # noqa: BLE001
        log(f"新闻抓取异常（已忽略）：{exc}")
        news = {}
    total = sum(len(v) for v in news.values())
    detail = "、".join(f"{k} {len(v)}" for k, v in news.items()) or "无"
    log(f"  新闻：{total} 条候选（{detail}）（{time.time() - t0:.1f}s）")

    log("生成速览…")
    data["digest"] = build_digest(cfg, news)
    # 只留标题，够排查"为什么这条被选中"就行，别把整份缓存塞进调试文件
    data["_news_raw"] = {k: [it.get("title", "") for it in v[:6]] for k, v in news.items()}
    return data


def write_html(path: Path, cfg: Config, size: tuple[int, int], refresh: int = 600) -> None:
    """顺手产出一个网页版，给「懒得越狱」或临时调试用。

    Kindle 自带的体验版浏览器能直接打开；meta refresh 实现自动刷新。
    """
    w, h = size
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>AI 信息屏</title>
<style>
  html, body {{ margin: 0; padding: 0; background: #fff; }}
  body {{ display: flex; justify-content: center; }}
  img {{ width: 100%; max-width: {w}px; height: auto; display: block; image-rendering: -webkit-optimize-contrast; }}
  .tip {{ font: 600 14px/1.6 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         color: #999; text-align: center; padding: 10px; }}
</style>
</head>
<body>
<div>
  <img src="dashboard.png" alt="AI 信息屏">
  <p class="tip">自动刷新间隔 {refresh // 60} 分钟 · 刷新页面可立即更新</p>
</div>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Kindle AI 信息屏图片")
    parser.add_argument("-c", "--config", default="dashboard/config.yaml",
                        help="配置文件路径（默认 dashboard/config.yaml）")
    parser.add_argument("-o", "--out", default=None, help="覆盖输出的 PNG 路径")
    parser.add_argument("--no-ai", action="store_true", help="跳过 AI 摘要，直接用原始标题")
    parser.add_argument("--offline", action="store_true", help="完全跳过联网（只测排版）")
    parser.add_argument("--now", default=None,
                        help="调试用：把时间固定成 ISO 格式，例如 2026-09-18T08:30")
    parser.add_argument("--no-html", action="store_true", help="不生成网页版")
    parser.add_argument("--layout", default=None,
                        help="临时用哪套版式（bands / poster / c1 …），不改配置文件。"
                             "云端「Run workflow」那个下拉就是把它传进来的。")
    args = parser.parse_args()

    config_path = resolve(args.config)
    cfg = Config.load(str(config_path) if config_path else None)
    if config_path and config_path.exists():
        log(f"配置：{config_path}")
    else:
        log("未找到配置文件，使用默认配置")

    if args.no_ai:
        cfg.set("digest.use_ai", False)
    if args.layout:
        cfg.set("style.layout", args.layout)
        log(f"本次强制版式：{args.layout}（配置文件里写的被临时盖掉，不改文件）")

    data = collect(cfg, offline=args.offline)

    if args.now:
        data["generated_at"] = datetime.fromisoformat(args.now)

    # 日历是纯本地推算（不联网、不依赖任何库），放在时间定稿之后算
    data["calendar"] = calendar_info(data["generated_at"])
    cal = data["calendar"]
    log("日历：" + " · ".join(
        p for p in (f"{cal.solar_year}-{cal.solar_month:02d}-{cal.solar_day:02d}",
                    cal.weekday, cal.lunar_text,
                    f"{cal.term_current}第{cal.term_current_days}天", cal.badge) if p))

    log(f"渲染 {cfg.size[0]}x{cfg.size[1]}…")
    renderer = Renderer(cfg, data)
    log(f"  字体：{renderer.fonts.describe()}")
    image = renderer.render()
    for note in renderer.notes:
        log(f"  ! {note}")

    out_png = resolve(args.out) or resolve(cfg.get("output.png", "docs/dashboard.png"))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    image.convert("L").save(out_png, format="PNG", optimize=True)
    size_kb = out_png.stat().st_size / 1024
    log(f"已输出 {out_png}（{size_kb:.1f} KB，8 位灰度）")

    if not args.no_html:
        out_html = resolve(cfg.get("output.html", "docs/index.html"))
        if out_html:
            out_html.parent.mkdir(parents=True, exist_ok=True)
            write_html(out_html, cfg, cfg.size)
            log(f"已输出网页版 {out_html}")

    out_debug = resolve(cfg.get("output.debug_json", "docs/debug.json"))
    if out_debug:
        out_debug.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": data.get("version"),
            "generated_at": data["generated_at"].isoformat(),
            "device": cfg.get("device.model"),
            "size": list(cfg.size),
            "digest_ai": data.get("digest", {}).get("ai"),
            "digest_model": data.get("digest", {}).get("model"),
            "counts": {
                "weather": bool(data.get("weather")),
                "quotes_valid": sum(1 for q in data.get("quotes", []) if q.get("price") is not None),
                "quotes_total": len(data.get("quotes", []) or []),
                "quotes_crypto_source": sources.crypto_source_label(data.get("quotes", [])),
                "digest_items": len(data.get("digest", {}).get("items") or []),
                "news_candidates": sum(len(v) for v in (data.get("_news_raw") or {}).values()),
            },
            "calendar": (dict(cal._asdict()) if hasattr(cal, "_asdict") else cal),
            "weather": data.get("weather"),
            "digest": data.get("digest"),
            "quotes": data.get("quotes"),
            "funds": data.get("funds"),
            "news_headlines": data.get("_news_raw"),
        }
        out_debug.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"已输出调试信息 {out_debug}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
