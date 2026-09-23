"""Small, reversible compatibility bridge for AstrBot 4.28.1's Lark adapter."""

import copy
import time

from astrbot.api.message_components import At, Plain, Reply
from astrbot.api.platform import MessageType
from astrbot.core.platform.platform import Platform
from astrbot.core.platform.sources.lark.lark_event import LarkMessageEvent
from lark_oapi.api.im.v1 import GetMessageRequest

from .client import client_view
from .identity import Directory
from .routing import Topic, get_field, parse_topic

EXTRA = "feishu_topics"
MISSING = object()


class Bridge:
    def __init__(self, context, config, store, logger):
        self.context = context
        self.config = config
        self.store = store
        self.logger = logger
        self.adapters = {}
        self.patches = []
        self.unique_patch = None
        self.active = True

    def install(self):
        allowed = self.config.get("platform_ids", [])
        for adapter in self.context.platform_manager.platform_insts:
            if adapter.meta().name != "lark" or (allowed and adapter.meta().id not in allowed):
                continue
            if id(adapter) not in self.adapters:
                self._install_adapter(adapter)
        if self.unique_patch is None:
            from astrbot.core.pipeline.waking_check.stage import UNIQUE_SESSION_ID_BUILDERS

            original = UNIQUE_SESSION_ID_BUILDERS["lark"]

            def topic_unique_session(event):
                data = event.get_extra(EXTRA)
                if self.active and data and data.get("topic"):
                    return f"{event.get_sender_id()}%{data['topic'].session_id}"
                return original(event)

            UNIQUE_SESSION_ID_BUILDERS["lark"] = topic_unique_session
            self.unique_patch = (UNIQUE_SESSION_ID_BUILDERS, original, topic_unique_session)

    def _patch(self, obj, attr, value):
        previous = obj.__dict__.get(attr, MISSING)
        setattr(obj, attr, value)
        self.patches.append((obj, attr, previous, value))

    def _install_adapter(self, adapter):
        directory = Directory(
            adapter.lark_api,
            self.logger,
            ttl=self.config.get("name_cache_ttl", 3600),
            timeout=self.config.get("lookup_timeout", 3),
            member_fallback=self.config.get("member_name_fallback", True),
        )
        scope = f"{adapter.meta().id}:{adapter.appid}"
        self.adapters[id(adapter)] = (adapter, scope, directory)
        original_handle = adapter.handle_msg
        original_create = adapter.create_event
        original_send = adapter.send_by_session

        async def handle(abm):
            if not self.active:
                return await original_handle(abm)
            await self.prepare(adapter, scope, directory, abm)
            await original_handle(abm)

        def create(abm):
            event = original_create(abm)
            data = getattr(abm, "_feishu_topics", None)
            if not self.active or not data:
                return event
            event.set_extra(EXTRA, data)
            event.bot = self._client(adapter, scope, directory, abm.group_id, data["topic"])
            return event

        async def send(session, chain):
            if not self.active:
                return await original_send(session, chain)
            topic = parse_topic(session.session_id)
            if topic is None:
                if self.config.get("sanitize_output", True):
                    chain = copy.deepcopy(chain)
                    for part in chain.chain:
                        if isinstance(part, Plain):
                            part.text = directory.display(part.text)
                return await original_send(session, chain)
            if session.message_type != MessageType.GROUP_MESSAGE:
                raise ValueError("Topic targets must use GroupMessage.")
            await self.store.resolve(scope, topic.chat_id, topic.root_id)
            client = self._client(adapter, scope, directory, topic.chat_id, topic)
            await LarkMessageEvent.send_message_chain(chain, client, reply_message_id=topic.root_id)
            await Platform.send_by_session(adapter, session, chain)

        self._patch(adapter, "handle_msg", handle)
        self._patch(adapter, "create_event", create)
        self._patch(adapter, "send_by_session", send)
        self.logger.info("[FeishuTopics] 已接入飞书适配器 %s", adapter.meta().id)

    def _client(self, adapter, scope, directory, chat, topic):
        async def sent(message_id, text, update):
            if not chat or not self.active:
                return
            try:
                await self.store.record(
                    scope=scope,
                    chat=chat,
                    message_id=message_id,
                    root=topic.root_id if topic else "",
                    sender=adapter.bot_open_id,
                    name=adapter.bot_name,
                    role="assistant",
                    text=text,
                    sent=time.time(),
                    update=update,
                )
            except Exception as exc:
                # A successful API send must never become a retry because history storage failed.
                self.logger.error(
                    "[FeishuTopics] 消息已发送，但记录上下文失败：%s", type(exc).__name__
                )

        return client_view(
            adapter.lark_api,
            directory,
            in_thread=topic is not None,
            sanitize=self.config.get("sanitize_output", True),
            on_sent=sent,
        )

    async def prepare(self, adapter, scope, directory, abm):
        raw = abm.raw_message
        for mention in get_field(raw, "mentions", []) or []:
            directory.remember(
                get_field(get_field(mention, "id"), "open_id", ""),
                get_field(mention, "name", ""),
            )
        directory.remember(adapter.bot_open_id, adapter.bot_name)
        directory.remember(abm.sender.user_id, abm.sender.nickname)
        abm.sender.nickname = await directory.resolve(abm.sender.user_id, abm.group_id)
        for part in abm.message:
            if isinstance(part, At) and str(part.qq).startswith("ou_"):
                directory.remember(str(part.qq), part.name)
                part.name = await directory.resolve(str(part.qq), abm.group_id)
            elif isinstance(part, Reply) and str(part.sender_id).startswith("ou_"):
                part.sender_nickname = await directory.resolve(str(part.sender_id), abm.group_id)
                if part.message_str:
                    part.message_str = directory.display(part.message_str, hide_unknown=False)
        abm.message_str = directory.display(abm.message_str, hide_unknown=False)
        topic = None
        if abm.group_id:
            mode, name = await directory.chat_info(abm.group_id)
            if name and abm.group:
                abm.group.group_name = name
            topic = await self._topic(adapter, scope, abm, mode)
            if topic:
                await self.store.save_topic(scope, topic, abm.message_str or "[非文本消息]")
                abm.session_id = topic.session_id
            await self.store.record(
                scope=scope,
                chat=abm.group_id,
                message_id=abm.message_id,
                root=topic.root_id if topic else "",
                sender=abm.sender.user_id,
                name=abm.sender.nickname,
                role="user",
                text=abm.message_str or "[非文本消息]",
                sent=abm.timestamp,
            )
        abm._feishu_topics = {"scope": scope, "chat": abm.group_id, "topic": topic}

    async def _topic(self, adapter, scope, abm, mode):
        raw = abm.raw_message
        thread = get_field(raw, "thread_id", "") or ""
        if (
            not thread
            and mode != "topic"
            and abm.group_id not in self.config.get("topic_chat_ids", [])
        ):
            return None
        root = get_field(raw, "root_id", "") or ""
        parent = get_field(raw, "parent_id", "") or ""
        if not root and thread:
            known = await self.store.find_thread(scope, abm.group_id, thread)
            if known:
                return known
        if not root and parent:
            import asyncio

            request = GetMessageRequest.builder().message_id(parent).build()
            response = await asyncio.wait_for(
                adapter.lark_api.im.v1.message.aget(request),
                self.config.get("lookup_timeout", 3),
            )
            items = response.data.items if response.success() and response.data else []
            if not items or items[0].chat_id != abm.group_id:
                raise ValueError("无法验证话题根消息；请检查消息读取权限。")
            root = items[0].root_id or items[0].message_id
        return Topic(abm.group_id, root or abm.message_id, thread)

    def event_context(self, event):
        entry = next(
            (
                self.adapters[id(adapter)]
                for adapter in self.context.platform_manager.platform_insts
                if adapter.meta().id == event.get_platform_id() and id(adapter) in self.adapters
            ),
            None,
        )
        if not entry:
            return None
        adapter, scope, directory = entry
        data = event.get_extra(EXTRA)
        if data:
            return adapter, scope, directory, data["chat"], data["topic"]
        # Native scheduled jobs use CronMessageEvent and preserve the original UMO.
        if event.get_platform_name() == "cron":
            topic = parse_topic(event.session_id)
            if topic:
                return adapter, scope, directory, topic.chat_id, topic
        return None

    def uninstall(self):
        self.active = False
        for obj, attr, previous, installed in reversed(self.patches):
            if getattr(obj, attr) is installed:
                if previous is MISSING:
                    delattr(obj, attr)
                else:
                    setattr(obj, attr, previous)
            else:
                self.logger.warning("[FeishuTopics] %s 已被其他插件包装，保留其包装。", attr)
        if self.unique_patch:
            registry, original, installed = self.unique_patch
            if registry.get("lark") is installed:
                registry["lark"] = original
        self.patches.clear()
        self.adapters.clear()
