#!/usr/bin/env python3
"""数据源自检工具。

什么时候用它：某天发现屏幕上少了行情、天气看着不对、或者内容明显变陈旧时，
先跑这个看看到底是哪个源挂了，比盯着屏幕猜要快得多。

    python dashboard/tools/probe_sources.py
    python dashboard/tools/probe_sources.py --config dashboard/config.yaml
    python dashboard/tools/probe_sources.py --only weather

它不修改任何东西，只做只读探测并打印结论。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from aiinfo import sources                                  # noqa: E402
from aiinfo.config import Config                            # noqa: E402
from aiinfo.digest import _find_token                       # noqa: E402
from aiinfo.lunar import calendar_info                      # noqa: E402


def _pick_marks() -> tuple[str, str, str]:
    """挑一套控制台打得出来的标记。

    Windows 的控制台编码默认是 GBK，直接 print ✅ 会抛 UnicodeEncodeError
    把整个自检打断（不是打不出来，是直接崩）。Actions 上是 UTF-8 没这个问题。
    """
    try:
        "✅".encode(sys.stdout.encoding or "utf-8")
    except (UnicodeEncodeError, LookupError):
        return "[OK]", "[NG]", "[!!]"
    return "✅", "❌", "⚠️ "


OK, BAD, WARN = _pick_marks()


def line(label: str, status: str, detail: str = "") -> None:
    print(f"  {status} {label:<18} {detail}", flush=True)


def probe_weather(cfg: Config) -> None:
    """把两个天气源都单独打一遍，再报告最终用的是哪个。

    为什么要**分别**探再报一次"最终"：这两个源的口径不一样（和风是国内
    融合实况，Open-Meteo 是全球数值模式插值），并排看才知道差多少。
    只报最终结果的话，"其实已经静默降级到兜底源了"这件事会被藏起来。
    """
    provider = str(cfg.get("weather.provider", "qweather")).lower()
    print(f"\n【天气 · 配置的源是 {provider}】")

    def show(tag: str, fn, **kw) -> dict | None:
        t0 = time.time()
        try:
            w = fn(cfg, **kw)
        except Exception as exc:                            # noqa: BLE001
            line(tag, BAD, f"异常 {exc}")
            return None
        cost = time.time() - t0
        if not w:
            line(tag, BAD, f"没拿到数据（{cost:.1f}s）")
            return None
        air = w.get("air") or {}
        air_txt = (f"｜空气 {air.get('level')} {air.get('aqi')}"
                   if air.get("aqi") is not None else "｜空气 无")
        level = sources.effective_wind_level(w)
        wind_txt = (f"{w.get('wind_dir') or '?'}{level}级"
                    if level is not None else "风 无数据")
        warn = w.get("warning") or []
        warn_txt = f"｜预警 {len(warn)} 条" if warn else ""
        line(tag, OK,
             f"{w.get('desc')} {w.get('temp')}° 体感{w.get('feels')}° "
             f"湿度{w.get('humidity')}% {wind_txt} "
             f"（{cost:.1f}s）{air_txt}{warn_txt}")
        line("", "", f"预报 {len(w.get('forecast') or [])} 天"
                     + (f"｜{w['forecast'][0].get('label')} "
                        f"{w['forecast'][0].get('high')}°/{w['forecast'][0].get('low')}°"
                        if w.get("forecast") else ""))
        return w

    host, key = sources._qweather_creds(cfg)
    if host and key:
        show("和风天气", sources._fetch_weather_qweather)
    else:
        missing = "、".join(n for n, v in (("api_host", host), ("api_key", key)) if not v)
        line("和风天气", WARN, f"没配置（缺 {missing}）—— 看 config.yaml 里 weather.qweather")
        print("     · 免费订阅：https://dev.qweather.com")
        print("     · ⚠️ 旧域名 devapi/api.qweather.com 已停止服务，"
              "必须用控制台给的专属 Host")

    show("Open-Meteo", sources._fetch_weather_openmeteo)

    print("  -- 最终采用（屏幕上会显示这个）--")
    w = show("实际取用", sources.fetch_weather)
    if w and w.get("source"):
        print(f"     ↑ 页脚会写「天气 {w['source']}」")
    if provider == "qweather" and w and w.get("source") != "和风天气":
        print(f"     {WARN} 配的是和风，实际用的是 {w.get('source')} —— 已静默降级，"
              "上面第一段写的就是原因")


def probe_quotes(cfg: Config) -> None:
    print("\n【行情 · 依次降级】")
    items = cfg.get("quotes.items", []) or []
    if not items:
        line("配置", WARN, "未配置自选")
        return
    pairs: list[tuple[str, str]] = []
    crypto: list[tuple[str, str]] = []
    for item in items:
        norm = sources.normalize_code(item.get("code", ""))
        if not norm:
            continue
        (crypto if norm.startswith(sources.CRYPTO_PREFIX) else pairs).append(
            (norm, item.get("code", "")))

    if pairs:
        print(f"  -- 指数 / 股票（{' -> '.join(sources._PROVIDERS)}）--")
        for name, fn in sources._PROVIDERS.items():
            t0 = time.time()
            try:
                got = fn(pairs)
            except Exception as exc:                        # noqa: BLE001
                line(name, BAD, f"异常 {exc}")
                continue
            detail = "、".join(f"{v['name']}{v['price']:.2f}" for v in list(got.values())[:4])
            line(name, OK if got else BAD,
                 f"命中 {len(got)}/{len(pairs)}（{time.time() - t0:.1f}s）{detail}")
        print("     ↑ 只要有一个源命中全部品种就没问题")

    if crypto:
        print(f"  -- 加密合约（{' -> '.join(n for n, _ in sources._CRYPTO_PROVIDERS)}）--")
        for name, fn in sources._CRYPTO_PROVIDERS:
            t0 = time.time()
            try:
                got = fn(crypto)
            except Exception as exc:                        # noqa: BLE001
                line(name, BAD, f"异常 {exc}")
                continue
            detail = "、".join(f"{v['name']}{v['price']:,.0f}" for v in list(got.values())[:4])
            line(name, OK if got else BAD,
                 f"命中 {len(got)}/{len(crypto)}（{time.time() - t0:.1f}s）{detail}")
        print("     ↑ Bitget 在国内直连会被重置连接，落到 Gate 是正常的，不是故障；")
        print("       屏幕上会如实标注这一轮用的是哪家。合约默认没开，"
              "要加就在 quotes.items 里写 bitget:BTCUSDT。")

    print("  -- 最终结果 --")
    for item in sources.fetch_quotes(cfg):
        flag = OK if item.get("price") is not None else BAD
        price = f"{item['price']:.2f}" if item.get("price") is not None else "--"
        pct = f"{item['pct']:+.2f}%" if item.get("pct") is not None else "--"
        source = f" ← {item['provider']}" if item.get("provider") else ""
        line(f"{item['name']}", flag, f"{price}  {pct}{source}")


def probe_feeds(cfg: Config) -> None:
    print("\n【新闻源 · RSS】")
    cats = cfg.categories
    if not cats:
        line("配置", WARN, "没有配置任何栏目")
        return
    alive = total = 0
    for cat in cats:
        print(f"  -- {cat.get('name')}（{cat.get('brief') or '-'}）--")
        for feed in (cat.get("feeds") or []):
            total += 1
            url = feed.get("url", "")
            name = feed.get("name", url)
            t0 = time.time()
            resp = sources.get(url, timeout=18, retries=0)
            if resp is None:
                line(name, BAD, "请求失败")
                continue
            if not sources.looks_like_feed(resp.text):
                line(name, BAD, "返回的不是 RSS（可能已下线或被反爬）")
                continue
            parsed = sources._parse_feed(resp.text, name)
            if parsed:
                alive += 1
                line(name, OK,
                     f"{len(parsed)} 条（{time.time() - t0:.1f}s）｜{parsed[0]['title'][:28]}")
            else:
                line(name, WARN, "能访问但没解析出条目")
    print(f"     ↑ {alive}/{total} 个源可用。单个栏目全挂只会让那条速览降级成标题直出，不影响其他区块。")


def probe_calendar(cfg: Config) -> None:
    print("\n【日历 · 纯本地推算】")
    if not cfg.get("calendar.enabled", True):
        line("开关", WARN, "calendar.enabled=false，日历条已关闭")
        return
    cal = calendar_info(datetime.now())
    line("今天", OK, f"{cal.solar_text} {cal.weekday}")
    line("农历", OK, f"{cal.lunar_text}（{cal.lunar_size}月）")
    line("干支", OK, f"{cal.ganzhi_year}年 属{cal.shengxiao} · "
                    f"{cal.ganzhi_month}月 {cal.ganzhi_day}日")
    line("节气", OK, f"{cal.term_current} 第 {cal.term_current_days} 天 · "
                    f"距 {cal.term_next} {cal.term_next_days} 天（{cal.term_next_date}）")
    line("节日", OK if cal.badge else WARN,
         f"今天的徽章：{cal.badge or '（无）'}｜{cal.upcoming or '近期没有节日'}")
    line("当日", OK, f"宜 {' '.join(cal.yi)}｜忌 {' '.join(cal.ji)}｜"
                    f"{cal.chong} {cal.sha} · {cal.zhiri}日")
    print("     ↑ 农历表覆盖 1884-2101，不联网、无第三方依赖。"
          "对不上官方万年历时先看系统时区是不是 Asia/Shanghai。")


def probe_ai(cfg: Config) -> None:
    print("\n【速览 · 模型接口】")
    if not cfg.get("digest.enabled", True):
        line("开关", WARN, "digest.enabled=false，速览已关闭（默认就是这样）")
        print("     · 这一整条链路（25 个 RSS 源 + 模型调用）根本不会跑，"
              "所以这一节的探测结果对当前屏幕没有影响")
        print("     · 想开回来把 config.yaml 里 digest.enabled 改成 true")
        print("     · 注意 digest.endpoint 原来指向 GitHub Models（models.github.ai），")
        print("       需要 GitHub token；既然不走 GitHub 了，得先确认这条路在你机器上通")
    token = _find_token()
    if not token:
        line("凭据", WARN, "没有找到模型凭据 —— 会直接降级为原始标题，这是设计好的行为")
        print("     · 想在本地测 AI 摘要：设置环境变量，具体看 digest.py 里的 _find_token()")
        return
    line("凭据", OK, f"已找到（{token[:4]}…，长度 {len(token)}）")
    news = sources.fetch_news(cfg)
    total = sum(len(v) for v in news.values())
    line("候选新闻", OK if total else BAD,
         "、".join(f"{k} {len(v)}" for k, v in news.items()) or "一条都没有")
    if not total:
        return
    cats = [c for c in cfg.categories if news.get(c["key"])]
    names = [c.get("name") or c["key"] for c in cats]
    chunks = []
    for cat, name in zip(cats, names):
        rows = news[cat["key"]][:6]
        chunks.append(f"【{name}】\n" + "\n".join(
            f"{i}. {it['title']}" for i, it in enumerate(rows, 1)))
    prompt = "以下是今天的候选新闻，请按栏目各挑一条编译成速览：\n\n" + "\n\n".join(chunks)
    from aiinfo.digest import _call_model
    t0 = time.time()
    items = _call_model(cfg, prompt, names)
    if items:
        line("模型", OK, f"{cfg.get('digest.model')}（{time.time() - t0:.1f}s）")
        for it in items:
            print(f"       · [{it.get('category_name')}] {it['title']}")
            if it.get("summary"):
                print(f"         {it['summary']}")
    else:
        line("模型", BAD, "调用失败，检查模型名是否有免费额度、或是否超出每日限流")


def main() -> int:
    # 先把 stdout 调成"绝不抛异常"模式：中文里夹了零宽字符或生僻字时，
    # GBK 控制台会直接把整个自检打断，而在 Actions 上又看不出问题
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="探测各数据源可用性")
    parser.add_argument("-c", "--config", default="dashboard/config.yaml")
    parser.add_argument("--only", choices=["weather", "calendar", "quotes", "feeds", "ai"],
                        help="只探测某一类")
    args = parser.parse_args()

    path = Path(args.config)
    if not path.is_absolute():
        path = ROOT / path
    cfg = Config.load(str(path))

    print("=" * 66)
    print(f" 数据源自检 · {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" 配置：{path}")
    print(f" 位置：{cfg.get('location.name')}（{cfg.get('location.latitude')}, {cfg.get('location.longitude')}）")
    print(f" 机型：{cfg.get('device.model')} -> {cfg.size[0]}x{cfg.size[1]}")
    print("=" * 66)

    jobs = {"weather": probe_weather, "calendar": probe_calendar, "quotes": probe_quotes,
            "feeds": probe_feeds, "ai": probe_ai}
    for key, fn in jobs.items():
        if args.only and args.only != key:
            continue
        try:
            fn(cfg)
        except Exception as exc:                            # noqa: BLE001
            print(f"  {BAD} {key} 探测时异常：{exc}")

    print("\n提示：换天气源改 config.yaml 里 weather（provider / qweather.api_host）；")
    print("      换新闻源改 categories 各栏目的 feeds；换行情改 quotes.items。")
    print("      改完下一轮定时任务就生效，版式改完记得跑 layout_check.py。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
