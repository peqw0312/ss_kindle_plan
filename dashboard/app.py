#!/usr/bin/env python3
"""云端出图服务：把一个 HTTP 端口变成「随时问、随时给图」的接口。

用法：
    python dashboard/app.py                  # 本机试跑，默认监听 8000
    PORT=8080 python dashboard/app.py        # 云端由平台注入 PORT

和 serve.py（局域网那版）的分工：
    serve.py  发的是 docs/ 里**已经算好**的静态文件 —— 简单，但电脑必须常开；
    app.py    自己就是生成器，跑在云端 —— 电脑关机、Kindle 不在同一个网络都行。

四个必须守住的点（每一条都是真踩过的坑）：

1. **响应头禁缓存**。反代或 CDN 一旦对 /dashboard.png 回 304，Kindle 的 curl 会
   当成「下载成功但文件没变」，屏幕就永远停在上一张，而两端日志全绿。
   这个故障排查起来极痛苦，所以宁可用 no-store 把缓存整条链路掐死。
2. **重建失败不清旧图**。上游接口抖一下是常态，这时把上一张好图继续发出去，
   屏幕上显示「20 分钟前的图」远好过显示一片空白或一个 500。
3. **重建要单飞**。Kindle 超时重试、或者你同时在浏览器里刷新，会打并发；
   天真的实现会让几个线程同时去抓天气和行情，浪费又容易被上游限流。
4. **冷启动不能等**。进程刚起来时缓存是空的，第一个请求要等 3~8 秒才出图，
   而 Kindle 的下载超时是 60 秒 —— 不至于失败，但没必要。启动时后台先预热一张。

出图时机
--------
不是"每 15 分钟"，而是**按 config 里 cloud.refresh_at 的时刻表**（默认一天四次：
00:05 翻日期、05:10 美股收盘、12:00、15:05 A 股收盘）。时钟已经交给 Kindle
本机画了，图不必再为了"时间准"而反复重算；天气和指数一天变几次就够。
判定按 `location.timezone`，**不看容器本地时间**（容器多半是 UTC）。
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import generate as pipeline                  # noqa: E402  # 复用主程序的抓取逻辑，绝不抄第二遍
from aiinfo import __version__               # noqa: E402
from aiinfo.config import Config             # noqa: E402
from aiinfo.lunar import calendar_info       # noqa: E402
from aiinfo.render import Renderer           # noqa: E402

#: 每个响应都压上这套头。见模块 docstring 第 1 条。
NO_CACHE_HEADERS = (
    ("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"),
    ("Pragma", "no-cache"),
    ("Expires", "0"),
)

#: 到点之后额外留给重建完成的时间，Kindle 按「时刻 + 这个缓冲」回来取才保证是新的。
#: 后台线程最长 30 秒醒一次检查到点，出图本身实测 1~8 秒，取 120 秒留足余量。
#: 少了这个缓冲就会出事：Kindle 正好卡在整点回来，那一刻手里还是上一档的旧图，
#: 而 `next_slot()` 已经翻到下一档了 —— 于是它被告知"睡到 5 小时后再来"，
#: 把这一档的新图整个错过，屏幕上就是一上午的昨天日期。
BUILD_GRACE_SECONDS = 120

#: 刚过点、后台重建还在路上的那段时间里，让 Kindle 隔多久再来一趟。
#: 比 `cloud.refresh_minutes` 短，所以新图最多晚 5 分钟出现；
#: 只在"到点后的一小会儿"生效，重建真失败时不会退化成高频轮询（见 next_fetch_at）。
STALE_RETRY_SECONDS = 300

CONFIG_PATH = Path(os.environ.get("AIINFO_CONFIG") or (HERE / "config.yaml"))


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def resolve_zone(name: str):
    """拿时区对象。拿不到就退回本机时区。

    **Windows 默认没有 IANA 时区库**（那是要另装 tzdata 包的），
    `ZoneInfo("Asia/Shanghai")` 会直接抛 ZoneInfoNotFoundError；
    云端是 Linux，/usr/share/zoneinfo 现成的，所以那边一直正常 ——
    也就是说这个坑只在"本机跑 app.py 试一下"时才炸，很容易漏。
    退回本机时区是等价的：本机时区本来就是按用户所在地设的，
    而中国没有夏令时，固定偏移和真时区一年到头完全一致。
    """
    try:
        return ZoneInfo(name), None
    except Exception as exc:                                  # noqa: BLE001
        local = datetime.now().astimezone().tzinfo or timezone.utc
        return local, f"{name}（{type(exc).__name__}，已退回本机时区）"


class Schedule:
    """按「时刻表」出图，而不是按固定间隔。

    为什么不用固定间隔：时间已经交给 Kindle 本机画了（见 config 的 clock.mode），
    图不再需要为了"时间准"而反复重算 —— 一天出四次就够。固定间隔会被迫
    按"最勤的那个需求"跑，而真正需要新数据的时刻其实只有几个。

    判定依据是「最近一个该出图的时刻有没有被出过」，不是"距上次过了多久"：
    进程重启、机器休眠、请求抖动都不会漏掉某一档，恢复了就自动补上。

    **时区不能省**：云端容器多半跑在 UTC，按它的本地时间判会把 00:05 当成
    北京时间 08:05，凌晨那档数据就全错位了。
    """

    def __init__(self, times: list[str], tz_name: str, fallback_seconds: int):
        self.tz, self.tz_fallback = resolve_zone(tz_name)
        self.slots: list[tuple[int, int]] = sorted(
            self._parse(t) for t in (times or []) if str(t).strip())
        self.fallback = max(60, int(fallback_seconds))

    @staticmethod
    def _parse(text: str) -> tuple[int, int]:
        hour, _, minute = str(text).strip().partition(":")
        h, m = int(hour), int(minute or 0)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"时刻表里的 {text!r} 不是合法时间")
        return h, m

    @property
    def enabled(self) -> bool:
        return bool(self.slots)

    def _at(self, local: datetime, slot: tuple[int, int]) -> datetime:
        return local.replace(hour=slot[0], minute=slot[1], second=0, microsecond=0)

    def last_slot(self, now: datetime | None = None) -> datetime | None:
        """最近一个「已经到点」的出图时刻；都还没到就取昨天的最后一个。"""
        if not self.enabled:
            return None
        local = (now or datetime.now(self.tz)).astimezone(self.tz)
        for slot in reversed(self.slots):
            candidate = self._at(local, slot)
            if candidate <= local:
                return candidate
        return self._at(local, self.slots[-1]) - timedelta(days=1)

    def next_slot(self, now: datetime | None = None) -> datetime:
        local = (now or datetime.now(self.tz)).astimezone(self.tz)
        for slot in self.slots:
            candidate = self._at(local, slot)
            if candidate > local:
                return candidate
        return self._at(local, self.slots[0]) + timedelta(days=1)

    def describe(self) -> str:
        if not self.enabled:
            return f"每 {self.fallback // 60} 分钟（未配置时刻表）"
        zone = self.tz_fallback or str(self.tz)
        return "、".join(f"{h:02d}:{m:02d}" for h, m in self.slots) + f"（{zone}）"


class Board:
    """一张 PNG 的缓存 + 单飞重建。

    对外只有两个方法：`png()` 拿图（必要时重建），`peek()` 看状态（绝不阻塞）。
    """

    def __init__(self, cfg: Config, schedule: Schedule):
        self.cfg = cfg
        self.schedule = schedule
        self._lock = threading.Lock()
        self._png: bytes | None = None
        self._built_at: float = 0.0
        self._meta: dict = {"ok": False, "reason": "还没生成过"}
        self.builds = 0

    # ---- 状态 ----
    def age(self) -> float | None:
        return (time.time() - self._built_at) if self._built_at else None

    def stale(self) -> bool:
        """该不该重新出图。

        有时刻表就按"最近一档到点了没有"判 —— 进程重启后 `_built_at` 是 0，
        自然补上最近那一档，不会漏数据。没配时刻表才退回固定间隔。
        """
        due = self.schedule.last_slot()
        if due is None:
            age = self.age()
            return age is None or age >= self.schedule.fallback
        return self._built_at < due.timestamp()

    def next_fetch_at(self) -> int:
        """Kindle 下一次来拉图「能拿到新图」的时刻（epoch 秒）。

        这是把服务端的时刻表接到客户端的唯一出口。没有它，Kindle 只能按固定
        间隔瞎轮询 —— 一天连 24 次 WiFi、下载 24 张图，其中 20 张一模一样。
        有了它，下载次数掉到一天四五次，而"几点换新图"这件事仍然是服务端说了算：
        改 `cloud.refresh_at` 重新发布即可，Kindle 那边一个字节都不用动。

        手里这张过期时要分两种情况，一刀切都会错：

        - **刚过点**（重建还在后台路上，最长 ~35 秒）：报一个短得多的时刻让它再来一趟。
          若直接报下一档，Kindle 在 00:05:01 来问就会被告知"睡到 05:10"，
          把这一档的新图整个错过，屏幕上是一上午的昨天日期。
        - **过期很久**（说明重建一直在失败）：仍然报下一档。这时反复短轮询毫无意义，
          只会把电池耗光 —— 上游挂掉的时候，最坏情况反而比原来的小时轮询更费电。
        """
        now = int(time.time())
        if not self.schedule.enabled:
            return now + self.schedule.fallback
        next_slot = int(self.schedule.next_slot().timestamp()) + BUILD_GRACE_SECONDS
        if not self.stale():
            return next_slot
        due = self.schedule.last_slot()
        if due is not None and now - int(due.timestamp()) < self.schedule.fallback:
            return min(next_slot, now + STALE_RETRY_SECONDS)
        return next_slot

    def peek(self) -> dict:
        """给 /health 用：只读状态，不触发任何重建。"""
        age = self.age()
        return {
            **self._meta,
            "has_image": self._png is not None,
            "bytes": len(self._png) if self._png else 0,
            "age_seconds": round(age, 1) if age is not None else None,
            "schedule": self.schedule.describe(),
            "next_build_at": (
                self.schedule.next_slot().isoformat(timespec="minutes")
                if self.schedule.enabled else None),
            "next_fetch_at": self.next_fetch_at(),
            "fallback_seconds": self.schedule.fallback,
            "builds": self.builds,
        }

    # ---- 取图 ----
    def png(self, force: bool = False) -> bytes | None:
        if not force and not self.stale():
            return self._png

        # 单飞：已经有图在手时**不去抢锁**，直接把旧图发出去，
        # 让正在重建的那个线程慢慢算 —— 请求不该为了「新 30 秒」多等 5 秒。
        if not self._lock.acquire(blocking=self._png is None):
            return self._png
        try:
            # 拿到锁之后重新判一次：可能刚才那个线程已经算好了
            if not force and not self.stale():
                return self._png
            self._rebuild()
        finally:
            self._lock.release()
        return self._png

    def _rebuild(self) -> None:
        t0 = time.time()
        try:
            data = pipeline.collect(self.cfg)
            data["generated_at"] = datetime.now()
            # 日历是纯本地推算（不联网、不依赖第三方库），放在时间定稿之后算
            data["calendar"] = calendar_info(data["generated_at"])

            renderer = Renderer(self.cfg, data)
            image = renderer.render().convert("L")
            buf = io.BytesIO()
            image.save(buf, format="PNG", optimize=True)
            png = buf.getvalue()
        except Exception as exc:                              # noqa: BLE001
            # 失败就失败，旧图照发。只把原因记下来给 /health 看。
            self._meta = {
                "ok": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(limit=8).splitlines()[-6:],
                "last_success_at": (
                    datetime.fromtimestamp(self._built_at).isoformat()
                    if self._built_at else None
                ),
            }
            log(f"重建失败（继续发上一张图）：{type(exc).__name__}: {exc}")
            return

        weather = data.get("weather") or {}
        quotes = data.get("quotes") or []
        self._png = png
        self._built_at = time.time()
        self.builds += 1
        self._meta = {
            "ok": True,
            "generated_at": data["generated_at"].isoformat(timespec="seconds"),
            "version": __version__,
            "device": self.cfg.get("device.model"),
            # 版面预设也报出来：它是 config 里能改的一项，线上跑的是哪个
            # 只能靠这个字段确认，不然"我改了 preset 生效没有"没法查。
            "style_preset": getattr(renderer, "preset", None),
            "size": list(self.cfg.size),
            "fonts": renderer.fonts.describe(),
            "notes": list(renderer.notes),
            # 时钟这块单独报出来：Kindle 端的 clock.conf 必须和它一致，
            # 不一致就说明改了字号没重新生成精灵图，时钟会错位。
            "clock_mode": self.cfg.get("clock.mode", "image"),
            "clock_region": list(renderer.clock_box) if renderer.clock_box else None,
            "weather_source": weather.get("source"),
            "weather": (
                f"{weather.get('desc')} {weather.get('temp')}°" if weather else "无数据"
            ),
            "quotes_valid": sum(1 for q in quotes if q.get("price") is not None),
            "quotes_total": len(quotes),
        }
        log(f"重建完成 {len(png) / 1024:.1f} KB，耗时 {time.time() - t0:.1f}s，"
            f"天气源 {weather.get('source') or '无'}")

    def warm_up(self) -> None:
        """启动时后台预热一张，免得第一个请求干等。"""
        threading.Thread(target=self.png, daemon=True, name="warmup").start()

    def keep_fresh(self, stop: threading.Event) -> None:
        """后台常驻刷新：到点就重算，让 Kindle 那一下几乎零等待。

        按时刻表睡到下一档，而不是每 N 秒醒一次空转 —— 没到点就该什么都不做。
        睡再长也切成不超过半小时的小段：容器挂起、时钟被校正是常事，
        长睡一觉很容易整体错过一档。
        真出问题时也不会把服务拖崩 —— 循环里任何异常都被 _rebuild 自己吞掉了。
        """
        while True:
            wait = self.schedule.fallback
            if self.schedule.enabled:
                delta = self.schedule.next_slot() - datetime.now(self.schedule.tz)
                wait = max(30, min(delta.total_seconds(), 1800))
            if stop.wait(wait):
                return
            if not self.stale():
                continue
            try:
                self.png(force=True)
            except Exception as exc:                          # noqa: BLE001
                log(f"后台刷新异常（忽略）：{exc}")


def preview_html(board: Board) -> bytes:
    """给人看的首页：手机或电脑打开就能看当前这张图。

    带一段内联 JS 定时刷新 —— meta refresh 会把整个页面重新拉一遍，
    而图片走的是同一个 URL，浏览器很容易拿缓存；加时间戳强制回源最省心。
    """
    meta = board.peek()
    generated = meta.get("generated_at") or "尚未生成"
    ok = meta.get("ok")
    status = "正常" if ok else f"异常：{meta.get('reason', '未知')}"
    color = "#1a7f37" if ok else "#b42318"
    font = meta.get("fonts") or ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 信息屏</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; background: #f5f6f7; color: #1f2328;
         font: 14px/1.6 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }}
  .wrap {{ max-width: 620px; margin: 0 auto; padding: 16px; }}
  .card {{ background: #fff; border: 1px solid #e3e5e8; border-radius: 10px;
           overflow: hidden; }}
  img {{ display: block; width: 100%; height: auto; }}
  .bar {{ display: flex; align-items: center; gap: 8px; padding: 10px 14px;
          border-bottom: 1px solid #e3e5e8; font-size: 13px; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; background: {color};
          flex: none; }}
  .meta {{ padding: 12px 14px; color: #59636e; font-size: 12px; line-height: 1.8;
           word-break: break-all; }}
  code {{ background: #f5f6f7; border-radius: 4px; padding: 1px 5px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="bar">
      <span class="dot"></span>
      <strong>AI 信息屏</strong>
      <span style="color:#59636e">· 生成于 {generated}</span>
      <span style="margin-left:auto;color:{color}">{status}</span>
    </div>
    <img id="shot" src="/dashboard.png?v=0" alt="AI 信息屏">
  </div>
  <div class="meta">
    Kindle 端填这个地址：<code id="url"></code><br>
    出图时刻：{meta.get('schedule', '—')} · 图片 {meta.get('bytes', 0) / 1024:.1f} KB<br>
    字体 {font}
  </div>
</div>
<script>
  var shot = document.getElementById('shot');
  document.getElementById('url').textContent = location.origin + '/dashboard.png';
  function tick() {{ shot.src = '/dashboard.png?t=' + Date.now(); }}
  setInterval(tick, {max(60, min(meta.get('fallback_seconds', 900), 600)) * 1000});
</script>
</body>
</html>
""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "aiinfo-cloud/1.0"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:                                 # noqa: N802
        self._route(head_only=False)

    def do_HEAD(self) -> None:                                # noqa: N802
        self._route(head_only=True)

    def _route(self, head_only: bool) -> None:
        path, _, query = self.path.partition("?")
        force = "force" in query
        try:
            if path in ("/", "/index.html"):
                self._send(preview_html(self.board), "text/html; charset=utf-8", head_only)
            elif path in ("/dashboard.png", "/dashboard", "/png"):
                png = self.board.png(force=force)
                if png is None:
                    self._fail(503, "图还没生成出来，稍等十几秒再试")
                else:
                    # X-Next-Image：告诉 Kindle 下一次什么时候来才有新图。
                    # 没有这个头它就只能按固定间隔轮询（一天 24 次，20 次拿回
                    # 完全相同的图）。局域网那台 serve.py 不发这个头，
                    # Kindle 端收不到就自动退回轮询 —— 备用路径不需要跟着改配置。
                    self._send(png, "image/png", head_only,
                               extra=(("X-Next-Image", str(self.board.next_fetch_at())),))
            elif path == "/health":
                body = json.dumps(self.board.peek(), ensure_ascii=False, indent=2)
                self._send(body.encode("utf-8"), "application/json; charset=utf-8", head_only)
            elif path == "/favicon.ico":
                self._send(b"", "image/x-icon", head_only, status=204)
            else:
                self._fail(404, f"没有这个路径：{path}\n试试 /dashboard.png 或 /health")
        except Exception as exc:                              # noqa: BLE001
            log(f"请求处理异常 {path}：{type(exc).__name__}: {exc}")
            try:
                self._fail(500, "服务内部错误，细节见服务端日志")
            except Exception:                                 # noqa: BLE001
                pass

    # ---- 响应helpers ----
    def _send(self, payload: bytes, content_type: str,
              head_only: bool, status: int = 200, extra=()) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        # Kindle 拿这个头校准自己的系统时间。时钟改由本机画之后，
        # 设备时间准不准**直接决定屏幕上显示的时间对不对**，所以每个响应都带上，
        # 让 Kindle 那边顺手就能比对，不用额外发一次请求。
        self.send_header("X-Epoch", str(int(time.time())))
        for key, value in extra:
            self.send_header(key, value)
        for key, value in NO_CACHE_HEADERS:
            self.send_header(key, value)
        self.end_headers()
        if not head_only and payload:
            self.wfile.write(payload)

    def _fail(self, status: int, message: str) -> None:
        self._send(message.encode("utf-8"), "text/plain; charset=utf-8", False, status=status)

    def log_message(self, fmt, *args) -> None:
        # 默认格式又长又带 Referer，换成「时间 客户端 请求行 状态」
        sys.stdout.write("%s  %-15s  %s\n" % (
            datetime.now().strftime("%H:%M:%S"),
            self.address_string(),
            fmt % args,
        ))
        sys.stdout.flush()

    def log_error(self, fmt, *args) -> None:
        # 断连、客户端取消之类的噪音不该淹掉真正的错误
        pass


def main() -> int:
    port = int(os.environ.get("PORT") or 8000)
    host = os.environ.get("HOST", "0.0.0.0")

    cfg = Config.load(str(CONFIG_PATH))
    tz_name = cfg.get("location.timezone", "Asia/Shanghai") or "Asia/Shanghai"
    schedule = Schedule(
        cfg.get("cloud.refresh_at") or [],
        tz_name,
        int(cfg.get("cloud.refresh_minutes", 15)) * 60,
    )
    board = Board(cfg, schedule)

    log(f"配置 {CONFIG_PATH}{'' if CONFIG_PATH.exists() else '（不存在，用默认值）'}")
    log(f"设备 {cfg.get('device.model')} · {cfg.size[0]}x{cfg.size[1]} · "
        f"{cfg.get('location.name')}")
    log(f"出图时刻 {schedule.describe()}")
    if schedule.tz_fallback:
        log(f"注意：读不到 IANA 时区 {schedule.tz_fallback}，已退回本机时区 —— "
            f"云端（Linux）不会有这个问题")
    log(f"时钟 clock.mode={cfg.get('clock.mode', 'image')}"
        + ("（图里留白，Kindle 用系统时间贴图）"
           if str(cfg.get('clock.mode', 'image')).lower() == 'local'
           else "（画进图里）"))

    Handler.board = board
    ThreadingHTTPServer.daemon_threads = True
    httpd = ThreadingHTTPServer((host, port), Handler)

    board.warm_up()
    stop = threading.Event()
    threading.Thread(target=board.keep_fresh, args=(stop,), daemon=True,
                     name="refresher").start()

    log(f"监听 http://{host}:{port}")
    log(f"  Kindle 端填 /dashboard.png，人看 / ，探活 /health")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("已停止。")
    finally:
        stop.set()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
