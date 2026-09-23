# 飞书姓名与话题增强

为 **AstrBot v4.28.1 的内置 `lark` 飞书适配器**补充真实姓名、话题会话、同群上下文和指定话题主动发送。继续使用原来的机器人配置、连接和凭证。

**v0.1.0 是供实群测试的首版。** 已用真实 AstrBot 和飞书 Python SDK 跑离线集成测试，飞书 HTTP 返回使用模拟数据；尚未在真实租户验收。

## 安装

本仓库为私有仓库。最方便的方式是下载插件 ZIP，然后在 AstrBot 管理面板的插件页选择**上传安装**，安装后重启 AstrBot。发布包不含测试环境或凭证。

- 已提供 ZIP 时，直接上传 `astrbot_plugin_feishu_topics-v0.1.0.zip`。
- 也可以登录 GitHub，在本仓库 `Code → Download ZIP` 下载源码包，再上传安装。
- 在有 GitHub 仓库访问权限的 AstrBot 主机上，也可在 AstrBot 工作目录执行：

```bash
git clone https://github.com/LuckySJTU/astrbot_plugin_feishu_topics.git data/plugins/astrbot_plugin_feishu_topics
```

私有仓库通过 Git 的认证方式访问；不要把访问令牌写进插件配置或提交到仓库。未配置仓库认证时，AstrBot 的“从 URL 安装”通常不能直接下载私有仓库。

启动日志应出现 `[FeishuTopics] 已接入飞书适配器 <你的适配器ID>`。插件默认对所有内置 `lark` 实例生效，可以用 `platform_ids` 限定。

## 飞书权限

在已有飞书应用上检查以下权限，修改后按飞书要求发布版本并生效：

| 用途 | 权限与配置 |
| --- | --- |
| 按用户 ID 查询姓名 | `contact:contact.base:readonly`；姓名字段同时检查 `contact:user.base:readonly`，通讯录数据范围需覆盖对应成员 |
| 通讯录不可见时用群成员姓名补充 | `im:chat.members:read`，或已有 `im:chat:readonly` 等等价权限 |
| 自动识别话题群，包括首条根消息 | `im:chat:read` 或 `im:chat:readonly` 等价权限 |
| 同群各话题的上下文 | 订阅 `im.message.receive_v1`，开启 `im:message.group_msg`；只收到 @ 消息时，上下文也只有这些消息 |
| 在指定话题内发消息 | 现有机器人发消息权限，例如 `im:message:send_as_bot` |
| 查询引用消息或补查根消息 | `im:message:readonly` 或已有 `im:message` 等价权限 |
| 流式卡片 | `cardkit:card:write`，并在 AstrBot 开启流式输出 |

姓名查询失败时会显示“未知成员”，不会猜测真实姓名。已收到的 `@` 事件中若带姓名，也会缓存使用。

权限依据见飞书官方[用户信息](https://open.feishu.cn/document/server-docs/contact-v3/user/get)、[群成员](https://open.feishu.cn/document/server-docs/group/chat-member/get)、[群信息](https://open.feishu.cn/document/server-docs/group/chat/get-2)、[接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)与[回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)文档。

## 五分钟测试

以下示例假设指令前缀为 `/`，使用其他唤醒前缀时相应调整。`ft_bind`、`ft_send` 和 `ft_forget` 需要当前发送者是 **AstrBot 管理员**。

1. 在话题 A 中发送 `/ft_topic`。应看到真实姓名、话题根消息 ID 和主动消息目标。
2. 在话题 B 中发送同一指令。两个话题的主动消息目标应该不同；同一话题中不同楼层的目标应相同。启用 AstrBot“独立会话”后，会额外带发送者前缀。
3. 在 A 中发送 `/ft_bind 日报`，在 B 中发送 `/ft_send 日报 这是一条定向发送测试`。正文应出现在 A 中；B 中只出现命令执行结果。
4. 在 B 中说一条新的测试事实，比如“今天测试数字是 7293”。在 A 中 @ 机器人问“群里另一个话题提到的测试数字是多少？”。前提是机器人已收到 B 的消息。
5. 在 A 中通过 AstrBot 原生定时任务设定“一分钟后在当前话题提醒我检查结果”。确认定时任务的会话目标包含 `~ft~`。到时应回到 A，重启后仍有效。

**流式回复测试：** 开启流式输出，在两个话题同时 @ 机器人要求长回复。两张卡片应各自留在原话题。模型输出中的 `ou_...` 会在每次累计文本更新时转为姓名；ID 尚未拼完整时暂显示“未知成员”，后续更新为已知姓名。结构化 `@` 的底层用户 ID、链接和会话地址保持原值。原生 `/sid` 是专用诊断命令，也保留真实 ID，方便设置管理员。

飞书根消息缺少 `thread_id` 且应用无法查询群信息时，可将该话题群 `oc_...` 填入插件配置 `topic_chat_ids`，再重试。普通群不要填入该项。

## 指令和模型工具

| 指令 | 作用 |
| --- | --- |
| `/ft_topic` | 当前姓名、群、话题、可持久化的发送目标 |
| `/ft_topics` | 当前群最近活跃的 20 个已记录话题、预览、别名和地址 |
| `/ft_bind 日报` | 管理员为当前话题绑定群内唯一别名；已有别名不覆盖其他话题 |
| `/ft_send 日报 正文内容` | 管理员主动向指定话题发送；也可使用 `om_` 根消息 ID 或已记录的 `omt_` 话题 ID |
| `/ft_forget CONFIRM` | 管理员清除此群的插件上下文、别名和话题路由；不删除飞书消息或 AstrBot 自身会话 |

提供两个模型工具：`feishu_list_topics` 查询当前群话题，`feishu_send_to_topic` 按别名/根消息 ID 发送。发送工具仍检查管理员身份，并且只能向**当前群已经记录过的话题**发送。

## 定时任务与其他插件接入

在某个话题中创建原生定时任务时，保存该事件的 `event.unified_msg_origin` 即可。其他插件仍然使用 AstrBot 的标准入口：

```python
from astrbot.api.event import MessageChain

# 触发时保存这个字符串，不要只保存 get_group_id()。
target = event.unified_msg_origin
# 定时任务触发时；target 可以从数据库加载，不需要保留旧 event。
await self.context.send_message(target, MessageChain().message("日报已生成"))
```

示例目标：`feishu:GroupMessage:oc_群ID~ft~om_根消息ID`。实际值请复制 `/ft_topic` 的结果，不要使用示例占位符。`feishu` 是 AstrBot 中配置的适配器实例 ID。

要从话题 B 给 A 设置定时任务，可以先在 A 绑定别名，通过 `/ft_topics` 或 `feishu_list_topics` 获取 A 的完整目标，再将其用于定时任务的发送目标。原生工具如何选择目标仍取决于模型；测试时检查任务保存的目标最可靠。

**安装前已经创建、只含群 ID 的定时任务不会自动迁移。** 它们继续发到群主流，需重新建立或更新目标。第三方插件如果直接调用飞书 HTTP 接口、绕过 `context.send_message()`，也需要自行携带话题目标。

不存在、未登记、跨群、已撤回或失去访问权限的话题会报错，**不会退回到群里新建话题**。插件禁用期间话题目标不可用；再次启用后保留的数据库可继续提供路由。

## 上下文和数据边界

- 每个话题使用独立的 AstrBot 会话。`thread_id` 标识飞书话题，`om_` 根消息 ID 用作稳定路由，避免首条消息和后续回复分裂为两个会话。旧的整群会话历史不会自动拆分迁入；如果你按旧 UMO 单独绑定过人格或配置，也需检查新话题的配置匹配。
- 另存同群近期记录，按话题标注，供模型补充背景。默认每次最多 40 条、12000 字符；每群最多保存 500 条、7 天，单条截取 4000 字符。可通过配置调整。
- 接收记录发生在唤醒检查之前，因此未 @ 机器人但已被飞书推送的消息也能成为背景；不会因此自动触发 LLM 回复。
- 只包含安装后机器人实际收到的消息以及本插件观察到的已发送回复；不会自动回填安装前历史。图片、文件等非文本消息只保留文本摘要或类型占位，不保存媒体本体。
- 群上下文作为临时输入发送给已配置的模型，不反复复制到每条 AstrBot 会话历史。不同群、不同机器人实例/App ID 分开存储。不开启 `share_group_context` 时，只提供当前会话位置。
- 本插件数据在 `data/plugin_data/astrbot_plugin_feishu_topics/topics.sqlite3`。备份时保留整个插件数据目录。姓名缓存有有效期，失败缓存 60 秒。
- 卸载或禁用会恢复本插件覆盖的适配器入口，不修改 AstrBot 源文件。升级 AstrBot 后应重新跑测试；目前声明兼容 `>=4.28.1,<4.29`，实测基线为 4.28.1。
- 首版主要验证内置 Agent。Dify、Coze 等外部 Agent 对临时上下文的使用方式未验证；文件上传、音视频实群投递仍使用上游能力，需要现场验收。

## 开发与验证

Python 3.12 或 3.13：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
pytest -q
```

测试导入真实的 AstrBot 4.28.1 适配器、事件、消息链和飞书 SDK 请求对象，仅模拟网络返回。覆盖姓名权限降级、群成员分页、话题根/回复、多话题并发、普通群与私聊、原生 Cron 事件、流式卡片及回退、跨群拒绝、重载和持久化。GitHub Actions 会运行同一组检查。

实现依据和扩展点见 [docs/compatibility.md](docs/compatibility.md)。实群测试记录建议包含：AstrBot 版本、是否流式、指令、预期与实际落点、脱敏日志。不要提供 App Secret 或访问令牌。
