#!/usr/bin/env python3
"""验一下「Kindle 真去拉图时会不会拿到一张能用的图」。

默认就验设备上那个地址，不用抄 URL：

    python dashboard/tools/check_cloud.py

也可以指定别的地址（比如刚 push、想确认 Actions 已经把图上去了）：

    python dashboard/tools/check_cloud.py --url https://raw.githubusercontent.com/...

它验的东西都是真踩过的坑，不是走个过场：

  * 拿到的到底是不是 PNG（而不是 404 页）—— 仓库没 push、分支名不对，都是这个表现
  * 尺寸 / 位深是不是等于设备规格（eips 只认原生分辨率的 8 位灰度，别的分辨率
    它不报错、只是不刷）
  * 图新不新 —— GitHub 的 raw 带 Last-Modified，就是那次提交的时间。
    Actions 静默失败时屏幕会停在旧图上，这边一查就知道
  * 下载耗时（Kindle 的下载超时是 60 秒，别撞上去）

结果同时打到屏幕和一个文本文件（Windows 上 PowerShell 不回显 stdout，
落盘之后用编辑器打开最省事）。
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent.parent
OUT: list[str] = []

# 图最久能旧到什么时候。四个出图点之间最大间隔是 9 小时（15:05 → 00:05），
# 留一倍余量：超过 24 小时就说明定时任务根本没跑，而不是"刚好在间隔里"。
MAX_AGE_HOURS = 24


def say(line: str = "") -> None:
    OUT.append(line)


def get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except Exception as exc:                                   # noqa: BLE001
        return 0, {}, f"{type(exc).__name__}: {exc}".encode("utf-8")


def url_from_device_config() -> str:
    """读 Kindle 那边 config.sh 的 DASHBOARD_URL —— 要验的就是它，别自己抄一遍。

    抄一份写死在这里，早晚和设备上那份对不上，然后"自检全绿、屏幕不更新"。
    """
    conf = ROOT / "kindle" / "extensions" / "aistatus" / "config.sh"
    if not conf.is_file():
        return ""
    for line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
        if re.match(r"^\s*DASHBOARD_URL\s*=", line):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _age_hours(last_modified: str) -> float | None:
    try:
        when = parsedate_to_datetime(last_modified)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 3600


# 从 raw 地址反推仓库和路径，再问 API"这个文件最后一次提交是几点"。
_RAW_RE = re.compile(
    r"^(?:https?://)?raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$")


def _age_from_api(raw_url: str) -> float | None:
    """返回小时；地址不是 GitHub raw、或者 API 没答话，就返回 None（不算失败）。

    未登录的 API 限额是 60 次/小时/来源 IP —— 这是个人手动跑的自检，够用。
    """
    m = _RAW_RE.match(raw_url)
    if not m:
        say("   · 地址不是 GitHub raw，判断不了图新不旧")
        return None
    owner, repo, ref, path = m.groups()
    api = (f"https://api.github.com/repos/{owner}/{repo}/commits"
           f"?sha={quote(ref)}&path={quote(path)}&per_page=1")
    status, _, body = get(api, {"Accept": "application/vnd.github+json",
                                "User-Agent": "aistatus-check"})
    if status != 200:
        say(f"   · 问 API 没成功（status={status}），跳过新旧判断"
            "（未登录限 60 次/小时，别连着刷）")
        return None
    try:
        commits = json.loads(body.decode("utf-8"))
        when = datetime.fromisoformat(commits[0]["commit"]["committer"]["date"]
                                      .replace("Z", "+00:00"))
    except Exception:                                          # noqa: BLE001
        say("   · API 返回看不懂（仓库还没有任何提交？），跳过新旧判断")
        return None
    say(f"  最后一次提交    = {when.astimezone().strftime('%m-%d %H:%M')}（API 查的）")
    return (datetime.now(timezone.utc) - when).total_seconds() / 3600


def check(url: str, cfg_size: tuple[int, int] | None) -> bool:
    all_ok = True
    t0 = time.time()
    status, headers, body = get(url)
    cost = time.time() - t0
    hl = {str(k).lower(): v for k, v in headers.items()}

    say(f"GET {url}")
    say(f"  status          = {status}  {cost:.2f}s  {len(body) / 1024:.1f} KB")
    if status != 200:
        say("  !! 没拿到图。最常见的三个原因：仓库还没 push、"
            "分支名不是 URL 里那个、docs/dashboard.png 还没被 Actions 提交过")
        say(f"  返回内容前 200 字节：{body[:200]!r}")
        return False
    if cost > 55:
        say(f"  !! 下载用了 {cost:.0f}s，Kindle 那边 60s 就超时了")
        all_ok = False

    say(f"  content-type    = {hl.get('content-type')}")
    say(f"  cache-control   = {hl.get('cache-control')}"
        "   · raw 的 CDN 缓存约 5 分钟，所以 REFRESH_LAG 必须比出图点晚一截")

    is_png = body[:8] == bytes.fromhex("89504e470d0a1a0a")
    say(f"  magic           = {body[:8].hex()}"
        f"  {'PNG OK' if is_png else '!! 不是 PNG，多半是错误页'}")
    all_ok = all_ok and is_png

    if is_png:
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(body))
            want = tuple(cfg_size) if cfg_size else None
            good = im.mode == "L" and (want is None or im.size == want)
            say(f"  Pillow          = mode={im.mode} size={im.size}"
                + (f"  期望 mode=L size={want}" if want else "")
                + ("" if good else "   !! 规格不对，eips 不会刷它"))
            all_ok = all_ok and good
        except Exception as exc:                               # noqa: BLE001
            say(f"  !! Pillow 打不开：{exc}")
            all_ok = False

    lm = hl.get("last-modified")
    age = _age_hours(lm) if lm else None
    if age is not None:
        say(f"  last-modified   = {lm}  → {age / 60:.1f} 小时前")
    else:
        # 实测：raw.githubusercontent.com **根本不发 Last-Modified**，
        # 所以"图新不新"没法从图片地址本身看出来。改问 API 要那次提交的时间。
        say(f"  last-modified   = {lm or '(缺)'}")
        age = _age_from_api(url)
    if age is not None:
        if age > MAX_AGE_HOURS:
            say(f"  !! 这张图已经 {age / 24:.1f} 天没变过 —— Actions 大概率没在跑"
                "（公开仓库满 60 天无活动会自动关掉 schedule）")
            all_ok = False
        else:
            say(f"  ✓ {age / 60:.1f} 小时前提交的（超过 {MAX_AGE_HOURS}h 才算异常）")

    # 设备端 curl 不带 -z、也不发 If-None-Match，所以永远收不到 304。
    # 这一条只是把风险量出来：服务器确实会回 304，所以别给设备的下载命令加条件头。
    etag = hl.get("etag")
    if etag:
        st2, _, _ = get(url, {"If-None-Match": etag})
        say(f"  带 If-None-Match 再拉 = {st2}"
            + ("   · 回了 304，**别给设备的 curl 加 -z / If-None-Match** —— "
               "Kindle 会当成\"下载成功但没写文件\"，屏幕永远停在上一张"
               if st2 == 304 else ""))

    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="验一下 Kindle 会拉到的那个地址")
    parser.add_argument("--url", default=None,
                        help="要验的地址；不填就读 kindle/extensions/aistatus/config.sh 的 DASHBOARD_URL")
    parser.add_argument("--out", default=str(ROOT / ".workbuddy" / "_cloud_check.txt"),
                        help="结果写到哪个文件")
    args = parser.parse_args()

    cfg_size = None
    try:
        sys.path.insert(0, str(ROOT / "dashboard"))
        from aiinfo.config import Config
        cfg = Config.load(str(ROOT / "dashboard" / "config.yaml"))
        cfg_size = cfg.size
    except Exception as exc:                                   # noqa: BLE001
        say(f"（读 config.yaml 失败，跳过尺寸比对：{exc}）")

    url = args.url or url_from_device_config()
    if not url:
        say("!! 没有可验的地址：--url 没给，config.sh 的 DASHBOARD_URL 也是空的")
        return 1
    passed = check(url, cfg_size)

    say("\n" + ("全部通过 ✓" if passed else "有项目没通过，见上面 !! 标记"))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(OUT), encoding="utf-8")
    print(f"结果已写入 {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
