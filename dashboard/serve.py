#!/usr/bin/env python3
"""这台电脑是这块屏**唯一的出图端**：抓数据、画图、答给 Kindle。

2026-09-29 改的成本：原来出图在 GitHub Actions，用户按「反正电脑每天都开机，
慢吞吞的、网络还一堆问题」把它去掉了。换来两个必须一直记得的后果：

  · **电脑不在 = 屏停在最后一张，而且不会自愈。** 没有云端可退了。
    Windows 自动更新重启、服务被我停了、路由器抽风，都算"电脑不在"。
  · 屏上数据的新旧，完全由下面 AUTO_BUILD_MINUTES 那一轮决定。

它同时是设备的取图地址、"下一班多久来"的答复方、和调试台本体（调试台必须由它
托管才谈得上点一下推出 —— Pages 那份是 HTTPS，调 http://192.168.x.x 会被浏览器
按混合内容拦掉，那是浏览器的规则，不是写法能绕的）。

⚠️ 本机出图要有和风的凭据（起服务时从 .workbuddy/_qweather_local.env 读，不进仓库）。
没有它时 generate.py 会**成功**画出一张没有天气的图 —— 所以出图后必须自检，
空图不发：设备拿到 404 会留着上一张好的，这比贴一张空图强，也是现在唯一的安全网。

  python dashboard/serve.py                     # 监听 0.0.0.0:8731
  python dashboard/serve.py --auto-build 10     # 每 10 分钟自动出一张（0 = 关）

路由：
  GET  /                调试台（docs/monitor.html）
  GET  /dashboard.png   设备来取图。只发"有内容的那张"，不合格就 404
  GET  /config.sh       设备那份配置（调试台的排班表照着它算）
  GET  /skin.txt        屏上现在挂哪套版式（记在本机，不再记在云端分支）
  GET  /skins/…         皮肤墙：本机出的各版式真图 + manifest.json
  GET  /poll.json       设备每轮问一句"接下来隔多久来"
  GET  /report          设备上报"屏上贴的是哪张"（cksum 和 busybox 对得上）
  GET  /state           本机这一趟会发哪张 + 设备上报 + 两者是否同一张
  POST /publish?skin=…  现场出一张（skin 省略 = 用配置文件里那套；选过的会记住）
  POST /boost           进入调屏模式：接下来 30 分钟屏来得更勤

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

# 和 render.py 的 Renderer.LAYOUTS 对齐。这里抄一份是因为不想为了三个名字
# 在设备/服务两侧都依赖导入生产代码；对不上的话 generate.py 自己会退回默认
# 并在日志里说清可选值，不会静默画错。
LAYOUTS = ("c1", "arc")

STATE = {"last_build": None, "last_error": None, "skin": None, "seconds": None,
         "log": ""}          # 上一轮出图的完整输出，调试台的日志面板读它

SKINS = ROOT / "docs" / "skins"                  # 皮肤墙：本机出的那几张真图
SKIN_STATE = ROOT / "docs" / "skin.txt"          # 屏上现在挂哪套版式（没人选过=空）
DEVICE_CONFIG = ROOT / "kindle" / "extensions" / "aistatus" / "config.sh"
SKINS_TOOL = ROOT / "dashboard" / "tools" / "skins.py"
MONITOR = ROOT / "docs" / "monitor.html"
POLL = ROOT / "docs" / "poll.json"
DEVSTATE = ROOT / "docs" / "device_state.json"
YESTERDAY = ROOT / "docs" / "yesterday.json"


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


LOCAL_ENV = ROOT / ".workbuddy" / "_qweather_local.env"


def load_local_env() -> None:
    """把本机的和风凭据带进进程环境，让「立刻出图」在这台电脑上真的能出。

    凭据仍然不进仓库：这个文件在 .workbuddy/ 下，被 `.workbuddy/_*` 那条规则挡着；
    私钥也不写在里面，只写一个指向 PEM 文件的路径。已经存在的环境变量一律不覆盖 ——
    命令行里显式带的算数。
    """
    try:
        lines = LOCAL_ENV.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and not os.environ.get(k):
            os.environ[k] = v
    # 私钥单独放一个 PEM 文件里（本来就在那儿），这里只把它读成环境变量
    path = os.environ.pop("QWEATHER_PRIVATE_KEY_FILE", "")
    if path and not os.environ.get("QWEATHER_PRIVATE_KEY"):
        try:
            os.environ["QWEATHER_PRIVATE_KEY"] = (ROOT / path).read_text(encoding="utf-8").strip()
        except Exception:
            pass


QWEATHER_VARS = ("QWEATHER_HOST", "QWEATHER_ISS", "QWEATHER_SUB",
                 "QWEATHER_KID", "QWEATHER_PRIVATE_KEY")


def creds_state() -> dict:
    """这台电脑上五个和风凭据各在不在。**只报有没有，值永远不出去**。

    以前这一块读的是云端构建日志里的 ✓/✗ 五行；现在云端没了，再看那份日志
    就只会得到"没有自检行"。所以直接问进程环境。
    """
    return {k: bool((os.environ.get(k) or "").strip()) for k in QWEATHER_VARS}


def can_build() -> tuple[bool, str]:
    """这台电脑能不能现场出**有天气**的图。

    为什么先问这个：和风的 Key 只在 GitHub Secrets 里（仓库是公开的，不能写进配置）。
    本机没有它时 generate.py 照样"成功"，只是画出一张没有天气的图 —— 而局域网地址排在
    而本机是唯一的出图端 —— 没有第二个源可以盖、也没有别处可以退。按钮按下去之前就该说清楚。
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

    为什么必须查：这台是唯一的出图端，发出去什么屏上就是什么。出一次没有数据的图，
    屏上就是一片空 —— 2026-09-27 真发生过（一次没带凭据的测试出图盖在屏上几小时）。
    """
    try:
        d = json.loads(DEBUG.read_text(encoding="utf-8"))
    except Exception:
        return "读不到 debug.json，不知道这张图里有没有数据"
    if not (d.get("counts") or {}).get("weather"):
        return "这张图没有天气那一块（出图时没拿到和风的数据）"
    return ""


DEVICE_SEEN = {"at": 0.0, "gap": 0, "ua": ""}


def note_device_fetch(handler) -> None:
    """记下"设备上一班是什么时候来的、隔多久来一次"。

    为什么不写"每 N 秒一班"这种承诺：说过一次谎 —— 调试台喊"每 30 秒一班"，
    而设备心跳有个 60 秒地板，实测一直是 66 秒。界面和机器说的不一样，比慢更害人。
    这里改成量：谁在取图看 User-Agent 就行（设备是 curl，浏览器是 Mozilla）。
    """
    ua = handler.headers.get("User-Agent", "") or ""
    if not (ua.lower().startswith("curl") or "wget" in ua.lower()):
        return
    now = time.time()
    if DEVICE_SEEN["at"]:
        DEVICE_SEEN["gap"] = int(round(now - DEVICE_SEEN["at"]))
    DEVICE_SEEN["at"] = now
    DEVICE_SEEN["ua"] = ua


def current_image():
    """这一趟给设备哪一张。返回 (bytes, 是谁, 一句话说明)。

    2026-09-29 起这台电脑是**唯一的出图端**（GitHub 那条按用户要求去掉了），所以
    没有"退而发云端那张"这回事了。规矩反而更简单，只剩一条：

      **这张必须是有内容的那张。** 不合格就回 404 —— 设备拿到 404 会**留着上一张
      好的**，这比贴一张空图强，也是新架构下唯一的安全网。

    另一件事必须说清楚而不是藏起来：这张多久没更新了。自动出图那条要是悄悄死了，
    屏会停在一张越来越旧的图上，而它看起来完全正常 —— 和本项目反复栽的那个坑
    （"界面说的和机器做的不一样"）一模一样。
    """
    if not IMAGE.exists():
        return b"", "", f"本机还没出过图（自动出图每 {AUTO_BUILD_MINUTES} 分钟一轮，也可以按「立刻出图」）"
    body = IMAGE.read_bytes()
    if not body:
        return b"", "", "docs/dashboard.png 是个空文件，不发"
    bad = empty_reason()
    if bad:
        return b"", "", bad + " —— 这张不发，屏会留着上一张好的"
    age = int(time.time() - IMAGE.stat().st_mtime)
    note = ""
    if age > AUTO_BUILD_MINUTES * 120:          # 两轮都没出新图 = 自动出图可能死了
        note = f"这张已经 {age // 60} 分钟没更新过，查自动出图那一行日志"
    return body, "本机出的", note

BOOST_SECONDS = 10          # 调屏模式下设备每隔多少秒来取一次。
# 取 10 是因为不睡觉时 secure_sleep 是按 10 秒一段睡的 —— 填 12 会被凑成 20。
BOOST_WINDOW = 30 * 60      # 调屏持续多久，到点自己回落到平时的 1 分钟
AUTO_BUILD_MINUTES = 10     # 每隔这么久自动出一张（0 = 关）。现在这是屏新旧的唯一决定者


def write_poll(boost_until: int) -> None:
    """把"下一班多久来取"写成一个小文件，设备每轮醒来读它。

    为什么设备只能"被问"不能"被推"：它睡着时 WiFi 射频是关的（实测 ping 不通），
    所以没有任何办法把它叫醒。能做的只有让它下次醒来时顺手问一句"接下来睡多久" ——
    这就是 boost 的全部机制，也解释了为什么第一次按发布仍要等到它下一班
    （平时最多 1 分钟），之后才变成 boost_seconds 那一档。
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
        return False, why + " —— 出了也是一张没有天气的图，不发。"
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
    STATE["log"] = (res.stdout or "") + (res.stderr or "")
    if res.returncode != 0:
        tail = (res.stderr or res.stdout or "").strip().splitlines()[-3:]
        STATE["last_error"] = " / ".join(tail) or f"退出码 {res.returncode}"
        return False, STATE["last_error"]
    reason = empty_reason()
    if reason:
        STATE["last_error"] = reason
        return False, f"本机出了图，但**不发**：{reason}。屏会留着上一张好的。"
    STATE["last_error"] = None
    STATE["last_build"] = datetime.now().strftime("%H:%M:%S")
    STATE["skin"] = skin or "（配置文件的默认）"
    if skin:
        # 记住这次选的版式：下一轮自动出图也照它出。以前这个记忆存在云端 screen
        # 分支的 skin.txt 里，现在这台是唯一出图端，存在自己旁边就行。
        try:
            SKIN_STATE.write_text(skin, encoding="utf-8")
        except Exception:
            pass
        rebuild_wall()
    return True, f"已出图（{STATE['seconds']}s）"


def rebuild_wall() -> None:
    """刷新皮肤墙（docs/skins/）。只在**手动**按「立刻出图」之后跑。

    为什么不跟着自动出图一起跑：那要把所有数据源再抓一遍，白白翻倍。皮肤墙是
    给人挑版式用的，人在挑的时候才会看它。
    """
    try:
        subprocess.run([sys.executable, str(SKINS_TOOL)], capture_output=True,
                       text=True, timeout=240, cwd=str(ROOT))
        print("  [皮肤墙] 已刷新", file=sys.stderr)
    except Exception as exc:
        print(f"  [皮肤墙] 没刷成：{type(exc).__name__}: {exc}", file=sys.stderr)


def chosen_skin() -> str | None:
    """这一轮该用哪套版式：有人在调试台选过就用它，否则交给配置文件。"""
    try:
        name = SKIN_STATE.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return name if name in LAYOUTS else None


def auto_build_loop(minutes: int) -> None:
    """电脑开着的时候，每隔 AUTO_BUILD_MINUTES 分钟自己出一张。

    为什么要有它：**GitHub 的定时任务不守时**。名义上每小时，实测 2026-09-28/29
    两天里是 2.9 ~ 8.7 小时一次。屏改成每分钟来取之后，"取"这一端已经不是瓶颈了，
    慢的是"出" —— 取到的可能是六小时前那张。数据要新只能让出图这一端跑得更勤。
    2026-09-29 起这条就是**唯一**的出图节奏（云端那套按用户要求去掉了）：服务停了、
    电脑重启了，屏就停在最后一张不会自愈，所以它死了必须能在调试台上看见。
    """
    while True:
        time.sleep(minutes * 60)
        ok, msg = build(chosen_skin())
        sys.stderr.write(f"  [自动出图 {'成功' if ok else '没成'}] {msg}\n")


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
            note_device_fetch(self)
            return
        if path.startswith("/skins/"):
            # 皮肤墙：本机 docs/skins/ 里的那几张真图。以前是代理 GitHub Pages 的
            # （Pages 不发 CORS 头，fetch 拿不到），现在图就在本机，直接读盘。
            name = path[len("/skins/"):].replace("..", "")
            f = SKINS / name
            if not name or not f.is_file():
                self._send(404, b"not in the wall", "text/plain")
                return
            ctype = "application/json" if name.endswith(".json") else "image/png"
            self._send(200, f.read_bytes(), ctype)
            return
        if path == "/skin.txt":
            # 屏上现在挂的是哪套版式。以前记在云端 screen 分支，现在记在本机这个
            # 文件里；没人选过就是空，页面据此显示"用的是配置文件那套"。
            body = SKIN_STATE.read_bytes() if SKIN_STATE.exists() else b""
            self._send(200, body, "text/plain; charset=utf-8")
            return
        if path == "/config.sh":
            # 调试台的排班表要知道设备多久来取一班。以前从 raw.githubusercontent
            # 读仓库里那份，现在直接给本机这份 —— 反正拷进设备的就是它。
            if DEVICE_CONFIG.is_file():
                self._send(200, DEVICE_CONFIG.read_bytes(), "text/plain; charset=utf-8")
            else:
                self._send(404, b"no config.sh", "text/plain")
            return
        if path == "/build.log":
            # 上一轮出图的原样输出。云端那套去掉之后，"到底跑了什么"只剩这一份。
            self._send(200, STATE["log"].encode("utf-8"), "text/plain; charset=utf-8")
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
            # 比对必须用"这一趟真正发出去的那张"，而不是硬盘上那个文件 —— 它可能不合格。
            out = {"device": None, "served": "", "hash": "", "note": "", "empty": empty_reason()}
            ok_creds, why = can_build()
            out["can_build"] = {"ok": ok_creds, "why": why}
            out["device_seen"] = dict(DEVICE_SEEN,
                                      ago=int(round(time.time() - DEVICE_SEEN["at"]))
                                      if DEVICE_SEEN["at"] else None)
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
            info["creds"] = creds_state()
            ok_c, why_c = can_build()
            info["can_build"] = {"ok": ok_c, "why": why_c}
            info["skin"] = (SKIN_STATE.read_text(encoding="utf-8").strip()
                            if SKIN_STATE.exists() else "")
            if IMAGE.exists():
                st = IMAGE.stat()
                info["mtime"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                info["bytes"] = st.st_size
            self._send(200, json.dumps(info, ensure_ascii=False).encode(), "application/json")
            return
        if path in ("/", "/index.html"):
            # 调试台只有这一份：必须由本机托管，才谈得上"点一下推给屏"。
            if MONITOR.exists():
                self._send(200, MONITOR.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, "找不到 docs/monitor.html，调试台打不开；"
                                "屏的取图不受影响".encode(), "text/plain; charset=utf-8")
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
        msg += "（已进调屏模式：接下来 30 分钟屏来得更勤；隔多久来一班看上面那行实测）"
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

    load_local_env()
    ok_creds, why = can_build()

    ap = argparse.ArgumentParser(description="信息屏局域网快道")
    ap.add_argument("--port", type=int, default=8731)
    ap.add_argument("--bind", default="0.0.0.0",
                    help="0.0.0.0 才能被 Kindle 访问；127.0.0.1 只给自己看")
    ap.add_argument("--auto-build", type=int, default=AUTO_BUILD_MINUTES,
                    help=f"电脑开着时每隔几分钟自动出一张（0 = 关，默认 {AUTO_BUILD_MINUTES}）")
    args = ap.parse_args()

    if ok_creds:
        # 起来就先出一张。电脑重启之后屏是"停着的那张"，等第一轮自动出图最多要
        # 一个间隔；现在这台是唯一出图端，那段时间没有任何东西会替它补上。
        import threading
        threading.Thread(target=lambda: print(
            f"  [启动出图] {build(chosen_skin())[1]}", file=sys.stderr), daemon=True).start()
    if args.auto_build > 0:
        import threading
        threading.Thread(target=auto_build_loop, args=(args.auto_build,), daemon=True).start()
        print(f"  自动出图：每 {args.auto_build} 分钟一张（全局变量 AUTO_BUILD_MINUTES 改默认值）")
    else:
        print("  自动出图：关（屏的新旧完全跟着云端那 3～9 小时走）")

    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    url = f"http://{args.bind}:{args.port}/"
    print(f"局域网快道已启动：{url}")
    print("  本机出图：" + ("可以（和风的凭据已带进这台进程）" if ok_creds
                        else "不行 —— " + why + "；这台只发云端镜像"))
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
