"""Display-name resolution without changing authentication or mention IDs."""

import asyncio
import re
import time
from collections import OrderedDict

from lark_oapi.api.contact.v3 import GetUserRequest
from lark_oapi.api.im.v1 import GetChatMembersRequest, GetChatRequest

USER_ID = re.compile(r"(?<![A-Za-z0-9_])ou_[A-Za-z0-9_-]+(?![A-Za-z0-9_])")
STREAM_USER_ID = re.compile(r"(?<![A-Za-z0-9_])ou_[A-Za-z0-9_-]*(?![A-Za-z0-9_])")
PROTECTED = re.compile(
    r"(<[^>]+>|https?://[^\s<>]+|[^:\s<>]+:(?:GroupMessage|FriendMessage|OtherMessage):[^\s<>`]+)"
)


class TTLCache:
    def __init__(self, size=5000):
        self.items = OrderedDict()
        self.size = size

    def get(self, key):
        entry = self.items.get(key)
        if not entry:
            return None
        value, until = entry
        if time.monotonic() >= until:
            self.items.pop(key, None)
            return None
        self.items.move_to_end(key)
        return value

    def put(self, key, value, ttl):
        self.items[key] = (value, time.monotonic() + ttl)
        self.items.move_to_end(key)
        while len(self.items) > self.size:
            self.items.popitem(last=False)


class Directory:
    def __init__(self, client, logger, *, ttl=3600, timeout=3.0, member_fallback=True):
        self.client = client
        self.logger = logger
        self.ttl = ttl
        self.timeout = timeout
        self.member_fallback = member_fallback
        self.names = TTLCache()
        self.chats = TTLCache(1000)
        self.member_queries = TTLCache(1000)
        self.name_lock = asyncio.Lock()
        self.chat_lock = asyncio.Lock()

    def remember(self, user_id: str, name: str):
        name = (name or "").strip()
        if user_id and name and not name.startswith("ou_"):
            self.names.put(user_id, name[:100], self.ttl)

    async def resolve(self, user_id: str, chat: str = "") -> str:
        if not user_id.startswith("ou_"):
            return "未知成员"
        cached = self.names.get(user_id)
        if cached is not None:
            return cached or "未知成员"
        async with self.name_lock:
            cached = self.names.get(user_id)
            if cached is not None:
                return cached or "未知成员"
            try:
                request = GetUserRequest.builder().user_id(user_id).user_id_type("open_id").build()
                response = await asyncio.wait_for(
                    self.client.contact.v3.user.aget(request), self.timeout
                )
                if response.success() and response.data and response.data.user:
                    self.remember(user_id, response.data.user.name)
                elif not response.success():
                    self.logger.debug("[FeishuTopics] Name lookup code=%s", response.code)
            except Exception as exc:
                self.logger.debug("[FeishuTopics] Name lookup failed: %s", type(exc).__name__)
            if not self.names.get(user_id) and chat and self.member_fallback:
                if self.member_queries.get(chat) is None:
                    # Negative cache also covers permission failures and truncated pagination.
                    self.member_queries.put(chat, True, 60)
                    try:
                        await asyncio.wait_for(self._load_members(chat), self.timeout)
                    except Exception as exc:
                        self.logger.debug(
                            "[FeishuTopics] Member lookup failed: %s", type(exc).__name__
                        )
            name = self.names.get(user_id)
            if name:
                return name
            self.names.put(user_id, "", 60)
            self.logger.warning(
                "[FeishuTopics] 无法获取一名成员的姓名；检查通讯录/群成员权限及应用可见范围。"
            )
            return "未知成员"

    async def _load_members(self, chat: str):
        token = None
        seen = set()
        for _ in range(10):
            builder = (
                GetChatMembersRequest.builder()
                .chat_id(chat)
                .member_id_type("open_id")
                .page_size(100)
            )
            if token:
                builder.page_token(token)
            response = await self.client.im.v1.chat_members.aget(builder.build())
            if not response.success() or not response.data:
                return
            for member in response.data.items or []:
                self.remember(member.member_id, member.name)
            token = response.data.page_token
            if not response.data.has_more:
                self.member_queries.put(chat, True, self.ttl)
                return
            if not token or token in seen:
                return
            seen.add(token)

    async def chat_info(self, chat: str) -> tuple[str, str]:
        cached = self.chats.get(chat)
        if cached is not None:
            return cached
        async with self.chat_lock:
            cached = self.chats.get(chat)
            if cached is not None:
                return cached
            try:
                req = GetChatRequest.builder().chat_id(chat).user_id_type("open_id").build()
                response = await asyncio.wait_for(self.client.im.v1.chat.aget(req), self.timeout)
                if response.success() and response.data:
                    info = (response.data.chat_mode or "", response.data.name or "")
                    self.chats.put(chat, info, self.ttl)
                    return info
            except Exception as exc:
                self.logger.debug("[FeishuTopics] Chat lookup failed: %s", type(exc).__name__)
            self.chats.put(chat, ("", ""), 60)
            return "", ""

    def display(self, text: str, *, hide_unknown=True, streaming=False) -> str:
        """Replace display text only; preserve URLs and real <at user_id=...> tags."""

        def replace(match):
            uid = match.group()
            return self.names.get(uid) or ("未知成员" if hide_unknown else uid)

        # The SDK updates cards with cumulative text. Mask even a partial `ou_`
        # until a subsequent update completes it and can render the actual name.
        pattern = STREAM_USER_ID if streaming else USER_ID
        return "".join(
            part if i % 2 else pattern.sub(replace, part)
            for i, part in enumerate(PROTECTED.split(text))
        )
