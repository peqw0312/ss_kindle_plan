#!/usr/bin/env python3
"""UI 调参台 —— 你在浏览器里拖，它用真渲染器当场重画那张 1072×1448 的图。

为什么不是"画个示意图给你看"：
    前面几轮反复栽在同一个地方 —— 我按想象出一版预览，你说丑，我再猜。
    而且预览和成品是两套代码时，预览好看也不代表能落地。
    这里没有第二套代码：滑块改的是 render.py 真正读的那些值，模块框是渲染器
    报回来的真实矩形，每次改动都调 Renderer().render() 重新出一张真图。
    所以**你在页面上看到的 = Kindle 上会显示的**，逐像素一致。

用法（仓库根目录执行）：
    .venv\\Scripts\\python.exe dashboard\\tools\\ui_studio.py
    然后浏览器打开它印出来的地址（默认 http://127.0.0.1:8100）

能改的三件事：
    · 21 处字号          —— 滑块，松手自动重画
    · 模块上下顺序        —— 在预览图上按住那块拖
    · 开关 / 时钟 / 天数  —— 点一下

改完点「导出预设给我」，会写出 .workbuddy/_studio/preset.json。
把那个文件给我，我把它折进 render.py 的 STYLE_PRESETS 并同步到 config.yaml。

页面顶部实时显示两件事，它们是这条路的刹车：
    · 纵向余量 —— 低于 40px 会标红（和 layout_check 同一根安全线）
    · 渲染器自检告警 —— 版面挤了、字被裁了，当场就能看到

为什么数据要缓存：
    出图本身只要零点几秒，抓天气和行情要几秒。要"实时预览"就不能每次都去
    抓一遍 —— 调字号的时候那个 30° 不会变。所以数据抓一次留着用，顶部标了
    它是多久之前抓的，想换新的点「换最新数据」。
"""

from __future__ import annotations

import base64
import io
import json
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))

from PIL import Image, ImageDraw                               # noqa: E402
from aiinfo import render as R                                   # noqa: E402
from aiinfo.config import Config                                 # noqa: E402
from aiinfo.lunar import calendar_info                           # noqa: E402

OUT_DIR = ROOT / ".workbuddy" / "_studio"
BASE_CONFIG = ROOT / "dashboard" / "config.yaml"
SAFETY_PX = 40                     # 和 layout_check 同一根线

#: 页面上暴露哪些字号、怎么分组。名字必须是 render.py 里真实存在的常量。
GROUPS = [
    ("日历条", [
        ("FS_CAL_DAY", "大字日期"),
        ("FS_LUNAR", "农历"),
        ("FS_CAL_MONTH", "月份条"),
        ("FS_CAL_WEEK", "星期"),
        ("FS_CAL_LINE", "干支 / 地点行"),
        ("FS_BADGE", "节气徽章"),
        ("FS_YIJI", "宜忌文字"),
        ("FS_YIJI_MARK", "宜 / 忌 方框"),
        ("FS_CLOCK", "时钟文字（画进图里时）"),
    ]),
    ("天气", [
        ("FS_TEMP", "温度"),
        ("FS_DESC", "天气描述"),
        ("FS_VALUE", "数据格的值"),
        ("FS_LABEL", "数据格的标签"),
    ]),
    ("行情 / 速览", [
        ("FS_QUOTE_PRICE", "指数价格"),
        ("FS_QUOTE_PCT", "涨跌幅"),
        ("FS_QUOTE_NAME", "指数名称"),
        ("FS_ITEM_TITLE", "速览标题"),
        ("FS_ITEM_SUM", "速览摘要"),
        ("FS_ITEM_CHIP", "速览栏目角标"),
    ]),
    ("其它", [
        ("FS_SECTION", "区块标题"),
        ("FS_FOOT", "页脚"),
    ]),
]

#: 开关类：直接写进 cfg 的点号键
TOGGLES = [
    ("calendar.show_yiji", "显示宜忌 / 冲煞"),
    ("calendar.show_ganzhi", "显示干支生肖"),
    ("weather.show_air", "抓取空气质量"),
    ("quotes.enabled", "显示行情"),
    ("digest.enabled", "显示今日速览"),
]

ALL_KEYS = [k for _, items in GROUPS for k, _ in items]
BLOCK_LABELS = dict(R.Renderer.BLOCK_LABELS)

#: 每个字号控件 → (样张文字, 属于哪个模块, 是不是粗体)。
#:
#: 为什么要有这张表而不是把名字写清楚点：「干支 / 地点行」这种叫法对没读过
#: 代码的人是猜谜 —— 他得在二十几个滑块和一整张图之间自己找对应，找不到就
#: 干脆不调。这里直接把**真正受影响的那行字**按当前字号画出来当标签，
#: 拖到哪一格，那一格本身就在变，不用去图上找。
#: 样张取的是屏幕上真实会出现的内容，不是占位符。
#: 样张刻意取短：标签那一列不宽，一行太长会被按比例缩回去，反而看不出字号大小。
SAMPLES: dict[str, tuple[str, str | None, bool]] = {
    "FS_CAL_DAY":     ("19", "calendar", True),
    "FS_LUNAR":       ("八月初九", "calendar", True),
    "FS_CAL_MONTH":   ("2026年9月", "calendar", True),
    "FS_CAL_WEEK":    ("周六", "calendar", False),
    "FS_CAL_LINE":    ("丙午年 属马", "calendar", False),
    "FS_BADGE":       ("白露 第13天", "calendar", True),
    "FS_YIJI":        ("筑堤 修补", "calendar", False),
    "FS_YIJI_MARK":   ("宜", "calendar", True),
    "FS_CLOCK":       ("下午 4:34", "calendar", True),
    "FS_TEMP":        ("30°", "weather", True),
    "FS_DESC":        ("晴间多云", "weather", False),
    "FS_VALUE":       ("57%", "weather", True),
    "FS_LABEL":       ("湿度", "weather", False),
    "FS_QUOTE_PRICE": ("29,644", "quotes", True),
    "FS_QUOTE_PCT":   ("+0.67%", "quotes", True),
    "FS_QUOTE_NAME":  ("纳指100", "quotes", False),
    "FS_ITEM_TITLE":  ("英伟达发布新芯片", "digest", True),
    "FS_ITEM_SUM":    ("系列收官作首次双主角", "digest", False),
    "FS_ITEM_CHIP":   ("3A游戏", "digest", True),
    "FS_SECTION":     ("今日速览", "digest", True),
    "FS_FOOT":        ("更新 09-19", None, False),
}

#: 这几个开关是在**抓数据那一步**读的（见 generate.collect / sources），
#: 数据一缓存，点了当场不会有任何变化 —— 必须顺手重抓，否则就是"配置没生效"
#: 那一类静默故障的翻版。
DATA_KEYS = {"weather.show_air", "digest.enabled", "quotes.enabled"}

#: 加了滑块忘了加样张 = 那一格又变回"不知道调哪个"。当场停，别让它上线。
_missing = [k for k in ALL_KEYS if k not in SAMPLES]
if _missing:
    raise SystemExit("SAMPLES 里缺这些控件的样张，页面上它们会没有标注：" + "、".join(_missing))

_data_lock = threading.Lock()
_DATA: dict = {}


def board_data(cfg) -> tuple[dict, float]:
    """抓一次数据就留着用。见文件头「为什么数据要缓存」。

    那把锁是必需的：这服务是多线程的，自动预览会在几秒里发来好几个请求，
    不加锁就会同时抓两遍天气，还会让一个请求读到另一个写到一半的 data。
    """
    with _data_lock:
        if _DATA.get("data"):
            return _DATA["data"], _DATA["at"]
        import generate
        data = generate.collect(cfg)
        data["generated_at"] = datetime.now()
        data["calendar"] = calendar_info(data["generated_at"])
        _DATA.update(data=data, at=time.time())
        return data, _DATA["at"]


def forget_data() -> None:
    with _data_lock:
        _DATA.clear()


def label_images(r: "R.Renderer") -> dict:
    """给每个字号控件出一张样张：那几个字，就按当前字号、当前字体画出来。

    借渲染器自己的 `text()` 画，所以描边、粗体是不是真字重这些细节和成品图一致；
    画完把画布换回去，不污染真正要返回的那张图。
    """
    keep = (r.img, r.d)
    out: dict[str, str] = {}
    try:
        for key, (text, _blk, bold) in SAMPLES.items():
            if not hasattr(r, key):
                continue
            font = r.f(getattr(r, key), bold)
            pad = r.px(2) + max(1, r._stroke(bold))
            w = max(4, int(r.d.textlength(text, font=font)) + pad * 2)
            h = max(4, r.lh(font) + pad)
            canvas = Image.new("L", (w, h), 255)
            r.img, r.d = canvas, ImageDraw.Draw(canvas)
            r.text((pad, 0), text, font, R.INK, anchor="lt", strong=bold)
            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            out[key] = base64.b64encode(buf.getvalue()).decode()
    finally:
        r.img, r.d = keep
    return out


def render(over: dict) -> tuple[bytes, dict]:
    """按页面上的参数出一张真图，连同余量、模块矩形和告警一起返回。"""
    cfg = Config.load(str(BASE_CONFIG))
    preset = str(over.get("preset") or "")
    if preset in R.STYLE_PRESETS:
        cfg.set("style.preset", preset)

    # clock.mode / weather.days 也来自页面，而数据抓什么由它们决定，
    # 所以必须在缓存之前定下来。
    mode = str(over.get("clock.mode") or cfg.get("clock.mode"))
    cfg.set("clock.mode", mode)
    cfg.set("weather.days", int(over.get("weather.days", cfg.get("weather.days", 4))))
    for key, _label in TOGGLES:
        if key in over:
            cfg.set(key, bool(over[key]))

    data, data_at = board_data(cfg)

    # 字号走 Renderer 的**实例属性**，不改模块全局：这服务是多线程的，
    # 自动预览会并发，改全局就是两个请求互相踩版面（表现为"滑块偶尔没反应"）。
    r = R.Renderer(cfg, data)
    for key, value in over.items():
        if key in ALL_KEYS and value is not None:
            setattr(r, key, int(value))

    # 顺序要在预设之后覆盖：预设自己也带 layout.order（「天气优先」那版就是），
    # 但人在页面上拖过的那一下，优先级高于任何预设。
    order = over.get("layout.order")
    if isinstance(order, list) and order:
        r.cfg.set("layout.order", [k for k in order if k in R.Renderer.BLOCKS])

    img = r.render().convert("L")
    samples = label_images(r)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    png = buf.getvalue()
    return png, {
        "slack": r.slack,                       # 渲染器自己算的，别在这里再算一遍
        "safe": r.slack >= r.px(SAFETY_PX),
        "samples": samples,
        "notes": list(r.notes),
        "clock_box": list(r.clock_box) if r.clock_box else None,
        "blocks": {k: list(v) for k, v in r.block_boxes.items()},
        "order": r.layout_order(),
        "labels": BLOCK_LABELS,
        "img_w": r.w, "img_h": r.h,
        "preset_used": r.preset,
        "data_age": int(time.time() - data_at),
        "bytes": len(png),
    }


HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>信息屏 · UI 调参台</title>
<style>
 :root{color-scheme:light}
 body{margin:0;background:#eceef0;color:#1c1f23;font:14px/1.65
      -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
 header{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid #d6d9dd;
        padding:10px 18px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
 header h1{margin:0;font-size:16px}
 header .hint{color:#5b6470;font-size:12.5px}
 header .sp{flex:1}
 main{display:grid;grid-template-columns:minmax(300px,380px) 1fr;gap:18px;padding:18px}
 .panel{background:#fff;border:1px solid #d6d9dd;border-radius:10px;padding:14px 16px;
        max-height:calc(100vh - 90px);overflow:auto}
 fieldset{border:1px solid #e3e5e8;border-radius:8px;margin:0 0 14px;padding:10px 12px 12px}
 legend{font-size:12.5px;font-weight:700;color:#5b6470;padding:0 6px}
 .row{display:grid;grid-template-columns:1fr 1.15fr 40px;gap:8px;align-items:center;
      margin:7px 0;font-size:12.5px}
 .row .lab{min-width:0;display:flex;flex-direction:column;gap:2px}
 .row .lab b{font-size:11px;color:#7c8794;font-weight:700;white-space:nowrap;
             overflow:hidden;text-overflow:ellipsis}
 .row .lab img{max-height:46px;max-width:100%}
 .row.dim{opacity:.38}
 .row.dim .lab b::after{content:' · 那块现在没显示'}
 .row label{color:#3c434b}
 .row output{text-align:right;font-variant-numeric:tabular-nums;color:#5b6470}
 input[type=range]{width:100%;accent-color:#1c1f23}
 select,button{font:inherit;padding:5px 10px;border:1px solid #cfd3d8;border-radius:6px;
        background:#fff;cursor:pointer}
 button.go{background:#1c1f23;color:#fff;border-color:#1c1f23;font-weight:600}
 .toggles label{display:block;font-size:12.5px;margin:4px 0;color:#3c434b}
 .view{background:#fff;border:1px solid #d6d9dd;border-radius:10px;padding:14px;
       display:flex;flex-direction:column;gap:10px;align-items:center}
 #stage{position:relative;line-height:0;width:100%;max-width:600px}
 /* 宽度必须是确定的：.view 是 align-items:center 的 flex 列，里面的东西按内容
    定宽，图没加载完时内容宽度就是 0 —— 模块框会全被算成 0×0，看着像"拖不动"。 */
 #shot{width:100%;height:auto;border:1px solid #cfd3d8;
       box-shadow:0 1px 4px rgba(0,0,0,.07)}
 .blk{position:absolute;border:1px dashed transparent;border-radius:6px;
      cursor:grab;touch-action:none;transition:border-color .12s,background .12s}
 .blk:hover{border-color:#7c8794;background:rgba(124,135,148,.07)}
 .blk.hot{border:2px solid #b42318;background:rgba(180,35,24,.09)}
 .blk.drag{border-color:#1c1f23;background:rgba(28,31,35,.10);cursor:grabbing;z-index:3}
 .blk span{position:absolute;left:0;top:0;background:#1c1f23;color:#fff;font-size:11px;
           line-height:1;padding:4px 7px;border-radius:0 0 6px 0;opacity:0;
           transition:opacity .12s;white-space:nowrap}
 .blk:hover span,.blk.drag span,.blk.hot span{opacity:.92}
 .blk em{position:absolute;right:3px;top:3px;font-size:10px;color:#1c1f23;
         background:#fff;border:1px solid #cfd3d8;border-radius:4px;padding:1px 5px;
         font-style:normal;opacity:0;transition:opacity .12s}
 .blk:hover em{opacity:.9}
 #order{font-size:12.5px;color:#3c434b;width:100%}
 #order b{background:#1c1f23;color:#fff;border-radius:4px;padding:1px 7px;margin:0 2px;
          font-weight:600;display:inline-block}
 #order b.off{background:#c3c8ce;font-weight:400;padding:1px 5px}
 #status{font-size:12.5px;width:100%;display:flex;gap:12px;flex-wrap:wrap;align-items:center}
 .ok{color:#1a7f37;font-weight:600} .bad{color:#b42318;font-weight:600}
 .warn{background:#fff7ed;border:1px solid #f4c98a;border-radius:8px;padding:8px 11px;
       font-size:12.5px;color:#8b3b12;width:100%}
 #out{width:100%;font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;
      background:#f6f7f8;border:1px solid #d6d9dd;border-radius:8px;padding:10px;
      max-height:200px;overflow:auto;display:none;white-space:pre}
</style></head><body>
<header>
  <h1>信息屏 · UI 调参台</h1>
  <span class="hint">滑块左边那行字<b>就是它管的东西</b>；右边那张图上的虚线框<b>按住能拖</b>换位置</span>
  <span class="sp"></span>
  <span class="hint">基准</span><select id="preset"></select>
  <button onclick="refreshData()">换最新数据</button>
  <button class="go" onclick="apply()">重画</button>
  <button onclick="exportPreset()">导出预设给我</button>
</header>
<main>
  <div class="panel" id="controls"></div>
  <div class="view">
    <div id="status">加载中…</div>
    <div id="order"></div>
    <div id="warn"></div>
    <div id="stage"><img id="shot" alt="预览"></div>
    <pre id="out"></pre>
  </div>
</main>
<script>
const GROUPS = __GROUPS__;
const TOGGLES = __TOGGLES__;
const LABEL = __LABELS__;
const SBLK = __SAMPLE_BLOCK__;
const DATA_KEYS = __DATA_KEYS__;
const PRESETS = __PRESETS__;
const DEFAULTS = __DEFAULTS__;
const state = Object.assign({}, DEFAULTS);
let meta = null, timer = 0, busy = false, again = false, drag = null;
const SMP = {};                       // 每个控件对应的样张 <img>，出图后换 src

/** 建元素。`text` 要单独走 textContent：Object.assign 只是往 JS 对象上挂了个
 *  叫 text 的属性，DOM 上什么都没有 —— 而 <option> 恰好有 .text 这个真实属性，
 *  所以下拉框一直是好的，滑块名 / 分组标题 / 模块标签却全是空白。
 *  这个坑让整页标注消失过一次，别改回 Object.assign。 */
function el(t,a){
  const e=document.createElement(t);
  if(a){ a=Object.assign({},a);
         if('text' in a){ e.textContent=a.text; delete a.text; }
         Object.assign(e,a); }
  return e;
}
function say(html){ document.getElementById('status').innerHTML=html; }
function schedule(){ clearTimeout(timer); timer = setTimeout(apply, 220); }

function build(){
  const box=document.getElementById('controls');
  const ps=document.getElementById('preset');
  PRESETS.forEach(p=>ps.append(el('option',{value:p,text:p})));
  ps.value = state['__preset'];
  ps.onchange=schedule;

  for(const [gname,items] of GROUPS){
    const fs=el('fieldset'); fs.append(el('legend',{text:gname}));
    for(const [key,label] of items){
      const row=el('div',{className:'row'});
      const lab=el('div',{className:'lab'});
      lab.append(el('b',{text:label}));
      const img=el('img'); img.alt=label; SMP[key]=img; lab.append(img);
      const inp=el('input',{type:'range',min:14,max:280,step:1});
      const out=el('output');
      inp.value = state[key] ?? 30; out.value = inp.value;
      inp.id=key;
      inp.oninput=()=>{out.value=inp.value; state[key]=+inp.value; schedule()};
      row.onmouseenter=()=>hot(SBLK[key]);
      row.onmouseleave=()=>hot(null);
      row.append(lab,inp,out); fs.append(row);
    }
    box.append(fs);
  }

  const fs=el('fieldset'); fs.append(el('legend',{text:'开关'}));
  fs.className='toggles';
  for(const [key,label] of TOGGLES){
    const lb=el('label'); const cb=el('input',{type:'checkbox'});
    cb.checked = !!state[key];
    cb.onchange=()=>{ state[key]=cb.checked; DATA_KEYS.includes(key)?refreshData():schedule(); };
    lb.append(cb,document.createTextNode(' '+label
      + (DATA_KEYS.includes(key) ? '（要重抓数据，会慢几秒）' : '')));
    fs.append(lb);
  }
  const cm=el('div',{className:'row'});
  cm.append(el('label',{text:'时钟'}));
  const sel=el('select');
  [['off','不要时间（最省电）'],['local','Kindle 每分钟贴（时间准）'],['image','画进图里（会过时）']]
    .forEach(([v,t])=>sel.append(el('option',{value:v,text:t})));
  sel.value=state['clock.mode'];
  sel.onchange=()=>{state['clock.mode']=sel.value; schedule()};
  const wd=el('select');
  [['4','含今天 4 天'],['3','含今天 3 天'],['2','含今天 2 天'],['1','只看今天']]
    .forEach(([v,t])=>wd.append(el('option',{value:v,text:t})));
  wd.value=state['weather.days'];
  wd.onchange=()=>{state['weather.days']=+wd.value; schedule()};
  cm.append(sel,el('span'));
  fs.append(cm,wd);
  box.append(fs);
}

/** 鼠标停在某个滑块上时，把图上归它管的那一块描红 —— 样张说明"是哪行字"，
 *  红框说明"那行字在屏幕哪里"，两个一起才不用猜。 */
function hot(key){
  document.querySelectorAll('.blk.hot').forEach(n=>n.classList.remove('hot'));
  if(!key) return;
  const n=document.querySelector('.blk[data-key="'+key+'"]');
  if(n) n.classList.add('hot');
}

function paintSamples(){
  for(const key in SMP){
    const img=SMP[key];
    if(meta.samples && meta.samples[key]) img.src='data:image/png;base64,'+meta.samples[key];
    const blk=SBLK[key];
    img.closest('.row').classList.toggle('dim', !!(blk && !meta.blocks[blk]));
  }
}

function payload(){
  return Object.assign({preset:document.getElementById('preset').value}, state);
}

async function apply(){
  clearTimeout(timer);
  if(busy){ again = true; return; }      // 连拉时不排队发请求，只补最后一次
  busy = true;
  say('出图中…');
  try{
    const t0=Date.now();
    const r=await fetch('/render',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify(payload())});
    const j=await r.json();
    if(!j.ok){
      say('<span class="bad">'+j.error+'</span>');
      document.getElementById('warn').innerHTML =
        '<div class="warn">'+String(j.trace||'').replace(/</g,'&lt;')+'</div>';
      return;
    }
    meta=j.meta;
    const shot=document.getElementById('shot');
    // 先按真实比例把版面撑出来再换图：src 刚赋值时图片还没参与布局，
    // 那一刻读 clientWidth 会是 0，模块框就全被算成零高零宽（拖不动的那种"没反应"）。
    shot.style.aspectRatio = meta.img_w + '/' + meta.img_h;
    shot.onload = () => { if (!drag) drawBoxes(); };   // 解码完成后再按实际尺寸画一遍
    shot.src='data:image/png;base64,'+j.png;
    const age=meta.data_age;
    say('纵向余量 <b class="'+(meta.safe?'ok':'bad')+'">'+meta.slack+'px</b>'
      +'（安全线 40px） · 图 '+(meta.bytes/1024).toFixed(0)+'KB · '
      +((Date.now()-t0)/1000).toFixed(1)+'s · 时钟区 '+(meta.clock_box||'—')
      +' · 数据 '+(age<60 ? age+' 秒前' : Math.round(age/60)+' 分钟前'));
    document.getElementById('warn').innerHTML =
      meta.notes.length ? '<div class="warn">'+meta.notes.join('<br>')+'</div>' : '';
    drawBoxes();
    paintSamples();
  }catch(e){ say('<span class="bad">请求失败：'+e+'</span>'); }
  finally{
    busy=false;
    if(again){ again=false; apply(); }
  }
}

/* ---------- 模块拖动 ---------- */

function drawBoxes(){
  const stage=document.getElementById('stage');
  stage.querySelectorAll('.blk').forEach(n=>n.remove());
  if(!meta) return;
  const shot=document.getElementById('shot');
  const sx=shot.clientWidth/meta.img_w, sy=shot.clientHeight/meta.img_h;
  for(const key of meta.order){
    const b=meta.blocks[key]; if(!b) continue;
    const d=el('div',{className:'blk'});
    d.style.left =(b[0]*sx)+'px';
    d.style.top  =(b[1]*sy)+'px';
    d.style.width =(b[2]-b[0])*sx+'px';
    d.style.height=(b[3]-b[1])*sy+'px';
    d.dataset.key=key;
    d.append(el('span',{text:(LABEL[key]||key)}));
    d.append(el('em',{text:'按住拖我'}));
    d.onpointerdown=grab;
    stage.append(d);
  }
  showOrder(meta.order);
}

function showOrder(order){
  // 图上没有框的那块（关掉了或这轮没数据）也要列出来：它还在顺序里，
  // 只是现在看不见 —— 藏起来会让人以为拖坏了。
  document.getElementById('order').innerHTML='模块顺序（从上到下）：'
    + order.map(k=>{
        const on = meta && meta.blocks[k];
        return '<b'+(on?'':' class="off"')+'>'+(LABEL[k]||k)+(on?'':' 未开')+'</b>';
      }).join(' › ');
}

function grab(e){
  if(!meta || drag) return;
  e.preventDefault();
  const box=e.currentTarget;
  drag={key:box.dataset.key, order:meta.order.slice()};
  box.classList.add('drag');
  box.setPointerCapture(e.pointerId);
  box.addEventListener('pointermove',onMove);
  box.addEventListener('pointerup',drop);
  box.addEventListener('pointercancel',drop);
}

function onMove(e){
  if(!drag) return;
  const rect=document.getElementById('shot').getBoundingClientRect();
  const py=(e.clientY-rect.top)*(meta.img_h/rect.height);      // 换算成图上的像素
  const others=drag.order.filter(k=>k!==drag.key);
  let idx=0;
  for(const k of others){ const b=meta.blocks[k]; if(b && py>(b[1]+b[3])/2) idx++; }
  const want=others.slice(0,idx).concat(drag.key, others.slice(idx));
  if(want.join()!==drag.order.join()){ drag.order=want; showOrder(want); }
}

function drop(e){
  const box=e.currentTarget;
  box.classList.remove('drag');
  box.removeEventListener('pointermove',onMove);
  box.removeEventListener('pointerup',drop);
  box.removeEventListener('pointercancel',drop);
  if(!drag) return;
  const changed = drag.order.join()!==meta.order.join();
  state['layout.order']=drag.order.slice();
  drag=null;
  if(changed) apply(); else drawBoxes();
}

window.addEventListener('resize',()=>{ if(meta && !drag) drawBoxes(); });

async function refreshData(){
  say('重新抓数据…（要几秒）');
  await fetch('/forget',{method:'POST',body:'{}'});
  apply();
}

function exportPreset(){
  const fs={};
  for(const [,items] of GROUPS) for(const [k] of items) if(k in state) fs[k]=state[k];
  const out={
    note:'把整个文件发给 Qoder，说"照这个改"即可',
    base_preset:document.getElementById('preset').value,
    layout_order:(meta?meta.order:state['layout.order'])||null,
    clock_mode:state['clock.mode'],
    weather_days:state['weather.days'],
    toggles:{},
    style_preset_from_studio:fs,
  };
  for(const [k] of TOGGLES) out.toggles[k]=!!state[k];
  const o=document.getElementById('out'); o.style.display='block';
  o.textContent=JSON.stringify(out,null,2);
  fetch('/save',{method:'POST',headers:{'content-type':'application/json'},
                 body:JSON.stringify(out)})
    .then(r=>r.json()).then(j=>{ o.textContent += '\\n\\n已写出：'
      +(j.path||('失败 '+j.error))+'\\n把这个文件发给 Qoder 即可。'; });
}
build(); apply();
</script></body></html>
"""


def page() -> bytes:
    cfg = Config.load(str(BASE_CONFIG))
    active = str(cfg.get("style.preset") or "经典")
    # 默认值 = 模块常量，再叠上当前生效预设里的那几项，页面一打开就是屏上的样子
    defaults: dict = {k: getattr(R, k) for k in ALL_KEYS if hasattr(R, k)}
    defaults.update({k: v for k, v in R.STYLE_PRESETS.get(active, {}).items()
                     if "." not in k})
    for key, _label in TOGGLES:
        defaults[key] = bool(cfg.get(key, True))
    defaults["clock.mode"] = str(cfg.get("clock.mode"))
    defaults["weather.days"] = int(cfg.get("weather.days", 4))
    defaults["layout.order"] = list(cfg.get("layout.order") or R.Renderer.BLOCKS)
    defaults["__preset"] = active
    return (HTML
            .replace("__GROUPS__", json.dumps(GROUPS, ensure_ascii=False))
            .replace("__TOGGLES__", json.dumps(TOGGLES, ensure_ascii=False))
            .replace("__LABELS__", json.dumps(BLOCK_LABELS, ensure_ascii=False))
            .replace("__SAMPLE_BLOCK__", json.dumps({k: v[1] for k, v in SAMPLES.items()}))
            .replace("__DATA_KEYS__", json.dumps(sorted(DATA_KEYS)))
            .replace("__PRESETS__", json.dumps(list(R.STYLE_PRESETS), ensure_ascii=False))
            .replace("__DEFAULTS__", json.dumps(defaults, ensure_ascii=False))
            ).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                    # noqa: N802
        if self.path.split("?")[0] in ("/", "/index.html"):
            self._send(200, page(), "text/html; charset=utf-8")
        else:
            self._send(404, b"nope", "text/plain")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):                                   # noqa: N802
        try:
            if self.path == "/render":
                png, meta = render(self._body())
                self._send(200, json.dumps(
                    {"ok": True, "png": base64.b64encode(png).decode(), "meta": meta}
                ).encode(), "application/json")
            elif self.path == "/forget":
                forget_data()
                self._send(200, b'{"ok":true}', "application/json")
            elif self.path == "/save":
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                out = OUT_DIR / "preset.json"
                out.write_text(json.dumps(self._body(), ensure_ascii=False, indent=2),
                               encoding="utf-8", newline="\n")
                self._send(200, json.dumps({"ok": True, "path": str(out)}).encode(),
                           "application/json")
            else:
                self._send(404, b"nope", "text/plain")
        except Exception as exc:                          # noqa: BLE001
            import traceback
            self._send(200, json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                                        "trace": traceback.format_exc()[-600:]}).encode(),
                       "application/json")

    def log_message(self, *a):  # 静音，调参时刷屏没用
        pass


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8100
    ThreadingHTTPServer.daemon_threads = True
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("=" * 58)
    print(" 信息屏 · UI 调参台")
    print("=" * 58)
    print(f" 浏览器打开：  http://127.0.0.1:{port}")
    print(" Ctrl+C 停止。")
    print(" 第一次出图要抓一次天气和行情（几秒），之后滑块和拖动只重画，很快。")
    print("=" * 58, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
