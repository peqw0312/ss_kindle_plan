#!/usr/bin/env python3
"""云端出图服务的自检工具。

两种用法：

    # 1. 本机试跑：起一个临时进程，把整条链路验一遍
    python dashboard/tools/check_cloud.py

    # 2. 验线上：对着已经发布出去的地址检查（改完代码、重新发布之后跑这个）
    python dashboard/tools/check_cloud.py --url https://<你的>.app.workbuddy.host

它验的东西都是真踩过的坑，不是走个过场：

  * 拿到的到底是不是 PNG（而不是 404 页）
  * 尺寸 / 位深是不是等于设备规格（eips 只认原生分辨率的 8 位灰度）
  * **带 If-Modified-Since 会不会被回 304** —— 回了 304，Kindle 的 curl 会当成
    「下载成功但没写文件」，屏幕就永远停在上一张，而两端日志全绿
  * 服务端日志里有没有中文字体（没有就是一片方块 □□□）
  * 冷启动耗时（Kindle 的下载超时是 60 秒，别撞上去）

结果同时打到屏幕和一个文本文件（Windows 上 PowerShell 不回显 stdout，
落盘之后用编辑器打开最省事）。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PY = sys.executable
OUT: list[str] = []


def say(line: str = "") -> None:
    OUT.append(line)


def fetch(base: str, path: str, headers: dict | None = None):
    req = urllib.request.Request(base.rstrip("/") + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()
    except Exception as exc:                                   # noqa: BLE001
        return 0, {}, f"{type(exc).__name__}: {exc}".encode("utf-8")


def _local_clock_conf(root: Path) -> dict[str, int] | None:
    """读本机 Kindle 端那份时钟坐标（Kindle 实际按它贴图）。"""
    conf = root / "kindle" / "extensions" / "aistatus" / "clock" / "clock.conf"
    if not conf.exists():
        return None
    out: dict[str, int] = {}
    for raw in conf.read_text(encoding="utf-8").splitlines():
        if "=" in raw and not raw.startswith("#"):
            key, _, value = raw.partition("=")
            key, value = key.strip(), value.strip()
            if key in ("CLOCK_X", "CLOCK_Y", "CLOCK_W", "CLOCK_H"):
                out[key] = int(value)
    return out or None


def _compare_clock_region(region) -> list[str]:
    """线上图的留白区 vs 本机 clock.conf —— Kindle 按后者贴图，两者必须能对上。

    **允许 1~2px 的差**，这是实测过的、无害的：出图和生成精灵图用的是不同字体文件
    （本机 Noto Sans SC 独立 .otf，云端 Noto Sans CJK SC .ttc），字形度量差 1px，
    于是留白区左边界本机 644 / 云端 645。精灵图左侧本来就是若干列纯白内边距，
    而云端那一列也是空白，所以是**白压白**，屏幕上没有任何可见差异（已逐像素量过）。

    真正要拦的是差得更多的情况：那说明字体或字号在两边不是一套了，
    精灵图会盖到别的字上、或者露出一条空白缝。
    """
    out: list[str] = []
    conf = _local_clock_conf(ROOT)
    if not conf:
        out.append(f"（没找到 {ROOT / 'kindle/extensions/aistatus/clock/clock.conf'}，"
                   f"跳过与本机坐标的比对）")
        return out
    try:
        x, y, right, bottom = (int(v) for v in region)
    except (TypeError, ValueError):
        out.append(f"!! clock_region 格式看不懂：{region!r}")
        return out

    dx = x - conf["CLOCK_X"]
    dy = y - conf["CLOCK_Y"]
    dw = (right - x) - conf["CLOCK_W"]
    dh = (bottom - y) - conf["CLOCK_H"]
    out.append(f"本机 clock.conf : ({conf['CLOCK_X']},{conf['CLOCK_Y']}) "
               f"{conf['CLOCK_W']}×{conf['CLOCK_H']}   ← Kindle 按这个贴图")
    out.append(f"差值            : dx={dx} dy={dy} d宽={dw} d高={dh}")

    if max(abs(dx), abs(dy), abs(dw), abs(dh)) <= 2:
        out.append("两边几何一致（≤2px 属字体度量的正常误差，"
                   "落在精灵图的白色内边距里，屏幕上看不出来）")
    else:
        out.append("!! 差得太多：出图和精灵图已经不是同一套几何了。"
                   "重跑 make_clock_assets.py，再把 clock/ 重拷进 Kindle。")
    return out


def check(base: str, cfg_size: tuple[int, int] | None) -> bool:
    """对着一个 base 地址把该验的都验一遍。返回是否全过。"""
    all_ok = True

    status, _, body = fetch(base, "/health")
    say(f"=== /health ===  status={status}")
    health: dict = {}
    if status == 200:
        try:
            health = json.loads(body.decode("utf-8"))
            say(json.dumps(health, ensure_ascii=False, indent=2))
        except Exception:                                      # noqa: BLE001
            say(body.decode("utf-8", "replace"))
    else:
        say(body.decode("utf-8", "replace"))
        return False

    say(f"\n=== 状态判读 ===")
    ok = health.get("ok")
    say(f" ok              = {ok}"
        + ("" if ok else f"   !! 出图失败：{health.get('reason')}"))
    all_ok = all_ok and bool(ok)

    fonts = str(health.get("fonts") or "")
    # 判据用「排他」而不是「包含」：中文字体的族名千奇百怪
    # （Noto Sans SC / Noto Sans CJK SC / 微软雅黑 / PingFang / 思源黑体…），
    # 列白名单永远漏。反过来，只有这几种是明确不含汉字的，命中才算错。
    latin_only = ("dejavu", "liberation", "times", "courier", "freesans",
                  "arial ", "helvetica", "nimbus")
    looks_cjk = bool(fonts) and not any(m in fonts.lower() for m in latin_only)
    say(f" fonts           = {fonts or '(空)'}"
        + ("" if looks_cjk else "   !! 像是不含汉字的字体，中文会变成方块"))
    all_ok = all_ok and looks_cjk

    # 时刻表：不再有 refresh_seconds。「图是不是老了」不能靠 age 跟 TTL 比 ——
    # 一天只出四次，age 涨几个钟头是正常的。判据是 next_build_at 必须在未来。
    age = health.get("age_seconds")
    say(f" schedule        = {health.get('schedule')}")
    next_at = health.get("next_build_at")
    say(f" age / next_build= {age}s / {next_at}")
    if next_at:
        try:
            nxt = datetime.fromisoformat(str(next_at))
            if nxt.tzinfo is None:
                nxt = nxt.replace(tzinfo=datetime.now().astimezone().tzinfo)
            ahead = (nxt - datetime.now(nxt.tzinfo)).total_seconds()
            ok_next = ahead > -60
            say(f" 距下次出图      = {ahead / 60:.1f} 分钟"
                + ("" if ok_next else "   !! next_build_at 已经在过去，后台刷新可能卡住了"))
            all_ok = all_ok and ok_next
        except ValueError:
            say(" (next_build_at 解析不了)")
    else:
        say(" !! /health 里没有 next_build_at —— 线上可能还是旧版本，重新发布一次")

    cmode = health.get("clock_mode")
    cregion = health.get("clock_region")
    say(f" 时钟            = mode={cmode} region={cregion}")
    if cmode == "local":
        say("   → 图里时钟那格是留白的，靠 Kindle 贴精灵图。")
        warn = _compare_clock_region(cregion)
        for row in warn:
            say("     " + row)
        all_ok = all_ok and not any(r.startswith("!!") for r in warn)

    qv, qt = health.get("quotes_valid"), health.get("quotes_total")
    say(f" 行情            = {qv}/{qt} 条有效"
        + ("" if qv == qt else "   · 有降级的，页脚会如实标出"))
    say(f" 天气源          = {health.get('weather_source')}  {health.get('weather') or ''}")

    # --- 图片本体 ---
    t0 = time.time()
    status, headers, body = fetch(base, "/dashboard.png")
    hl = {str(k).lower(): v for k, v in headers.items()}
    cost = time.time() - t0
    say(f"\n=== /dashboard.png ===  status={status}  {cost:.2f}s  {len(body) / 1024:.1f} KB")
    say(f" content-type    = {hl.get('content-type')}")
    say(f" cache-control   = {hl.get('cache-control')}")
    # X-Epoch 是给 Kindle 校准系统时间用的。时钟改由本机画之后，设备时间准不准
    # 直接决定屏幕上的时间对不对，所以这个头是**功能必需**，不是可有可无的装饰。
    epoch = hl.get("x-epoch")
    if epoch:
        drift = int(epoch) - int(time.time())
        say(f" x-epoch         = {epoch}  与本地相差 {drift}s"
            + ("" if abs(drift) <= 5 else "   !! 服务端时间不准"))
        all_ok = all_ok and abs(drift) <= 5
    else:
        say(" x-epoch         = (缺)   !! Kindle 无法校准系统时间")
        all_ok = False
    # X-Next-Image 是"几点换新图"从服务端传到设备的唯一通道。缺了它 Kindle
    # 不会坏，但会**静默退回每小时轮询** —— 屏幕照常刷新，日志全绿，只是白耗电，
    # 所以必须在这里点名，不能靠肉眼发现。
    nxt = hl.get("x-next-image")
    if nxt:
        try:
            wait = int(nxt) - int(time.time())
        except ValueError:
            wait = None
        # 12 小时这个上限是 Kindle 端 next_image_from_headers 的判据，
        # 两边必须一致：服务端发出一个超出窗口的值，客户端就当没收到，优化等于没做。
        good = wait is not None and 0 < wait <= 43200
        say(f" x-next-image    = {nxt}"
            + (f"  即 {wait / 60:.0f} 分钟后换图" if wait is not None else "  !! 不是数字")
            + ("" if good else "   !! 不在 Kindle 接受的窗口内，它会退回轮询"))
        all_ok = all_ok and good
    else:
        say(" x-next-image    = (缺)   !! 线上还是旧版本，Kindle 会退回每小时轮询；重新发布")
        all_ok = False
    is_png = body[:8] == bytes.fromhex("89504e470d0a1a0a")
    say(f" magic           = {body[:8].hex()}  {'PNG OK' if is_png else '!! 不是 PNG'}")
    all_ok = all_ok and is_png

    if is_png:
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(body))
            want = tuple(cfg_size) if cfg_size else None
            good = im.mode == "L" and (want is None or im.size == want)
            say(f" Pillow          = mode={im.mode} size={im.size}"
                + (f"  期望 mode=L size={want}" if want else "")
                + ("" if good else "   !! 规格不对"))
            all_ok = all_ok and good
        except Exception as exc:                               # noqa: BLE001
            say(f" !! Pillow 打不开：{exc}")
            all_ok = False

    # --- 条件请求：必须仍然是完整 200 ---
    stale = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() - 86400))
    status2, _, body2 = fetch(base, "/dashboard.png", {"If-Modified-Since": stale})
    same = len(body2) == len(body)
    good = status2 == 200 and same
    say(f"\n=== 带 If-Modified-Since 再拉一次 ===")
    say(f" status={status2}  长度一致={same}"
        + ("   ✓ 完整 200，不会被 304 卡住" if good
           else "   !! 有风险：回了 304 或空内容，Kindle 会永远停在旧图"))
    all_ok = all_ok and good

    # --- 人看的首页 ---
    status3, headers3, _ = fetch(base, "/")
    say(f"\n=== / （给人看的预览页）===  status={status3}  {headers3.get('Content-Type')}")
    all_ok = all_ok and status3 == 200

    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="云端出图服务自检")
    parser.add_argument("--url", default=None,
                        help="验一个已经在跑的地址（比如线上链接）；不填则本机起进程试跑")
    parser.add_argument("--port", type=int, default=8123, help="本机试跑用哪个端口")
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

    if args.url:
        say(f"对线上地址自检：{args.url}\n")
        passed = check(args.url, cfg_size)
    else:
        base = f"http://127.0.0.1:{args.port}"
        say(f"本机试跑 {ROOT / 'dashboard' / 'app.py'}，端口 {args.port}\n")
        env = dict(os.environ, PORT=str(args.port), PYTHONIOENCODING="utf-8",
                   PYTHONUNBUFFERED="1")
        proc = subprocess.Popen(
            [PY, str(ROOT / "dashboard" / "app.py")],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        try:
            # 等预热：冷启动要抓天气和行情，给足时间
            deadline = time.time() + 180
            while time.time() < deadline:
                time.sleep(2)
                if proc.poll() is not None:
                    say("!! 进程提前退出，看下面的服务端日志")
                    break
                try:
                    st, _, bd = fetch(base, "/health")
                    if st == 200 and json.loads(bd.decode("utf-8")).get("has_image"):
                        break
                except Exception:                              # noqa: BLE001
                    continue
            passed = check(base, cfg_size)
            # 强制重算一次，确认这条路径也是通的
            t0 = time.time()
            st, _, bd = fetch(base, "/dashboard.png?force=1")
            say(f"\n=== ?force=1（强制重算）===  status={st}  {time.time() - t0:.1f}s  "
                f"{len(bd) / 1024:.1f} KB")
            passed = passed and st == 200
        finally:
            proc.terminate()
            try:
                log, _ = proc.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                log, _ = proc.communicate()
            say("\n=== 服务端日志 ===")
            say((log or "").strip() or "(空)")

    say("\n" + ("全部通过 ✓" if passed else "有项目没通过，见上面 !! 标记"))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(OUT), encoding="utf-8")
    print(f"结果已写入 {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
