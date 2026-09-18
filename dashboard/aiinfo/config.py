"""配置加载：把 config.yaml 深合并到默认值上。

设计取舍：不引入 pydantic / dataclass 校验，保持零学习成本——
用户删掉某一行不会导致崩溃，缺什么用什么默认值。
"""

from __future__ import annotations

import copy
import os
from typing import Any

import yaml

# Kindle 各型号的屏幕参数。eips 只认真实分辨率，写错会显示不全或花屏。
DEVICE_PRESETS: dict[str, tuple[int, int]] = {
    "kindle_pw1": (758, 1024),
    "kindle_pw2": (1080, 1430),
    "kindle_pw3": (1072, 1448),   # Paperwhite 3 / 第七代 —— 本项目目标机型
    "kindle_pw4": (1072, 1448),
    "kindle_pw5": (1236, 1648),
    "kindle_basic": (600, 800),
    "kindle_10": (600, 800),
    "kindle_11": (1072, 1448),
    "kindle_oasis2": (1264, 1680),
    "kindle_oasis3": (1264, 1680),
    "kindle_scribe": (1860, 2480),
    "kobo_clara": (1072, 1448),
}

DEFAULTS: dict[str, Any] = {
    "device": {
        "model": "kindle_pw3",
        # 留空则按 model 预设取；两者都填以 width/height 为准
        "width": 0,
        "height": 0,
        "margin": 48,
    },
    "style": {
        # 设计预设，见 render.py 的 STYLE_PRESETS：经典 / 大字 / 紧凑 / 中式。
        # 一组配平过的字号 + 边距，不是单个字号开关。
        # 改完必须重跑 make_clock_assets.py —— 日历条的字一变，时钟留白区就跟着动；
        # 要拷哪个文件由那个脚本最后告诉你就行（只挪坐标时 clock.conf 一个文件）。
        "preset": "经典",
    },
    "location": {
        "name": "杭州 · 临平山",
        "latitude": 30.4159,
        "longitude": 120.2804,
        "timezone": "Asia/Shanghai",
    },
    "calendar": {
        "enabled": True,
        "show_yiji": True,      # 宜忌 / 冲煞 一行
        "show_ganzhi": True,    # 干支与生肖
    },
    "weather": {
        "enabled": True,
        "days": 4,          # 含今天在内展示几天
        "show_air": True,
        "show_sun": True,   # 显示日出日落
    },
    "digest": {
        "enabled": True,
        "title": "今日速览",
        "items": 3,
        "max_title_lines": 1,      # 1 行 = 6 寸屏上约 27 个汉字，标题短一点更好读
        "max_summary_lines": 2,
        "use_ai": True,
        "model": "openai/gpt-4o-mini",
        "endpoint": "https://models.github.ai/inference/chat/completions",
        "timeout": 60,
        # 每个栏目各出一条，栏目的顺序就是屏幕上的顺序
        "categories": [
            {
                "key": "game",
                "name": "游戏",
                "brief": "主流 3A 游戏热文",
                "keywords": ["PS5", "Xbox", "Switch", "任天堂", "索尼", "微软", "Steam",
                             "发售", "销量", "重制", "预告", "独占", "评测", "3A",
                             "主机", "游戏", "DLC", "复刻"],
                "feeds": [
                    {"name": "机核 GCORES", "url": "https://www.gcores.com/rss"},
                    {"name": "游研社", "url": "https://www.yystv.cn/rss/feed"},
                    {"name": "触乐", "url": "https://www.chuapp.com/feed"},
                    {"name": "PC Gamer", "url": "https://www.pcgamer.com/rss/"},
                    {"name": "GameSpot", "url": "https://www.gamespot.com/feeds/news/"},
                    {"name": "Eurogamer", "url": "https://www.eurogamer.net/feed"},
                    {"name": "RockPaperShotgun", "url": "https://www.rockpapershotgun.com/feed"},
                    {"name": "VGC", "url": "https://www.videogameschronicle.com/feed/"},
                    {"name": "GamesIndustry", "url": "https://www.gamesindustry.biz/feed"},
                    {"name": "Nintendo Life", "url": "https://www.nintendolife.com/feeds/news"},
                ],
            },
            {
                "key": "ai",
                "name": "AI",
                "brief": "人工智能与大模型行业动态",
                "keywords": ["AI", "人工智能", "大模型", "模型", "OpenAI", "Anthropic",
                             "Google", "DeepMind", "芯片", "GPU", "英伟达", "NVIDIA",
                             "算力", "智能体", "Agent", "开源", "机器人", "Claude",
                             "Gemini", "GPT", "DeepSeek", "推理", "训练"],
                "feeds": [
                    {"name": "量子位", "url": "https://www.qbitai.com/feed"},
                    {"name": "爱范儿", "url": "https://www.ifanr.com/feed"},
                    {"name": "IT之家", "url": "https://www.ithome.com/rss/"},
                    {"name": "InfoQ", "url": "https://www.infoq.cn/feed"},
                    {"name": "雷锋网", "url": "https://www.leiphone.com/feed"},
                    {"name": "极客公园", "url": "https://www.geekpark.net/rss"},
                    {"name": "Solidot", "url": "https://www.solidot.org/index.rss"},
                    {"name": "TechCrunch AI",
                     "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
                    {"name": "The Verge AI",
                     "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
                    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/"},
                ],
            },
            {
                "key": "cn",
                "name": "中国",
                "brief": "中国国内新闻热点",
                "keywords": ["中国", "国内", "经济", "政策", "发布", "国务院", "央行",
                             "消费", "就业", "出口", "电网", "高铁", "航天"],
                "feeds": [
                    {"name": "中国新闻网", "url": "https://www.chinanews.com.cn/rss/scroll-news.xml"},
                    {"name": "中新网要闻", "url": "https://www.chinanews.com.cn/rss/importnews.xml"},
                    {"name": "人民网", "url": "http://www.people.com.cn/rss/politics.xml"},
                    {"name": "新华网", "url": "http://www.xinhuanet.com/politics/news_politics.xml"},
                    {"name": "界面新闻", "url": "https://a.jiemian.com/index.php?m=article&a=rss"},
                ],
            },
        ],
    },
    "quotes": {
        "enabled": True,
        "providers": ["tencent", "sina", "yahoo"],
        # 加密合约走独立的取价链路（Bitget -> Gate -> HTX），见 sources.py 的 _from_bitget。
        # 代码保留，配置里不写 bitget: 前缀就不会跑。
        "items": [
            {"code": "usNDX", "name": "纳指100"},
            {"code": "usINX", "name": "标普500"},
            {"code": "sh000001", "name": "上证指数"},
        ],
        "funds": [],
    },
    "clock": {
        # 时刻文字样式：cn12「下午 4:34」/ en12「4:34 PM」/ h24「16:34」
        "style": "cn12",
        # image：时钟画进图里 —— 想让它准，只能让图反复重算
        # local：图里这块留白，Kindle 用**自己系统的时间**贴精灵图。
        #        于是"时间准"和"图多久重算一次"彻底解耦：天气行情一天算几次，
        #        但屏幕上每一分钟都是对的。
        #        前提是先跑 dashboard/tools/make_clock_assets.py 生成精灵图，
        #        Kindle 端再设 CLOCK_MODE=local；两者缺一就会剩一块空白。
        "mode": "image",
        # 生成精灵图时用的字体。留空 = 跟出图走同一套自动解析。
        # **必须和"图上其他文字"一致**，否则时钟会像另一块拼上去的。
        # 云端出图实测解析到 Noto Sans CJK SC，所以本机生成精灵图时指到
        # 本机同族的 Noto；云端不生成精灵图，这两个路径在那边不存在也无妨。
        "font": "",
        "font_bold": "",
    },
    "display": {
        # posterize > 0 时把灰度压到 N 级，用来预览 16 级墨水屏的实际观感
        "posterize": 0,
    },
    "network": {
        # true 时跟随 HTTP_PROXY / HTTPS_PROXY 等环境变量。
        # 默认 false：数据源国内都能直连，跟着代理走反而会被绕到境外节点。
        "use_env_proxy": False,
    },
    "cloud": {
        # 云端出图服务（dashboard/app.py）在**哪几个时刻**重算一张，
        # 形如 ["00:05", "12:00", "15:05"]，按 location.timezone 判定。
        # **不能按服务器本地时间**：云端容器多半跑在 UTC，那样 00:05 会落到北京时间 08:05。
        # 留空则退回下面的固定间隔。
        "refresh_at": [],
        "refresh_minutes": 15,
    },
    "output": {
        "png": "docs/dashboard.png",
        "html": "docs/index.html",
        "debug_json": "docs/debug.json",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """把 override 深合并进 base 的副本。list 整体替换，不做元素级合并。"""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """极薄的配置包装，支持 cfg["a"]["b"] 与 cfg.get("a.b", default)。"""

    def __init__(self, data: dict):
        self.data = _deep_merge(DEFAULTS, data or {})

    # ---- 载入 ----
    @classmethod
    def load(cls, path: str | None) -> "Config":
        if not path or not os.path.exists(path):
            return cls({})
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"配置文件格式不对，顶层应该是字典：{path}")
        return cls(raw)

    # ---- 访问 ----
    def __getitem__(self, key: str):
        return self.data[key]

    def get(self, dotted: str, default=None):
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    # ---- 派生 ----
    @property
    def size(self) -> tuple[int, int]:
        """返回 (宽, 高) 像素。显式宽高优先，否则按机型预设。"""
        w = int(self.get("device.width", 0) or 0)
        h = int(self.get("device.height", 0) or 0)
        if w > 0 and h > 0:
            return w, h
        model = str(self.get("device.model", "kindle_pw3")).lower().strip()
        if model in DEVICE_PRESETS:
            return DEVICE_PRESETS[model]
        # 未知机型：硬照 PDF 缩放，比崩溃友好
        print(f"[config] 未知机型 {model!r}，回退到 1072x1448。")
        return DEVICE_PRESETS["kindle_pw3"]

    @property
    def scale(self) -> float:
        """相对 1072x1448 基准的缩放系数，让同一套排版适配所有机型。"""
        w, _ = self.size
        return w / 1072.0

    @property
    def categories(self) -> list[dict]:
        """资讯栏目。每个栏目出一条，栏目顺序就是屏幕上的顺序。

        兼容老配置：只写了 digest.feeds 没写 categories 时，当成单一「综合」栏目。
        """
        cats = self.get("digest.categories", []) or []
        cats = [c for c in cats if isinstance(c, dict) and (c.get("feeds") or [])]
        if cats:
            return cats
        feeds = self.get("digest.feeds", []) or []
        if not feeds:
            return []
        return [{
            "key": "all",
            "name": self.get("digest.title", "速览"),
            "brief": "",
            "keywords": self.get("digest.keyword_filter", []) or [],
            "feeds": feeds,
        }]
