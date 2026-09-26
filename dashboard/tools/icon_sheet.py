#!/usr/bin/env python3
"""天气图标矩阵（2026-09-26）：只出审查稿，不碰生产。

把 render.py 里那套「柔雾 · 灰实心」图标按三个维度铺开看有没有优化空间：
  1 灰阶/反相 —— 11 种天气 × 6 种墨色（纸底 0/70/130，黑底 255/195/130）@96px
  2 尺寸 —— 4 种代表天气 × 120/96/72/48/32px，另加黑底白 48px 一列
  3 预报行对齐 —— align_cloud 关/开 两排对照，带云体中心参考线

用法：python dashboard/tools/icon_sheet.py
产物：.workbuddy/_directions/图标-*.png + 图标.html
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

from design_directions import OUT, W, H, Sheet                # noqa: E402
from design_arc import FontsArc                              # noqa: E402

INK, SOFT, GRAY, LIGHT, PAPER = 0, 70, 130, 195, 255

KINDS = ["sun", "sun_cloud", "moon", "moon_cloud", "cloud", "drizzle",
         "rain_light", "rain", "shower", "rain_heavy", "rain_storm", "snow",
         "thunder", "fog"]
CN = {"sun": "晴", "sun_cloud": "多云", "moon": "月", "moon_cloud": "夜多云",
      "cloud": "阴", "drizzle": "毛毛雨", "rain_light": "小雨 1 滴",
      "rain": "中雨 2 滴", "shower": "阵雨 3 滴", "rain_heavy": "大雨 3 滴",
      "rain_storm": "暴雨 4 滴", "snow": "雪", "thunder": "雷", "fog": "雾"}
FC_ROW = ["sun_cloud", "rain_light", "rain_storm", "cloud"]


def head(sh, y, title, note):
    sh.ink(40, y, y + 44, title, sh.f.sans(34, True), INK)
    sh.ink(40, y + 48, y + 78, note, sh.f.sans(24), GRAY)
    return y + 124


def sheet_modes() -> Sheet:
    sh = Sheet(FontsArc())
    y = head(sh, 36, "图标 × 墨色矩阵",
             "左半纸底：黑 0 / 深灰 70 / 灰 130；右半黑底：白 255 / 浅 195 / 灰 130。"
             "雨系图标现在按强度计滴数：毛毛雨/小雨 1 滴、中雨 2 滴、大雨与阵雨 3 滴、"
             "暴雨数到第四滴。灰底黑字那列就是「昨天」格用到的组合。")
    cols = [(INK, PAPER), (SOFT, PAPER), (GRAY, PAPER),
            (PAPER, INK), (LIGHT, INK), (GRAY, INK)]
    lab = ["0/255", "70/255", "130/255", "255/0", "195/0", "130/0"]
    x0 = 210
    cw = (W - 40 - x0) // 6
    pitch = (H - y - 40) // len(KINDS)
    size = min(96, pitch - 16)
    for j, (_, bg) in enumerate(cols):
        if bg == INK:
            sh.d.rectangle([x0 + j * cw, y, x0 + (j + 1) * cw,
                            y + len(KINDS) * pitch], fill=INK)
        sh.ink(x0 + j * cw + cw // 2, y - 34, y - 6, lab[j],
               sh.f.mono(20), GRAY if bg == PAPER else LIGHT, anchor="mt")
    for i, k in enumerate(KINDS):
        cy = y + i * pitch + pitch // 2
        sh.ink(40, cy - 26, cy + 6, k, sh.f.mono(22, True), INK)
        sh.ink(40, cy + 6, cy + 34, CN[k], sh.f.sans(20), GRAY)
        for j, (fg, bg) in enumerate(cols):
            sh.icons.icon(x0 + j * cw + cw // 2, cy, size, k, fg, bg=bg)
        sh.d.rectangle([40, y + (i + 1) * pitch - 1, W - 40,
                        y + (i + 1) * pitch], fill=LIGHT)
    return sh


def sheet_sizes() -> Sheet:
    sh = Sheet(FontsArc())
    y = head(sh, 36, "图标 × 尺寸",
             "120 / 96 / 72 / 48 / 32px 纸底黑，最后一列黑底白 48px。重点看雨系：滴数"
             "要在 48px 还数得清（1/2/3 滴分得开），32px 那列是故意画的下限 —— 生产里"
             "最小只用到 40px 以上。")
    sizes = [120, 96, 72, 48, 32]
    rows = ["sun_cloud", "rain_light", "rain", "rain_heavy", "rain_storm",
            "thunder"]
    x0 = 220
    cw = (W - 40 - x0 - 160) // 5
    for j, s in enumerate(sizes):
        sh.ink(x0 + j * cw + cw // 2, y - 34, y - 6, f"{s}px",
               sh.f.mono(22), GRAY, anchor="mt")
    sh.ink(W - 40 - 80, y - 34, y - 6, "48 on ink", sh.f.mono(20), GRAY,
           anchor="mt")
    sh.d.rectangle([W - 40 - 160, y, W - 40, y + len(rows) * 150], fill=INK)
    for i, k in enumerate(rows):
        cy = y + i * 150 + 75
        sh.ink(40, cy - 30, cy + 30, k, sh.f.mono(26, True), INK)
        sh.ink(40, cy + 30, cy + 60, CN[k], sh.f.sans(22), GRAY)
        for j, s in enumerate(sizes):
            sh.icons.icon(x0 + j * cw + cw // 2, cy, s, k, INK)
        sh.icons.icon(W - 40 - 80, cy, 48, k, PAPER, bg=INK)
        sh.d.rectangle([40, y + (i + 1) * 150 - 1, W - 40, y + (i + 1) * 150],
                       fill=LIGHT)
    return sh


def sheet_align() -> Sheet:
    sh = Sheet(FontsArc())
    y = head(sh, 36, "预报行对齐：align_cloud 关 / 开",
             "云类图标云体中心本来不一高（雨要让位给雨滴、多云要让位给太阳）。"
             "灰线=云体中心参考线：上排不补，一高一低；下排 align_cloud=True 补齐。")
    for r, align in ((0, False), (1, True)):
        by = y + r * 260
        sh.ink(40, by + 40, by + 80, "OFF" if not align else "ON",
               sh.f.mono(30, True), INK if align else GRAY)
        guide = by + 150
        sh.d.rectangle([160, guide, W - 40, guide + 1], fill=LIGHT)
        for j, k in enumerate(FC_ROW):
            cx = 260 + j * 220
            sh.icons.icon(cx, guide, 96, k, INK, align_cloud=align)
            sh.ink(cx, by + 210, by + 244, k, sh.f.mono(22), GRAY, anchor="mt")
    return sh


GRAY_COLS = [(INK, GRAY), (SOFT, GRAY), (LIGHT, GRAY), (PAPER, GRAY),
             (INK, SOFT), (PAPER, SOFT)]
GRAY_LAB = ["0/130", "70/130", "195/130", "255/130", "0/70", "255/70"]


def _matrix(sh, y, painter, cols, lab):
    """通用矩阵：行=kind，列=(前景, 底色)。底色整列铺满再画图标。"""
    x0 = 210
    cw = (W - 40 - x0) // len(cols)
    pitch = (H - y - 40) // len(KINDS)
    size = min(96, pitch - 16)
    for j, (_, bg) in enumerate(cols):
        if bg != PAPER:
            sh.d.rectangle([x0 + j * cw, y, x0 + (j + 1) * cw,
                            y + len(KINDS) * pitch], fill=bg)
        sh.ink(x0 + j * cw + cw // 2, y - 34, y - 6, lab[j], sh.f.mono(20),
               GRAY if bg == PAPER else (GRAY if bg == SOFT else LIGHT),
               anchor="mt")
    for i, k in enumerate(KINDS):
        cy = y + i * pitch + pitch // 2
        sh.ink(40, cy - 26, cy + 6, k, sh.f.mono(22, True), INK)
        sh.ink(40, cy + 6, cy + 34, CN[k], sh.f.sans(20), GRAY)
        for j, (fg, bg) in enumerate(cols):
            painter(sh, x0 + j * cw + cw // 2, cy, size, k, fg, bg)
        sh.d.rectangle([40, y + (i + 1) * pitch - 1, W - 40,
                        y + (i + 1) * pitch], fill=LIGHT if cols[0][1] == PAPER
                       else PAPER)
    return sh


def sheet_gray() -> Sheet:
    """灰底矩阵：昨天格用的是 130 灰，反相格用的是黑 —— 图标在这两块上必须同样读得出。"""
    sh = Sheet(FontsArc())
    y = head(sh, 36, "C1 图标 × 灰底 / 深灰底",
             "版面里已经有两种非纸底：昨天格 130 灰、今天格黑。这里把 130 灰和 70 深灰"
             "各铺四种墨色（列名 墨色/底色）。结论：130 灰底上只有纯黑和纯白读得出，"
             "70 和 195 在 130 底上几乎消失 —— 所以昨天格用黑，今天格用白。")
    return _matrix(sh, y, lambda s, cx, cy, sz, k, fg, bg:
                   s.icons.icon(cx, cy, sz, k, fg, bg=bg), GRAY_COLS, GRAY_LAB)


LINE_COLS = [(INK, PAPER), (SOFT, PAPER), (PAPER, INK), (INK, GRAY), (PAPER, GRAY)]
LINE_LAB = ["0/255", "70/255", "255/0", "0/130", "255/130"]


def sheet_line() -> Sheet:
    """ARC 信息终端选用的那套线稿图标，同一批 kind 换 painter 画。"""
    from design_arc import line_icon
    sh = Sheet(FontsArc())
    y = head(sh, 36, "线稿图标（ARC 信息终端选用）× 墨色矩阵",
             "云是四瓣并集轮廓、日是圈+辐条、雨是 1/2/3/4 根短斜条（滴数=强度，暴雨数到第四滴）、"
             "雪是六向星、雷是折线。线宽 = size×0.055，40px 以下开始并笔。"
             "落选的另两套（冲压实心 / 点阵）在 .workbuddy/_archive/2026-09-26/。")
    return _matrix(sh, y, lambda s, cx, cy, sz, k, fg, bg:
                   line_icon(s, cx, cy, sz, k, fg, bg=bg), LINE_COLS, LINE_LAB)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    shots = [("图标-灰阶.png", sheet_modes(),
              "14 种天气 × 6 种墨色。看反相和浅灰那两列有没有糊掉的。"),
             ("图标-尺寸.png", sheet_sizes(),
              "尺寸下限审查：32px 起雨滴/雷开始并笔，生产里最小用到 40px（预报小行）。"),
             ("图标-对齐.png", sheet_align(),
              "align_cloud 关/开对照：灰线是云体中心，开的那排四朵云齐平。"),
             ("图标-灰底.png", sheet_gray(),
              "版面已经有两种非纸底：昨天格 130 灰、今天格黑。灰底上可用的是纯黑和纯白，"
              "130 前景在 130 底上直接消失 —— 所以昨天格的图标一律用黑。"),
             ("图标-线稿.png", sheet_line(),
              "ARC 信息终端选用的线稿一套：14 种天气 × 纸/黑/130 灰三种底。"
              "落选的冲压实心和点阵已归档。")]
    cards = []
    for name, sh, note in shots:
        png = OUT / name
        sh.img.save(png, format="PNG", optimize=True)
        print(f"{name} -> {png.name}")
        cards.append(f"""
    <figure>
      <h2>{name[:-4]}</h2>
      <div class="shot"><img src="{name}" alt="{name}"></div>
      <p class="d">{note}</p>
    </figure>""")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>天气图标审查 · 五张</title>
<style>
 body{{margin:0;background:#14161a;color:#d8d5cd;
      font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
 header{{padding:24px 28px 16px;border-bottom:3px solid #3a3d42;background:#1b1e22}}
 h1{{margin:0 0 6px;font-size:21px;color:#f0ede4}}
 header p{{margin:0;color:#a8a49a;font-size:13.5px;max-width:900px}}
 main{{padding:24px 28px 80px;display:grid;grid-template-columns:repeat(auto-fit,
       minmax(420px,1fr));gap:22px;max-width:1700px}}
 figure{{margin:0;background:#1b1e22;border:1px solid #3a3d42;padding:14px 16px 16px}}
 h2{{margin:0 0 8px;font-size:18px;color:#f0ede4}}
 .d{{margin:10px 0 0;color:#b3afa5;font-size:12.5px}}
 .shot{{line-height:0}}
 img{{width:100%;height:auto;border:1px solid #3a3d42}}
</style></head><body>
<header><h1>天气图标审查 · 灰阶 / 尺寸 / 对齐 / 灰底 / 线稿</h1>
<p>前三张是生产里那套「柔雾 · 灰实心」图标（aiinfo.render.Renderer.icon）原样调用，只换墨色和尺寸。
雨系现在按强度计滴数：毛毛雨与小雨 1 滴、中雨 2 滴、大雨与阵雨 3 滴、暴雨 4 滴（不靠把滴画大）。
第四张是灰底矩阵 —— 版面里已经有 130 灰的「昨天」格和黑色的「今天」格，图标在非纸底上必须照样读得出。
第五张是 ARC 信息终端选用的线稿一套；落选的冲压实心与点阵已归档到 <code>.workbuddy/_archive/2026-09-26/</code>。</p></header>
<main>{''.join(cards)}</main></body></html>
"""
    (OUT / "图标.html").write_text(html, encoding="utf-8")
    print(f"ok -> {OUT / '图标.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
