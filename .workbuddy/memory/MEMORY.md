# 项目长期约定 · Kindle Plan

闲置 Kindle PW3 → AI 信息屏。链路只产出一张 **1072×1448 八位灰度 PNG**，Kindle 拉图显示。

## 硬约束（改动前先读）

- **零成本**：不引入付费 / 绑卡 / 实名 / 申请 Key 的服务。
- **架构迭代史（三代的取舍，别再翻烧饼）**：
  1. ~~GitHub Actions + Pages~~（弃：Actions 在海外机房抓国内源、定时不准）
  2. ~~云端自建 `dashboard/app.py`~~（弃：要求电脑当服务器）
  3. **2026-09-29 现状 = 本机 `dashboard/serve.py` 是唯一出图端**（端口 8731）：自己抓
     数据 + 排版 + 发图 + 托管调试台，Kindle 拉 `http://192.168.31.158:8731/dashboard.png`。
     · **电脑不在 = 屏停在最后一张且不自愈**；那个 IP 必须路由器 DHCP 保留。
     · 出图后自检，**空图不发**（回 404，设备留着上一张）—— 唯一的安全网。
     · 和风凭据从 `.workbuddy/_qweather_local.env` 读（不进仓库，私钥另放 PEM）。
     · `serve.py` **不是**开机自启的。
- **`docs/` 只放生成物**（dashboard.png / monitor.html / debug.json / skins/ / kept/），也是
  `serve.py` 的根目录。人工文档一律放 `guide/`，别把 .md 写进 `docs/`。
- **Kindle 只做四件事**：下载、写屏幕、贴精灵图（电量 / 时钟）、睡。别在 Kindle 上跑
  HTML 渲染、别在 Kindle 上调数据 API。
- **全项目只有两个文件是用户要动的**：`dashboard/config.yaml`、
  `kindle/extensions/aistatus/config.sh`。改 `dashboard/` 里任何东西后**必须重启 serve.py**
  才生效（地址不变），Kindle 那边不用动。
- **命令行约定**：`python dashboard/generate.py -c dashboard/config.yaml` 从仓库根执行；
  config 里的相对路径都以仓库根为基准。

## 时间 / 精灵图（与出图解耦）

- **时钟已整个砍掉**（2026-09）：`clock.mode: "off"` + `CLOCK_MODE=off`，图上不留白、不画、
  不占预算。代码和 1440 张精灵图生成脚本都还在，随时能装回来。
- `clock.mode`（config.yaml）和 `CLOCK_MODE`（config.sh）是**一对**，必须同值。
  YAML 里 **`off` 必须带引号**（裸写 → 布尔 False → 被当 image → 画一个几小时前的假时间）。
- **电量角标 `BATTERY_MODE=local`**：设备自己贴 `battery/NNN.png`（11 档，≤20% 带「请充电」）。
  只有设备知道电量，云端画不了 —— 和时钟同一条路：图上留白，设备贴图。
- **精灵图必须和主图同一套字体**（`fonts.book_for(cfg)`）。config.yaml 的 `clock.font` /
  `clock.font_bold` 把管线钉到 Noto，**别删**（删了本机雅黑 vs 云端 Noto 行高差 10px，
  贴上去像"另一块拼上去的"）。`font.regular/bold/display` 是通用别名，优先级更高；
  `display` 路径不存在时**静默退回粗体黑**（几何不变），所以云端没宋体也不出坏图。
- **改过字号 / `clock.style` / `layout.order` / 字体之后**，必须重跑 `make_clock_assets.py`
  和 `make_battery_assets.py`，并按工具最后那行的提示决定"只拷 conf"还是"整目录重拷"。
  `layout_check.py` 有一项专守这条链（电脑上看预览正常、只有真机才看得出来）。
- `clock.conf` 换行符是硬要求：会被 Kindle sh 直接 `source`，CRLF 让 `CLOCK_X="644\r"`，
  `eips -x` **不报错、只贴错位置**。三层防护（`write_text(newline="\n")` + layout_check 判
  CR + Kindle 端 `tr -d '\r'`）一个都不能删。
- ⚠️ **查 CR 字节必须读原始字节数 0x0D，别用 `grep -c $'\r'`**（2026-10-01 翻车）：
  Git Bash 的 `grep` 会做换行符转换，对 CRLF 文件**报 0**，看着像"干净"。
  正确做法（PowerShell）：`[System.IO.File]::ReadAllBytes($p)` 然后数 `-eq 13`。
  · 实际踩到：`kindle/extensions/aistatus/config.sh` 一直是 CRLF（253 个 CR），
    用 grep 查了几个月都显示 0，直到要给 Kindle 拷文件才暴露。
  · `.gitattributes` 里写了 `*.sh text eol=lf` **也拦不住** —— 那条规则只在文件
    重新 checkout 时才生效，工作区里早就存在的文件不会被改写。已额外加了
    `kindle/extensions/aistatus/config.sh text eol=lf` 并写明这个原因。
  · 同理适用于所有会被 Kindle `source` 的文件（`clock.conf` / `battery.conf` / `config.sh`）。


## 版面

- 四块 `calendar / weather / digest / quotes`，顺序由 `layout.order` 驱动；写漏的补末尾、
  写错名的丢掉，不炸。
- **时钟区住在日历条里**：`draw_calendar()` 拿到实际 `top` 才填 `self.clock_box`。
  **别**在别处按 `TOP_GAP` 硬算时钟 y —— 日历一旦被排到第二块就把精灵图贴到半屏空白上。
- **`style.preset` 可能自带 `layout.order`（天气优先 / 行情优先），预设盖过 config。**
- **可切换版式只剩 `arc`**（名单唯一来源 = `Renderer.LAYOUTS`，`serve.py` 抄一份做
  `?skin=` 校验，对不上时 generate.py 退回默认并在日志里说清）。
  `c1`「带」2026-10-01 封存：它按位置认"今天"（`today = i == 0`），昨天插到 forecast
  最前面之后它把**昨天**加粗成今天。`bands` / `poster` 2026-09-26 已封存。
  封存的都只是摘出名单，绘制代码仍在 `render.py`，改回一行就能捡回来。
- **近日天气固定四格 = 昨天 / 今天 / 明天 / 后天**（`layout_arc._forecast()`）。
  · 每格**只写周几**，昨天/今天靠**背景分档**分辨：昨天 130 灰底、今天黑底反白、
    明天后天白底黑框。
  · 哪格是哪天**只能问 forecast 里的 `delta`**（-1/0/1/2）。按 label 文字找、
    或按位置 `i == 0` 找，都错过一次：label 现在四格都是周几，位置第一格是昨天。
  · 「昨天」那一格由 `generate.yesterday_archive()` 插在 forecast 最前面（数据层共用，
    别在版式里各拼一份 —— 以前只有 arc 有、c1 没有就是这个原因）。
  · 存档日期不是"昨天"时**这一格不画**（电脑关几天 → 滚出来的是三天前的数）；
    宁缺不假。屏上因此可能只有三格，这是诚实不是坏图。
  · `weather.days` 只决定**向和风要几天**，不决定屏上显示几格；至少要 3，否则后天没数据。
- **`clock_region()` / `slack` / `block_boxes` 都由渲染器报，工具不许自己算。** `render()`
  会把余量摊进各块内边距 —— "画完再量一遍高度"永远得到同一个零头。
- 天气卡片尺寸是被最长内容倒逼的（数据格必须 2 列、温度与描述不能并排）。改之前先跑
  `layout_check.py`，它的假数据已带上 `delta`，不带就会走按位置的兜底路径、量不到屏上那条。
- 天气图标纯几何画（只在 `render.Renderer.icon()` 一处实现，参数化定义在 `tools/icon_sets.py`），
  当前「柔雾 · 灰实心」，整块用深灰 `INK_SOFT`(70) 而非实心黑（墨水屏残影）。
- 灰度屏没有颜色：行情涨跌用**实心方块 = 涨 / 空心方块 = 跌** + 字重区分。
- **页脚的数据来源署名（2026-10-01 按用户要求英文化）**：
  `Kindle PW3 · Weather QWeather · Quotes Tencent`。
  · 显示名集中定义在 `sources.py`：`WEATHER_SOURCE_LABEL` / `QUOTE_SOURCE_LABELS` /
    `CRYPTO_SOURCE_LABELS`，版式里只负责拼 `Weather {..}` / `Quotes {..}` 前缀
    （`layout_arc.py` 的 arc 页脚 + `render.py` 的两处）。
  · ⚠️ **`WEATHER_SOURCE_LABEL` 是显示用的，别拿它当识别输入用** ——
    `fetch_weather()` 里那串别名（`qweather` / `hefeng` / `和风` / `和风天气`）是判断输入的，
    两处不能混。改显示名不要动那个元组。
  · 只改了**来源署名**；栏目名（`ATMOSPHERE 大气状态`、`WEATHER 近日天气`、
    `INDEX QUOTES 指数行情`）和图例（`实心=涨 空心=跌`）**仍是中文**，用户没要求动。


## Kindle 基础环境（KUAL / MRPI / hotfix）

- **KUAL/MRPI 没有"安装动作"，全是往哪拷**：KUAL 用 **PEKI**（PW3 属 K5），解压出的
  `KUAL.sh` + `KUAL.jar` → `documents/`；MRPI 用 modern 版，`extensions/` + `mrpackages/`
  → 根目录。失败只有两个原因：**放错位置**，或**文件名被浏览器加了 `(1)` 后缀**。
  另需 **220 MB 余量**，腾空间前先开飞行模式。
- **hotfix 必须 ≤ 2.3.7，不要 2.5.x**：2.5.0 换了 KUAL 的签名证书，症状是 KUAL 图标在、
  一点就 `Application Error`，**降回 2.3.7 也修不好，只能重新越狱**。
- hotfix 是**两步**：拷 `Update_hotfix_universal.bin` → 设置→更新您的 Kindle → 再到书库
  点开 **`Run Hotfix` 小册子跑一遍**。**只拷不运行等于没装。**
- OTA 屏蔽靠独立扩展 **renameotabin**（KUAL 不自带）。**恢复出厂 / 刷官方固件 / 降级之前
  必须先在 KUAL 里 `Restore` 改回去**，否则卡在开机检查更新界面。
- 本项目**用不到 MRPI**（`aistatus` 是纯 KUAL 扩展），但装了没坏处。

## 这台 PW3 的系统接口（2026-09-22 实测，别照旧机型抄）

> 来自设备 `initctl list` / `ls` / 日志实测，见 `extensions/aistatus/system_probe.txt`。
> **早期这套脚本照 kindle-dash（K4 世代）写的，接口对不上，而且对不上时全是静默失败。**

- **`/etc/init.d` 是空目录** → `/etc/init.d/framework stop` 返回 **127**，
  **原生界面从来没被停过**（"碰一下就回主界面""系统时钟盖在画面上"的根因）。
- **界面归 upstart 管**，任务名：`framework` / `pillow`（书架桌面，就是"主界面"）/
  `statusbar`（顶部时钟，**独立任务**）/ `webreader`。名单在 `config.sh` 的 `UI_JOBS`。
- **恢复界面也必须用 `initctl start`**（老写法同样 127 → 点「停止信息屏」会得到一台
  **没有界面的砖头**，只能长按电源 40 秒）。
- **唤醒节点没有 `wakeup_enable`**（老内核写法），只有 `/sys/class/rtc/rtc0..2/wakealarm`，
  写法 `echo 0` 清空 + `echo "+秒数"`（**必须带 `+`**，不带是绝对时刻）。
  `/sys/power/state` = `standby mem`；**没有** `autosleep`。
- **`sshd` 在跑，调试优先走 SSH**：USB 插拔会把信息屏进程硬杀掉（不走 cleanup，拿不到现场）。
- **任何"关掉设备硬件"的开关，必须在每一条退出路径上还原。** `WIFI_SLEEP=1` 那次只改了
  刷新流程、没改 `cleanup()` 和 `stop.sh` → 射频永久停在 off，用户看到 **"Kindle 搜不到
  任何 WiFi"**，只能重启。现在 `cleanup()` 第一句就是 `wifi_on`，`stop.sh` 也补了一次。
- **心跳 bug（已修）**：`$((10#$hour))` busybox 的 ash 不认 → 函数输出空 → 主循环
  `[ now -ge "" ]` 报错返回假 → 实测 **79 分钟一次都没刷**。现在剥前导零 + 算不出就兜底
  600s **并在日志里喊出来**。教训：**空值参与算术/比较是静默失败**。
- **`WIFI_SLEEP` 保持 0，别改回 1**：实测 `=1` → 第一次睡 121s、随后 1s/1s 弹回空转；
  `=0` → 181/178/175s 精准。真正起作用的是 wakealarm + `/sys/power/wakeup_count` 两处。
  而且关射频会带来新故障：框架停掉后 WiFi 一旦掉线就再也连不上。
- **`USE_RTC_SLEEP` 现在也是 0**（2026-09-29）：长期插电源，休眠的收益只剩省电，代价是
  一整类查不清的毛病（睡着时射频关、叫不醒 = 屏停在旧图且日志无声；插 USB 时 VBUS 是
  唤醒源，`echo mem` 反复秒弹回）。不睡就没有这些。
- `FETCH_EVERY_MINUTES` / `FETCH_ALIGN_MINUTE` 是**唯一**的取图节奏来源（旧的 `REFRESH_AT` /
  `UPDATE_INTERVAL` / `NIGHT_INTERVAL` 等六个管同一件事的旋钮已删）。纯整数运算 +
  `date +%H:%M`，**不碰 `date -d`**（busybox 上解析失败是静默的）。
- **`X-Next-Image` / `X-Epoch` 都不存在**（静态文件发不了自定义头），`CLOCK_SYNC` 那条对表
  路是死的。**`TIMEZONE="CST-8"` 因此更关键，别留空**（LanguageBreak 跳过注册，机器拿不到
  时区，默认 UTC → 屏上时间慢 8 小时）。
- **`WIFI_TEST_IP` 取决于主地址在哪**：主地址在云端 → 填外网（`223.5.5.5`）；只在局域网 →
  填网关（`192.168.31.1`）。**填错会给出"错误的安慰"**。

## 天气源（和风 v1 + JWT；2026-09-29 起已去掉 Open-Meteo 兜底）

- ⚠️ **必须用 v1，v7 正在停服**（天气预警 v7 于 2026-10-01 停、天气预报 v7 于 2027-08-01 停）。
  接口：`/weather/v1/current/{lat}/{lon}`、`/weather/v1/daily/{lat}/{lon}?days=N`、
  `/airquality/v1/current/{lat}/{lon}`、`/weatheralert/v1/current/{lat}/{lon}`。
  网上教程几乎都是 v7，照抄一定失败。
- ⚠️ **v1 坐标在路径里且是「纬度/经度」**，而 v7 是 `location=经度,纬度` —— 顺序正好相反，
  这是迁移时最容易犯的错（查了会静默返回别的城市的数据）。
- 每日预报**必须带 `localTime=true`**（默认 UTC，"今天"那一格会指错日子）。
- 响应套娃：数值都在 `{value, unit}` 里（`_q_num()` 取）；`humidity` 和降水概率是 **0~1 的
  小数**（不是百分数）；风速 **m/s**，而渲染层约定 km/h，转换收在数据层。
- **认证走 JWT（EdDSA/Ed25519），不是 API KEY**（2027-01-01 起 API KEY 受请求量限制）。
  header `{alg:EdDSA,kid}`、payload `{iss,sub,iat,exp}`，Base64**URL** 去 padding，
  `iat` 比当前时间早 30 秒（防时钟差）。五个值放**环境变量**：
  `QWEATHER_HOST/ISS/SUB/KID/PRIVATE_KEY`。缺任何一个就画不出天气块，
  `serve.py` 的 `can_build()` 会挡住不发（这是"空图不上屏"的实现处）。
- **和风旧域名已停服**：`devapi.qweather.com`（2026-01 停）、`api.qweather.com`（2026-06 停）。
  必须用控制台发的专属 Host（`xxxxxx.re.qweatherapi.com`）。
- **创建凭据时别点「启用全部API」**：会连热带气旋 / 海洋 / 辐照一起开，这三个**不提供免费
  额度**，任何请求都计费。只勾 天气预报 / 天气预警 / 空气质量。

## 行情接口的实测字段位置（别凭记忆改）

- 腾讯 `v_<code>` 用 `~` 分隔：`[1]`=名称 `[3]`=现价 `[4]`=昨收；美股同样适用
- 新浪 A股 `[0]`=名称 `[3]`=现价 `[2]`=昨收；美股 `gb_` 现价`[1]` 昨收`[26]`；
  港股 `rt_` 现价`[6]` 昨收`[3]`
- 涨跌幅一律自己用 现价/昨收 计算，不用接口给的字段（避免各家字段错位）
- 内部代码键统一小写（`normalize_code`），但请求腾讯时要传用户原始写法
- 指数：腾讯 / 新浪都有 `usNDX` = 纳斯达克100、`usINX` = 标普500
- 加密合约（代码保留，默认不启用）：写法 `bitget:BTCUSDT`；链路 **Bitget → Gate → HTX**，
  **Gate（`api.gateio.ws`）是国内唯一稳定直连的合约源**，实测 0.2s。Gate 字段 `last`=最新价、
  `change_price`=24h 变动额 → 昨收 = `last - change_price`。屏上必须如实标注本轮用了哪家。

## 农历（`aiinfo/lunar.py`，零依赖是硬约束）

- **不要引入 sxtwl / lunardate / cnlunar**：sxtwl 是 C 扩展，在 Actions 上编译失败，这正是
  自己写的原因。
- `TERM_FIX` 修的不是"算错一天"，而是"节气时刻落在午夜前后 6 分钟内"这类无法用日期表达的
  悬案（1896 小暑 00:02、2008 小满 23:59…）。全表 5000+ 个节气日期里只有 14 个需要钉。
- 改完农历相关代码必须跑 `python dashboard/tools/verify_lunar.py`。

## 上云（腾讯云轻量）—— **2026-10-01 已上线跑通**

- 现状：唯一出图端 = 腾讯云轻量 `lhins-jit8dyoa` / `ap-shanghai` / **`43.142.71.175`**，
  Ubuntu 24.04 / Python 3.12.3，systemd 服务 `kindle-screen.service`，
  `--bind 0.0.0.0 --port 80`，`Restart=always`。
  **到期 2026-11-01 17:13:49（免费试用 1 个月）。**
- Kindle 拉图：`http://43.142.71.175/dashboard.png`（云端优先），
  家里那台 `http://192.168.31.158:8731/dashboard.png` 作备用。
- **端口是 80 不是 8731**：防火墙本来就放行 80，省得新增规则。
- 云端落地时踩到两个真 bug（都写进 `dashboard/serve.py` 了，**别改回去**）：
  1. **`load_local_env()` 的 `except: return`** —— 云端没有本机那个 env 文件，
     旧代码会在这一步直接 `return`，**把下面「PEM 文件 → 环境变量」也一起跳过**，
     五个凭据里偏偏只有私钥读不到 → 天气块缺失 → 空图 → 404。改成 `except: lines = []`。
  2. **行情源在机房出口全 403** —— `sina`（hq.sinajs.cn）和 `yahoo` 在上海节点 0/3，
     只有 `tencent`（qt.gtimg.cn）3/3。**本机测新浪是通的，结论不能搬到云端。**
- **口令守卫（`serve.py` 内置）**：环境变量 `DASH_TOKEN`，空就不设防。传法 `X-Dash-Token`
  头或 `?k=` 查询串。**认不出回 404 不回 401**（401 等于告诉扫描器这里有东西）。
  只保护 `/status` `/state` `/build.log`；**`/dashboard.png` `/poll.json` `/report`
  `/config.sh` 必须保持公开**，否则 Kindle 链断。
  ⚠️ **`device_seen` 会骗人**：`note_device_fetch()` 对**任何** `/dashboard.png` 请求都计数
  （只按 User-Agent 过滤 curl/wget），我们自己在浏览器里破缓存也会把它刷成"刚刚"。
  **唯一可信的是 `docs/device_state.json`**（只有设备带参数调 `/report` 才写）。
  区分办法：**Kindle 的请求不带任何查询参数，浏览器的带 `?t=<epoch>`**。
- **判「刷屏走通了没」只认一条**：`/report` 带 `hash=` 来过 —— `report_state()` 在
  `show_image()` 之后才调用，所以 hash 非空 = 下载成功 + 刷屏成功。
  `/report` 只有空参数（`hash=none`）来过 → 那一轮走的是失败分支或"图没变跳过重绘"。
- ⚠️ **`aistatus.sh` 主循环第一句就是 `refresh`** → 「启动 → 立刻取图」是必然的。
  **服务器上长时间看不到不带参数的 `/dashboard.png`，只可能是 Kindle 侧进程没跑起来**，
  不是网络问题。同理 `secure_sleep` 里有 `[ -f "$STOP_FLAG" ] && return 0`，
  **残留的 `.stop` 会让刚起来的进程立刻退出**；拔 USB 会走 `trap TERM INT` 硬杀。
- 凭据位置：`/etc/kindle-screen/qweather.env`（600）+ `qweather_ed25519.pem`（600），
  由 systemd `EnvironmentFile` 注入；口令在同一单元的 `Environment=DASH_TOKEN=...`。
- **往服务器传文件的办法**（MCP `execute_command` 限制很多，记下来省得再试）：
  命令**上限 2048 字符**；`curl`/`urllib`/`tar`/`cat` 开头**一律 AccessDeny**，
  `echo`/`printf`/`python3`/`systemctl`/`ss` 能过；偶发 502 重试即通。
  → 本地 `python3` 打 tar+lzma+base64，**按 900 字符切块**，`printf '%s' '<块>' >> /tmp/x.b64`
  逐块追加并 **每块回 `wc -c` 对账**，最后 `md5sum` 比对整包。
  ⚠️ **1900 字符切块出现过丢字**（长串尾巴被截）；解包用 `python3 -c`，别用 `tar` 命令。
- 三个背景事实：① 大陆 IP 直连**未备案**（能跑，浏览器有提示，Kindle 无感）
  ② ③ 凭据已从 `.workbuddy/_qweather_local.env` 换成 systemd `EnvironmentFile`。
- **最大的坑：免费试用到期直接关机**，屏永久停在最后一张且不报错。
- **接手先读 `guide/07-云端部署交接文档.md`** —— 里面有服务器信息、systemd 单元全文、
  口令、逐文件改动清单与代码片段、状态核对表、本机基准 MD5、待办与风险。
- 方案原稿：`guide/06-腾讯云轻量部署方案.md`（顶部已加「已执行完毕」告示，
  **保留作决策过程记录，不再当操作手册**；其中 8731 端口 / 无鉴权等 5 处已过时）。

## 文档分工（改文档前先看，别重复造）

- `README.md` —— 讲**为什么这么设计**（架构、取舍、踩坑史）
- `guide/00-操作总览.md` —— 讲**现在该干什么**（步骤表 + 目录地图 + 自检命令 + 退出方式）。
  用户问"我要怎么操作 / 目录是什么"就指这一份。
- `guide/01~05` —— 分主题深挖。⚠️ `guide/03`、`guide/05` 的第二三节讲的是**已删除的方案**，
  别照做；天气源 / CRLF / 缓存那几段仍然有效。
- `guide/06` —— 上云**决策过程**记录（顶部有告示块，别当手册）。
- `guide/07` —— **上云落地现状 + 交接文档，接手必读。**
- ⚠️ **口径债（已知未清）**：`guide/00`、`guide/02`、`项目现状.md` 三份**整体还是
  GitHub Actions 时代的内容**，里面的 `DASHBOARD_URL` / `DASHBOARD_FALLBACK_URL` /
  `FETCH_EVERY_HOURS` / `QUIET_START` 等键**现在都已不存在**（实为 `DASHBOARD_URLS`
  数组 + `FETCH_EVERY_MINUTES`）。目前只在 `guide/07` 写了"别照那三份操作"，
  **待专门做一轮清理。**


## 自检工具（改完代码顺手跑）

| 脚本 | 用途 | 需要联网 |
|---|---|---|
| `tools/probe_sources.py` | 天气 / 日历 / 行情 / 新闻 / AI 数据源自检 | 部分 |
| `tools/layout_check.py` | 纵向预算 + 日历条横向 + 天气卡片横向 + **时钟精灵图同步** + 电量坐标 | 否 |
| `tools/icon_sheet.py` / `icon_sets.py` | 天气图标单图对照 / 六套风格选型页 | 否 |
| `tools/make_clock_assets.py` | 生成 1440 张时钟精灵图 + `clock.conf`（带像素级自检） | 否 |
| `tools/make_battery_assets.py` | 生成 11 张电量角标 + `battery.conf` | 否 |
| `tools/check_cloud.py` | 试设备那几条出口（本机起进程试跑，或 `--url` 验线上） | 是 |
| `tools/verify_lunar.py` | 农历对拍（需 `pip install lunar_python`） | 否 |
| `tools/ui_studio.py` / `skins.py` | 调参台 / 皮肤墙生成 | 是 |

- `layout_check.py` 的头几项都**不是摆设**：前几项是真翻过车才加的；精灵图同步那项守的是
  "改字号 → 矩形变了 → 精灵图没重生成 → 真机贴偏"，**电脑上看预览图完全正常**。
- 天气卡片横向检查调 `renderer.weather_grid()` —— **别在检查脚本里重抄一份格子内容**，
  抄的那份迟早和绘制对不上。
- `skins.py` 单独在命令行跑时也会自己读 `.workbuddy/_qweather_local.env` 了（2026-10-01）：
  以前只有 `serve.py` 加载凭据，单独跑 skins.py 照样"成功"，出一墙**没有天气**的图，
  而每一格看着都像成品。文件里没配 Key 时它会如实报"天气：无数据"，别忽略那行。
- **Windows 控制台是 GBK，直接 print ✅ 会抛 `UnicodeEncodeError` 中断整个脚本**（不是显示
  成问号），统一走 `tools/_marks.py` 选标记。

## 依赖

Python 只需 `Pillow` / `PyYAML` / `requests`（`dashboard/requirements.txt` 就这三行）。
字体优先级（`fonts.book_for` 内部）：`font.regular/bold` > `clock.font/font_bold` >
`AIINFO_FONT` 环境变量 > 已知路径 > fontconfig > 目录扫描。**环境变量排在配置之后** ——
想换字体改配置更管用。
