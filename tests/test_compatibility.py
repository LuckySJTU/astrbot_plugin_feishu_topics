"""Regression coverage for interfaces changed by AstrBot 4.29."""

import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from astrbot.api import message_components as Comp
from astrbot.api.event import MessageChain
from astrbot.core.event_bus import EventBus
from astrbot.core.platform.sources.lark.lark_adapter import LarkPlatformAdapter
from conftest import response
from lark_oapi.api.im.v1 import GetMessageResponseBody

pytestmark = pytest.mark.skipif(
    not hasattr(EventBus, "_apply_session_isolation"),
    reason="This regression targets the AstrBot 4.29 event bus.",
)


async def test_event_bus_preserves_topic_before_pipeline_dispatch(harness, receive):
    a = await receive(harness, mid="om_a", thread="omt_a")
    b = await receive(harness, mid="om_b", thread="omt_b")
    processed = []
    finished = asyncio.Event()

    async def execute(event):
        processed.append((event.session_id, event.get_extra("_session_isolated")))
        if len(processed) == 2:
            finished.set()

    queue = asyncio.Queue()
    queue.put_nowait(a)
    queue.put_nowait(b)
    scheduler = NS(
        ctx=NS(astrbot_config={"platform_settings": {"unique_session": True}}),
        execute=execute,
    )
    manager = NS(get_conf_info=lambda _: {"id": "test"})
    bus = EventBus(queue, {"test": scheduler}, manager)
    dispatcher = asyncio.create_task(bus.dispatch())
    try:
        await asyncio.wait_for(finished.wait(), timeout=1)
    finally:
        dispatcher.cancel()
        await asyncio.gather(dispatcher, return_exceptions=True)
    assert processed == [
        ("ou_alice%oc_group~ft~om_a", True),
        ("ou_alice%oc_group~ft~om_b", True),
    ]
    bus._apply_session_isolation(b, {"platform_settings": {"unique_session": False}})
    assert not b.get_extra("_session_isolated")


async def test_post_and_quote_metadata_survive_adapter_wrapper(harness, receive):
    content = {
        "title": "笔记",
        "content": [
            [{"tag": "text", "text": "    第一行 ou_alice"}],
            [],
            [{"tag": "text", "text": "第二行  "}],
        ],
    }
    harness.client.im.v1.message.aget.return_value = response(
        GetMessageResponseBody(
            {
                "items": [
                    {
                        "message_id": "om_quoted",
                        "msg_type": "post",
                        "chat_id": "oc_group",
                        "sender": {"id": "ou_bob"},
                        "create_time": "1800000000000",
                        "body": {"content": json.dumps(content)},
                    }
                ]
            }
        )
    )
    event = await receive(
        harness,
        mid="om_reply",
        root="om_root",
        parent="om_quoted",
        message_type="post",
        content=content,
    )
    assert event.get_extra("lark_quoted_post_id") == "om_quoted"
    assert event.session_id == "oc_group~ft~om_root"
    assert event.message_str == "笔记\n    第一行 小王\n\n第二行  "
    quote = next(part for part in event.get_messages() if isinstance(part, Comp.Reply))
    assert quote.sender_nickname == "小李"
    assert quote.message_str == "笔记\n    第一行 小王\n\n第二行  "


async def test_inactive_wrapper_forwards_new_keyword(harness, receive):
    event = await receive(harness)
    harness.plugin.bridge.active = False
    await harness.adapter.handle_msg(event.message_obj, quoted_post_id="om_quoted")
    forwarded = harness.adapter._event_queue.get_nowait()
    assert forwarded.get_extra("lark_quoted_post_id") == "om_quoted"


@pytest.mark.skipif(not hasattr(Comp, "Button"), reason="Portable buttons require AstrBot 4.29.4")
async def test_topic_button_send_retains_callback_data_and_group_type(harness, receive):
    from astrbot.core.platform.button_interaction import decode_button_callback

    event = await receive(harness)
    chain = MessageChain(
        chain=[
            Comp.Plain("请 ou_alice 确认"),
            Comp.ActionRow(
                buttons=[
                    Comp.Button(
                        id="confirm",
                        label="ou_alice 确认",
                        action=Comp.Button.CallbackAction(data={"user_id": "ou_alice"}),
                    )
                ]
            ),
        ]
    )
    await harness.context.send_message(event.unified_msg_origin, chain)
    req = harness.client.im.v1.message.areply.call_args.args[0]
    assert req.message_id == "om_root" and req.body.reply_in_thread
    assert req.body.msg_type == "interactive"
    assert json.loads(req.body.content) == {"type": "card", "data": {"card_id": "card_test"}}
    card_request = harness.client.cardkit.v1.card.acreate.call_args.args[0]
    assert card_request.body is card_request.request_body
    elements = json.loads(card_request.body.data)["body"]["elements"]
    assert elements[0]["content"] == "请 小王 确认"
    button = elements[1]["columns"][0]["elements"][0]
    assert button["text"]["content"] == "小王 确认"
    value = button["behaviors"][0]["value"]
    assert value["astrbot_message_type"] == "GroupMessage"
    assert decode_button_callback(value["astrbot_callback"]) == ("confirm", {"user_id": "ou_alice"})
    harness.client.im.v1.message.areply.assert_awaited_once()
    assert chain.chain[0].text == "请 ou_alice 确认"


@pytest.mark.skipif(
    not hasattr(LarkPlatformAdapter, "convert_card_action"),
    reason="Portable buttons require AstrBot 4.29.4",
)
@pytest.mark.parametrize("source_chat", ["oc_group", "oc_other"])
async def test_button_callback_resolves_source_topic_without_recording_a_chat(
    harness, receive, source_chat
):
    from astrbot.core.platform.button_interaction import encode_button_callback

    await receive(harness, mid="om_root", thread="omt_a")
    harness.client.im.v1.message.aget.return_value = response(
        GetMessageResponseBody(
            {
                "items": [
                    {
                        "message_id": "om_card",
                        "chat_id": source_chat,
                        "root_id": "om_root",
                        "thread_id": "omt_a",
                    }
                ]
            }
        )
    )
    harness.plugin.store.record = AsyncMock(wraps=harness.plugin.store.record)
    callback = {
        "header": {"event_id": "ev_button", "create_time": "1800000000000"},
        "event": {
            "operator": {"open_id": "ou_alice"},
            "action": {
                "tag": "button",
                "value": {
                    "astrbot_callback": encode_button_callback("confirm", {"test": True}),
                    "astrbot_message_type": "GroupMessage",
                },
            },
            "context": {"open_message_id": "om_card", "open_chat_id": "oc_group"},
        },
    }
    if source_chat != "oc_group":
        with pytest.raises(ValueError, match="来源"):
            await harness.adapter.convert_card_action(callback)
        assert harness.adapter._event_queue.empty()
    else:
        await harness.adapter.convert_card_action(callback)
        event = harness.adapter._event_queue.get_nowait()
        assert event.session_id == "oc_group~ft~om_root"
        assert event.get_button_interaction().data == {"test": True}
        await event.send(MessageChain().message("已确认 ou_alice"))
        req = harness.client.im.v1.message.areply.call_args.args[0]
        assert req.message_id == "om_card" and req.body.reply_in_thread
    assert not any(
        call.kwargs["role"] == "user" for call in harness.plugin.store.record.call_args_list
    )
