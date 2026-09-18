# 项目长期约定 · Kindle Plan

把闲置 Kindle 改造成 AI 信息屏。**云端为主、本机为备**：两边都只产出一张 PNG，
Kindle 拉图显示。

## 架构约定（改动前先读）

- **零成本是硬约束**：不引入任何需要付费、需要绑卡、需要实名或需要申请 Key 的服务。
  可选的和风天气必须始终有 Open-Meteo 兜底，配置缺失时不能让屏幕空着。
- **出图有两条路，共用同一套代码**（这是关键，别让它们漂移）：
  - **方案 A · 云端（主）**：`dashboard/app.py` 跑在应用托管上，自己抓数据 + 排版 +
    在同一个 HTTP 端口发图。电脑可以关机。链接形如
    `https://kindle-dashboard-20828.app.workbuddy.host/dashboard.png`。
  - **方案 B · 本机局域网（备）**：`generate.py` 出 `docs/dashboard.png`，
    `serve.py` 发出去，Kindle 拉 `http://192.168.31.158:8000/dashboard.png`。
    电脑必须常开且不休眠。
  - 两边**都不自己写抓取逻辑** —— `app.py` 直接 `import generate` 复用 `collect()`。
- **2026-09 起放弃 GitHub / Gitee**：用户本机挂代理，Actions 的境外 IP 也抓不到
  国内源；Gitee Pages 已于 2024-07 下线。历史文件 `.github/workflows/*.yml.disabled`
  保留但停用。
- **`docs/` 只放本机生成物**（`dashboard.png` / `index.html` / `debug.json`），
  同时也是 `serve.py` 的根目录。**云端不落盘**，所以 `output:` 那几项在云端是无关项。
  人工文档一律放 `guide/`，不要把 .md 写进 `docs/`。
- **Kindle 端只消费一张 PNG**。不要在 Kindle 上跑 HTML 渲染、不要在 Kindle 上
  调 API。Kindle 只做四件事：下载、写屏幕、**贴本机时钟**、休眠。
- **时间与出图是解耦的两条线（2026-09 定稿，别再把它们绑回去）**：
  - **图里时钟那格是留白的**。Kindle 用**自己的系统时间**每分钟贴一张
    `clock/HHMM.png`（`eips -g sprite -w du -x 644 -y 18`）。
  - 因此**图不需要为了"时间准"而反复重算**，一天只出 4 次。
  - `clock.mode`（`config.yaml`）和 `CLOCK_MODE`（Kindle `config.sh`）
    是**一对**，必须同时是 `local` 或同时退回 `image`。混着配的两种错法：
    Kindle `local` + 图 `image` = 时钟叠成一团；Kindle `off` + 图 `local` = 右上角永远空白。
- **`clock/` 目录（1440 张精灵图，约 4 MB）必须随 `aistatus` 一起拷进 Kindle**。
  它不是拉下来的，是本机 `make_clock_assets.py` 生成的。少了它右上角就是空白。
  **改过字号 / `clock.style` / 字体之后必须重新生成 + 重新整目录拷。**
- **Kindle 端配两个地址**：`DASHBOARD_URL`（主）+ `DASHBOARD_FALLBACK_URL`（备），
  按顺序试。留这个后路是因为主地址是 https，而 2015 年老固件的根证书库偏旧。
  实测云端域名是 **TLS 1.2 + DigiCert**（不是 Let's Encrypt），PW3 大概率没问题。
- **`WIFI_TEST_IP` 取决于主地址在哪**：主地址在云端 → 填外网地址（`223.5.5.5`）；
  主地址在局域网 → 填网关（`192.168.31.1`）。**填错会给出"错误的安慰"**。
- **命令行参数约定**：`python dashboard/generate.py -c dashboard/config.yaml`
  从仓库根目录执行；config 里的所有相对路径都以仓库根为基准。

## 云端出图服务（`dashboard/app.py`）

标准库 `http.server`，`PORT` 环境变量注入，绑 `0.0.0.0`。四个不变量，少一个就会出
"两端日志全绿但屏幕不动"这种最难查的故障：

1. **响应头必须禁缓存**（`no-store` + 摘掉条件请求）。理由同 `serve.py`：
   304 → Kindle 的 curl 当成"下载成功但不写文件"。
2. **重建失败不清旧图**，继续发上一张好的，把原因记进 `/health` 的 `reason`。
   屏幕上显示"20 分钟前的图"远好过显示空白或 500。
3. **重建要单飞**。已有图在手时不抢锁，直接发旧图，让正在重建的线程慢慢算 ——
   请求不该为了"新 30 秒"多等 5 秒。没图时才阻塞等。
4. **冷启动要预热**：起进程时后台先算一张，否则第一个请求干等 3~8 秒。

- 三个路径：`/dashboard.png`（Kindle 用，带 **`X-Epoch`** 校时 + **`X-Next-Image`**
  下发时刻表，`?force=1` 强制重算）、
  `/`（给人看的预览页）、`/health`（排障唯一入口）。
- **出图节奏是"时刻表"不是"固定间隔"**：`cloud.refresh_at` 默认
  `["00:05","05:10","12:00","15:05"]`。四个点各自有理由，别删：
  `00:05` 翻日期（不然上午还是昨天的日期）、`05:10` 美股收盘（夏令时 04:00 /
  冬令时 05:00，取两者之后，一年不用改）、`12:00`、`15:05` A 股收盘。
  留空才退回 `cloud.refresh_minutes`（默认 15）的固定间隔。
- **时刻表会通过 `X-Next-Image` 头下发给 Kindle（2026-09 起），所以它现在同时决定
  "图什么时候变新"和"Kindle 多久去看一眼"** —— 别再当成两回事。改 `refresh_at`
  重新发布即可，Kindle 端 `config.sh` 的 `UPDATE_INTERVAL` 只在**收不到这个头**时
  才生效（局域网 `serve.py` 不发，它只是静态发文件、不参与调度）。
  `Board.next_fetch_at()` 是这条链的唯一出口，三种情况别搞混：
  手里图是新的 → 下一档 + `BUILD_GRACE_SECONDS`(120s)；刚过点且重建在路上 →
  `STALE_RETRY_SECONDS`(300s) 后再来（**不能**报下一档，否则 00:05:01 来问的
  设备会被告知"睡到 05:10"，把这一档整个错过）；过期很久（= 重建一直失败）→
  仍然报下一档，别退化成高频轮询，上游挂掉时反复短轮询比原来的小时轮询更费电。
  **Kindle 端 `next_image_from_headers` 只接受「未来 12 小时内」的值，这个窗口
  两端必须一致**：服务端发出超窗口的值，客户端当没收到、静默退回轮询，
  优化等于没做（`check_cloud.py` 会按同一个 43200s 判据点名）。
- **拉回来的图和屏幕上那张逐字节相同时直接跳过刷新**（`aistatus.sh` 里 `cmp -s`）。
  `eips -f` 是整屏黑白闪一遍，白闪对墨水屏是纯损耗。
- **时刻一律按 `location.timezone` 判定，不能用容器本地时间**：云端容器跑 UTC，
  按本地判会让 `00:05` 落到北京 `08:05`。`class Schedule` 负责这件事，
  `_verify_clock.py` 里有专门的 UTC 陷阱用例。
- **Windows 上没有 IANA 时区库**：`ZoneInfo("Asia/Shanghai")` 会抛
  `ZoneInfoNotFoundError`（未装 `tzdata`）。`app.resolve_zone()` 捕获后退回本机时区
  （中国无夏令时，等价）并打日志 —— 否则本机试跑直接崩。
- **`X-Epoch` 是功能必需，不是装饰**：时钟由 Kindle 本机画之后，设备时间准不准
  直接决定屏幕上的时间对不对。Kindle 拿这个头对表，偏超过
  `CLOCK_SYNC_TOLERANCE`（默认 120s）就自动校准。`app.py` 和 `serve.py` 都带。
- **`/health` 的判读**：`age_seconds` **一直涨是正常的**（一天只出四次），
  判断"是不是卡住"要看 `next_build_at` 必须是未来时间；
  `clock_region` 必须和 Kindle 上 `clock/clock.conf` 的 `X/Y/W/H` 一致。
- **云端没有你本机的环境变量** → 和风的 Key 必须写进 `dashboard/config.yaml`，
  不能只靠 `QWEATHER_KEY`。
- **图上所有文字（含时钟精灵图）统一走 `fonts.book_for(cfg)`**。`config.yaml` 的
  `clock.font` / `clock.font_bold` 把管线钉到本机的 Noto，与云端解析出的 Noto 对齐。
  **别删这两项**：删了本机就是微软雅黑、云端是 Noto，行高差 10px，
  时钟会像"另一块拼上去的"，且留白区会对不上。
  `font.regular` / `font.bold` 是通用别名，优先级更高。
- `book_for` 对"配置指向的路径不存在"是**静默退回**的（云端那些 Windows 路径
  本来就不存在，必须容错）；但 `make_clock_assets.py` **故意不静默** ——
  本机生成精灵图时路径不对就直接报错停下。

## Kindle 基础环境（KUAL / MRPI / hotfix）

- **装 KUAL/MRPI 没有任何"安装动作"，全是往哪拷**：KUAL 用 **PEKI**
  （K5 及更新机型，PW3 属 K5），解压出的 `KUAL.sh` + `KUAL.jar` → `documents/`；
  MRPI 用 modern 版，解压出的 `extensions/` + `mrpackages/` 两个文件夹 → 根目录。
  失败原因只有两个：**放错位置**，或**文件名被浏览器加了 `(1)` 后缀**。
  另需 **220 MB 余量**，腾空间前先开飞行模式。
- **hotfix 必须 ≤ 2.3.7，不要用最新的 2.5.x**：2.5.0 换了 KUAL 的签名证书，
  症状是 KUAL 图标正常出现在书库里、但一点就报 `Application Error`，
  而且**降级回 2.3.7 也修不好，只能重新越狱**（PW3 用户实测）。
- hotfix 是**两步**：拷 `Update_hotfix_universal.bin` → 设置→更新您的 Kindle →
  再到书库点开 **`Run Hotfix` 小册子跑一遍**。**只拷不运行等于没装。**
- OTA 屏蔽靠独立扩展 **renameotabin**（KUAL 不自带）。**恢复出厂 / 刷官方固件 /
  降级之前必须先在 KUAL 里 `Restore` 改回去**，否则会卡在开机检查更新界面。
- 本项目**用不到 MRPI**（`aistatus` 是纯 KUAL 扩展，不走 `.bin` 包），
  但装了没坏处，以后 USBNetwork / 更新 hotfix 都要它。

## 托管与调度

- `dashboard/serve.py`：标准库静态服务（本机备用方案）。**必须摘掉
  `If-Modified-Since` / `If-None-Match`**，响应头压 `Cache-Control: no-store`。
- `dashboard/tools/install_task.py`：注册 Windows 计划任务 `KindleAIScreen`。
  **它读 `config.yaml` 的 `cloud.refresh_at`，每个时刻各注册一个每日触发器**
  （不是"每 60 分钟一次"那种固定间隔）。`--at 07:30,12:00`（**逗号分隔，不是可重复参数**）
  可临时覆盖；`refresh_at` 留空时用 `--minutes` 指定固定间隔。
  用 Python 驱动 PowerShell（`.ps1` 在中文路径下会按 GBK 读而乱码）。
  `--status/--run/--remove`。
  **改完 `refresh_at` 要重跑一次它**，任务只在注册那一刻读配置。
  云端为主之后这条只服务备用方案，但留着没坏处（还能保持 `docs/` 有新图当兜底）。
- 本机局域网 IP **192.168.31.158**（以太网），网关 **192.168.31.1**。
- 走本机方案时**电脑必须不休眠**，否则图不更新 —— 而 Kindle 那边不会报错，
  只是显示旧图。

## 天气源（和风为主 + Open-Meteo 兜底）

- `sources.fetch_weather(cfg)` 是分发器：先试和风，失败/未配置则回落到 Open-Meteo，
  结果里带 `source` 字段，页脚如实显示。
- **和风的旧域名已停服**：`devapi.qweather.com`（2026-01 停）、
  `api.qweather.com`（2026-06 停）。必须用控制台发的专属 Host
  （`xxxxxx.re.qweatherapi.com`）。网上教程几乎都是旧的。
- 和风的 `location` 参数是 **`经度,纬度`**（lon,lat 顺序，别写反）。
- 两个源口径不同：和风直接给 `windScale`（风级），Open-Meteo 只给 km/h。
  **换算统一收在 `sources.effective_wind_level()`**，渲染层和自检工具都调它
  —— 之前这段逻辑长在 render.py 里，导致自检打出来是 `None` 而屏幕上是正常的。
  数据层现在也会就地补上 `wind_level`，下游不必关心用的哪个源。
- 和风比国内实况有差距这件事：Open-Meteo 是全球数值模式（约 11km）插值，
  实测温度差 1~2°C、湿度差 8 点、风速差一倍，且**没有灾害预警**。

## 时钟与横向预算（踩过坑）

- `clock.style: cn12` = 「下午 4:34」，时段词用 `凌晨/上午/中午/下午/晚上`。
- **12 小时制比 24 小时制宽得多**：「晚上 11:59」= 385px，「16:34」= 210px。
  日历条第一行的宽度必须按**最宽形态**（`clock_probe()`）预留，
  否则一到晚上时钟就压到农历文字上。`layout_check.py` 已固化这条检查。

### 本机时钟（`clock.mode: local`）的几何与产物

- 时钟区矩形由 `Renderer.clock_region(cal)` 算出来，`render()` 把它存进
  `self.clock_box`；`/health` 的 `clock_region` 和 `clock.conf` 的 `X/Y/W/H`
  都来自这里 —— **一处改动、两边自动一致**，别在任何地方手写坐标。
- 当前定值：`(644, 18) → (1030, 129)`，386×111px，`CLOCK_TAG=b10886d42255`。
- **云端算出来的左边界会比本机大 1px**（线上实测 `[645, 18, 1030, 129]` vs
  本机 `[644, ...]`）。原因：出图与生成精灵图用的是**不同的字体文件** ——
  本机 `Noto Sans SC`（独立 `.otf`），云端 `Noto Sans CJK SC`（`.ttc`），
  字形度量差 1px，而左边界 = 右边界 − 字宽。
  **实测无害**：本机与云端在 x=640..650 全列纯白，精灵图左侧 10 列也纯白，
  是白压白，屏幕上没有任何可见差异。**别把它当成 bug 去追。**
  `check_cloud.py --url` 会把这个差值打出来，`≤2px` 判一致，超了才报错
  （超了说明两边字体/字号不是一套了，那时才是真故障）。
- 精灵图**画布尺寸正好等于那块留白**，排版走的是和主图同一行代码
  （`Renderer.clock_sprite`），所以贴回去接缝看不出来。
- `make_clock_assets.py` 自带**像素级自检**：精灵图贴回留白版 vs 时钟直接画进图里，
  必须逐像素一致。改字号/字体后这个检查会当场失败 —— 这是有意的设计。

### `clock.conf` 的换行符是硬要求（静默故障）

`clock.conf` 会被 Kindle 的 sh **直接 `source`**。`Path.write_text` 在 Windows 上
默认把 `\n` 翻成 `\r\n`，于是 `CLOCK_X="644\r"`，`eips -x` 拿到带回车的参数
**不报错、只把时钟贴到错的位置**。三层防护，一个都别删：

1. `write_text(..., newline="\n")` —— 生成脚本显式写 LF
2. `layout_check.py` 检查里面有 CR 就直接判失败
3. Kindle 端 `aistatus.sh` / `refresh.sh` / `clock_check.sh` 各 `tr -d '\r'` 兜底
   （USB 拷贝绕过 git，`.gitattributes` 挡不住）

### `eips -x/-y` 待真机验证

官方文档只说"在 K4 上不生效"，PW3（2015）没有定论。所以 Kindle 端有
`bin/clock_check.sh`（KUAL 菜单第 4 项）：跑一次抬头看——时钟在右上角=可用；
在**左上角**=这个固件忽略 `-x/-y`，把 `CLOCK_MODE=off` + `clock.mode: image`；
什么都没有=精灵图没拷全。**这是唯一没法在电脑上验的环节。**
`-w du`（局部刷新波形）同理需实测，不支持就换 `gl16` / `gc16`。

## 版面预算（1448px 纵向，2026-09 版）

日历条 565 + 天气 550 + 行情 221 + 页脚 58 + 顶部 10 = **1404px，余 44px**。
余量均摊成三块的内边距（不堆在底部）。速览默认关闭。

- **余量安全线 40px**。以前是因为"本机雅黑 / 云端 Noto 行高不同"才设的；
  现在图上文字（含时钟精灵图）统一由 `fonts.book_for` 解析，两边是同一套字，
  但改字号/换字体仍会让字形宽度变化，余量还是留着。
- 天气卡片两个尺寸是**被最长内容倒逼**出来的，别凭"看着挺合理"改：
  - 数据格必须 **2 列**（3 列时每格 161px，而「空气 轻度 118」要 220px，
    旧的「轻度污染 118」要 308px，会糊到隔壁格）
  - 温度和描述**不能并排**（图标 124 + 「29°」203 + 「多云转小雨」220 = 585px，
    而左半区只有 427px）。必须上下两行。
  - 左右分界 `WEATHER_SPLIT = 0.46`
- 空气格用**短等级**（`_air_short()`：「轻度污染」→「轻度」）+ 灰色 AQI 数字。

## 行情接口的实测字段位置（别凭记忆改）

- 腾讯 `v_<code>` 用 `~` 分隔：`[1]`=名称 `[3]`=现价 `[4]`=昨收；美股同样适用
- 新浪 A股 `[0]`=名称 `[3]`=现价 `[2]`=昨收；美股 `gb_` 现价`[1]` 昨收`[26]`；
  港股 `rt_` 现价`[6]` 昨收`[3]`
- 涨跌幅一律自己用 现价/昨收 计算，不用接口给的字段，避免各家字段错位
- 内部代码键统一小写（`normalize_code`），但请求腾讯时要传用户原始写法
- 指数：腾讯/新浪都有 `usNDX` = 纳斯达克100、`usINX` = 标普500

## 加密合约行情（代码保留，默认不启用）

- 代码写法 `bitget:BTCUSDT`（前缀只是命名空间，不代表只在 Bitget 取价）
- 链路 **Bitget → Gate → HTX**：`api.bitget.com` 国内直连被重置，
  **Gate（api.gateio.ws）是国内唯一稳定直连的合约源**，实测 0.2s
- Gate 字段：`last`=最新价、`change_price`=24h 变动额 → 昨收 = `last - change_price`
- 屏幕上必须如实标注本轮用的是哪家（`crypto_source_label()`）

## 农历模块（`aiinfo/lunar.py`，零依赖是硬约束）

- **不要引入 sxtwl / lunardate / cnlunar 等日历库**：sxtwl 是 C 扩展，
  在 GitHub Actions 上编译失败，这正是自己写的原因
- `TERM_FIX` 修的不是"算错一天"，而是"节气时刻落在午夜前后 6 分钟内"这类
  无法用日期表达的悬案（1896 小暑 00:02、2008 小满 23:59…）。全表 5000+ 个
  节气日期里只有 14 个需要钉。
- 改完农历相关代码必须跑 `python dashboard/tools/verify_lunar.py`

## 文档分工（改文档前先看，别重复造）

- `README.md` —— 讲**为什么这么设计**（架构、取舍、踩坑史）
- `guide/00-操作总览.md` —— 讲**现在该干什么**：6 步顺序表 + 目录地图 +
  自检命令 + 退出方式。**用户问"我要怎么操作 / 目录是什么"就指这一份。**
- `guide/01~04` —— 分主题深挖（越狱 / Kindle 端 / 部署方案 / 排障）
- **全项目只有两个文件是用户要动的**：`dashboard/config.yaml`、
  `kindle/extensions/aistatus/config.sh`。改完 `dashboard/` 里任何东西
  **必须重新发布云端**才会生效（链接不变），Kindle 那边不用动。

## 自检工具（改完代码顺手跑）

| 脚本 | 用途 | 需要联网 |
|---|---|---|
| `dashboard/tools/probe_sources.py` | 天气（**两源并排对比**）/日历/行情/新闻/AI | 部分 |
| `dashboard/tools/layout_check.py` | 纵向预算 + 日历条横向 + **天气卡片横向** + **时钟精灵图同步**，出 5 张预览图 | 否 |
| `dashboard/tools/make_clock_assets.py` | 生成 1440 张时钟精灵图 + `clock.conf`（带像素级自检） | 否 |
| `dashboard/tools/check_cloud.py` | 云端服务自检（本机起进程试跑，或 `--url` 验线上） | 是 |
| `dashboard/tools/install_task.py` | 计划任务：注册/状态/触发/注销 | 否 |
| `dashboard/tools/verify_lunar.py` | 农历对拍（对 lunar_python） | 否 |

- `layout_check.py` 的四项都**不是摆设**：前三项是真实翻过车才加进去的；
  第四项（时钟精灵图 vs 版面几何）守的是"改字号 → 矩形变了 → 精灵图没重生成 →
  真机上时钟贴偏"这条链 —— **电脑上看预览图完全正常，只有真机才看得出来**。
  改字号 / `clock.style` / 字体之后**必跑**。
- 天气卡片横向检查调 `renderer.weather_grid()` —— **不要在检查脚本里重抄一份
  格子内容**，抄的那份迟早和绘制对不上。
- `check_cloud.py` 的字体判据是**排他式**（只把 DejaVu/Liberation/Arial 这类
  明确不含汉字的判错），不是白名单 —— 白名单曾经在字体名从
  `Noto Sans CJK SC` 换成 `Noto Sans SC` 后误报。
- Windows 控制台是 GBK，直接 print ✅ 会抛 `UnicodeEncodeError` **中断整个脚本**，
  统一走 `tools/_marks.py` 选标记。

## 依赖

Python 只需 `Pillow` / `PyYAML` / `requests` 三个，刻意保持极简
（`dashboard/requirements.txt` 就这三行，云端发布时按它装）。
`serve.py`、`app.py`、`install_task.py` 只用标准库。

字体优先级（`fonts.book_for` 内部）：`config.yaml` 的
`font.regular/font.bold` > `clock.font/clock.font_bold` > `AIINFO_FONT` 环境变量 >
已知路径 > fontconfig > 目录扫描。**环境变量排在配置之后** —— 想换字体改配置更管用。
