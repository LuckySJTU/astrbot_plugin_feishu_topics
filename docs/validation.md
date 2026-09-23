# v0.1.0 验证记录

日期：2026-09-23。

| 环境 | 检查 | 结果 |
| --- | --- | --- |
| macOS / Python 3.13.11 / AstrBot 4.28.1 / lark-oapi 1.7.3 | `pytest -q` | 32 passed |
| macOS / Python 3.13.11 / AstrBot 4.28.1 / lark-oapi 1.4.15 | `pytest -q` | 32 passed |
| Ruff | 静态检查与格式检查 | 通过 |
| 配置 | JSON 语法 | 通过 |

测试直接实例化 AstrBot 官方适配器，将 SDK 接收事件交给真实 `convert_msg()`，使用真实消息链、事件、标准 `Context.send_message()` 和 `CronMessageEvent.send()`。网络接口使用 AsyncMock，无需租户密钥，不发送真实消息。

关键断言包括实际 SDK 请求的 `body.reply_in_thread`、目标根消息 ID、真实 @ 标识符未变、流式累计文本中的姓名、不同话题并发不串线、定时目标重启后仍可解析、未知目标拒绝、同群上下文不跨群、卸载恢复入口。

SDK 会出现其自身的 deprecated API 警告，测试没有以关闭告警的方式隐藏它们。

GitHub Actions 的 Linux / Python 3.12 结果以仓库 Actions 页面为准。

尚待使用者在实际飞书群确认：租户权限是否生效、可见成员姓名、流式卡片显示效果、话题内实际落点、原生定时工具创建任务时保留的会话目标，以及媒体投递。
