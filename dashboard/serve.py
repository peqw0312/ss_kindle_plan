#!/usr/bin/env python3
"""局域网快道：点一下就出图，Kindle 下一班来取（默认 10 分钟内）。

为什么还要它：GitHub 那条路"出图"要跑一轮 Actions（约 1 分钟），前面还压着一层
CDN 缓存。这台电脑开着的时候，直接让它跑同一段 generate.py，几秒就有图。

⚠️ 它是**加速器，不是替代**。设备那边 DASHBOARD_URLS 是"按顺序试"，局域网排在
第一个，电脑关着 / 服务没起时这一条几百毫秒就失败，自动落到 GitHub —— 所以这个
服务停了，屏只是变慢，不会白屏。原来"电脑不是服务器"的约束没有被推翻。

  python dashboard/serve.py                     # 监听 0.0.0.0:8731
  python dashboard/serve.py --port 8731 --poll-hint 10

它只**加速**，不改云端默认：按这里发布的图立刻可取，但 config.yaml 里写的版式不变，
电脑一关、设备落回 GitHub 时，屏上会变回配置文件那套。想把默认也换掉，就去 Pages
那个调试台按「发布」（它触发一轮 Actions，把选择记进 screen 分支）。

路由：
  GET  /                一个极简控制台（三个按钮）
  GET  /dashboard.png   最新那张图（no-store，设备每来一次都要拿真的）
  GET  /status          现在这张图的时间/大小/用的哪套版式
  POST /publish?skin=…  现场出一张（skin 省略 = 用配置文件里那套）

只在家庭局域网里用：默认监听所有网卡且**没有任何鉴权** —— 同一 WiFi 下任何人都能
按那个发布按钮。想收紧就 `--bind 127.0.0.1`（但那样 Kindle 就取不到了）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "docs" / "dashboard.png"
CONFIG = ROOT / "dashboard" / "config.yaml"
GEN = ROOT / "dashboard" / "generate.py"

# 和 render.py 的 Renderer.LAYOUTS 对齐。这里抄一份是因为不想为了三个名字
# 在设备/服务两侧都依赖导入生产代码；对不上的话 generate.py 自己会退回默认
# 并在日志里说清可选值，不会静默画错。
LAYOUTS = ("c1", "arc")

STATE = {"last_build": None, "last_error": None, "skin": None, "seconds": None}

REPO_NAME = "ss_kindle_plan"
PAGES_HOST = "peqw0312.github.io"
MONITOR = ROOT / "docs" / "monitor.html"
POLL = ROOT / "docs" / "poll.json"
DEVSTATE = ROOT / "docs" / "device_state.json"


def cksum_of(path) -> str:
    """算出和设备上 busybox cksum 一样的 "CRC 字节数"。

    为什么能对上：cksum 用的就是 POSIX CRC-32（zlib.crc32 同一个多项式），
    输出是 "校验和 字节数"。所以本机和设备能对同一张图给出同一个串，
    调试台才敢说"屏上这张 == 我显示的这张"，而不是靠时间猜。
    """
    import zlib
    try:
        data = path.read_bytes()
    except Exception:
        return ""
    return f"{zlib.crc32(data) & 0xffffffff} {len(data)}"
BOOST_SECONDS = 30          # 调屏模式下设备每隔多少秒来取一次
BOOST_WINDOW = 30 * 60      # 调屏持续多久，到点自己回落到平时的 10 分钟


def write_poll(boost_until: int) -> None:
    """把"下一班多久来取"写成一个小文件，设备每轮醒来读它。

    为什么设备只能"被问"不能"被推"：它睡着时 WiFi 射频是关的（实测 ping 不通），
    所以没有任何办法把它叫醒。能做的只有让它下次醒来时顺手问一句"接下来睡多久" ——
    这就是 boost 的全部机制，也解释了为什么第一次按发布仍要等到它下一班
    （平时最多 10 分钟），之后才变成 30 秒一班。
    """
    import json as _json
    try:
        POLL.parent.mkdir(parents=True, exist_ok=True)
        POLL.write_text(_json.dumps({"boost_until": boost_until,
                                     "boost_seconds": BOOST_SECONDS}), encoding="utf-8")
    except Exception as exc:
        print(f"  [提醒] poll.json 写不出去，调屏模式不会生效：{type(exc).__name__}: {exc}")


def boost_now() -> int:
    """按发布 / 按调屏时调用：把高频窗口往后推 30 分钟。"""
    import time as _t
    until = int(_t.time()) + BOOST_WINDOW
    write_poll(until)
    return until


def build(skin: str | None) -> tuple[bool, str]:
    """跑一次出图。返回 (成没成, 给人看的一句话)。"""
    cmd = [sys.executable, str(GEN), "--config", str(CONFIG), "--no-html"]
    if skin:
        cmd += ["--layout", skin]
    t0 = time.time()
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=str(ROOT))
    except Exception as exc:                       # 超时、python 没了……都要报出来
        STATE["last_error"] = f"{type(exc).__name__}: {exc}"
        return False, STATE["last_error"]
    STATE["seconds"] = round(time.time() - t0, 1)
    if res.returncode != 0:
        tail = (res.stderr or res.stdout or "").strip().splitlines()[-3:]
        STATE["last_error"] = " / ".join(tail) or f"退出码 {res.returncode}"
        return False, STATE["last_error"]
    STATE["last_error"] = None
    STATE["last_build"] = datetime.now().strftime("%H:%M:%S")
    STATE["skin"] = skin or "（配置文件的默认）"
    return True, f"已出图（{STATE['seconds']}s）"


PAGE = """<!doctype html><meta charset=utf-8>
<title>信息屏 · 局域网快道</title>
<style>
 body{font:15px/1.6 system-ui,"Segoe UI",sans-serif;margin:28px;max-width:560px;color:#1c1917}
 button{font:inherit;padding:8px 14px;margin:4px 8px 4px 0;border:1px solid #d6d3d1;
        border-radius:6px;background:#fff;cursor:pointer}
 #out{margin-top:14px;padding:10px;border:1px solid #e7e5e4;border-radius:6px;
      font:13px ui-monospace,Consolas,monospace;white-space:pre-wrap;min-height:2.4em}
 img{margin-top:16px;width:100%;max-width:300px;border:1px solid #d6d3d1}
 .k{color:#78716c;font-size:13px}
</style>
<h1>信息屏 · 局域网快道</h1>
<p class="k">按一下就现场出图。Kindle 每 __STEP__ 分钟来取一次，所以最多等那么久就上屏；
它取的是这个服务的 <code>/dashboard.png</code>。电脑关着时设备自动改走 GitHub。</p>
<div>
  <button onclick="pub('')">出一张（默认版式）</button>
  __BUTTONS__
</div>
<div id="out">还没跑过。</div>
<img src="/dashboard.png?t=0" alt="当前这张">
<script>
async function pub(skin) {
  const out = document.getElementById('out');
  out.textContent = '正在出图…';
  const t0 = Date.now();
  try {
    const r = await fetch('/publish' + (skin ? '?skin=' + skin : ''), {method: 'POST'});
    const j = await r.json();
    out.textContent = (j.ok ? '✓ ' : '✗ ') + j.msg
      + (j.ok ? '\\n屏上最快 ' + Math.round((Date.now()-t0)/1000) + ' 秒后取到（设备下一班）' : '');
    document.querySelector('img').src = '/dashboard.png?t=' + Date.now();
  } catch (e) { out.textContent = '请求失败：' + e.message; }
}
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str, no_store: bool = True) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        if no_store:
            self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/dashboard.png":
            if IMAGE.exists():
                # 必须 no-store：设备每来一次都要拿到真的，缓存一层就白改了
                self._send(200, IMAGE.read_bytes(), "image/png")
            else:
                self._send(404, b"no image yet", "text/plain")
            return
        if path.startswith("/skins/"):
            # 皮肤墙要 fetch manifest.json 拿清单。<img> 跨域能显示，fetch 不行 ——
            # GitHub Pages 不发 Access-Control-Allow-Origin。所以在这里转一道手，
            # 页面只用相对路径，不用知道云端在哪。
            import urllib.request
            url = f"https://{PAGES_HOST}/{REPO_NAME}/{path[1:]}"
            try:
                with urllib.request.urlopen(url, timeout=15) as r:
                    body = r.read()
                self._send(200, body, "application/json" if path.endswith(".json") else "image/png")
            except Exception:
                self._send(404, b"not in the wall", "text/plain")
            return
        if path == "/report":
            # 设备上报"屏上现在贴的是哪张"。GET 带参数就行：设备那边只有 curl，
            # 让它发 POST + JSON 是给自己找麻烦。
            from urllib.parse import parse_qs as _pq
            q = _pq(urlparse(self.path).query)
            rec = {k: (q.get(k) or [""])[0] for k in ("ok", "hash", "src", "at")}
            import time as _t
            rec["seen"] = int(_t.time())
            try:
                DEVSTATE.write_text(json.dumps(rec), encoding="utf-8")
            except Exception:
                pass
            self._send(200, b'{"ok":true}', "application/json")
            return
        if path == "/state":
            # 把设备上报的、本机这张的、算好的比对结果一起给调试台。
            body = b'{"device":null}'
            try:
                if DEVSTATE.exists():
                    import json as _j
                    rec = _j.loads(DEVSTATE.read_text(encoding="utf-8"))
                    mine = cksum_of(IMAGE)
                    rec["local_hash"] = mine
                    rec["same"] = bool(rec.get("hash")) and rec["hash"] == mine
                    body = _j.dumps({"device": rec}).encode()
            except Exception as exc:
                body = ('{"error": "%s"}' % type(exc).__name__).encode()
            self._send(200, body, "application/json")
            return
        if path == "/poll.json":
            # 设备每轮醒来读它：几十十字节，比取整张图便宜得多
            body = POLL.read_bytes() if POLL.exists() else b'{"boost_until": 0}'
            self._send(200, body, "application/json")
            return
        if path == "/status":
            info = dict(STATE)
            info["image"] = str(IMAGE)
            info["exists"] = IMAGE.exists()
            if IMAGE.exists():
                st = IMAGE.stat()
                info["mtime"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                info["bytes"] = st.st_size
            self._send(200, json.dumps(info, ensure_ascii=False).encode(), "application/json")
            return
        if path in ("/", "/index.html"):
            # 调试台本体在这里也发一份：同一台机器、同一个协议，才谈得上"点一下推给
            # 设备"。Pages 那份是 HTTPS，去调 HTTP 的局域网接口会被浏览器按混合内容
            # 拦掉 —— 这是浏览器的规则，不是我们写法能绕的。
            if MONITOR.exists():
                self._send(200, MONITOR.read_bytes(), "text/html; charset=utf-8")
                return
            step = getattr(self.server, "poll_hint", "?")
            buttons = "".join(
                f'<button onclick="pub(\'{l}\')">发布 {l}</button>' for l in LAYOUTS)
            html = PAGE.replace("__STEP__", str(step)).replace("__BUTTONS__", buttons)
            self._send(200, html.encode(), "text/html; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        u = urlparse(self.path)
        if u.path == "/boost":
            until = boost_now()
            self._send(200, ('{"ok": true, "boost_until": %d, "every_seconds": %d}'
                             % (until, BOOST_SECONDS)).encode(), "application/json")
            return
        if u.path != "/publish":
            self._send(404, b"not found", "text/plain")
            return
        q = parse_qs(urlparse(self.path).query)
        skin = (q.get("skin") or [""])[0].strip()
        if skin and skin not in LAYOUTS:
            self._send(200, json.dumps({"ok": False, "msg": f"不认识的版式「{skin}」，可选：{' / '.join(LAYOUTS)}"}).encode(),
                       "application/json")
            return
        boost_now()          # 按发布 = 我在改东西，顺手把调屏窗口续上
        ok, msg = build(skin or None)
        msg += f"（已进调屏模式：每 {BOOST_SECONDS} 秒一班，30 分钟后自动回落）"
        self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False).encode(),
                   "application/json")

    def log_message(self, fmt: str, *args) -> None:
        # 只打一行，别把设备的每次取图都刷成两行噪音
        sys.stderr.write("  %s\n" % (fmt % args))


def main() -> int:
    # Windows 控制台默认 GBK，⚠️ 这类字符一 print 就抛 UnicodeEncodeError，
    # 会把服务**在启动那一下打死**（真发生过：警告分支只在 0.0.0.0 时走，
    # 用 127.0.0.1 测的时候根本碰不到）。改成打不出来就替换成 ?，不影响功能。
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="信息屏局域网快道")
    ap.add_argument("--port", type=int, default=8731)
    ap.add_argument("--bind", default="0.0.0.0",
                    help="0.0.0.0 才能被 Kindle 访问；127.0.0.1 只给自己看")
    ap.add_argument("--poll-hint", default="10",
                    help="设备取图间隔（分钟），只用于页面上那句话")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    srv.poll_hint = args.poll_hint            # type: ignore[attr-defined]
    url = f"http://{args.bind}:{args.port}/"
    print(f"局域网快道已启动：{url}")
    print(f"  图：{IMAGE}")
    print(f"  设备侧应把 http://<这台电脑的IP>:{args.port}/dashboard.png 放在 DASHBOARD_URLS 第一位")
    if args.bind == "0.0.0.0":
        print("  ⚠️ 无鉴权，同一 WiFi 内任何人都能按发布按钮。家庭局域网可以，公共网络别开。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。设备会自动落回 GitHub 那条路，不用管它。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
