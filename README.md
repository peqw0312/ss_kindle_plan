# 把闲置的 Kindle 改成 AI 信息屏

一台 2015 年的 Kindle Paperwhite 3，挂在墙上或者摆在桌上，自动更新：

- **撕页日历** —— 照传统「一天撕一张」的样式做：大字日期 + 农历 + 干支生肖 +
  节气进度 + 节日徽章 + 宜忌冲煞。全部本地推算，断网也准
- **天气** —— 温度、体感、湿度、风力、降水、空气质量、未来三天。
  主源和风天气（国内），没配 Key 时自动退回 Open-Meteo
- **行情** —— 纳指100、标普500、上证指数；涨跌用实心 / 空心方块区分（灰度屏专用处理）
- **时钟** —— 日历条右上角「下午 4:34」，12 小时制，给老人看不用换算。
  **由 Kindle 用自己的系统时间画**，所以屏幕上每一分钟都是准的，而云端一天只需出四次图

而且是**真的零成本**：不需要树莓派、不需要 NAS、不需要云主机、
不需要任何 API Key、不用注册任何账号、不用绑卡。

---

## 它是怎么工作的

核心洞察是：**Kindle 只需要一张 PNG**；再进一步，**时间那部分连 PNG 都不用重画**。

```
  云端：dashboard/app.py（推荐）
    ├─ 一天只重算 4 次（00:05 / 05:10 / 12:00 / 15:05，按北京时判定）
    │    其余时间把上一张原样发出去
    ├─ 本地推算农历 / 节气 / 干支 / 宜忌（不联网、无第三方库）
    ├─ 抓和风天气（兜底 Open-Meteo）+ 腾讯 / 新浪行情
    ├─ 压两个头：X-Epoch 给 Kindle 校系统时间，
    │    X-Next-Image 告诉它「下一个换图时刻是几点」—— 它照这个睡，不用瞎轮询
    └─ 用 Pillow 排版成 1072×1448 的 8 位灰度 PNG（时钟那格留白）
        │
        ▼  https://<你的>.app.workbuddy.host/dashboard.png
        │
  本机（备用，电脑开着时才有）
    generate.py 按点出一张 → serve.py 发到局域网（同样带 X-Epoch）
        │
        ▼  http://192.168.x.x:8000/dashboard.png
        │
  Kindle：curl 拉图 → eips 写屏幕（留白处空着）
        │
        ├─ 每一分钟醒一次，只做一件事：把自己的系统时间贴进那块留白
        │    eips -g 0734.png -w du -x 644 -y 18     ← 386×111 的小图
        │    这一步不联网
        │
        └─ 只在云端说的那个时刻才重新拉整图（一天四五次 WiFi，而不是每小时一次）
           其余时间贴完就休眠，不联网、不重算
```

**为什么要把时钟拆出来**：如果时钟画在图里，「时间准」就和「图多久重算一次」绑死了 ——
想让钟准，服务端就得反复重算，等于为了看一眼时间反复去拉天气和行情。
现在图一天出四次就够，屏幕上每一分钟却都是对的，上游调用量降到原来的 4%。

**为什么连"多久拉一次图"也要云端说了算**：时钟拆出去之后，Kindle 那侧其实不用再
定时去看了 —— 它每小时连一次 WiFi、下载 61KB，而一天只有四次是真的换了内容，
剩下二十趟拿回来的是**逐字节相同**的图。但设备自己又算不出时刻表（那四个点各自
有理由，还会随配置变），所以云端把「下一个换图时刻」直接写在响应头里发下来。
这样下载次数降到一天四五次，而改时刻表只需要重新发布云端，Kindle 一个字节都不用动。
顺带一条：拿回来的图和屏幕上那张一模一样时就直接跳过刷新 ——
`eips -f` 是整屏黑白闪一遍，白闪对墨水屏是纯损耗。

Kindle 端 `config.sh` 里有**主地址 + 备用地址**，按顺序试，主地址失败自动退到备用。

**为什么要有云端这份**：本机方案要求电脑常开、还得关掉睡眠，一睡图就不更新
（而且 Kindle 那边不会报错，只是继续显示旧图，很容易忽略）。搬到云端之后
电脑可以关机，Kindle 只要连上 WiFi 就行。

**为什么不用 GitHub**：原来走的是 GitHub Actions 生成 + GitHub Pages 托管，
放弃了 —— 这台电脑挂着代理，调一次版式要等几分钟；而且 Actions 跑在美国机房，
部分国内新闻源会被拦。现在两条路都是国内的。

---

## 准备工作

| 项目 | 说明 |
|---|---|
| Kindle Paperwhite 3（或任何越狱后的 Kindle） | 本项目按 PW3 的 1072×1448 调优，其他型号改一行配置即可 |
| WiFi | Kindle 要能连外网（走云端）或和电脑同一个局域网（走本机） |
| 一台电脑 | Windows / macOS / Linux 都行，装得上 Python 3.10+。**走云端的话只要配一次**，不用常开 |
| USB 线 | 越狱和拷文件用 |
| 一小时 | 越狱本身大概 15 分钟，剩下的是配置 |

> **不想越狱也能用一半**：用 Kindle 自带的「体验版网页浏览器」打开云端链接或
> `http://192.168.x.x:8000/` 就能看，页面自带自动刷新。代价是要手动开浏览器、
> 界面有边框、不能长期挂着。想真正当信息屏用，还是建议越狱。

---

## 五步走

### 第 1 步 · 在电脑上把它跑起来

```bash
cd "Kindle Plan"
python -m venv .venv
.venv\Scripts\pip install -r dashboard/requirements.txt   # Windows
.venv/bin/pip install -r dashboard/requirements.txt       # macOS / Linux

python dashboard/generate.py -c dashboard/config.yaml
```

跑完打开 `docs/dashboard.png` 看看效果。**先调到满意再往下走**，改这个只要几秒。

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

**方案 A（推荐）**：把 `dashboard/` 发布到云端，拿到一个 https 链接。
链接形如 `https://<你的>.app.workbuddy.host/dashboard.png`，之后数据一直是新的，
电脑可以关机。

**方案 B（备用）**：在本机跑，电脑需要常开。

```bash
python dashboard/tools/install_task.py    # 按 config.yaml 的时刻表生成（Windows 计划任务）
python dashboard/serve.py                 # 局域网服务（这个窗口要一直开着）
# 别忘了把电脑的睡眠改成「从不」
```

两条路的详细步骤见 [guide/03-部署方案.md](guide/03-部署方案.md)。

### 第 4 步 · 越狱 Kindle

按 [guide/01-越狱与基础环境.md](guide/01-越狱与基础环境.md) 走。要点：

- PW3 固件 **≤ 5.16.2.1.1** 可用 LanguageBreak；高于这个版本先别动手
- 越狱本身不复杂，但**顺序不能错**，尤其是「先离线点商店、再联网」
- 越狱后**必须单独执行一次 Rename OTA Binaries**，否则亚马逊会静默升级把你的越狱干掉

### 第 5 步 · 装 Kindle 端并启动

按 [guide/02-Kindle端部署.md](guide/02-Kindle端部署.md) 走。核心就四件事：

1. 把 `kindle/extensions/aistatus` 拷到 Kindle 的 `/mnt/us/extensions/`
2. 打开 `config.sh`，填 `DASHBOARD_URL`（云端）和 `DASHBOARD_FALLBACK_URL`（本机）
3. **把 `clock/` 目录一起拷过去**（1440 张时钟小图，约 4 MB）。这份是本机生成的，
   不是拉下来的 —— 少了它屏幕上时钟那格就是一块空白
4. KUAL → AI 信息屏 → 先点「🕐 时钟贴图自检」确认时钟能贴到右上角，
   再点「⟳ 测试下载并刷新一次」，最后才点「启动信息屏」

> 第 3 步那份小图要跑 `python dashboard/tools/make_clock_assets.py` 生成，约半分钟。
> 改动过字号或字体之后要重新生成、重新拷 —— `layout_check.py` 会告诉你是不是忘了。

---

## 目录结构

```
Kindle Plan/
├── dashboard/                    ← 电脑 / 云端：抓数据 + 排版出图 + 发图
│   ├── generate.py               出图主程序（跑一次 = 出一张图，输出到 docs/）
│   ├── app.py                    ★ 云端出图服务（按时刻表重算，带缓存，不落盘）
│   ├── serve.py                  局域网静态服务（发 docs/，给 Kindle 拉图）
│   ├── config.yaml               ★ 你主要改这个文件
│   ├── requirements.txt
│   ├── aiinfo/
│   │   ├── config.py             配置解析 + 机型分辨率表
│   │   ├── fonts.py              跨平台中文字体解析（含 .ttc 简繁字形选择）
│   │   ├── lunar.py              农历 / 节气 / 干支 / 宜忌（纯本地，零依赖）
│   │   ├── sources.py            天气（和风→Open-Meteo）/ 行情 / RSS，全部带降级
│   │   ├── digest.py             今日速览（默认关闭，代码保留）
│   │   └── render.py             墨水屏排版（撕页日历条 + 天气 + 行情；时钟区可留白）
│   └── tools/
│       ├── make_clock_assets.py  ★ 生成 1440 张时钟精灵图 + clock.conf（时钟本机化）
│       ├── check_cloud.py        云端服务自检（本机试跑 / 验线上地址）
│       ├── install_task.py       注册 Windows 计划任务（按 refresh_at 的那几个点）
│       ├── probe_sources.py      数据源自检（含各天气源对比）
│       ├── layout_check.py       版式自检：量像素 + 精灵图与几何是否同步
│       └── verify_lunar.py       农历对拍（需要 pip install lunar_python）
├── kindle/extensions/aistatus/   ← Kindle 部分：拉图 + 显示 + 省电
│   ├── config.sh                 ★ Kindle 端的配置（主地址 + 备用地址 + 时钟）
│   ├── config.xml / menu.json    KUAL 菜单
│   ├── clock/                    ★ 时钟精灵图 1440 张 + clock.conf（由上面那支脚本生成）
│   └── bin/
│       ├── aistatus.sh           主循环（1 分钟心跳：贴时钟；到点才拉整图）
│       ├── start.sh / stop.sh
│       ├── refresh.sh            单次刷新（两个地址都试一遍，排障神器）
│       ├── clock_check.sh        ★ 时钟贴图真机自检（eips -x/-y 在老机型上要实测）
│       ├── info.sh               查设备分辨率
│       └── log.sh
├── docs/                         ← 本机输出目录（自动生成，别手改）
│   ├── dashboard.png             信息屏图片
│   └── index.html                网页版（给浏览器看）
├── guide/                        ← 详细文档
└── .github/workflows/            ← 已停用（原 GitHub 方案的残留）
```

---

## 成本说明

| 项目 | 费用 |
|---|---|
| 云端出图 + 托管 | ¥0，不用注册、不用实名、不用绑卡 |
| 本机生成 + 局域网托管 | ¥0（电费按电脑本来就要开着算） |
| 和风天气（可选） | 免费订阅，5 万次/月，本项目用掉约 130 次 |
| Open-Meteo 天气 / 空气质量 | 免费，无需注册 |
| 腾讯 / 新浪行情 | 免费，无需注册 |
| 农历 / 节气推算 | 本地算法，连网都不用 |
| 电费 | Kindle 用 RTC 休眠，每次唤醒只贴一张小图就继续睡（不联网）。整图那侧因为照云端的时刻表来取，一天只连四五次 WiFi。每分钟醒一次仍是这套方案唯一的真实开销，想要更久就把 `CLOCK_INTERVAL` 调大 |

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
5. **304 会让 Kindle 永远停在旧图**：如果服务端认 `If-Modified-Since`，文件没变时回 304，
   Kindle 的 curl 会当成「下载成功但没写文件」—— 屏幕永远停在上一张，而两端日志全绿。
   这一条是整个项目最难查的坑，`serve.py` 和 `app.py` 都专门把协商头摘掉、压上 `no-store`。
6. **2015 年前后的 Kindle 根证书偏旧**：连现代 https 站点可能报 `certificate error`。
   云端链接实测是 TLS 1.2 + DigiCert 签发，PW3 大概率没问题；真不行的话
   Kindle 端会自动退到 http 的局域网备用地址。
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
11. **时钟的字体必须和图上其他字是同一套**：时钟是**本机**生成的精灵图，贴到**云端**出的图上。
    本机默认解析到微软雅黑，云端解析到 Noto Sans CJK —— 两者行高差 **10px**，而时钟区的
    坐标正是用这些字量出来的。结果就是时钟要么压着别的字、要么偏出去一截，看着像"另一块
    拼上去的"。解法是让出图和生成精灵图都走 `fonts.book_for(cfg)`，由
    `config.yaml` 的 `clock.font / clock.font_bold` 统一指到本机的 Noto。
    `layout_check.py` 会把「精灵图与几何是否同一套」当成自检项。
12. **云端容器跑在 UTC**：出图时刻表如果按容器本地时间判，`00:05` 会落到北京时间 `08:05`，
    于是每天上午屏幕上是昨天的日期。代码一律按 `location.timezone`（Asia/Shanghai）判定。
13. **Windows 上没有 IANA 时区库**：`ZoneInfo("Asia/Shanghai")` 在没装 `tzdata` 的 Windows 上
    直接抛 `ZoneInfoNotFoundError`。`app.py` 会捕获它并退回本机时区（中国没有夏令时，等价），
    同时打一条日志 —— 免得本机试跑时莫名其妙崩掉。
14. **`eips -x / -y` 在 2015 年的机器上要实测**：官方文档只明确说不支持 K4，PW3 没有定论。
    所以 Kindle 端带一支 `clock_check.sh`：跑一次就能看出时钟是不是贴到了左上角
    （`-x/-y` 被忽略的特征）。真不支持就把 `CLOCK_MODE` 改回 `image`，其余一切照常。
15. **每分钟醒一次的代价**：`CLOCK_INTERVAL=60` 意味着一晚上上千次 RTC 唤醒。这是为了
    「屏幕上的分钟数永远是对的」刻意换来的，`CLOCK_INTERVAL` 可以调大（比如 300），
    或者 `CLOCK_MODE=off` 完全关掉本机时钟、退回画进图里。

---

## 关于「云端方案」

这个项目最早是走 **GitHub Actions 生成 + GitHub Pages 托管**的，代码和文档的痕迹
（`.github/workflows/aistatus.yml.disabled`、`digest.endpoint` 指向 GitHub Models）
都还留着，但**已经停用**了。放弃的原因：

| 问题 | 说明 |
|---|---|
| 电脑挂着代理 | 访问 GitHub 得靠代理，调一次版式要等 Actions，太慢 |
| 国内源在境外 IP 上会被拦 | Actions 跑在美国机房，部分国内 RSS 根本抓不到 |
| 想换的图床都没了 | Gitee Pages 已于 **2024-07-15 下线**，国内没有对等的免费静态托管 |

现在的"云端"是自建的 `dashboard/app.py` —— 一个跑在应用托管上的 HTTP 服务，
即出图又发图，替换掉了原来"Actions 生成 + Pages 托管"的两段式。

想让历史工作流重新跑起来，把 `aistatus.yml.disabled` 改回 `.yml` 即可 —— 但注意
`generate.py` 里那段写 `GITHUB_OUTPUT` 的代码已经删掉了，需要的话得自己加回来。

---

## 文档

| 文档 | 内容 |
|---|---|
| [guide/01-越狱与基础环境.md](guide/01-越狱与基础环境.md) | PW3 越狱、装 KUAL/MRPI、锁死自动升级 |
| [guide/02-Kindle端部署.md](guide/02-Kindle端部署.md) | 装扩展、配置、启动、恢复正常使用 |
| [guide/03-部署方案.md](guide/03-部署方案.md) | 云端与本机两条路：跑通管线 / 天气源 / 发布 / 定时生成 |
| [guide/04-排障手册.md](guide/04-排障手册.md) | 屏幕不更新、下载失败、字体方块等 |

---

## 项目来源与致谢

Kindle 端的省电机制参考了两个成熟开源项目的实测结论，本项目在其基础上重写了实现：

- [pascalw/kindle-dash](https://github.com/pascalw/kindle-dash) —— RTC 闹钟 + `echo mem` 强制休眠方案
- [loehnertj/kindle_dashboard_viewer](https://github.com/loehnertj/kindle_dashboard_viewer) —— WiFi 状态查询与 `deferSuspend` 的失败经验

越狱相关请以社区官方文档为准：
[kindlemodding.org](https://kindlemodding.org/) ／ [MobileRead Wiki](https://wiki.mobileread.com/wiki/Kindle_Hacks_Information) ／
中文可以参考 [书伴 bookfere.com](https://bookfere.com/post/326.html)
