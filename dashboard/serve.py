#!/usr/bin/env python3
"""局域网图片服务：把 docs/ 用一个小 HTTP 服务发出去，等 Kindle 来拉图。

用法：
    python dashboard/serve.py                      # 发 docs/，监听 8000
    python dashboard/serve.py --root docs --port 8000
    python dashboard/serve.py --quiet              # 不打请求日志

为什么不直接用 `python -m http.server`：

1. **它会配合缓存返回 304**。它带 Last-Modified，还认 If-Modified-Since；
   Kindle 上的 curl/wget 一旦收到 304 就**不写文件**，结果屏幕永远停在上一张，
   而日志里一切正常——这是最难查的那种故障。这里把协商头整个摘掉，
   再压上 no-store，每次请求都是完整的 200 + 图片本体。
2. 请求日志默认打一行，`--quiet` 可以关掉。常年挂着的小服务不需要刷屏。
3. 启动时把本机在局域网里的地址直接算出来打给你，省得再去 ipconfig 里找。

只依赖标准库。默认绑 0.0.0.0，所以同一个 WiFi 下的 Kindle 能直接访问。
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 每次请求都强制回源，别让 Kindle 端拿到 304 或旧图
NO_CACHE_HEADERS = (
    ("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"),
    ("Pragma", "no-cache"),
    ("Expires", "0"),
)


class NoCacheHandler(SimpleHTTPRequestHandler):
    """静态文件处理器：去掉条件请求，压上禁缓存响应头。"""

    server_version = "aiinfo-lan/1.0"
    #: 关掉每行都带时间戳和 Referer 的默认格式，输出更短
    quiet = False

    def send_head(self):
        # 关键：把条件请求头摘掉。留着它们，文件没变时这里会直接回 304，
        # 而 Kindle 端的 curl 会把 304 当成"下载成功但不写文件"。
        for header in ("If-Modified-Since", "If-None-Match"):
            if header in self.headers:
                del self.headers[header]
        return super().send_head()

    def end_headers(self):
        # 必须在 super() 之前 add_header，之后 headers 就已经发出去了
        #
        # X-Epoch：Kindle 拿这个头校准自己的系统时间。时钟改由 Kindle 本机画
        # 之后，**设备时间准不准直接决定屏幕上显示的时间对不对**，所以云端和
        # 局域网这两条路都必须带上这个头，不能只在云端带。
        self.send_header("X-Epoch", str(int(time.time())))
        for key, value in NO_CACHE_HEADERS:
            self.send_header(key, value)
        super().end_headers()

    def do_HEAD(self):
        # 有些固件会先探一下再下，走同一条路径即可
        self.do_GET()

    def log_message(self, fmt, *args):
        if self.quiet:
            return
        # 默认格式太长，换成「时间 客户端 请求行 状态」
        sys.stdout.write("%s  %-15s  %s\n" % (
            datetime.now().strftime("%H:%M:%S"),
            self.address_string(),
            fmt % args,
        ))
        sys.stdout.flush()

    def log_error(self, fmt, *args):
        # 断连、客户端取消之类的噪音不该淹掉真正的错误
        pass


def local_addresses() -> list[str]:
    """列出本机在局域网里的 IPv4 地址，给 Kindle 直接抄。

    先用 UDP connect 到一个外部地址问内核"你会用哪个网卡出去"——不会真的发包，
    但拿到的正是 Kindle 该用的那个地址。再补上所有非回环地址兜底。
    """
    found: list[str] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("223.5.5.5", 80))
            found.append(sock.getsockname()[0])
        finally:
            sock.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith(("127.", "169.254.")):
                found.append(ip)
    except OSError:
        pass
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="局域网里给 Kindle 发图的静态服务")
    parser.add_argument("--root", default="docs",
                        help="要发布的目录（默认 docs，和 output.png 的目录一致）")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--bind", default="0.0.0.0",
                        help="监听地址。默认 0.0.0.0 让局域网都能连")
    parser.add_argument("--quiet", action="store_true", help="不打请求日志")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_absolute():
        root = ROOT / root
    if not root.is_dir():
        print(f"目录不存在：{root}")
        print("先在仓库根目录跑一次 python dashboard/generate.py 把图生成出来。")
        return 1

    NoCacheHandler.quiet = args.quiet
    handler = partial(NoCacheHandler, directory=str(root))

    try:
        httpd = ThreadingHTTPServer((args.bind, args.port), handler)
    except OSError as exc:
        print(f"端口 {args.port} 起不来：{exc}")
        print("换一个端口：python dashboard/serve.py --port 8080")
        return 1

    png = root / "dashboard.png"
    print("=" * 62)
    print(" AI 信息屏 · 局域网图片服务")
    print("=" * 62)
    print(f" 目录   {root}")
    if png.exists():
        size_kb = png.stat().st_size / 1024
        mtime = datetime.fromtimestamp(png.stat().st_mtime)
        age = (datetime.now() - mtime).total_seconds() / 60
        print(f" 图片   dashboard.png  {size_kb:.0f} KB，"
              f"生成于 {mtime:%m-%d %H:%M}（{age:.0f} 分钟前）")
    else:
        print(" 图片   还没生成！先跑 python dashboard/generate.py")
    print("-" * 62)
    for ip in local_addresses() or ["127.0.0.1"]:
        print(f" Kindle 端填： http://{ip}:{args.port}/dashboard.png")
    print("-" * 62)
    print(" Ctrl+C 停止。这个窗口要一直开着（或做成开机自启）。")
    print(" 顺带一提：Kindle 只连这台电脑，所以家里断网也不影响看图，")
    print(" 只要电脑还在生成新图。")
    print("=" * 62, flush=True)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
