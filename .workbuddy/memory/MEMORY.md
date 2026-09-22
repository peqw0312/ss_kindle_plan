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
- **区块顺序是配置驱动的（`layout.order`），时钟跟着日历走**。
  `Renderer.render()` 按 `layout.order` 从上往下画，四块：
  `calendar / weather / digest / quotes`。写漏的补到末尾、写错名的丢掉，不炸。
  - **时钟区住在日历条里**：`draw_calendar()` 拿到实际 `top` 之后才填
    `self.clock_box`。**不要**在别处按 `TOP_GAP` 硬算时钟的 y —— 日历一旦被排到
    第二块，硬算出来的坐标就把精灵图贴到半屏高的空白上。
  - `style.preset` 里也可以带 `layout.order`（「天气优先」「行情优先」就是靠它），
    **预设盖过 config**。config 里那一项只在用不带顺序的预设时才有意义。
  - 改过顺序 = 改过字号，同样要重跑 `make_clock_assets.py`。
- **`clock_region()` / `slack` / `block_boxes` 都由渲染器报，工具不许自己算**。
  尤其余量：`render()` 会把富余摊进各块的内边距，所以"画完之后再量一遍各块高度"
  得到的永远是同一个零头（曾经这么错过一次，报出来 23px，看着像快溢出了）。
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

## 这台 PW3 的系统接口（2026-09-22 实测，别再照旧机型抄）

> 全部来自设备上 `initctl list` / `ls` / 日志实测，见 `extensions/aistatus/system_probe.txt`
> （每次启动重写）。**这套脚本早期是照 kindle-dash（K4 世代）写的，接口对不上，
> 而且对不上时全是静默失败** —— 下面每一条都对应一个"看起来在工作其实没工作"。

- **`/etc/init.d` 存在但是空目录**（`ls -l /etc/init.d/` → `total 0`）。
  所以 `/etc/init.d/framework stop` 在这台机器上返回 **127 = 命令不存在**，
  也就是**原生界面从来没被停过**。用户报的"碰一下就回主界面""系统时钟盖在画面上"
  根因就在这，不是"对抗不了系统刷新"。
  - ⚠️ 那行日志以前是**无条件打印**的（"framework 已停止，本进程存活"），
    它只证明我们自己活着，不证明界面停了 —— 撒谎撒了几个星期。
    现在改成打印 `initctl status` 的实际结果 + 停前/停后进程数。
- **界面归 upstart 管**，任务名（实测在跑的）：`framework`、`pillow`（书架/桌面，
  就是"主界面"本身）、`statusbar`、`webreader`。
  **`statusbar` 是独立任务** —— 只停 framework 的话顶部那个系统时钟照样刷。
  名单放在 `config.sh` 的 `UI_JOBS`，换机型改那一行，别在脚本里写死。
- **恢复界面也必须用 `initctl start`**。`stop.sh` 以前同样写着
  `/etc/init.d/framework start`（也是 127）：一旦界面真被停掉，
  点「停止信息屏」就会得到一台**没有界面的砖头**，只能长按电源 40 秒。
- **唤醒节点没有 `wakeup_enable`**（那是老内核的写法），只有
  `/sys/class/rtc/rtc0..2/wakealarm`，三个都可写；`rtc0 = max77696`（走系统时间那颗）。
  写法是 `echo 0` 清空 + `echo "+秒数"`（**必须带 `+`**，不带就是绝对时刻）。
  实测改完第一次休眠：「计划 60s，实际睡了 61s」→ **真睡进去了**。
  改之前每次都是"找不到节点 → 退化成普通 sleep 循环 → 整夜 CPU 醒着"，
  **一晚掉 60% 电就是这个**，跟贴不贴时钟无关。
- `/sys/power/state` = `standby mem`；**没有** `autosleep`。
- 设备上 **`sshd` 在跑**（`initctl list` 里 start/running）。WiFi IP 由探针打印。
  **以后调试优先走 SSH**：USB 插拔本身会把信息屏进程硬杀掉（不走 cleanup，
  日志里连"退出"都没有），拿不到现场。
- 待查：**心跳算出来 60s，配置应该封顶 600s**。`首次休眠实测` 那行已经带上
  `睡前时刻` 和 `next_image_at`，下次一看就知道是算错还是被夹。

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

### 「帖」版式（`style.layout: poster`，2026-09-22 起为线上版式）

- 设计稿三方向里选中的「三 · 帖」，又按真机反馈迭代了一轮（字太小、下半页空）：
  定稿 = 设计稿「丙 · 疏朗」+ 整组垂直居中，**速览不上屏**（`digest.enabled: false`）。
  实现住在 `render.py` 的 `_poster_body` / `_render_poster`，字阶块距是配平过的整组
  `P_FS_*` + `PG`，**不和条带版的 `FS_*` 共用一张表**。`style.preset` 对 poster 无效。
- **整组内容在 [电量矩形下沿, 页脚上沿] 之间垂直居中；块距写死不随内容伸缩** ——
  开关模块 / 有无预警只平移整组，不缩放，墙上的"地图"一天四次重画之间不跳。
  余量不摊进内边距（条带版照旧均摊，两套规则各自写在代码里）。
  主体高度用 `_poster_body_height()` 在 8×8 草稿上空画一遍量取（同 clock_sprite
  的换画布手法），别改回解析式堆 px —— 堆出来和真画差过 20px。
- 显示字走 `fonts.book_for` 的第三路 `font.display`（本机 Source Han Serif SC Heavy
  = 思源宋体；云端没有就**自动退回粗体黑**，几何不变）。云端有没有宋体：
  `fc-list | grep -i "serif cjk"`。**宋体字面里没有 `°`**：温度的度数符号由
  `_big_num()` 单独用黑体画，照排会出豆腐块。
- **时钟在 poster 里已砍（2026-09 用户决定）**：不留白、不画、不占预算。
  条带版（bands）的时钟逻辑原样保留，切回 bands 仍有时钟。
- 右上角 `POSTER_BATTERY = (814,40,1016,96)` 留给**电量精灵图**（样式 B：
  百分比大字 + 进度条；≤20% 档位自带灰字「请充电」）。电量只有设备自己知道，
  云端画不了 —— 和当初时钟同一条路：图上留白、Kindle 按档位贴图。矩形固定，
  换电量样式不用改版面。链路三件套（2026-09-22 建好）：
  · `tools/make_battery_assets.py` —— 11 张档位图（000/010/…/100）+ `battery.conf`
    （LF 换行、指纹跳过、像素级自检同 make_clock_assets）
  · Kindle `config.sh` 的 `BATTERY_MODE` / `BATTERY_WAVE`；`aistatus.sh` 的
    `stamp_battery()` 在 `show_image` 之后贴。缺精灵图 / 读不到电量只记一次日志
    然后跳过 —— 右上角留白是无害的，不能把整轮刷新搞失败
  · `layout_check.py` 的「电量」检查：battery.conf 坐标 vs `battery_region()`
  没拷精灵图之前右上角就是一块留白，无害。
- `layout_check.py` 认 poster：纵向预算改成渲染后量 `block_boxes` + `slack`，
  条带版的两栏横向检查跳过（poster 所有长字符串走 `clip_text` 兜底）。
  最坏情况（2 预警 / 4 预报 / 3 指数 / 农历干支节气全开）余量 48px，平时 102px。
- 设计稿与规格：`.workbuddy/_directions/index.html`（三方向）、
  `poster2.html`（空间配平三案）、`poster3.html`（电量四案）；
  `tools/design_directions.py` 重跑可得。

### 条带版（`style.layout: bands`，旧线上版式）

日历条 565 + 天气 550 + 行情 221 + 页脚 58 + 顶部 10 = **1404px，余 44px**。
余量均摊成三块的内边距（不堆在底部）。速览默认关闭。

- **余量安全线 40px**。以前是因为"本机雅黑 / 云端 Noto 行高不同"才设的；
  现在图上文字（含时钟精灵图）统一由 `fonts.book_for` 解析，两边是同一套字，
  但改字号/换字体仍会让字形宽度变化，余量还是留着。
- 天气卡片几个尺寸是**被最长内容倒逼**出来的，别凭"看着挺合理"改：
  - 数据格必须 **2 列**（3 列时每格 161px，而「空气 轻度 118」要 220px，
    旧的「轻度污染 118」要 308px，会糊到隔壁格）
  - 左半区两行：**第一行「图标 + 温度」，第二行天气描述**。
    温度和描述**不能并排**（图标 124 + 「29°」203 + 「多云转小雨」220 = 585px，
    而左半区只有 431px）。描述那一行还要再减 `px(28)` 让开数据格 ——
    它和第三行格子在同一条水平带上，顶到 `right_x` 就会和「空气」贴上。
  - **今天的高低在预报条里，不在描述行里**。描述 + 「高30° 低23°」要 510px，
    上限 403px，硬塞的结果是被截成「多云转小雨 · 高3…」（试过，不行）。
  - 预报条的格数 = `weather.days`（**含今天在内**，`forecast[:days]`）。
    4 天时每格 234px，5 天就只剩 186px —— 加天数前先跑自检的"预报 N 格"那几行。
  - 左右分界 `WEATHER_SPLIT = 0.46`
- 空气格用**短等级**（`_air_short()`：「轻度污染」→「轻度」）+ 灰色 AQI 数字。
- **天气图标是纯几何画的，用的是选型页六套里的 E「柔雾 · 灰实心」**
  （六套的参数化定义在 `tools/icon_sets.py`，成品实现只在 `render.Renderer.icon()` 一处）：
  - **整块深灰 `INK_SOFT`(70) 而不是实心黑**。大面积实心黑在墨水屏上刷新残影明显，
    而 70 这一档远看照样成一个实心块面。`icon(fill=...)` 就是这个色阶，
    默认 `INK_SOFT`；预报条显式传 `INK_SOFT`，唤醒页的雾传 `GRAY_LIGHT`。
  - **云的剪影 = 四个圆 + 一段下公切线**（`ICON_LOBES`）。每个圆心都落在底边上方
    正好一个半径处 → 每个圆都与底边相切 → 外公切线就是底边本身，不用解切线：
    圆心依次连线再沿底边收口即可。
    · 更早的"三个椭圆 + 一块矩形垫底"被指名骂过：矩形直边和圆相交处全是直角、
      两端还露在圆外面，看着就是"云底下压一根横杠"。
    · **不能填成凸包**（把上公切线也填了 / 圆心连线之外再补一块）：圆之间的凹口
      才是"云"的识别特征，没了就变成一颗斜鸡蛋。
  - **天体躲在云后面靠白缝**：`_stamp_solid()` 先把云剪影用 `MaxFilter` 胀一圈
    贴白、再贴云体，于是伸进云里的那截被切出一道均匀的白缝。
    （旧的腐蚀描边 + `solid_behind` 那套随 E 一起删了 —— E 是实心，没有描边。）
  - 所有系数都以 `R = size/2` 或 `size` 为单位，所以任何尺寸下比例都一样。
    改形状只改 `ICON_*` 那几个常量，别在 `icon()` 里写死像素。
  - 改图标只看整屏图看不出好坏：跑 `icon_sheet.py` 看单图，
    或跑 `icon_sets.py` 出选型页重新比六套。

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
| `dashboard/tools/layout_check.py` | 纵向预算 + 日历条横向 + **天气卡片横向（含预报条格宽）** + **时钟精灵图同步**，出 5 张预览图 | 否 |
| `dashboard/tools/ui_studio.py` | **调参台**：浏览器里拖模块换顺序、拉字号，每次改动都调真 `Renderer` 重出真图。默认 `127.0.0.1:8100` | 是（抓一次，之后走缓存） |
| `dashboard/tools/icon_sheet.py` | 把天气图标单独铺成对照图（124 / 64 / 40 三档），改图标先看它 | 否 |
| `dashboard/tools/icon_sets.py` | 图标**选型页**：六套参数化风格（A 细线 / B 粗线 / C 实心 / D 双调 / E 灰实心 / F 点线）各铺 11 种天气 × 三档真尺寸，出 `.workbuddy/_studio/icon_sets.html`。绘制函数按"能直接搬进 render.py"的形状写 | 否 |
| `dashboard/tools/design_variants.py` | 把 `STYLE_PRESETS` 每一版渲染出来对比，并报各版的时钟留白区 | 是 |
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
