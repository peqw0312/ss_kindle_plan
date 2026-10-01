# 自动化任务执行记录 · Kindle 云端切流验证

任务：复查 Kindle AI 信息屏是否已切到腾讯云并完成一次完整刷屏上报。

## 2026-10-01 22:41 —— 第一次执行（重启之后）

**结论：❌ 尚未成功上报。** 服务端全绿，阻塞点在 Kindle 侧进程没跑住。

四项检查实测：
1. `docs/device_state.json` 的 `ok/hash/src/at` **全空**，`seen`/mtime 均停在 21:48:30。
2. `/state`：`device.hash` 空串、`device.same` false。`device_seen` 被 22:19:30 的浏览器
   请求（带 `?t=`）污染，不可信。
3. `/status`：`can_build.ok=true`、`last_error=null`、五项凭据全 true → **服务端无问题**。
4. `journalctl --since -25min` 只有 22:19:30 一条**带 `?t=`** 的 `/dashboard.png`（浏览器）；
   **不带参数的 Kindle 请求 0 条**。

关键时间线：Kindle 最后一次无参数取图 = 22:12:18；`/poll.json` 最后一次 = 22:28:29
（唯一证明设备活过 22:19 的证据，且说明新 config.sh 确实被读到）；`/report` 从未带 hash。

根因：`aistatus.sh` 主循环第一句就是 `refresh`，启动必然取图 → 不取图 = 进程没稳定跑起来。
候选：残留 `.stop` 让 `secure_sleep` 立刻返回退出 / 拔 USB 走 trap 硬杀 /
KUAL 停止→启动之间 `initctl` 连坐。

**已产出**：
- `guide/07-云端部署交接文档.md`：重写 §8.4（时间线 + 根因 + 下一步）、更新顶部一句话现状、
  「Kindle 侧」状态表（🟡 → 🔴）、待办 #1。
- `.workbuddy/memory/2026-10-01.md`：追加「补充（同日 22:41，自动化复查）」一节。
- `.workbuddy/memory/MEMORY.md`：按系统要求清理超长（1441 行 → 约 270 行，去掉 serve.py
  单机那一代整节），并新增两条判据（`device_seen` 会被浏览器污染、`/report` 带 hash 才算走通、
  主循环第一句是 refresh 所以没取图就是进程没起来）。

**下次执行该看什么**：`device_state.json` 的 `hash` 是否非空；日志里有没有**不带 `?t=`** 的
`GET /dashboard.png`，以及紧随其后的 `GET /report?ok=1&hash=…`。
若仍为空，直接建议走 SSH 看 `aistatus.log`（USB 插拔会硬杀进程，拿不到现场）。
