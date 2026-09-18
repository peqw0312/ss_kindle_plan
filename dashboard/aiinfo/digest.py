"""速览生成：每个栏目挑一条。

免费方案的核心：GitHub Actions 里的 GITHUB_TOKEN 可以直接调 GitHub Models
（workflow 里声明 permissions: models: read 即可），不需要申请任何 API Key，
也不需要绑卡。免费额度对"一天生成几十次"这种用量绰绰有余。

三条硬性要求：
1. 任何失败都必须降级成"栏目里排名最高的那条原始标题"——墙上的屏幕不能因为模型限流就空掉。
2. 输出必须短。墨水屏刷新慢，一条摘要超过 40 字在 6 寸屏上就是灾难。
3. 栏目结构不能被模型改掉。模型少给、多给、换名，都要由程序纠正回来。
"""

from __future__ import annotations

import json
import os
import re

import requests

from .sources import clean_text

_TOKEN_ENV_KEYS = ("GITHUB_TOKEN", "GH_MODELS_TOKEN", "AIINFO_TOKEN")

SYSTEM_PROMPT = """你是一块电子墨水屏的信息编辑。你的读者只有 5 秒钟：他路过这块屏，扫一眼就走。

下面按「{names}」几个栏目分别给出一批候选新闻，请**每个栏目各挑 1 条**最重要的，编译成中文速览。

每个栏目的定位（据此判断"什么才算这条栏目里最重要的事"）：
{briefs}

硬性要求：
1. 栏目不能换、不能少、不能多，顺序与给定顺序一致。
2. 标题不超过 18 个字，必须一眼看懂"发生了什么"，去掉"重磅""炸裂"这类标题党词汇。
3. 摘要不超过 34 个字，说清"为什么它重要"或"对普通人意味着什么"，不要复述标题。
   3A 游戏栏目讲清是什么游戏、什么平台、什么动向；AI 栏目保留英文专有名词（GPT-5、Claude、CUDA）；
   国内热点栏目用平实的中文陈述事实。
4. 不要出现 Markdown 标记、表情符号、序号、书名号之外的符号装饰。
5. 候选里如果某一栏目全是公关稿或没有实质内容，就挑其中相对最有信息量的一条。

只输出一个 JSON 对象，不要任何解释文字，格式如下：
{{"items": [{{"category": "栏目名", "title": "标题", "summary": "摘要"}}]}}"""


def _find_token() -> str | None:
    for key in _TOKEN_ENV_KEYS:
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _format_feed(cat_name: str, news: list[dict], limit: int = 8) -> str:
    lines = [f"【{cat_name}】"]
    for i, item in enumerate(news[:limit], 1):
        title = clean_text(item.get("title"), 70)
        summary = clean_text(item.get("summary"), 110)
        source = item.get("source", "")
        line = f"{i}. [{source}] {title}"
        if summary:
            line += f" —— {summary}"
        lines.append(line)
    return "\n".join(lines)


def _extract_json(text: str) -> dict | None:
    """从模型回复里抠出 JSON。模型的嘴很松，得多留几手。"""
    if not text:
        return None
    # 去掉 ```json ``` 围栏
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    # 退一步：抓第一个平衡的 {...}
    start = text.find("{")
    while start != -1:
        depth = 0
        for idx in range(start, len(text)):
            ch = text[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start:idx + 1]
                    try:
                        return json.loads(chunk)
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


def _parse_loose(text: str) -> list[dict]:
    """连 JSON 都没有时的兜底：按行拆 '栏目：标题：摘要' 这种松散格式。"""
    items = []
    for raw_line in (text or "").splitlines():
        line = clean_text(raw_line)
        line = re.sub(r"^[\d]+[.、)）:：\s-]+", "", line)     # 去掉行首序号
        if len(line) < 6:
            continue
        parts = re.split(r"[：:—-]{1,2}", line, maxsplit=2)
        if not parts or not parts[0]:
            continue
        if len(parts) >= 3:
            items.append({"category": parts[0], "title": parts[1], "summary": parts[2]})
        else:
            items.append({"category": "", "title": parts[0],
                          "summary": parts[1] if len(parts) > 1 else ""})
    return items


def _call_model(cfg, prompt: str, names: list[str], briefs: str = "") -> list[dict] | None:
    token = _find_token()
    if not token:
        print("[digest] 没找到 GITHUB_TOKEN，跳过 AI 摘要，直接用原始标题。")
        return None

    endpoint = cfg.get("digest.endpoint", "https://models.github.ai/inference/chat/completions")
    model = cfg.get("digest.model", "openai/gpt-4o-mini")
    timeout = int(cfg.get("digest.timeout", 60))

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.format(
                names=" / ".join(names), briefs=briefs or "（按栏目名理解即可）")},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 900,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        resp = requests.post(endpoint, headers=headers, json=body, timeout=timeout)
    except Exception as exc:                           # noqa: BLE001
        print(f"[digest] 调用模型失败（网络）：{exc}")
        return None

    if resp.status_code != 200:
        print(f"[digest] 调用模型失败：HTTP {resp.status_code} {clean_text(resp.text, 200)}")
        return None

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:                           # noqa: BLE001
        print(f"[digest] 模型返回结构异常：{exc}")
        return None

    data = _extract_json(content)
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        candidates = data["items"]
    elif isinstance(data, list):
        candidates = data
    else:
        candidates = _parse_loose(content)

    out = []
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        title = clean_text(entry.get("title") or entry.get("headline"), 60)
        if not title:
            continue
        out.append({
            "category_name": clean_text(entry.get("category") or entry.get("name"), 12),
            "title": title,
            "summary": clean_text(entry.get("summary") or entry.get("desc") or "", 90),
        })
    return out or None


def _fallback_item(cat: dict, news: list[dict]) -> dict | None:
    """降级模式：直接用该栏目排名最高那条的原始标题。"""
    if not news:
        return None
    top = news[0]
    return {
        "category": cat["key"],
        "category_name": cat.get("name") or cat["key"],
        "title": clean_text(top.get("title"), 60),
        "summary": clean_text(top.get("summary"), 90),
        "source": top.get("source", ""),
    }


def build_digest(cfg, news: dict[str, list[dict]]) -> dict:
    """每个栏目各出一条。永远返回可渲染的结构。"""
    title = cfg.get("digest.title", "今日速览")
    cats = cfg.categories if cfg.get("digest.enabled", True) else []
    if not cats:
        return {"title": title, "items": [], "ai": False, "model": ""}

    per_cat = max(1, int(cfg.get("digest.items", len(cats))) // max(1, len(cats)))
    active = [c for c in cats if (news or {}).get(c["key"])]
    empty = [c.get("name") or c["key"] for c in cats if c not in active]
    if empty:
        print(f"[digest] 这些栏目本轮没有候选（源可能被墙）：{'、'.join(empty)}")
    if not active:
        return {"title": title, "items": [], "ai": False, "model": "",
                "empty_categories": empty}

    # ---- 先按栏目各出一份降级结果，AI 成功的那部分再覆盖 ----
    picked: dict[str, dict] = {}
    for cat in active:
        item = _fallback_item(cat, news[cat["key"]])
        if item:
            picked[cat["key"]] = item

    used_ai = False
    if cfg.get("digest.use_ai", True) and any(news.get(c["key"]) for c in active):
        names = [c.get("name") or c["key"] for c in active]
        # 把栏目定位一起给模型：光看"3A游戏"这四个字，它很容易把手机游戏也选进来
        briefs = "\n".join(f"- {c.get('name') or c['key']}：{c.get('brief') or '（无特别说明）'}"
                           for c in active)
        chunks = [_format_feed(c.get("name") or c["key"], news[c["key"]]) for c in active]
        prompt = ("以下是今天的候选新闻，请按栏目各挑一条编译成速览：\n\n"
                  + "\n\n".join(chunks))
        ai_items = _call_model(cfg, prompt, names, briefs)
        if ai_items:
            by_name = {n: c["key"] for n, c in
                       zip(names, active)}
            by_key = {c["key"]: c for c in active}
            # 模型可能改栏目名，所以先按名字对，对不上就按顺序对
            for idx, entry in enumerate(ai_items):
                key = None
                label = entry.get("category_name") or ""
                if label in by_name:
                    key = by_name[label]
                elif idx < len(active):
                    key = active[idx]["key"]
                if not key or key not in by_key:
                    continue
                picked[key] = {
                    "category": key,
                    "category_name": by_key[key].get("name") or key,
                    "title": entry["title"],
                    "summary": entry.get("summary", ""),
                }
                used_ai = True
            if used_ai:
                print(f"[digest] AI 摘要成功，覆盖 {len(picked)} 个栏目"
                      f"（模型 {cfg.get('digest.model')}）。")

    if not used_ai:
        print("[digest] 使用降级模式：直接展示原始标题。")

    items: list[dict] = []
    for cat in cats:                       # 保持配置里的栏目顺序
        item = picked.get(cat["key"])
        if item:
            items.append(item)
        if len(items) >= per_cat * len(active):
            break

    return {"title": title, "items": items, "ai": used_ai,
            "model": cfg.get("digest.model", "") if used_ai else "",
            "empty_categories": empty}
