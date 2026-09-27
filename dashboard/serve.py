#!/usr/bin/env python3
"""局域网快道：点一下就出图，Kindle 下一班来取（默认 10 分钟内）。

为什么还要它：GitHub 那条路"出图"要跑一轮 Actions（约 1 分钟），前面还压着一层
CDN 缓存。这台电脑开着的时候，直接让它跑同一段 generate.py，几秒就有图。

⚠️ 它是**加速器，不是替代**。设备那边 DASHBOARD_URLS 是"按顺序试"，局域网排在
第一个，电脑关着 / 服务没起时这一条几百毫秒就失败，自动落到 GitHub —— 所以这个
服务停了，屏只是变慢，不会白屏。原来"电脑不是服务器"的约束没有被推翻。

⚠️ 本机出图要有和风的 Key（环境变量 / 命令行带，仓库里不能写）。没有它时 generate.py
会**成功**画出一张没有天气的图 —— 所以这里出图后要自检，空图不发，屏上继续是云端那张。

  python dashboard/serve.py                     # 监听 0.0.0.0:8731
  python dashboard/serve.py --port 8731 --poll-hint 10

它只**加速**，不改云端默认：按这里发布的图立刻可取，但 config.yaml 里写的版式不变，
电脑一关、设备落回 GitHub 时，屏上会变回配置文件那套。想把默认也换掉，就去 Pages
那个调试台按「发布」（它触发一轮 Actions，把选择记进 screen 分支）。

路由：
  GET  /                一个极简控制台（三个按钮）
  GET  /dashboard.png   设备来取图。**发的是"有内容的最新那张"**：本机亲手出的、
                        自检有数据的图优先，否则原样转发云端那张 —— 硬盘上恰好躺着
                        一个旧文件不等于那是该上屏的那张
  GET  /status          现在这张图的时间/大小/用的哪套版式
  GET  /state           设备上报 + 本机这一趟会发哪张 + 两者是否同一张
  POST /publish?skin=…  现场出一张（skin 省略 = 用配置文件里那套）

只在家庭局域网里用：默认监听所有网卡且**没有任何鉴权** —— 同一 WiFi 下任何人都能
按那个发布按钮。想收紧就 `--bind 127.0.0.1`（但那样 Kindle 就取不到了）。
"""

from __future__ import annotations

import argparse
import json
import os
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
DEBUG = ROOT / "docs" / "debug.json"
BUILT = ROOT / "docs" / "dashboard.built.json"

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


def cksum_of_bytes(data: bytes) -> str:
    import zlib
    return f"{zlib.crc32(data) & 0xffffffff} {len(data)}" if data else ""


def cksum_of(path) -> str:
    """算出和设备上 busybox cksum 一样的 "CRC 字节数"。

    为什么能对上：cksum 用的就是 POSIX CRC-32（zlib.crc32 同一个多项式），
    输出是 "校验和 字节数"。所以本机和设备能对同一张图给出同一个串，
    调试台才敢说"屏上这张 == 我显示的这张"，而不是靠时间猜。
    """
    try:
        return cksum_of_bytes(path.read_bytes())
    except Exception:
        return ""


def can_build() -> tuple[bool, str]:
    """这台电脑能不能现场出**有天气**的图。

    为什么先问这个：和风的 Key 只在 GitHub Secrets 里（仓库是公开的，不能写进配置）。
    本机没有它时 generate.py 照样"成功"，只是画出一张没有天气的图 —— 而局域网地址排在
    设备取图列表第一个，这张空图会盖掉云端那张好的。按钮按下去之前就该说清楚。
    """
    e = os.environ
    if not (e.get("QWEATHER_HOST") or "").strip():
        return False, "本机没有 QWEATHER_HOST"
    jwt = all((e.get(k) or "").strip()
              for k in ("QWEATHER_ISS", "QWEATHER_SUB", "QWEATHER_KID", "QWEATHER_PRIVATE_KEY"))
    if jwt:
        return True, ""
    if (e.get("QWEATHER_KEY") or "").strip():
        return True, ""
    return False, "本机没有和风凭据（QWEATHER_ISS/SUB/KID/PRIVATE_KEY 或 QWEATHER_KEY，它们只在 GitHub Secrets 里）"


def empty_reason() -> str:
    """本机这张图是不是"没内容"。出空白图不会报错，所以只能自己查。

    为什么必须查：局域网地址排在 DASHBOARD_URLS **第一个**，本机这张会盖掉云端那张
    好的 —— 出一次没有数据的图，屏上就是一片空。真发生过。
    """
    try:
        d = json.loads(DEBUG.read_text(encoding="utf-8"))
    except Exception:
        return "读不到 debug.json，不知道这张图里有没有数据"
    if not (d.get("counts") or {}).get("weather"):
        return "这张图没有天气那一块（出图时没拿到和风的数据）"
    return ""


def built_hash() -> str:
    """上一次**在这个服务上**按「立刻出图」跑出来的那张是谁。"""
    try:
        return json.loads(BUILT.read_text(encoding="utf-8")).get("hash", "")
    except Exception:
        return ""


def mark_built() -> str:
    h = cksum_of(IMAGE)
    try:
        BUILT.write_text(json.dumps({"hash": h, "at": int(time.time())}), encoding="utf-8")
    except Exception:
        pass
    return h


MIRROR = {"at": 0.0, "data": b"", "lm": 0.0, "err": ""}
MIRROR_TTL = 60.0


def cloud_image() -> dict:
    """云端那张（GitHub Pages），带 60 秒缓存。

    用 Pages 不用 raw.githubusercontent：这台机器上 raw 那条经常直接连不通，
    Pages 是通的 —— 和 DASHBOARD_URLS 里的顺序一致。
    """
    url = f"https://{PAGES_HOST}/{REPO_NAME}/dashboard.png"
    now = time.time()
    if MIRROR["data"] and now - MIRROR["at"] < MIRROR_TTL:
        return MIRROR
    try:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "aistatus-lan-lane"})
        if MIRROR["data"] and MIRROR["lm"]:
            req.add_header("If-Modified-Since",
                           time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(MIRROR["lm"])))
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                MIRROR["data"] = r.read()
                MIRROR["err"] = ""
                # lm 只认云端给的 Last-Modified：没有这个头就当不知道云端那张多新，
                # 此时本机刚出的那张优先（current_image 里 lm=0 走这个分支）
                MIRROR["lm"] = 0.0
                lm = r.headers.get("Last-Modified")
                if lm:
                    from email.utils import parsedate_to_datetime
                    MIRROR["lm"] = parsedate_to_datetime(lm).timestamp()
        except urllib.error.HTTPError as exc:
            if exc.code != 304:
                raise
            MIRROR["err"] = ""       # 304 = 还是我缓存里那张，不是故障
        MIRROR["at"] = now
    except Exception as exc:
        MIRROR["err"] = f"{type(exc).__name__}: {exc}"
        MIRROR["at"] = now           # 失败也歇 60 秒，别每次设备来取都重连一遍
    return MIRROR


def current_image():
    """这一趟给设备哪一张。返回 (bytes, 是谁, 一句话说明)。

    规矩只有一条：**这张必须是"有内容的最新那张"**，而不是"本机硬盘上恰好躺着的
    那张"。所以：
      1. 只有这个服务亲手出的、且自检有数据的、且不比云端旧的图，才算本机这张有效；
      2. 否则发云端那张（局域网只是加速器，内容仍以云端为准）；
      3. 云端也取不到时，退而发本机这张，但把"可能是旧的/空的"写在说明里。
    """
    m = cloud_image()
    if IMAGE.exists():
        st = IMAGE.stat()
        mine = cksum_of(IMAGE)
        if mine and mine == built_hash() and st.st_size:
            if not m["lm"] or st.st_mtime >= m["lm"]:
                return IMAGE.read_bytes(), "本机刚出的", ""
            if m["data"]:
                return m["data"], "云端镜像", "本机那张虽然是在这里出的，但云端后来又出了一班更新的"
            return IMAGE.read_bytes(), "本机那张（云端取不到）", ""
        why = "硬盘上那张不发：" + (empty_reason() or "它不是在调试台按「立刻出图」出的")
        if m["data"]:
            return m["data"], "云端镜像", why
        return IMAGE.read_bytes(), "硬盘上那张（退路）", why + f"，而云端取不到（{m['err']}）"
    if m["data"]:
        return m["data"], "云端镜像", "本机还没有图"
    return b"", "", f"本机没有图，云端也取不到（{m['err']}）"

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
    ok_creds, why = can_build()
    if not ok_creds:
        return False, why + " —— 出了也是一张没有天气的图，不发。要立刻换新内容请去云端那份调试台按发布。"
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
    reason = empty_reason()
    if reason:
        STATE["last_error"] = reason
        return False, f"本机出了图，但**不发**：{reason}。屏上仍是云端那张好的。"
    STATE["last_error"] = None
    mark_built()
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
        try:
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError):
            # 对方中途挂断不是故障：浏览器换页会掐掉没看完的图，设备 curl 超时会直接走人。
            # 不接住它，socketserver 会替每个这样的请求打一整页 traceback，
            # 日志里真正有用的那几行就被埋了。
            pass

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/dashboard.png":
            # 必须 no-store：设备每来一次都要拿到真的，缓存一层就白改了
            body, src, note = current_image()
            # 先记下"这一趟发的是哪张"再发：客户端中途挂断（浏览器换页很常见）时，
            # 写在发送之后的那行就丢了，而这条日志是事后唯一说得清设备拿到什么的东西
            sys.stderr.write(f"  [发图] {src} {cksum_of_bytes(body)}"
                             + (f" —— {note}" if note else "") + "\n")
            if body:
                self._send(200, body, "image/png")
            else:
                self._send(404, note.encode("utf-8") or b"no image yet", "text/plain")
            return
        if path.startswith("/skins/") or path == "/skin.txt":
            # 皮肤墙要 fetch manifest.json 拿清单，页面也要读 skin.txt 才知道
            # 屏上现在挂的是哪套。<img> 跨域能显示，fetch 不行 —— GitHub Pages
            # 不发 Access-Control-Allow-Origin。所以在这里转一道手，页面只用相对路径。
            import urllib.request
            url = f"https://{PAGES_HOST}/{REPO_NAME}{path}"
            try:
                with urllib.request.urlopen(url, timeout=15) as r:
                    body = r.read()
                ctype = ("application/json" if path.endswith(".json")
                         else "text/plain; charset=utf-8" if path.endswith(".txt")
                         else "image/png")
                self._send(200, body, ctype)
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
            # 把设备上报的、**这一趟真正会发出去的那张**、算好的比对结果一起给调试台。
            # 比对必须用"发出去的那张"而不是硬盘上那个文件：局域网快道可能正在发云端镜像。
            out = {"device": None, "served": "", "hash": "", "note": "", "empty": empty_reason()}
            ok_creds, why = can_build()
            out["can_build"] = {"ok": ok_creds, "why": why}
            try:
                body_bytes, src, note = current_image()
                out["served"], out["hash"], out["note"] = src, cksum_of_bytes(body_bytes), note
            except Exception as exc:
                out["note"] = f"{type(exc).__name__}: {exc}"
            try:
                if DEVSTATE.exists():
                    rec = json.loads(DEVSTATE.read_text(encoding="utf-8"))
                    rec["same"] = bool(rec.get("hash")) and rec["hash"] == out["hash"]
                    out["device"] = rec
            except Exception:
                pass
            self._send(200, json.dumps(out, ensure_ascii=False).encode(), "application/json")
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
            info["empty"] = empty_reason()
            info["adopted"] = bool(IMAGE.exists()) and cksum_of(IMAGE) == built_hash()
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
