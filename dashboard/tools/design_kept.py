#!/usr/bin/env python3
"""已确定要留的产物 · 一张汇总页（2026-09-26）。

这里不设计新东西，只做一件事：把用户已经点头的两套版面 + 图标决策，
用**当前生产代码**重新出图，拼成一页可对比的汇总，并写清每套的状态。

  C1「带」        已上生产（style.layout=c1）—— 最坏情况 + 平时无预警两态
  信息终端（ARC）   本轮定稿，待移植 —— 最坏 + 无预警两态，图标用 B 套线稿

图标决策一起验证：雨系滴数=强度（毛毛雨/小雨 1、中雨 2、大雨与阵雨 3、暴雨 4），
非纸底（今天格黑、昨天格 130 灰）只用纯黑或纯白 —— 灰底矩阵在 图标.html 里。

用法：python dashboard/tools/design_kept.py
产物：.workbuddy/_directions/保留-*.png + 确定.html
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "dashboard"))

from design_directions import OUT, W, H                     # noqa: E402
from design_bw import worst_bw                                 # noqa: E402
from design_arc import FontsArc, lay_terminal, render as arc_render   # noqa: E402

from aiinfo.config import Config                            # noqa: E402
from aiinfo.lunar import calendar_info                      # noqa: E402
from aiinfo.render import Renderer                          # noqa: E402

CFG = Config.load(str(ROOT / "dashboard" / "config.yaml"))
BAT = (814, 40, 1016, 96)


# ---- C1：生产引擎直接出图 ---------------------------------------------------

C1_WORST = {
    "weather": {
        "temp": 30, "desc": "多云转小雨", "icon": "sun_cloud", "source": "Open-Meteo",
        "feels": 33, "humidity": 78, "pop": 60, "wind_dir": "东南", "wind_level": 3,
        "air": {"level": "轻度污染", "aqi": 118}, "uv": 6,
        "warning": [{"title": "杭州市气象台发布强对流天气黄色预警", "severity": "yellow"},
                    {"title": "杭州市气象台发布雷电黄色预警信号", "severity": "yellow"}],
        "forecast": [
            {"label": "今天", "icon": "sun_cloud", "desc": "多云转小雨", "high": 30, "low": 24},
            {"label": "明天", "icon": "rain_light", "desc": "小雨", "high": 29, "low": 23},
            {"label": "周六", "icon": "rain_heavy", "desc": "大雨", "high": 27, "low": 22},
            {"label": "周日", "icon": "rain_storm", "desc": "暴雨", "high": 26, "low": 21},
        ],
    },
    "quotes": [{"name": "纳斯达克100", "price": 29446.98, "pct": 1.73},
               {"name": "标普500", "price": 7765.42, "pct": -1.14},
               {"name": "上证指数", "price": 3955.10, "pct": 0.14}],
    "funds": [], "digest": {"items": []},
}


def c1_shot(fname, day, warn):
    data = {"weather": dict(C1_WORST["weather"], warning=C1_WORST["weather"]["warning"]
                            if warn else []),
            "quotes": list(C1_WORST["quotes"]), "funds": [], "digest": {"items": []},
            "calendar": calendar_info(day)}
    r = Renderer(CFG, data)
    img = r.render()
    img.save(OUT / fname, format="PNG", optimize=True)
    print(f"C1 {fname}: slack={r.slack} notes={r.notes}")
    return r.slack


# ---- 信息终端：ARC 工具出图 -------------------------------------------------

def term_shots():
    data = worst_bw()
    data["wx"]["fc"] = [("昨天", "cloud", "阴", "28°", "23°")] + data["wx"]["fc"][:3]
    out = []
    for fname, d in (("袭-甲.png", data),
                     ("袭-甲-无预警.png", dict(data, wx=dict(
                         data["wx"], warn=[])))):
        sh, extra = arc_render("甲 · 信息终端", lay_terminal, d)
        if extra < 40:
            raise SystemExit(f"{fname}: 余量 {extra}px 低于安全线 40")
        sh.img.save(OUT / fname, format="PNG", optimize=True)
        print(f"信息终端 {fname}: 余量 {extra}px")
        out.append(extra)
    return out


def shot(src, cap, rect=BAT):
    return f"""
      <figure>
        <div class="shot"><img src="{src}" alt="{cap}">
          <span class="bat" style="left:{rect[0] / W * 100}%;top:{rect[1] / H * 100}%;
                width:{(rect[2] - rect[0]) / W * 100}%;height:{(rect[3] - rect[1]) / H * 100}%"></span>
        </div>
        <figcaption>{cap}</figcaption>
      </figure>"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    s_worst = c1_shot("保留-C1-最坏.png", date(2026, 2, 14), True)
    s_calm = c1_shot("保留-C1-真实.png", date(2026, 9, 25), False)
    t_worst, t_calm = term_shots()

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>已确定的产物 · 汇总</title>
<style>
 body{{margin:0;background:#14161a;color:#d8d5cd;
      font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
 header{{padding:26px 30px 18px;border-bottom:3px solid #3a3d42;background:#1b1e22}}
 h1{{margin:0 0 8px;font-size:22px;color:#f0ede4;letter-spacing:.04em}}
 header p{{margin:0;color:#a8a49a;font-size:13.5px;max-width:1000px}}
 main{{padding:26px 30px 90px;max-width:1560px}}
 section{{margin:0 0 34px;padding:18px 20px 20px;background:#1a1d21;
          border:1px solid #33383f}}
 h2{{margin:0 0 4px;font-size:19px;color:#f0ede4}}
 .st{{display:inline-block;font-size:11.5px;letter-spacing:.1em;padding:3px 9px;
      border:1px solid #4a4f57;color:#b3afa5;margin:0 0 10px}}
 .st.live{{border-color:#7aa06b;color:#a9cf97}}
 .st.new{{border-color:#8a93a5;color:#c3cbdb}}
 .st.keep{{border-color:#b09a63;color:#d6c08a}}
 .note{{margin:0 0 14px;color:#b3afa5;font-size:13px;max-width:1040px}}
 .row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:16px}}
 figure{{margin:0}}
 .shot{{position:relative;line-height:0}}
 img{{width:100%;height:auto;border:1px solid #33383f;background:#fff}}
 .bat{{position:absolute;border:2px dashed #e5534b;box-sizing:border-box}}
 figcaption{{margin-top:7px;font-size:12.5px;color:#9aa4b2;line-height:1.6}}
 ul{{margin:8px 0 0;padding-left:20px;color:#b3afa5;font-size:13px}}
 li{{margin:3px 0}}
 a{{color:#c3cbdb}}
</style></head><body>
<header><h1>已确定的产物 · 汇总（2026-09-26）</h1>
<p>这一页只放**你已经点头**的东西，全部由 <code>dashboard/tools/design_kept.py</code>
现跑现出（图标一改就能整页重出）。三套共用同一个电量精灵图位（红虚线），
换版面不用重拷 <code>clock/</code>。1072×1448、五档灰 0/70/130/195/255、正文 ≥26px。</p></header>
<main>

<section>
  <h2>1 · C1「带」</h2>
  <span class="st live">已上生产 · style.layout=c1</span>
  <p class="note">四条横带：日期带 / 大气带 / 预报带 / 数据带。图标是「柔雾 · 灰实心」那套，
  现在雨系按强度数滴数：毛毛雨与小雨 1 滴、中雨 2 滴、大雨与阵雨 3 滴、暴雨 4 滴（不靠把滴画大）。
  内容挤的时候走截断梯队（调休 → 倒计时 → 第二条预警 → 数据格 → 预报天气词 → 第三个指数），
  砍了什么写在页脚。</p>
  <div class="row">
    {shot("保留-C1-最坏.png", f"最坏情况：长节日+倒计时+调休 / 2 条预警 / 4 天预报（小雨→暴雨四档）/ 3 只指数。余量 {s_worst}px")}
    {shot("保留-C1-真实.png", f"平时无预警态：同一套版面，内容少时间距自动摊开，整页仍顶到页脚。余量 {s_calm}px")}
  </div>
</section>

<section>
  <h2>2 · 信息终端（ARC Raiders 译法）</h2>
  <span class="st new">本轮定稿 · 待移植成 style.layout</span>
  <p class="note">危险斜纹 + 切角面板 + DIN/Impact 工业字 + 分段仪表 + 涨跌条；
  图标用<b>线稿轮廓</b>那一套（云是四瓣并集轮廓、日是圈+辐条、雨是 1/2/3/4 根短斜条）。
  预报叫「近日预报」：昨天灰底、今天反相、明/后天白底，大后天不要了。
  涨跌不写字例：灰条+▼=跌、黑条+▲=涨，条长=幅度（满长 ≈ 3%）。</p>
  <div class="row">
    {shot("袭-甲.png", f"最坏情况：2 条预警 / 昨天+今天+明后天 / 6 个数据格全开。余量 {t_worst}px")}
    {shot("袭-甲-无预警.png", f"无预警态：预警盒收成一行灰字 NO ACTIVE THREAT，省下的空间摊进段距，其余块位置不变。余量 {t_calm}px")}
  </div>
</section>

<section>
  <h2>3 · 天气图标（决定记录）</h2>
  <span class="st live">C1 用柔雾实心 · 信息终端用线稿</span>
  <p class="note">两套各留各的语言，但守同两条规矩：滴数=强度；云体中心对所有天气固定，
  一排画出来天然齐平。非纸底只能用纯黑或纯白 —— 130 灰底上 70 和 195 会几乎消失，
  所以昨天格用黑、今天格用白。审查页：<a href="图标.html">图标.html</a>
  （灰阶 / 尺寸 / 云体对齐 / 灰底 / 线稿五张）。</p>
  <div class="row">
    {shot("图标-线稿.png", "选定：线稿一套的 14 种天气 × 纸/黑/130 灰三种底", rect=(0, 0, W, H))}
    {shot("图标-灰底.png", "证据：C1 图标在 130 灰底与 70 深灰底上的可读性矩阵", rect=(0, 0, W, H))}
  </div>
</section>

<section>
  <h2>4 · 还没办的</h2>
  <ul>
    <li><b>云端重发布</b>：C1 的改动（图标滴数、反相底色修正）要生效得由你在 WorkBuddy 桌面端点一次发布 —— 我这边碰不到。</li>
    <li><b>信息终端要不要移植</b>：现在只活在 <code>dashboard/tools/design_arc.py</code> 里，生产 <code>render.py</code> 没这个 layout。你说要我再动。</li>
    <li><b>未提交</b>：本轮改过的 <code>render.py</code>、<code>sources.py</code>、<code>lunar.py</code>、<code>config.yaml</code> 和几个设计工具都还没进 git。</li>
    <li><b>被否掉的已经归档</b>：大日期杂志封面款、赛博朋克一轮、复古报纸一轮、ARC 的落选图标（冲压实心 / 点阵）、
    以及一次性出稿脚本，全部移到 <code>.workbuddy/_archive/2026-09-26/</code>（没删，要捞随时能捞）。
    <code>_directions/</code> 现在只剩这页用到的 11 个文件。</li>
  </ul>
</section>

</main></body></html>
"""
    (OUT / "确定.html").write_text(html, encoding="utf-8")
    print(f"ok -> {OUT / '确定.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
