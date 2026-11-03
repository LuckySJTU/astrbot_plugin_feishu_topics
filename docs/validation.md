# 验证记录

## v0.1.1 — 2026-11-04

| 环境 | 检查 | 结果 |
| --- | --- | --- |
| macOS / Python 3.13.11 / AstrBot 4.29.4 / lark-oapi 1.7.5 | `pytest -q` | 39 passed |
| macOS / Python 3.13.11 / AstrBot 4.28.1 / lark-oapi 1.7.5 | `pytest -q` | 33 passed, 6 skipped |
| Ruff | 静态检查与格式检查 | 通过 |

新版回归覆盖事件总线真正分发前的独立会话构建、富文本引用元数据、按钮卡片的群聊类型、显示文字转换与回调参数保留、按钮回调的来源话题恢复和跨群拒绝。旧版跳过它尚未提供的接口；原有流式、话题、上下文和定时发送测试继续运行。

CI 另运行 AstrBot 4.28.1、4.29.0、4.29.4 / Python 3.12，以及 4.29.4 / Python 3.13，实际结果见 [GitHub Actions](https://github.com/LuckySJTU/astrbot_plugin_feishu_topics/actions/workflows/test.yml)。

所有自动测试只模拟外部网络，使用真实 AstrBot 适配器与 SDK 请求模型。4.29.4 的租户权限、客户端显示和按钮实群交互尚未现场验收。已有话题地址和数据库结构未改变；升级无需迁移。

## v0.1.0 — 2026-09-23

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

使用者已在 AstrBot 4.28.1 内置飞书适配器上反馈确认：话题识别、同群上下文以及流式长回复中的姓名显示正常。这是已测试环境的结果，不代表所有租户和客户端均已验证。

要求真正 @ 时，模型可能调用 `send_message_to_user` 发送正文和 `mention_user`，随后另行生成流式确认。已结合脱敏日志与模拟网络对照确认，这一行为不由姓名转换额外发送消息造成。正式版保留已有姓名转换能力，不扩展流式 @ 提醒。

原生定时工具创建任务时保留的会话目标、媒体实群投递和其他租户权限配置仍需部署者按实际环境验证。上述自动测试中的 Cron 与媒体边界不能替代实群验收。

仓库示例与测试使用虚构的姓名和 ID，不收录真实群聊记录、应用凭证或部署配置。发布 ZIP 使用文件白名单打包。
