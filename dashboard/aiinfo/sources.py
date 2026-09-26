"""数据抓取层：天气 / 空气质量 / 行情 / 基金 / 新闻 RSS。

所有函数都遵循同一个契约：**绝不抛异常打断渲染**，抓不到就返回 None 或空列表，
渲染层会把缺失的区块自动收掉。这是刻意的设计——墨水屏挂在墙上，
宁可少显示一块信息，也不能因为某个接口抽风就整屏空白。

网络环境差异是真实存在的：GitHub Actions 跑在境外机房 IP，
部分国内财经接口会拦；本地跑又相反。所以行情做了三级降级。
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 20

#: 剩下这几个数据源（和风天气 / Open-Meteo / 腾讯 / 新浪）在国内都能直连，
#: 所以默认**忽略系统代理**。本机常常挂着代理，一旦把国内接口绕到境外节点，
#: 轻则变慢、重则被对方按异地 IP 拦掉。要改回「跟随系统代理」，
#: 在 config 里设 network.use_env_proxy: true，并在启动时调用 configure_network()。
_SESSION = requests.Session()
_SESSION.trust_env = False


def configure_network(cfg) -> None:
    """按配置决定是否跟随系统代理。启动时调一次即可。"""
    follow = bool(cfg.get("network.use_env_proxy", False)) if cfg is not None else False
    _SESSION.trust_env = follow

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://\S+")
# Hacker News 的 RSS description 里只有这些字段，直接搬上屏就是一行乱码
_FEED_NOISE_RE = re.compile(r"(Article URL|Comments URL|Points|#\s*Comments)\s*:?", re.I)


def clean_text(raw: str | None, limit: int = 0) -> str:
    """剥掉 HTML 标签与多余空白，压成单行纯文本。"""
    if not raw:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", str(raw)))
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = _WS_RE.sub(" ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def clean_summary(raw: str | None, limit: int = 0) -> str:
    """摘要清洗：去掉 URL 和 RSS 里的结构化噪声，没信息量就返回空串。

    宁可让某条新闻少一行摘要，也好过在墙上顶着一行
    「Article URL: https://... Comments URL: https://...」。
    """
    text = clean_text(raw)
    if not text:
        return ""
    text = _FEED_NOISE_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip(" -|,·、;；")
    if len(text) < 12:            # 剩下的都是残渣，不如不显示
        return ""
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def get(url: str, *, headers: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
        encoding: str | None = None, retries: int = 2,
        params: dict | None = None) -> requests.Response | None:
    """带重试的 GET。失败返回 None 而不是抛异常。"""
    merged = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        merged.update(headers)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = _SESSION.get(url, headers=merged, timeout=timeout, params=params)
            if resp.status_code == 200:
                if encoding:
                    resp.encoding = encoding
                return resp
            last_err = RuntimeError(f"HTTP {resp.status_code}")
        except Exception as exc:                       # noqa: BLE001 - 兜底是设计要求
            last_err = exc
        if attempt < retries:
            time.sleep(0.8 * (attempt + 1))
    # 把查询串一起打出来：和风的参数全在 URL 里，只打路径等于没打。
    # 但 key 必须先抹掉 —— 这份输出会被写进公开仓库的 screen 分支当构建日志，
    # 一旦哪天退回 API KEY 模式，原样打印就等于把凭据公开。
    if params:
        safe = {k: ("***" if "key" in k.lower() else v) for k, v in params.items()}
        shown = f"{url}?{urlencode(safe)}"
    else:
        shown = url
    print(f"[sources] 抓取失败 {shown} -> {last_err}")
    return None


def get_json(url: str, **kwargs) -> Any | None:
    resp = get(url, **kwargs)
    if resp is None:
        return None
    try:
        return resp.json()
    except Exception:
        print(f"[sources] 返回的不是合法 JSON：{url}")
        return None


# ---------------------------------------------------------------------------
# 天气
#   主源：和风天气（QWeather）—— 国内站点融合实况 + 官方合作数据，
#         带空气质量与气象灾害预警，比全球模式插值准得多
#   兜底：Open-Meteo —— 免 Key、不限量，没配和风 Key 时自动顶上
# ---------------------------------------------------------------------------

# WMO 天气代码 -> (中文描述, 图标类别)
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("晴", "sun"),
    1: ("晴间多云", "sun"),
    2: ("多云", "sun_cloud"),
    3: ("阴", "cloud"),
    45: ("有雾", "fog"),
    48: ("冻雾", "fog"),
    51: ("毛毛雨", "rain"),
    53: ("毛毛雨", "rain"),
    55: ("强毛毛雨", "rain"),
    56: ("冻毛毛雨", "rain"),
    57: ("强冻毛毛雨", "rain"),
    61: ("小雨", "rain"),
    63: ("中雨", "rain"),
    65: ("大雨", "rain_heavy"),
    66: ("冻雨", "rain_heavy"),
    67: ("强冻雨", "rain_heavy"),
    71: ("小雪", "snow"),
    73: ("中雪", "snow"),
    75: ("大雪", "snow"),
    77: ("雪粒", "snow"),
    80: ("小阵雨", "rain"),
    81: ("阵雨", "rain"),
    82: ("强阵雨", "rain_heavy"),
    85: ("小阵雪", "snow"),
    86: ("阵雪", "snow"),
    95: ("雷阵雨", "thunder"),
    # 96/99 的字面含义确实带冰雹（"雷暴伴小/大冰雹"），但**别照字面翻**：
    # Open-Meteo 那套全球模式约 11 公里网格，把普通夏天雷阵雨也常常标成 96/99，
    # 于是九月下旬的杭州会在墙上连着三天"雷阵雨伴冰雹"。墙上这块屏是家里人看的，
    # 喊错一次的信任代价远大于漏报一次，所以这里只保留"雷暴强弱"这一层信息。
    # 真要冰雹，气象部门会发预警 —— 预警那一栏才是该喊的地方。
    96: ("雷阵雨", "thunder"),
    99: ("强雷阵雨", "thunder"),
}

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def describe_weather(code: int | None) -> tuple[str, str]:
    if code is None:
        return ("--", "cloud")
    return WMO_CODES.get(int(code), ("未知", "cloud"))


#: 中国 AQI（HJ 633-2012）的污染物浓度限值。行 = 浓度（µg/m³），
#: 列对应 IAQI 0 / 50 / 100 / 150 / 200 / 300 / 400 / 500。
#: 只用得上 PM2.5 和 PM10 两档 —— 免费接口给的就是这两项。
AQI_BREAKPOINTS = {
    "pm2p5": [0, 35, 75, 115, 150, 250, 350, 500],
    "pm10": [0, 50, 150, 250, 350, 420, 500, 600],
}
AQI_IAQI = [0, 50, 100, 150, 200, 300, 400, 500]


def _iaqi(conc: float, limits: list[float]) -> float:
    """线性插值算单项 IAQI。浓度超过最后一档就按爆表 500 处理。"""
    if conc <= 0:
        return 0.0
    for i in range(1, len(limits)):
        if conc <= limits[i]:
            lo, hi = limits[i - 1], limits[i]
            return (conc - lo) / (hi - lo) * (AQI_IAQI[i] - AQI_IAQI[i - 1]) + AQI_IAQI[i - 1]
    return float(AQI_IAQI[-1])


def china_aqi(pm25: float | None, pm10: float | None) -> float | None:
    """按中国标准算 AQI = 各污染物 IAQI 的最大值。

    ⚠️ 这里踩过一个坑：Open-Meteo 的空气接口给的是 **美国 EPA 的 AQI**，
    而分级文字（优 / 良 / 轻度污染 …）是中国的。两边数值断点长得一样（50/100/
    150/200/300），但底下那套污染物限值完全不同 —— 同一口空气美国 AQI 能比中国
    AQI 高几十点，于是"美国 152 + 中国文字"就在屏幕上显示成了「中度污染」，
    而国内 App 显示的是「优 48」。所以自己用浓度算，不要拿 us_aqi 直接贴标签。
    """
    parts = []
    if pm25 is not None:
        parts.append(_iaqi(float(pm25), AQI_BREAKPOINTS["pm2p5"]))
    if pm10 is not None:
        parts.append(_iaqi(float(pm10), AQI_BREAKPOINTS["pm10"]))
    return max(parts) if parts else None


def aqi_level(aqi: float | None) -> tuple[str, str]:
    """中国 AQI -> (等级中文, 建议标签)。分级就是国内 App 那六档。"""
    if aqi is None:
        return ("--", "")
    v = float(aqi)
    if v <= 50:
        return ("优", "可户外")
    if v <= 100:
        return ("良", "可户外")
    if v <= 150:
        return ("轻度污染", "敏感人群少出门")
    if v <= 200:
        return ("中度污染", "减少户外")
    if v <= 300:
        return ("重度污染", "尽量待在室内")
    return ("严重污染", "关窗")


#: 蒲福风级的下限（km/h）：索引 i 就是 i 级，例如 2 级从 6 km/h 起。
BEAUFORT_KMH = [0, 1, 6, 12, 20, 29, 39, 50, 62, 75, 89, 104, 118]


def effective_wind_level(weather: dict) -> int | None:
    """风的蒲福风级。

    两个源给的口径不一样：和风直接给 `windScale`（就是风级），
    Open-Meteo 只给 km/h。渲染层不该关心"这一轮用的是哪个源"，
    所以换算统一收在这里 —— 以前这段换算逻辑长在 render.py 里，
    结果是自检工具打出来的值和屏幕上的值对不上（屏幕上正常，工具里是 None）。

    ⚠️ 换算原来是 `round(km/h ÷ 5)`，那是错的：风级不是线性刻度，
    越往上每档越宽。40 km/h 会被它算成 8 级（实际 6 级），
    100 km/h 会报成 12 级（实际 11 级）。改成查官方下限表。
    """
    level = weather.get("wind_level")
    if level is not None:
        return level
    speed = weather.get("wind_speed")
    if speed is None:
        return None
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        return None
    out = 0
    for i, lo in enumerate(BEAUFORT_KMH):
        if speed >= lo:
            out = i
    return out


def _fetch_weather_openmeteo(cfg) -> dict | None:
    """Open-Meteo：免 Key、不限量，作为和风天气的兜底。

    给的是全球数值模式（约 11km 网格）插到你那个坐标的值，空气质量来自
    CAMS 全球模式。对国内来说精度不如和风，但永远不会因为没配 Key 而空着。
    """
    lat = cfg.get("location.latitude")
    lon = cfg.get("location.longitude")
    tz = cfg.get("location.timezone", "Asia/Shanghai")
    days = int(cfg.get("weather.days", 4))
    if lat is None or lon is None:
        print("[sources] 未配置经纬度，跳过天气。")
        return None

    payload = get_json(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
        "is_day,precipitation,weather_code,wind_speed_10m,wind_direction_10m,"
        "surface_pressure"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,uv_index_max,sunrise,sunset"
        f"&timezone={requests.utils.quote(tz)}&forecast_days={max(2, min(days, 7))}",
        timeout=25,
    )
    if not payload or "current" not in payload:
        return None

    cur = payload.get("current", {})
    daily = payload.get("daily", {})
    desc, icon = describe_weather(cur.get("weather_code"))

    # 风向角度 -> 中文方位
    deg = cur.get("wind_direction_10m")
    compass = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    wind_dir = compass[int((float(deg) + 22.5) % 360 // 45)] if deg is not None else ""

    forecast = []
    times = daily.get("time", []) or []
    for i in range(len(times)):
        d_code = (daily.get("weather_code") or [None])[i] if i < len(daily.get("weather_code") or []) else None
        d_desc, d_icon = describe_weather(d_code)
        try:
            date_obj = datetime.strptime(times[i], "%Y-%m-%d")
            label = "今天" if i == 0 else ("明天" if i == 1 else WEEKDAYS[date_obj.weekday()])
        except Exception:
            label = f"D{i}"
        forecast.append({
            "label": label,
            "desc": d_desc,
            "icon": d_icon,
            "high": _round(daily.get("temperature_2m_max", [None] * (i + 1))[i]),
            "low": _round(daily.get("temperature_2m_min", [None] * (i + 1))[i]),
            "pop": _round(daily.get("precipitation_probability_max", [None] * (i + 1))[i]),
        })

    result = {
        "source": "Open-Meteo",
        "temp": _round(cur.get("temperature_2m")),
        "feels": _round(cur.get("apparent_temperature")),
        "humidity": _round(cur.get("relative_humidity_2m")),
        "wind_speed": _round(cur.get("wind_speed_10m")),
        "wind_dir": wind_dir,
        "precip": _round(cur.get("precipitation")),
        "pressure": _round(cur.get("surface_pressure")),
        "is_day": cur.get("is_day", 1),
        "desc": desc,
        "icon": icon,
        "code": cur.get("weather_code"),
        "uv": _round((daily.get("uv_index_max") or [None])[0]),
        "pop": _round((daily.get("precipitation_probability_max") or [None])[0]),
        "sunrise": _hhmm((daily.get("sunrise") or [None])[0]),
        "sunset": _hhmm((daily.get("sunset") or [None])[0]),
        "forecast": forecast,
        "air": None,
    }

    if cfg.get("weather.show_air", True):
        result["air"] = fetch_air_quality(lat, lon, tz)
    # 把 km/h 就地换成风级，下游（渲染层、自检工具）就不必各自换算一遍了
    result["wind_level"] = effective_wind_level(result)
    return result


def fetch_air_quality(lat, lon, tz: str) -> dict | None:
    payload = get_json(
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={lat}&longitude={lon}&current=pm2_5,pm10"
        f"&timezone={requests.utils.quote(tz)}",
        timeout=20,
    )
    if not payload or "current" not in payload:
        return None
    cur = payload["current"]
    # 故意不再请求 us_aqi：那是美国 EPA 的数，配不上中国的分级文字（见 china_aqi）
    aqi = china_aqi(cur.get("pm2_5"), cur.get("pm10"))
    level, advice = aqi_level(aqi)
    return {
        "aqi": _round(aqi),
        "level": level,
        "advice": advice,
        "pm25": _round(cur.get("pm2_5")),
        "pm10": _round(cur.get("pm10")),
    }


# --- 和风天气（QWeather）-----------------------------------------------------

#: 天气现象文本 -> 图标类别。顺序有意义：先判「雷」「雪」再判「雨」，
#: 否则「雷阵雨」会被划成雨、「雨夹雪」会被划成雨。
QWEATHER_ICONS: tuple[tuple[str, str], ...] = (
    ("雷", "thunder"),
    ("雪", "snow"),
    ("雨", "rain"),
    ("雾", "fog"),
    ("霾", "fog"),
    ("沙", "fog"),
    ("尘", "fog"),
    ("晴间多云", "sun_cloud"),
    ("少云", "sun_cloud"),
    ("多云", "sun_cloud"),
    ("阴", "cloud"),
    ("晴", "sun"),
)


def qweather_icon(text) -> str:
    """和风的天气现象文本转成图标类别。认不出来按「阴」画，绝不空着。"""
    raw = str(text or "")
    for keyword, icon in QWEATHER_ICONS:
        if keyword in raw:
            return icon
    return "cloud"


_WIND_CN = {
    "n": "北", "nne": "东北", "ne": "东北", "ene": "东北", "e": "东",
    "ese": "东南", "se": "东南", "sse": "东南", "s": "南", "ssw": "西南",
    "sw": "西南", "wsw": "西南", "w": "西", "wnw": "西北", "nw": "西北",
    "nnw": "西北",
}


def _qweather_creds(cfg) -> dict:
    """和风的凭据**只从环境变量读**。

    ⚠️ 本仓库是公开的（Kindle 没法带 token 认证，只能走公开地址），凭据写进
    config.yaml 等于把钥匙贴在世界都能看的门上。config.yaml 里那两项保持空着。

    优先 JWT（官方推荐；而且官方明确写了 2027-01-01 起 API KEY 方式会被限流）：
      QWEATHER_HOST         专属域名，如 abcdef.re.qweatherapi.com
      QWEATHER_ISS          开发者ID（控制台 → 设置，Q 开头的 10 位）
      QWEATHER_SUB          项目ID（控制台 → 项目管理）
      QWEATHER_KID          凭据ID（JWT 凭据详情页）
      QWEATHER_PRIVATE_KEY  Ed25519 私钥：PEM 原文，或 PEM 的 base64（一行更好贴）
    只有 API KEY 时退回老办法：QWEATHER_KEY。
    """
    env = os.environ
    host = str(cfg.get("weather.qweather.api_host", "") or env.get("QWEATHER_HOST", "")).strip()
    if str(cfg.get("weather.qweather.api_key", "") or "").strip():
        print("[sources] ⚠️ 和风 Key 写在 config.yaml 里，而这个仓库是**公开的** —— "
              "请清空它，改用 GitHub Secrets 的 QWEATHER_KEY。")
    if host and not host.startswith(("http://", "https://")):
        host = "https://" + host
    return {
        "host": host.rstrip("/"),
        "key": str(cfg.get("weather.qweather.api_key", "")
                   or env.get("QWEATHER_KEY", "")).strip(),
        "iss": env.get("QWEATHER_ISS", "").strip(),
        "sub": env.get("QWEATHER_SUB", "").strip(),
        "kid": env.get("QWEATHER_KID", "").strip(),
        "priv": env.get("QWEATHER_PRIVATE_KEY", "").strip(),
    }


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _qweather_jwt(creds: dict) -> str:
    """签一个和风要的 EdDSA JWT。字段严格按官方文档，不要自作主张加东西。

    header  {alg: EdDSA, kid}
    payload {iss: 开发者ID, sub: 项目ID, iat, exp}
    三段分别 Base64**URL** 编码（不是普通 Base64，也**不能带 = padding**）后用点拼起来。
    官方还特意说明：iat 建议设成当前时间**前 30 秒**，防的是机器之间几秒的时钟差
    直接把 token 判成"还没生效"。
    """
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    pem = creds["priv"]
    if "BEGIN" not in pem:                      # 一行 base64 的写法更好贴进 Secrets
        pem = base64.b64decode(pem).decode("utf-8")
    key = load_pem_private_key(pem.encode("utf-8"), password=None)

    now = int(time.time())
    head = _b64url(json.dumps({"alg": "EdDSA", "kid": creds["kid"]},
                              separators=(",", ":")).encode("utf-8"))
    body = _b64url(json.dumps({"iss": creds["iss"], "sub": creds["sub"],
                               "iat": now - 30, "exp": now + 3600},
                              separators=(",", ":")).encode("utf-8"))
    sig = _b64url(key.sign(f"{head}.{body}".encode("ascii")))
    return f"{head}.{body}.{sig}"


def _qweather_auth(creds: dict):
    """返回 (headers, params)。JWT 优先，没有 JWT 那四样才退回 ?key=。"""
    if all(creds.get(k) for k in ("priv", "kid", "iss", "sub")):
        try:
            return {"Authorization": "Bearer " + _qweather_jwt(creds)}, {}
        except Exception as exc:                                # noqa: BLE001
            print(f"[sources] 和风 JWT 签名失败（{exc!r}），尝试用 API KEY。")
    if creds.get("key"):
        return {}, {"key": creds["key"]}
    return None, None


def _q_num(obj, *path) -> Any:
    """从 {value: 12.3, unit: °C} 这种套娃里取数，任何一层缺失都返回 None。"""
    cur: Any = obj
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _fetch_weather_qweather(cfg) -> dict | None:
    """和风天气 **v1**：实况 + 逐天预报 + 空气质量 + 灾害预警。

    三件必须知道的事（2026-09-26 逐页对着官方文档核过，别照网上的旧教程改回去）：

    1. **v7 正在停服**：天气预警 v7 于 2026-10-01 停止运行，天气预报 v7 于
       2027-08-01 停止。本项目因此直接写 v1 —— 曾经用的
       `/v7/weather/now`、`/v7/warning/now` 那些路径全部作废。
    2. v1 的坐标在**路径**里，顺序是「纬度/经度」；而 v7 是
       `location=经度,纬度`。**两者顺序相反**，从 v7 抄来的代码必然查错城市。
       坐标最多支持小数点后两位（我们的 config.yaml 正好只留两位）。
    3. 每日预报默认返回 **UTC** 时间，必须带 `localTime=true`，否则
       "今天"那一格在北京时间早上会指到昨天/明天的日上去。

    接口的 `metadata.attributions` 要求署名，所以页脚会显示"天气 和风天气"。
    """
    creds = _qweather_creds(cfg)
    host = creds["host"]
    if not host:
        print("[sources] 没配和风天气的 API Host，天气回落到 Open-Meteo。")
        return None
    headers, params = _qweather_auth(creds)
    if headers is None:
        print("[sources] 和风没有可用凭据（JWT 需要 QWEATHER_PRIVATE_KEY / _ISS / "
              "_SUB / _KID，或者改用 QWEATHER_KEY），回落到 Open-Meteo。")
        return None

    lat = cfg.get("location.latitude")
    lon = cfg.get("location.longitude")
    if lat is None or lon is None:
        print("[sources] 未配置经纬度，跳过天气。")
        return None
    coord = f"{lat}/{lon}"

    def get(path: str, **query):
        p = dict(params)
        p.update(query)
        return get_json(f"{host}{path}", headers=headers, params=p, timeout=20)

    cur = get(f"/weather/v1/current/{coord}", lang="zh")
    if not isinstance(cur, dict) or "temperature" not in cur:
        err = (cur or {}).get("error") if isinstance(cur, dict) else None
        print(f"[sources] 和风实况失败（{err or '无响应'}），回落到 Open-Meteo。")
        return None

    wind = cur.get("wind") or {}
    text = str(_q_num(cur, "condition", "text") or "")
    # v1 的风速单位是 m/s，而渲染层和 Open-Meteo 那条路约定的是 km/h，先统一
    speed_ms = _q_num(wind, "speed", "value")
    vis_m = _q_num(cur, "visibility", "value")

    days = max(2, min(int(cfg.get("weather.days", 4)), 10))
    daily_payload = get(f"/weather/v1/daily/{coord}", days=days,
                        localTime="true", lang="zh") or {}
    day_list = daily_payload.get("days") or []

    today_date = datetime.now().date()
    forecast = []
    for i, d in enumerate(day_list[:days]):
        daytime = d.get("daytime") or {}
        start = str(d.get("forecastStartTime") or "")[:10]
        try:
            delta = (datetime.strptime(start, "%Y-%m-%d").date() - today_date).days
        except ValueError:
            delta = i
        label = {0: "今天", 1: "明天"}.get(delta) or (
            WEEKDAYS[datetime.strptime(start, "%Y-%m-%d").weekday()] if len(start) == 10 else f"D{i}")
        d_text = str(_q_num(daytime, "condition", "text") or "")
        pop = _q_num(daytime, "precipitation", "probability")
        astro = d.get("astro") or {}
        forecast.append({
            "label": label,
            "desc": d_text,
            "icon": qweather_icon(d_text),
            "high": _round(_q_num(d, "temperatureMax", "value")),
            "low": _round(_q_num(d, "temperatureMin", "value")),
            "precip": _round(_q_num(daytime, "precipitation", "amount", "value")),
            "uv": _round(d.get("uvIndexMax")),
            # v1 给的是 0~1 的小数，渲染层按百分比显示
            "pop": _round((float(pop) * 100) if pop is not None else None),
        })

    air = None
    if cfg.get("weather.show_air", True):
        ap = get(f"/airquality/v1/current/{coord}", lang="zh") or {}
        indexes = ap.get("indexes") or []
        # 一个国家/地区的标准一条，国内挂墙上当然看中国标准；没有就退回列表第一条
        pick = next((x for x in indexes
                     if str(x.get("code", "")).lower() in ("cn", "china")), None) \
            or (indexes[0] if indexes else None)
        if pick:
            aqi = _round(pick.get("aqi"))
            pollutants = ap.get("pollutances") or ap.get("pollutants") or []

            def pollutant(code):
                for p in pollutants:
                    if str(p.get("code", "")).lower() == code:
                        return _round(_q_num(p, "concentration", "value"))
                return None

            air = {
                "aqi": aqi,
                "level": str(pick.get("category") or "").strip() or aqi_level(aqi)[0],
                "advice": "",
                "pm25": pollutant("pm2p5"),
                "pm10": pollutant("pm10"),
            }

    warning: list[dict] = []
    if cfg.get("weather.show_warning", True):
        wp = get(f"/weatheralert/v1/current/{coord}", lang="zh") or {}
        for item in (wp.get("alerts") or [])[:2]:
            event = str((item.get("eventType") or {}).get("name") or "")
            color = str((item.get("color") or {}).get("code") or "")
            title = clean_text(item.get("headline") or f"{color}{event}预警", 22)
            if title:
                warning.append({"title": title,
                                "severity": color or str(item.get("severity") or "")})

    first = forecast[0] if forecast else {}
    sunrise = _hhmm((day_list[0].get("astro") or {}).get("sunrise")) if day_list else ""
    sunset = _hhmm((day_list[0].get("astro") or {}).get("sunset")) if day_list else ""
    # v1 的实况里没有"现在是白天还是黑夜"，但当天的日出日落就在预报里，比一下时刻
    # 就够了 —— 原来这里写死 is_day=1，等于晚上也画一个太阳出来。
    # "HH:MM" 是零填充的，字符串比较就是时间比较。
    now_hm = datetime.now().strftime("%H:%M")
    is_day = 1 if (not sunrise or not sunset or sunrise <= now_hm < sunset) else 0
    return {
        "source": "和风天气",
        "obs_time": cur.get("forecastStartTime") or cur.get("observationTime"),
        "temp": _round(_q_num(cur, "temperature", "value")),
        "feels": _round(_q_num(cur, "feelsLike", "value")),
        "humidity": _round((float(cur["humidity"]) * 100)
                           if cur.get("humidity") is not None else None),
        "wind_speed": _round((float(speed_ms) * 3.6) if speed_ms is not None else None),
        "wind_level": _round(wind.get("scale")),
        "wind_dir": _WIND_CN.get(str((wind.get("direction") or {}).get("compass", "")).lower(), ""),
        "desc": text,
        "icon": qweather_icon(text),
        "precip": _round(_q_num(cur, "precipitation", "amount", "value")),
        "pressure": _round(_q_num(cur, "pressure", "value")),
        "vis": _round((float(vis_m) / 1000) if vis_m is not None else None),
        "is_day": is_day,
        "uv": _round(cur.get("uvIndex")),
        "pop": first.get("pop"),
        "sunrise": sunrise,
        "sunset": sunset,
        "forecast": forecast,
        "air": air,
        "warning": warning,
    }


def fetch_weather(cfg) -> dict | None:
    """天气入口：和风优先，拿不到就回落 Open-Meteo。"""
    provider = str(cfg.get("weather.provider", "qweather") or "").lower()
    if provider in ("qweather", "hefeng", "和风", "和风天气"):
        result = _fetch_weather_qweather(cfg)
        if result:
            return result
    elif provider and provider not in ("openmeteo", "open-meteo"):
        print(f"[sources] 不认识的天气源 {provider!r}，改用 Open-Meteo。")
    return _fetch_weather_openmeteo(cfg)


def _round(value) -> Any:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _hhmm(iso: str | None) -> str:
    if not iso or "T" not in str(iso):
        return ""
    return str(iso).split("T", 1)[1][:5]


# ---------------------------------------------------------------------------
# 行情：腾讯 -> 新浪 -> Yahoo 三级降级
# ---------------------------------------------------------------------------

def normalize_code(code: str) -> str:
    """把用户写的代码统一成小写内部键，例如 usAAPL -> usaapl、600519 -> sh600519。

    必须全小写：腾讯返回的变量名是 v_usAAPL，用 .lower() 做键；
    如果内部键保留大小写，usAAPL 就永远查不到 usaapl，行情会静默丢失。

    加密合约写成 `bitget:BTCUSDT`，冒号前缀保留，后面统一小写。
    """
    c = str(code or "").strip().replace(" ", "")
    low = c.lower()
    if ":" in low:
        head, _, tail = low.partition(":")
        return f"{head}:{tail}"
    for prefix in ("sh", "sz", "bj", "hk", "us"):
        if low.startswith(prefix):
            return prefix + low[len(prefix):]
    # 纯数字：按位数猜市场
    if c.isdigit():
        if len(c) == 5:
            return "hk" + c
        if len(c) == 6:
            # 6/9 开头沪市，0/3 开头深市，4/8 开头北交所
            if c[0] in "69":
                return "sh" + c
            if c[0] in "03":
                return "sz" + c
            if c[0] in "48":
                return "bj" + c
    return low


def _mk(code: str, name: str, price, prev, *, fallback_name: str = "",
        is_crypto: bool = False) -> dict | None:
    """统一构造行情条目；涨跌幅自己算，避免各provider字段错位。"""
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    try:
        prev = float(prev)
    except (TypeError, ValueError):
        prev = 0.0
    change = price - prev if prev > 0 else 0.0
    pct = (change / prev * 100.0) if prev > 0 else 0.0
    return {
        "code": code,
        "name": (name or fallback_name or code).strip(),
        "price": price,
        "change": change,
        "pct": pct,
        "prev": prev,
        "is_crypto": bool(is_crypto),
    }


def _from_tencent(pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """腾讯行情。一个请求拿全部品种，覆盖 A 股 / 港股 / 美股，是首选源。

    实测字段：v_<code>="<类型>~<名称>~<代码>~<现价>~<昨收>~<今开>~…"
    美股同样适用，例如 v_usAAPL="200~苹果~AAPL.OQ~337.00~332.41~334.77~…"
    """
    if not pairs:
        return {}
    query = ",".join(orig for _, orig in pairs)
    resp = get(
        "https://qt.gtimg.cn/q=" + query,
        headers={"Referer": "https://gu.qq.com/"},
        encoding="gbk",
        timeout=20,
    )
    if resp is None:
        return {}
    out: dict[str, dict] = {}
    for line in resp.text.split(";"):
        line = line.strip()
        if "=" not in line or '"' not in line:
            continue
        key = normalize_code(line.split("=", 1)[0].replace("v_", "").strip())
        body = line.split('"', 2)[1] if line.count('"') >= 2 else ""
        f = body.split("~")
        if len(f) < 5:
            continue
        item = _mk(key, f[1], f[3], f[4])
        if item:
            out[key] = item
    return out


def _from_sina(pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """新浪行情，覆盖 A 股(gb不适用) / 美股(gb_) / 港股(rt_)，作为第二级降级。

    实测字段：
      A 股  hq_str_sh000001="上证指数,今开,昨收,现价,最高,最低,…"   -> 现价[3] 昨收[2]
      美股  hq_str_gb_aapl="苹果,现价,涨跌幅,时间,涨跌额,…,昨收[26]"  -> 现价[1] 昨收[26]
      港股  hq_str_rt_hk00700="英文名,中文名,今开,昨收,最高,最低,现价[6],…" -> 现价[6] 昨收[3]
    """
    if not pairs:
        return {}
    symbols: list[str] = []
    kind_of: dict[str, tuple[str, str]] = {}
    for norm, _orig in pairs:
        if norm.startswith(("sh", "sz", "bj")):
            sym, kind = norm, "cn"
        elif norm.startswith("hk"):
            sym, kind = "rt_" + norm, "hk"
        elif norm.startswith("us"):
            sym, kind = "gb_" + norm[2:], "us"
        else:
            continue
        symbols.append(sym)
        kind_of[sym] = (norm, kind)
    if not symbols:
        return {}

    resp = get(
        "https://hq.sinajs.cn/list=" + ",".join(symbols),
        headers={"Referer": "https://finance.sina.com.cn/"},
        encoding="gbk",
        timeout=20,
    )
    if resp is None:
        return {}

    out: dict[str, dict] = {}
    for m in re.finditer(r'hq_str_([\w]+)="([^"]*)"', resp.text):
        sym, body = m.group(1).lower(), m.group(2)
        if sym not in kind_of or not body.strip():
            continue
        norm, kind = kind_of[sym]
        f = body.split(",")
        name = price = prev = None
        if kind == "cn" and len(f) >= 4:
            name, price, prev = f[0], f[3], f[2]
        elif kind == "us" and len(f) >= 27:
            name, price, prev = f[0], f[1], f[26]
        elif kind == "hk" and len(f) >= 7:
            name, price, prev = (f[1] or f[0]), f[6], f[3]
        item = _mk(norm, name, price, prev)
        if item:
            out[norm] = item
    return out


_YAHOO_SUFFIX = {"sh": ".SS", "sz": ".SZ", "bj": ".BJ"}


def _to_yahoo_symbol(code: str) -> str | None:
    c = normalize_code(code)
    if c.startswith("us"):
        return c[2:].upper()
    if c.startswith("hk"):
        # Yahoo 港股是 4 位：hk00700 -> 0700.HK
        return c[2:].lstrip("0").zfill(4) + ".HK"
    for pre, suffix in _YAHOO_SUFFIX.items():
        if c.startswith(pre):
            return c[len(pre):] + suffix
    return None


def _from_yahoo(pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """Yahoo Finance，第三级降级。

    注意：国内网络直连 query1/query2 基本都会 403，它主要是给
    GitHub Actions（境外 IP）兜底用的，所以必须放在最后一档。
    """
    out: dict[str, dict] = {}
    for norm, _orig in pairs:
        sym = _to_yahoo_symbol(norm)
        if not sym:
            continue
        payload = get_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
            "?interval=1d&range=5d",
            headers={"Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"},
            timeout=20,
        )
        try:
            meta = payload["chart"]["result"][0]["meta"]
        except Exception:
            continue
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        name = meta.get("longName") or meta.get("shortName") or ""
        item = _mk(norm, name, price, prev)
        if item:
            out[norm] = item
    return out


_PROVIDERS = {
    "tencent": _from_tencent,
    "sina": _from_sina,
    "yahoo": _from_yahoo,
}

# ---------------------------------------------------------------------------
# 加密合约：Bitget -> Gate -> HTX 三级降级
#
# 为什么是三家：Bitget 是主力（用户指定的交易所），但 api.bitget.com 从国内
# 直连会被重置连接，只有 GitHub Actions 的美国 IP 能通。Gate（api.gateio.ws）
# 在 2026-09 实测国内 0.2 秒直连可用，是本地预览和 Bitget 挂掉时的实际兜底；
# HTX 作为最后一道，通不通看运气。
# ---------------------------------------------------------------------------

#: 加密合约的代码前缀，写成 bitget:BTCUSDT 这样（前缀只是命名空间，不代表只在 Bitget 取价）
CRYPTO_PREFIX = "bitget:"

#: 记录本轮实际用的是哪家行情，渲染层会在行情区块下面标出来
CRYPTO_SOURCE_LABELS = {
    "bitget": "Bitget 合约",
    "gate": "Gate 合约",
    "htx": "HTX 合约",
}

#: 合约行情都是小 JSON，8 秒足够；给太长会让一个被墙的源拖垮整轮生成
CRYPTO_TIMEOUT = 8


def _from_bitget(pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """Bitget U 本位永续合约。

    接口：GET /api/v2/mix/market/ticker?symbol=BTCUSDT&productType=usdt-futures
    实测字段：lastPr = 最新价，open24h = 24 小时前的开盘价，
             change24h = 涨跌幅（比率，0.01 表示 +1%）

    注意：**国内网络直连 api.bitget.com 会被重置连接**（GFW），
    GitHub Actions 走美国 IP 才通。所以本地预览会落到下面的 Gate。
    """
    out: dict[str, dict] = {}
    for norm, _orig in pairs:
        sym = norm.split(":", 1)[1].upper()
        payload = get_json(
            "https://api.bitget.com/api/v2/mix/market/ticker"
            f"?symbol={sym}&productType=usdt-futures",
            headers={"Accept": "application/json"},
            timeout=CRYPTO_TIMEOUT,
        )
        if payload is None and not out:
            # 头一个 symbol 就取不到：多半整条链路被重置，剩下几个别等了
            print("[sources] Bitget 首个 symbol 即失败，跳过该源剩余请求")
            break
        if not isinstance(payload, dict) or str(payload.get("code")) != "00000":
            continue
        data = payload.get("data")
        if isinstance(data, list):                      # 文档示例是数组，实际多为对象
            data = data[0] if data else None
        if not isinstance(data, dict):
            continue
        last = data.get("lastPr") or data.get("markPrice")
        prev = data.get("open24h") or data.get("openUtc")
        if prev is not None and float(prev or 0) <= 0:
            prev = None
        if prev is None:                                # 没有开盘价就用涨跌幅反推
            try:
                ratio = float(data.get("change24h"))
                prev = float(last) / (1 + ratio) if ratio != -1 else None
            except (TypeError, ValueError):
                prev = None
        item = _mk(norm, sym, last, prev, is_crypto=True)
        if item:
            out[norm] = item
    return out


def _from_gate(pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """Gate.io U 本位永续合约。**国内唯一稳定直连的合约源**，实测 0.2 秒。

    接口：GET /api/v4/futures/usdt/tickers?contract=BTC_USDT
    实测字段：last = 最新价，change_price = 24 小时变动绝对值
             （没有现成的"24 小时前开盘价"，用 last - change_price 反推，
              涨跌幅照旧由我们自己对现价/昨收算，跟股票那套保持一致）

    返回的是数组（按 contract 过滤后只有一个元素），但文档口径不保证，两种都接。
    """
    out: dict[str, dict] = {}
    for norm, _orig in pairs:
        sym = norm.split(":", 1)[1].upper()
        if not sym.endswith("USDT"):
            continue
        contract = sym[:-4] + "_USDT"
        payload = get_json(
            "https://api.gateio.ws/api/v4/futures/usdt/tickers"
            f"?contract={contract}",
            headers={"Accept": "application/json"},
            timeout=CRYPTO_TIMEOUT,
        )
        if payload is None and not out:
            print("[sources] Gate 首个 symbol 即失败，跳过该源剩余请求")
            break
        row = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(row, dict):
            continue
        last = row.get("last")
        prev = None
        try:
            if last is not None and row.get("change_price") is not None:
                prev = float(last) - float(row["change_price"])
                if prev <= 0:
                    prev = None
        except (TypeError, ValueError):
            prev = None
        item = _mk(norm, sym, last, prev, is_crypto=True)
        if item:
            out[norm] = item
    return out


def _from_htx(pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """火币 HTX 永续合约，最后一道兜底（通不通看运气）。

    接口：GET /linear-swap-ex/market/detail/merged?contract_code=BTC-USDT
    实测字段：tick.close = 最新价，tick.open = 24 小时前的开盘价
    """
    out: dict[str, dict] = {}
    for norm, _orig in pairs:
        sym = norm.split(":", 1)[1].upper()
        if not sym.endswith("USDT"):
            continue
        contract = sym[:-4] + "-USDT"
        payload = get_json(
            "https://api.hbdm.com/linear-swap-ex/market/detail/merged"
            f"?contract_code={contract}",
            timeout=CRYPTO_TIMEOUT,
        )
        if payload is None and not out:
            print("[sources] HTX 首个 symbol 即失败，跳过该源剩余请求")
            break
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            continue
        tick = payload.get("tick") or {}
        item = _mk(norm, sym, tick.get("close"), tick.get("open"), is_crypto=True)
        if item:
            out[norm] = item
    return out


#: 顺序即优先级。Bitget 是用户指定的交易所，但国内只有 Actions 能通，
#: 所以后面挂 Gate（国内可直连）和 HTX 两道兜底。
_CRYPTO_PROVIDERS = (
    ("bitget", _from_bitget),
    ("gate", _from_gate),
    ("htx", _from_htx),
)


def fetch_quotes(cfg) -> list[dict]:
    items = cfg.get("quotes.items", []) or []
    if not cfg.get("quotes.enabled", True) or not items:
        return []

    # (内部小写键, 用户原始写法)。原始写法要留着——腾讯接口对大小写敏感。
    pairs: list[tuple[str, str]] = []
    crypto: list[tuple[str, str]] = []
    display_names: dict[str, str] = {}
    for it in items:
        raw = str(it.get("code", ""))
        norm = normalize_code(raw)
        if not norm:
            continue
        (crypto if norm.startswith(CRYPTO_PREFIX) else pairs).append((norm, raw))
        display_names[norm] = it.get("name", "")

    results: dict[str, dict] = {}

    # --- 指数 / 股票：腾讯 -> 新浪 -> Yahoo ---
    for provider_name in (cfg.get("quotes.providers", ["tencent", "sina", "yahoo"]) or []):
        missing = [(n, o) for n, o in pairs if n not in results]
        if not missing:
            break
        fn = _PROVIDERS.get(provider_name)
        if not fn:
            continue
        try:
            got = fn(missing)
        except Exception as exc:                       # noqa: BLE001
            print(f"[sources] 行情源 {provider_name} 异常：{exc}")
            got = {}
        results.update(got)
        print(f"[sources] 行情源 {provider_name}：命中 {len(got)}/{len(missing)}")

    # --- 加密合约：Bitget -> HTX ---
    for provider_name, fn in _CRYPTO_PROVIDERS:
        missing = [(n, o) for n, o in crypto if n not in results]
        if not missing:
            break
        try:
            got = fn(missing)
        except Exception as exc:                       # noqa: BLE001
            print(f"[sources] 加密源 {provider_name} 异常：{exc}")
            got = {}
        for item in got.values():
            item["provider"] = provider_name
        results.update(got)
        print(f"[sources] 加密源 {provider_name}：命中 {len(got)}/{len(missing)}")

    out = []
    for norm, _raw in pairs + crypto:
        item = results.get(norm)
        if not item:
            # 抓不到也保留一行，界面显示“--”，便于判断是接口挂了还是代码写错
            out.append({"code": norm, "name": display_names.get(norm) or norm,
                        "price": None, "change": None, "pct": None, "prev": None,
                        "is_crypto": norm.startswith(CRYPTO_PREFIX)})
            continue
        if display_names.get(norm):
            item["name"] = display_names[norm]
        out.append(item)
    return out


def crypto_source_label(quotes: list[dict]) -> str:
    """本轮加密价格用的是哪家，用来在屏幕上如实标注。"""
    used = {q.get("provider") for q in (quotes or []) if q.get("is_crypto") and q.get("provider")}
    if not used:
        return ""
    label = " + ".join(sorted(CRYPTO_SOURCE_LABELS.get(u, u) for u in used))
    # 不是 Bitget 就说明主力源这一轮没通，如实写出来，免得以为是 Bitget 的报价
    if "bitget" not in used:
        label += "（Bitget 不可达）"
    return label


def fetch_funds(cfg) -> list[dict]:
    funds = cfg.get("quotes.funds", []) or []
    if not cfg.get("quotes.enabled", True) or not funds:
        return []
    out = []
    for fund in funds:
        code = str(fund.get("code", "")).strip()
        if not code:
            continue
        out.append(_fetch_one_fund(code, fund.get("name", "")))
    return out


def _fetch_one_fund(code: str, name: str) -> dict:
    entry = {"code": code, "name": name or code, "price": None, "pct": None, "is_fund": True}
    ts = int(time.time() * 1000)
    resp = get(f"https://fundgz.1234567.com.cn/js/{code}.js?rt={ts}",
               headers={"Referer": "https://fund.eastmoney.com/"}, timeout=20)
    if resp is not None:
        m = re.search(r"jsonpgz\((\{.*?\})\)", resp.text)
        if m:
            try:
                import json
                data = json.loads(m.group(1))
                entry["name"] = name or data.get("name", code)
                entry["price"] = float(data.get("gsz") or data.get("dwjz"))
                entry["pct"] = float(data.get("gszzl") or 0)
                entry["date"] = data.get("gztime", "")
                return entry
            except Exception:
                pass
    # 降级：非交易时段 fundgz 会空，去 pingzhongdata 抠最新净值
    resp = get(f"https://fund.eastmoney.com/pingzhongdata/{code}.js",
               headers={"Referer": "https://fund.eastmoney.com/"}, timeout=20)
    if resp is not None:
        m = re.search(r'Data_netWorthTrend\s*=\s*(\[.*?\]);', resp.text)
        if m:
            try:
                import json
                trend = json.loads(m.group(1))
                if trend:
                    last = trend[-1]
                    entry["price"] = float(last.get("y"))
                    if len(trend) > 1:
                        prev = float(trend[-2].get("y"))
                        if prev:
                            entry["pct"] = (entry["price"] - prev) / prev * 100
                    name_m = re.search(r'fS_name\s*=\s*"([^"]*)"', resp.text)
                    if name_m and not name:
                        entry["name"] = name_m.group(1)
            except Exception:
                pass
    return entry


# ---------------------------------------------------------------------------
# 新闻 RSS / Atom
# ---------------------------------------------------------------------------

_NS = {"atom": "http://www.w3.org/2005/Atom"}

# RSS 世界里不合法的东西比想象中多：裸 & 、控制字符、未闭合标签。
# 严格解析会整个源丢掉，所以先做一遍消毒。
_XML_BAD_RE = re.compile(r"[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD]")
_XML_BARE_AMP_RE = re.compile(
    r"&(?!(?:[a-zA-Z][a-zA-Z0-9]{1,7}|#\d{1,6}|#x[0-9a-fA-F]{1,6});)")


def _sanitize_xml(text: str) -> str:
    return _XML_BARE_AMP_RE.sub("&amp;", _XML_BAD_RE.sub("", text))


def looks_like_feed(text: str) -> bool:
    """很多站点关掉 RSS 后会把 /feed 指向一个 HTML 门禁页，必须提前识别。"""
    head = text[:1200].lower()
    return any(tag in head for tag in ("<rss", "<feed", "<rdf:rdf", "<channel"))


def _parse_feed(xml_text: str, source: str) -> list[dict]:
    items: list[dict] = []
    try:
        root = ET.fromstring(_sanitize_xml(xml_text))
    except Exception as exc:
        print(f"[sources] {source} XML 解析失败：{exc}")
        return []

    # RSS 2.0
    for node in root.iter():
        if node.tag.split("}")[-1] != "item":
            continue
        title = clean_text(_child_text(node, "title"))
        if not title:
            continue
        items.append({
            "title": title,
            "link": clean_text(_child_text(node, "link")),
            "summary": clean_summary(_child_text(node, "description"), limit=260),
            "published": _parse_date(_child_text(node, "pubDate")),
            "source": source,
        })

    # Atom
    if not items:
        for node in root.iter():
            if node.tag.split("}")[-1] != "entry":
                continue
            title = clean_text(_child_text(node, "title"))
            if not title:
                continue
            link = ""
            for child in node:
                if child.tag.split("}")[-1] == "link":
                    link = child.attrib.get("href", "") or link
            items.append({
                "title": title,
                "link": link,
                "summary": clean_summary(
                    _child_text(node, "summary") or _child_text(node, "content"), limit=260),
                "published": _parse_date(_child_text(node, "updated")
                                         or _child_text(node, "published")),
                "source": source,
            })
    return items


def _child_text(node, tag: str) -> str:
    for child in node:
        if child.tag.split("}")[-1] == tag:
            return "".join(child.itertext())
    return ""


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw.replace("Z", "+0000"), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def fetch_news(cfg, per_feed: int = 12) -> dict[str, list[dict]]:
    """按栏目并发抓取所有 RSS 源，返回 {栏目key: [条目…]}。

    每个条目都带上了 category / category_name，渲染层靠它打栏目标签。
    栏目内按「关键词命中数 -> 时间」排序，命中多的排前面。
    """
    if not cfg.get("digest.enabled", True):
        return {}
    cats = cfg.categories
    if not cats:
        return {}

    from concurrent.futures import ThreadPoolExecutor

    jobs = [(cat, feed) for cat in cats for feed in (cat.get("feeds") or [])]

    def one(job) -> tuple[str, list[dict]]:
        cat, feed = job
        url = feed.get("url", "")
        name = feed.get("name") or url
        if not url:
            return cat["key"], []
        resp = get(url, timeout=18, retries=1)
        if resp is None:
            return cat["key"], []
        if not looks_like_feed(resp.text):
            print(f"[sources] {name}：返回的不是 RSS（多半是反爬页或已下线），跳过")
            return cat["key"], []
        parsed = _parse_feed(resp.text, name)
        print(f"[sources] [{cat.get('name')}] {name}：{len(parsed)} 条")
        return cat["key"], parsed[:per_feed]

    collected: dict[str, list[dict]] = {c["key"]: [] for c in cats}
    if jobs:
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
            for key, items in pool.map(one, jobs):
                collected[key].extend(items)

    out: dict[str, list[dict]] = {}
    for cat in cats:
        key = cat["key"]
        ranked = rank_news(collected.get(key, []), cat.get("keywords") or [], keep=12)
        for item in ranked:
            item["category"] = key
            item["category_name"] = cat.get("name") or key
        out[key] = ranked
    return out


def rank_news(items: list[dict], keywords: list[str], keep: int = 16) -> list[dict]:
    """先按关键词相关性、再按时间排序，并去重。"""
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in items:
        title = item.get("title", "")
        if not title:
            continue
        fingerprint = re.sub(r"\W+", "", title)[:24]      # 归一化后取前 24 字做指纹
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(item)

    kw_lower = [k.lower() for k in keywords if k]

    def sort_key(item: dict):
        title = item.get("title", "").lower()
        summary = item.get("summary", "").lower()
        hits = sum(1 for k in kw_lower if k in title) * 2 + sum(1 for k in kw_lower if k in summary)
        published = item.get("published")
        # 时间戳取负值，让新的排前面
        ts = -published.timestamp() if published else 0
        return (-hits, ts)

    deduped.sort(key=sort_key)
    return deduped[:keep]
