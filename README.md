# 把闲置的 Kindle 改成 AI 信息屏

一台 2015 年的 Kindle Paperwhite 3，挂在墙上或者摆在桌上，自动更新：

- **撕页日历** —— 照传统「一天撕一张」的样式做：大字日期 + 农历 + 干支生肖 +
  节气进度 + 节日徽章 + 宜忌冲煞。全部本地推算，断网也准
- **天气** —— 温度、体感、湿度、风力、降水、空气质量、未来三天。
  主源和风天气（国内），没配 Key 时自动退回 Open-Meteo
- **行情** —— 纳指100、标普500、上证指数；涨跌用实心 / 空心方块区分（灰度屏专用处理）
- **电量** —— 右上角百分比 + 进度条，由 Kindle 用自己的读数贴上去（云端拿不到电量）
- **时钟已经整个砍掉了**：本来它由 Kindle 用自己的系统时间画、每分钟贴一次，
  代价是每天 1440 次唤醒。2026-09-22 拿它换了续航（`CLOCK_MODE=off`），
  现在设备一天只醒四次。代码和 1440 张精灵图的生成脚本都还在，想要随时能装回来

而且是**真的零成本**：不需要树莓派、不需要 NAS、不需要云主机、
不需要任何 API Key、不用注册任何账号、不用绑卡。

---

## 它是怎么工作的

核心洞察是：**Kindle 只需要一张 PNG**；再进一步，**时间那部分连 PNG 都不用重画**。

```
  GitHub Actions（.github/workflows/build.yml）
    ├─ 一天跑 4 次（cron 用 UTC：北京 00:05 / 05:10 / 12:00 / 15:05）
    ├─ 本地推算农历 / 节气 / 干支 / 宜忌（不联网、无第三方库）
    ├─ 抓和风天气（兜底 Open-Meteo）+ 腾讯 / 新浪行情
    ├─ 用 Pillow 排版成 1072×1448 的 8 位灰度 PNG（右上角电量那块留白）
    └─ 把 docs/dashboard.png 提交回本仓库
        │
        ▼  https://raw.githubusercontent.com/<owner>/<repo>/main/docs/dashboard.png
        │
  Kindle：curl 拉图 → eips 写屏幕 → 补贴右上角那小块电量
        │
        ├─ 一天只连四次 WiFi，按 config.sh 的 REFRESH_AT 自己算该几点来
        └─ 其余时间 `echo mem` 睡死，墨水屏保持画面本身零功耗
```

**为什么整条链路上没有你这台电脑**：以前出图靠自建的 `dashboard/app.py`（跑在应用
托管上）或者局域网那台 `serve.py`，两个都已经删了 —— 它们都要求"电脑开着且不睡"。
现在电脑只干一件事：改代码、commit、push。关机、休眠、断网都不影响这块屏。

**为什么一天只出四次图**：屏幕上唯一"必须新"的东西是日期、天气和收盘价。
四个点各自有理由（00:05 翻日历、05:10 美股收盘、12:00 午间、15:05 A 股收盘），
详见 `dashboard/config.yaml` 第 ⑪ 节。对比"每 15 分钟"的 96 次，上游调用降了 96%。

**为什么"几点来拉"写在设备上**：GitHub 的 raw 地址是静态文件，**发不了自定义头**，
所以旧版那套"云端用 `X-Next-Image` 告诉设备下一个换图时刻"没有了。
时刻表搬进 `kindle/extensions/aistatus/config.sh` 的 `REFRESH_AT` ——
代价是它和 Actions 的 cron 变成**同一张表的两个副本，改的时候必须一起改**。
不一起改不会坏，只会让 Kindle 在没有新图的时间点白连一次 WiFi 拿回旧图。

> 顺带纠正一条以前写在这里的说法：设备**没有**"图和屏幕上那张逐字节相同就跳过刷新"
> 这回事 —— 代码里从来没实现过。省下来的下载靠的是"一天只醒四次"，不是靠比对内容。

**为什么不用 GitHub**：原来走的是 GitHub Actions 生成 + GitHub Pages 托管，
放弃了 —— 这台电脑挂着代理，调一次版式要等几分钟；而且 Actions 跑在美国机房，
部分国内新闻源会被拦。现在两条路都是国内的。

---

## 准备工作

| 项目 | 说明 |
|---|---|
| Kindle Paperwhite 3（或任何越狱后的 Kindle） | 本项目按 PW3 的 1072×1448 调优，其他型号改一行配置即可 |
| WiFi | Kindle 要能连**外网**（图在 GitHub 上，局域网通不够） |
| 一台电脑 | Windows / macOS / Linux 都行，装得上 Python 3.10+。**只在改东西和 push 时需要**，平时可以关机 |
| 一个 GitHub 账号 | 免费公开仓库 + Actions。不用注册别的、不用实名、不用绑卡 |
| USB 线 | 越狱和拷文件用 |
| 一小时 | 越狱本身大概 15 分钟，剩下的是配置 |

> **不越狱行不行**：只能用 Kindle 自带的「体验版网页浏览器」手动打开那张图的链接看
> 一眼，**没有自动刷新** —— 以前那个自带刷新的 `index.html` 是靠本机 HTTP 服务的，
> 现在没有服务了（GitHub 的 raw 会把 HTML 当纯文本发，不执行脚本）。
> 所以不越狱实际上只能当预览用。

---

## 五步走

### 第 1 步 · 在电脑上把它跑起来

**先确认你有一个能用的 Python。** Windows 上 `python` 常常是微软商店的占位壳子 ——
敲了没反应、也不报错，看着像项目坏了其实没装 Python。用 `uv` 最省事（它自带一个
独立的 Python，不跟任何编辑器/IDE 绑在一起）：

```powershell
cd "Kindle Plan"
winget install astral-sh.uv          # 装过一次就不用再装
uv venv .venv --python 3.13
uv pip install --python .venv/Scripts/python.exe -r dashboard/requirements.txt

.venv\Scripts\python.exe dashboard\generate.py -c dashboard\config.yaml
```

macOS / Linux：

```bash
cd "Kindle Plan"
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python -r dashboard/requirements.txt
.venv/bin/python dashboard/generate.py -c dashboard/config.yaml
```

> 已经有可用的 Python 3.10+ 的话，传统写法也一样能用：
> `python -m venv .venv` 然后 `.venv\Scripts\pip install -r dashboard/requirements.txt`。
> 注意 `uv venv` 建出来的环境里**没有 pip**，要用上面 `uv pip --python` 那种写法。

跑完打开 `docs/dashboard.png` 看看效果。**先调到满意再往下走**，改这个只要几秒。

> 自检工具里 `verify_lunar.py`（农历对拍）要多装一个 `lunar_python`。
> 它只是验证用的，运行时不依赖，所以没写进 requirements.txt —— 但改过农历代码就必须装它跑一遍。

### 第 2 步 · 配天气源（可选，但推荐）

不配也能跑（自动退回 Open-Meteo）。但和风天气是国内融合实况，比全球数值模式插值
准得多，还带**灾害预警**。免费额度每月 5 万次，本项目一天只调用 4 次。

去 <https://dev.qweather.com> 注册（不用绑卡、不用实名），创建 Web API 项目，
把控制台给的 **API Host** 和 **API Key** 填进 `dashboard/config.yaml`。

> **⚠️ 网上教程大多已失效**：`devapi.qweather.com` 已于 2026-01 停服、
> `api.qweather.com` 于 2026-06 停服。必须用控制台发给你那个专属 Host
> （形如 `xxxxxx.re.qweatherapi.com`）。

详细步骤见 [guide/03-部署方案.md](guide/03-部署方案.md)。

### 第 3 步 · 把图发出去

**推上 GitHub，就这一步。** 仓库必须是**公开**的（Kindle 没法带 token 认证，
私有仓库的 Pages 还是付费功能），之后：

```bash
git push                                  # 剩下的交给 Actions
```

`.github/workflows/build.yml` 会按四个 UTC 时刻自己跑 `generate.py`，
把 `docs/dashboard.png` 提交回仓库，Kindle 直接找 `raw.githubusercontent.com` 拿。
**你这台电脑到此退出** —— 不当服务器、不用常开、没有需要养着的进程。

网页上 Actions 面板可以点 "Run workflow" 手动跑一次，改完版式立刻看效果。
详细步骤见 [guide/03-部署方案.md](guide/03-部署方案.md)。

### 第 4 步 · 越狱 Kindle

按 [guide/01-越狱与基础环境.md](guide/01-越狱与基础环境.md) 走。要点：

- PW3 固件 **≤ 5.16.2.1.1** 可用 LanguageBreak；高于这个版本先别动手
- 越狱本身不复杂，但**顺序不能错**，尤其是「先离线点商店、再联网」
- 越狱后**必须单独执行一次 Rename OTA Binaries**，否则亚马逊会静默升级把你的越狱干掉

### 第 5 步 · 装 Kindle 端并启动

按 [guide/02-Kindle端部署.md](guide/02-Kindle端部署.md) 走。核心就四件事：

1. 把 `kindle/extensions/aistatus` 拷到 Kindle 的 `/mnt/us/extensions/`
2. 打开 `config.sh`，填 `DASHBOARD_URL`（GitHub 的 raw 地址）。
   `DASHBOARD_FALLBACK_URL` **留空**是故意的：备用地址一旦生效，屏幕上的版式可能
   和仓库里那张不是同一版，会让人对着"我改了怎么没生效"白查半天
3. **把 `battery/` 目录拷过去**（11 张电量档位图 + `battery.conf`）。这份是本机
   `make_battery_assets.py` 生成的，不是拉下来的 —— 少了它右上角就是一块空白（无害）
4. 先点「⟳ 测试下载并刷新一次」看诊断，再点「启动信息屏」

> `clock/` 那 1440 张时钟精灵图现在**用不上**了：`CLOCK_MODE=off`（时钟整个砍掉，
> 换续航）。要恢复时间才需要 `make_clock_assets.py` 重新生成并拷过去，
> 同时把 `config.yaml` 的 `clock.mode` 一起改回 `local` —— 两边必须一致。

---

## 目录结构

```
Kindle Plan/
├── .github/workflows/build.yml   ★ 出图的就是它：定时跑 generate.py，把图提交回 docs/
├── dashboard/                    ← 出图逻辑（本机预览和 Actions 跑的是同一套代码）
│   ├── generate.py               出图主程序（跑一次 = 出一张图，输出到 docs/）
│   ├── config.yaml               ★ 你主要改这个文件
│   ├── requirements.txt          Actions 的依赖，只有 Pillow / PyYAML / requests
│   ├── aiinfo/
│   │   ├── config.py             配置解析 + 机型分辨率表
│   │   ├── fonts.py              跨平台中文字体解析（含 .ttc 简繁字形选择）
│   │   ├── lunar.py              农历 / 节气 / 干支 / 宜忌（纯本地，零依赖）
│   │   ├── sources.py            天气（和风→Open-Meteo）/ 行情 / RSS，全部带降级
│   │   ├── digest.py             今日速览（默认关闭，代码保留）
│   │   └── render.py             墨水屏排版：三套版式引擎 bands / poster / c1
│   └── tools/
│       ├── layout_check.py       ★ 版式自检：真渲染一遍量像素，余量/精灵图几何都在这
│       ├── check_cloud.py        验 Kindle 真正拉的那个地址（是不是 PNG、规格、新旧）
│       ├── make_battery_assets.py 生成 11 张电量档位图 + battery.conf
│       ├── make_clock_assets.py  生成 1440 张时钟精灵图（clock.mode=off 时用不上）
│       ├── probe_sources.py      数据源自检（含各天气源对比）
│       ├── icon_sets.py          图标选型页（六套完整图标 + 真实尺寸对照）
│       └── verify_lunar.py       农历对拍（需要 pip install lunar_python）
├── kindle/extensions/aistatus/   ← Kindle 部分：拉图 + 显示 + 省电
│   ├── config.sh                 ★ 地址、刷新时刻表、电量角标、怎么退出，全在这
│   ├── config.xml / menu.json    KUAL 菜单
│   ├── battery/                  ★ 电量精灵图 11 张 + battery.conf（本机生成后拷过去）
│   ├── clock/                    时钟精灵图（CLOCK_MODE=off 期间用不上，已在 .gitignore）
│   └── bin/
│       ├── aistatus.sh           主循环（醒 → 该拉图就拉 → 贴那两块小图 → 睡回去）
│       ├── start.sh / stop.sh    启停（stop.sh 会把射频和原生界面全部还原）
│       ├── refresh.sh            单次刷新 + 七步诊断（排障神器）
│       ├── clock_check.sh        时钟贴图真机自检（eips -x/-y 在老机型上要实测）
│       ├── info.sh               查设备分辨率
│       └── log.sh
├── guide/                        ← 详细文档（00 总览 → 05 托管选型评估）
└── docs/                         ← 出图结果（Actions 会重新生成并提交，别手改）
    ├── dashboard.png             信息屏图片 —— Kindle 拉的就是这一张
    └── index.html                网页版（给浏览器看）
```

---

## 成本说明

| 项目 | 费用 |
|---|---|
| GitHub Actions + 公开仓库 | ¥0。私有仓库这条走不通：Kindle 没法带 token 认证，而私有 Pages 是付费功能 |
| 和风的天气（可选） | 免费订阅，5 万次/月，本项目用掉约 130 次 |
| Open-Meteo 天气 / 空气质量 | 免费，无需注册 |
| 腾讯 / 新浪行情 | 免费，无需注册 |
| 农历 / 节气推算 | 本地算法，连网都不用 |
| 电费 | 电脑这边：只有你坐着改东西时才耗电，Actions 的算力不在你家。设备那边：靠 RTC 休眠，一天连四次 WiFi，其余时间睡死；墨水屏保持画面本身零功耗 |

**没有任何一步需要绑卡、填支付信息或注册账号。**（和风天气是可选的，注册只要手机号。）

---

## 几个已知的坑（都已在代码里处理）

1. **CRLF 换行符**：Windows 上编辑 Kindle 脚本会把换行变成 CRLF，Kindle 的 sh 会报莫名的
   `not found`。仓库里有 `.gitattributes` 强制 LF，别删。
   **`clock/clock.conf` 尤其要小心** —— 它会被 Kindle 的 sh 直接 `source`，
   CRLF 会让 `CLOCK_X` 变成 `644\r`，`eips` 不报错、只是把时钟贴到错的位置。
   生成脚本已经显式写 LF；`layout_check.py` 和 Kindle 端的 `clock_check.sh`
   各守了一道，但**别用记事本另存这个文件**。
2. **图片格式有硬要求**：`eips -g` 只认**原生分辨率的 8 位灰度 PNG**。尺寸不对就显示错位。
   代码固定输出 `L` 模式（8 位灰度）＋ config 里的机型分辨率。
3. **`rtcWakeup` 叫不醒**：`lipc-set-prop com.lab126.powerd rtcWakeup` 在多款机型上被实测证明
   不可靠。本项目改用 `echo mem > /sys/power/state` + RTC 硬件闹钟，这是 kindle-dash 验证过的路。
4. **灰度屏没有颜色**：涨跌不能用红绿，所以用**实心方块 = 涨 / 空心方块 = 跌**，
   配合字重区分。这是墨水屏设计上必要的一次转译。
5. **304 会让 Kindle 永远停在旧图**：如果下载方发 `If-Modified-Since` / `If-None-Match`，
   文件没变时服务器回 304，Kindle 的 curl 会当成「下载成功但没写文件」——
   屏幕永远停在上一张，而两端日志全绿。这是整个项目最难查的坑。
   现在这条由**设备侧**守住：`download_image()` 的 curl 不带 `-z`、不加条件头，
   永远要全量。实测 GitHub raw 确实会回 304（`check_cloud.py` 会当场演示一次），
   所以别给那行命令加缓存协商的参数。
6. **2015 年前后的 Kindle 根证书偏旧**：连现代 https 站点可能报 `certificate error`。
   `raw.githubusercontent.com` 这条实测能通（TLS 正常、约 1 秒）。
   以前"退到局域网 http 备用地址"那条兜底已经没了 —— `DASHBOARD_FALLBACK_URL`
   故意留空，理由写在 `config.sh` 的 ① 里。
7. **和风天气的旧域名已停服**：`devapi.qweather.com`（2026-01 停）和
   `api.qweather.com`（2026-06 停）都用不了了，必须用控制台发的专属 Host。
   网上教程几乎都是旧的，照抄一定失败。
8. **12 小时制比 24 小时制宽得多**：「晚上 11:59」比「16:34」宽将近一倍，
   日历条第一行的宽度必须按**最宽形态**预留，否则一到晚上就会压到农历文字上。
   `layout_check.py` 把这条固化成了自检项。
9. **字体宽度不能用眼睛估**：温度描述和数据格的实际宽度差 140px 这种量，
   肉眼看预览图是看不出来的。`layout_check.py` 会逐格量出「需要多少 / 有多少」。
10. **节气日期在"午夜前后 6 分钟"这种时刻上是悬案**：太阳视黄经迭代的精度是分钟级，
    而 1896 小暑算出来是 00:02、2008 小满算出来是 23:59 —— 换一套 ΔT 模型日期就翻一天。
    代码用一张 `TERM_FIX` 表把这些日期钉死在权威口径上，全表 5000+ 个日期里只有 14 个需要钉。
11. **精灵图的字体必须和图上其他字是同一套**：时钟、电量这两小块都是**本机**生成的
    精灵图，贴到**出图机器**画的那张图上。本机默认解析到微软雅黑，Actions 上解析到
    Noto Sans CJK —— 两者行高差 **10px**，而留白区的坐标正是用这些字量出来的。
    结果就是贴上去的东西要么压着别的字、要么偏出去一截，看着像"另一块拼上去的"。
    解法是让出图和生成精灵图都走 `fonts.book_for(cfg)`，由 `config.yaml` 统一指到 Noto。
    `layout_check.py` 会把「精灵图与几何是否同一套」当成自检项。
12. **UTC 换算**：GitHub 的 cron 只认 UTC，`00:05` 北京时间要写成 `5 16 * * *`（前一天）。
    写 cron 的那几行旁边都标了对应的北京时刻，改的时候两边一起看。
    代码内部一律按 `location.timezone`（Asia/Shanghai）判定，不按机器本地时间。
13. **Windows 上没有 IANA 时区库**：`ZoneInfo("Asia/Shanghai")` 在没装 `tzdata` 的 Windows 上
    直接抛 `ZoneInfoNotFoundError`。现在 `pip install tzdata` 就能解决；
    不装也会自动退回本机时区（中国没有夏令时，等价）。Actions 上是 Linux，没这个问题。
14. **`eips -x / -y` 在 2015 年的机器上要实测**：官方文档只明确说不支持 K4，PW3 没有定论。
    所以 Kindle 端带一支 `clock_check.sh`：跑一次就能看出时钟是不是贴到了左上角
    （`-x/-y` 被忽略的特征）。真不支持就把 `CLOCK_MODE` 改回 `image`，其余一切照常。
15. **唤醒次数就是续航的主要开销**：贴时钟那套（每分钟一次、一天 1440 次）已经把续航
    压垮过一次，2026-09-22 整个砍掉了。现在设备一天醒的次数由两个数决定：
    整图时刻表 `REFRESH_AT`（4~5 次）和 `.stop` 响应上限 `STOP_CHECK_MAX_SLEEP`
    （默认把长觉切成 10 分钟一段，一天 144 次）。后者调大更省电，但「停止信息屏」
    生效就更慢 —— 这是唯一一个"响应速度 vs 续航"的旋钮。

---

## 关于「为什么又回到 GitHub」

这个项目**最早**就是 GitHub Actions 生成 + Pages 托管，中途放弃改成自建的
`dashboard/app.py`（跑在应用托管上，即出图又发图），2026-09-24 又回到 GitHub。
绕这一圈的理由值得写下来，免得下次又翻烧饼：

| 当初放弃 GitHub 的理由 | 实测下来 |
|---|---|
| 电脑挂着代理，访问 GitHub 慢，调一次版式要等 Actions | **站不住**。本机到 raw / Pages / api / git 四个端点 1~1.4 秒可达，不需要代理。而且调版式根本不用等 Actions —— 本机 `generate.py` 两秒出图，Actions 只负责"每天那四次" |
| Actions 在美国机房，抓不到国内源 | **没验证过**，这是现在唯一真正悬着的风险。RSS 早就默认关闭了，还剩天气（Open-Meteo，国际源）和腾讯/新浪行情。第一次定时任务跑完看日志就知道 |
| Gitee Pages 已下线（2024-07-15），国内没有对等的免费静态托管 | **属实**，但和 GitHub 这条不冲突 —— 我们要的只是"一个公开可取的静态文件"，GitHub 自己就有 |

真正让天平倾斜的是另一件事：**自建服务要求电脑当服务器**（或者要求一个常开的
免费托管），而这块屏的初衷就是"电脑可以关机"。

两条硬约束记在这里：

- **仓库必须公开**。Kindle 没法带 token 认证，私有仓库的 Pages 又是付费功能。
  所以 `config.yaml` 里的经纬度只保留 2 位小数（≈1 公里见方），别改回 4 位。
- **公开仓库满 60 天无活动，GitHub 会自动关掉 schedule**。以后突然不更新了先查这里。

---

## 文档

| 文档 | 内容 |
|---|---|
| [guide/01-越狱与基础环境.md](guide/01-越狱与基础环境.md) | PW3 越狱、装 KUAL/MRPI、锁死自动升级 |
| [guide/02-Kindle端部署.md](guide/02-Kindle端部署.md) | 装扩展、配置、启动、恢复正常使用 |
| [guide/03-部署方案.md](guide/03-部署方案.md) | ⚠️ 写的是"云端 / 本机"两段式那版，已经作数了 —— 现在只有 GitHub 一条路，看本 README 和 `项目现状.md` 就够；里面关于天气源、CRLF、缓存的段落仍然有效 |
| [guide/04-排障手册.md](guide/04-排障手册.md) | 屏幕不更新、下载失败、字体方块等 |

---

## 项目来源与致谢

Kindle 端的省电机制参考了两个成熟开源项目的实测结论，本项目在其基础上重写了实现：

- [pascalw/kindle-dash](https://github.com/pascalw/kindle-dash) —— RTC 闹钟 + `echo mem` 强制休眠方案
- [loehnertj/kindle_dashboard_viewer](https://github.com/loehnertj/kindle_dashboard_viewer) —— WiFi 状态查询与 `deferSuspend` 的失败经验

越狱相关请以社区官方文档为准：
[kindlemodding.org](https://kindlemodding.org/) ／ [MobileRead Wiki](https://wiki.mobileread.com/wiki/Kindle_Hacks_Information) ／
中文可以参考 [书伴 bookfere.com](https://bookfere.com/post/326.html)
