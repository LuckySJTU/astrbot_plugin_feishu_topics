# 兼容性与实现依据

核对日期：2026-09-23。基线为 [AstrBot v4.28.1](https://github.com/AstrBotDevs/AstrBot/releases/tag/v4.28.1)，源码提交 `ab42c0d9b726d82ad0f9563e04c53a4460c00d61`。

## 上游行为与插件对应点

| 上游行为 | 本插件做法 |
| --- | --- |
| `LarkPlatformAdapter.convert_msg()` 仅为私聊查姓名，群聊昵称为 `open_id[:8]` | 在该实例的 `handle_msg()` 前补充姓名；不更改 `sender.user_id`、真实 @ 标识符和管理员检查 |
| `raw_message` 保留 SDK 消息的 `thread_id`、`root_id`、`parent_id` | 使用这些字段识别话题；根消息无 `thread_id` 时用 `chat_mode=topic` 或显式配置 |
| 群 `session_id` 原本是 `chat_id` | 在事件入队前改成 `chat_id~ft~root_message_id`，保留真实 `group_id` |
| `unique_session=True` 会在唤醒阶段重新生成 Lark 会话 ID | 对会话构建表中 `lark` 一项做可恢复的包装，仅对携带本插件话题元数据的事件保留后缀 |
| 回复入口 `_send_im_message()` 写死 `reply_in_thread=False` | 为每个事件的 `bot` 提供 SDK 代理视图，复制请求并设置 True；不修改共享客户端或 SDK 全局类 |
| `send_streaming()` 创建 CardKit 卡片，再通过相同的 IM reply 入口投递 | 初次卡片投递同样设置话题内回复；后续 `card_element.acontent()` 按累计文本替换显示姓名 |
| `send_by_session()` 仅理解群/私聊地址 | 仅在存在 `~ft~` 时解析并校验持久化话题，再复用原有消息链转换与附件发送逻辑 |
| `CronMessageEvent.send()` 调用标准 `context.send_message()` | 定时任务保存原 UMO 后自然经过相同路由；不新增独立调度服务 |
| 原生 cron 直接 `build_main_agent()`，不经过普通消息的 `on_llm_request` | 另用 `on_agent_begin` 为 cron 添加临时群上下文 |

来源：[Lark 适配器](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.1/astrbot/core/platform/sources/lark/lark_adapter.py)、[Lark 事件与流式卡片](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.1/astrbot/core/platform/sources/lark/lark_event.py)、[唤醒检查](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.1/astrbot/core/pipeline/waking_check/stage.py)、[Cron 事件](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.1/astrbot/core/cron/events.py)、[Cron 管理器](https://github.com/AstrBotDevs/AstrBot/blob/v4.28.1/astrbot/core/cron/manager.py)。

纯 `on_llm_request` 钩子不足以改变事件队列的会话身份，也无法覆盖直接调用主动发送的定时任务，因此必须有一层适配器兼容包装。此包装局限在插件启用的 Lark 实例；卸载时检查包装身份，避免覆盖随后由其他插件安装的包装。

## 飞书协议

`POST /open-apis/im/v1/messages/:message_id/reply` 使用 `om_` 消息 ID；`reply_in_thread=true` 表示在话题内回复。`omt_` 是话题 ID，不能直接代入这个路径。接收事件含 `thread_id` 时说明属于话题，单有 `root_id` 不能证明它是话题——普通引用回复也会有根消息。

飞书 SDK builder 同时设置 `request_body` 和用于序列化的 `body`。代理复制后同步这两个字段，测试检查发送出去的请求体，而不只检查局部变量。

发送失败会保留失败状态，不创建替代话题。路由必须属于当前机器人 App ID 和群的已记录话题；已失效的目标由飞书 API 返回错误。

## 流式姓名转换

AstrBot 的 CardKit 更新内容是累计文本，而不是单个 token。因此每次在累计文本上做替换，能够正确处理 `ou_` 与后续字符分属不同 chunk 的情况。尚未解析完整的 ID 暂显示“未知成员”，不等待整条回答生成完毕。已知 ID 拼完整后更新为真实姓名。真正的 @ 标签属性和结构化 user_id 不被替换。

真实 API 的鉴权、客户端渲染、租户权限、卡片限流和消息可见性只能通过实群测试验收。离线测试通过不能代表这些均已通过。
