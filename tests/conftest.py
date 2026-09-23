"""Real AstrBot 4.28.1 + real SDK models; only the external HTTP boundary is mocked."""

import importlib
import importlib.util
import itertools
import os
import sys
import tempfile
from asyncio import Queue
from pathlib import Path
from types import MethodType
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

RUNTIME = tempfile.TemporaryDirectory(prefix="feishu-topics-tests-")
os.environ["ASTRBOT_ROOT"] = RUNTIME.name

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "topic_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
)
package = importlib.util.module_from_spec(spec)
sys.modules["topic_plugin"] = package
spec.loader.exec_module(package)


def response(data=None, code=0, msg="OK"):
    return NS(data=data, code=code, msg=msg, success=lambda: code == 0)


@pytest_asyncio.fixture
async def harness(tmp_path, monkeypatch):
    from astrbot.api.star import Context, StarTools
    from astrbot.core.platform.sources.lark.lark_adapter import LarkPlatformAdapter
    from astrbot.core.utils.metrics import Metric

    monkeypatch.setattr(Metric, "upload", AsyncMock())
    monkeypatch.setattr(StarTools, "get_data_dir", lambda name: tmp_path)
    adapter = LarkPlatformAdapter(
        {"id": "feishu", "app_id": "cli_test", "app_secret": "not-a-real-secret"},
        {},
        Queue(),
    )
    adapter.bot_open_id = "ou_bot"
    adapter.bot_name = "小助手"
    client = adapter.lark_api
    counter = itertools.count()
    client.contact.v3.user.aget = AsyncMock(
        side_effect=lambda req: response(
            NS(
                user=NS(
                    name={
                        "ou_alice": "小王",
                        "ou_bob": "小李",
                        "ou_carol": "小陈",
                    }.get(req.user_id, "")
                )
            )
        )
    )
    client.im.v1.chat.aget = AsyncMock(return_value=response(NS(chat_mode="topic", name="研发群")))
    client.im.v1.chat_members.aget = AsyncMock(
        return_value=response(NS(items=[], has_more=False, page_token=None))
    )
    client.im.v1.message.areply = AsyncMock(
        side_effect=lambda req: response(NS(message_id=f"om_reply{next(counter)}"))
    )
    client.im.v1.message.acreate = AsyncMock(
        side_effect=lambda req: response(NS(message_id=f"om_created{next(counter)}"))
    )
    client.im.v1.message.aget = AsyncMock(return_value=response(NS(items=[])))
    client.cardkit.v1.card.acreate = AsyncMock(return_value=response(NS(card_id="card_test")))
    client.cardkit.v1.card_element.acontent = AsyncMock(return_value=response())
    client.cardkit.v1.card.asettings = AsyncMock(return_value=response())
    cfg = {"provider_ltm_settings": {"group_message_history_enable": False}}
    context = NS(platform_manager=NS(platform_insts=[adapter]), get_config=lambda **kw: cfg)
    context.send_message = MethodType(Context.send_message, context)
    main = importlib.import_module("topic_plugin.main")
    plugin = main.FeishuTopicsPlugin(context, {})
    await plugin.initialize()
    value = NS(plugin=plugin, adapter=adapter, client=client, context=context, tmp=tmp_path)
    yield value
    await plugin.terminate()


@pytest.fixture
def receive():
    async def incoming(
        harness,
        text="你好",
        *,
        mid="om_root",
        root="",
        thread="omt_a",
        parent="",
        chat="oc_group",
        sender="ou_alice",
        mentions=None,
    ):
        import json

        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

        event = P2ImMessageReceiveV1(
            {
                "schema": "2.0",
                "header": {"event_id": f"ev_{mid}", "event_type": "im.message.receive_v1"},
                "event": {
                    "sender": {"sender_id": {"open_id": sender}, "sender_type": "user"},
                    "message": {
                        "message_id": mid,
                        "root_id": root,
                        "thread_id": thread,
                        "parent_id": parent,
                        "chat_id": chat,
                        "chat_type": "group" if chat else "p2p",
                        "message_type": "text",
                        "content": json.dumps({"text": text}),
                        "create_time": "1800000000000",
                        "mentions": mentions or [],
                    },
                },
            }
        )
        await harness.adapter.convert_msg(event)
        return harness.adapter._event_queue.get_nowait()

    return incoming
