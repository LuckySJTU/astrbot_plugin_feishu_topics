import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from astrbot.api.event import MessageChain
from astrbot.api.provider import ProviderRequest
from astrbot.core.cron.events import CronMessageEvent
from astrbot.core.pipeline.waking_check.stage import build_unique_session_id
from astrbot.core.platform.message_session import MessageSession
from conftest import response
from topic_plugin.feishu_topics.routing import Topic, parse_topic
from topic_plugin.feishu_topics.store import Store


async def test_names_and_protocol_ids_are_separate(harness, receive):
    event = await receive(harness)
    assert event.get_sender_name() == "小王"
    assert event.get_sender_id() == "ou_alice"
    assert event.get_group_id() == "oc_group"
    assert event.session_id == "oc_group~ft~om_root"
    await event.send(MessageChain().message("你好 ou_alice，抄送 ou_unknown"))
    req = harness.client.im.v1.message.areply.call_args.args[0]
    assert req.message_id == "om_root"
    assert req.request_body.reply_in_thread is True
    assert req.body is req.request_body
    assert "小王" in json.loads(req.body.content)["zh_cn"]["content"][0][0]["text"]
    assert "ou_unknown" not in req.body.content
    assert "未知成员" in req.body.content
    harness.client.im.v1.message.acreate.assert_not_awaited()


async def test_topic_root_and_replies_share_session_but_topics_do_not(harness, receive):
    root = await receive(harness, "话题A", mid="om_a", thread="omt_a")
    reply = await receive(
        harness, "A的回复", mid="om_a2", root="om_a", thread="omt_a", sender="ou_bob"
    )
    other = await receive(harness, "话题B", mid="om_b", thread="omt_b")
    assert root.unified_msg_origin == reply.unified_msg_origin
    assert other.unified_msg_origin != root.unified_msg_origin
    assert build_unique_session_id(reply) == "ou_bob%oc_group~ft~om_a"
    reply.session_id = build_unique_session_id(reply)
    assert parse_topic(reply.session_id) == Topic("oc_group", "om_a")
    await harness.context.send_message(reply.unified_msg_origin, MessageChain().message("定时消息"))
    req = harness.client.im.v1.message.areply.call_args.args[0]
    assert req.message_id == "om_a"
    assert req.body.reply_in_thread


async def test_full_group_context_includes_unwoken_messages_and_is_scoped(harness, receive):
    await receive(harness, "A 话题今天讨论模型", mid="om_a", thread="omt_a")
    await receive(harness, "B 话题预算是 200", mid="om_b", thread="omt_b", sender="ou_bob")
    await receive(harness, "隔壁群秘密", mid="om_secret", thread="omt_secret", chat="oc_elsewhere")
    event = await receive(harness, "B 有什么进展", mid="om_a2", root="om_a", thread="omt_a")
    req = ProviderRequest(prompt="问问 ou_bob", system_prompt="系统")
    await harness.plugin.enrich_request(event, req)
    await harness.plugin.enrich_request(event, req)
    assert len(req.extra_user_content_parts) == 1
    assert req.extra_user_content_parts[0].model_dump_for_context()["_no_save"] is True
    assert req.prompt == "问问 小李"
    context = req.extra_user_content_parts[0].text
    assert "预算是 200" in context and "小李" in context
    assert "om_b" in context and "om_a" in context
    assert "隔壁群秘密" not in context
    assert '"text": "B 有什么进展"' not in context
    assert "预算是 200" not in req.system_prompt


async def test_normal_group_and_private_chat_keep_routing(harness, receive):
    harness.client.im.v1.chat.aget.return_value = response(NS(chat_mode="group", name="普通群"))
    event = await receive(harness, thread="", mid="om_regular", root="om_quote")
    assert event.session_id == "oc_group"
    assert build_unique_session_id(event) == "ou_alice%oc_group"
    await event.send(MessageChain().message("普通回复"))
    assert harness.client.im.v1.message.areply.call_args.args[0].body.reply_in_thread is False
    await harness.context.send_message(
        event.unified_msg_origin, MessageChain().message("普通主动消息")
    )
    req = harness.client.im.v1.message.acreate.call_args.args[0]
    assert req.body.receive_id == "oc_group"
    private = await receive(harness, chat="", thread="", mid="om_private")
    assert private.session_id == "ou_alice"
    assert private.get_sender_name() == "小王"


async def test_regular_group_thread_is_still_a_topic(harness, receive):
    harness.client.im.v1.chat.aget.return_value = response(NS(chat_mode="group", name="普通群"))
    event = await receive(harness, thread="omt_thread")
    assert event.session_id == "oc_group~ft~om_root"


async def test_topic_group_root_without_thread_id_stays_stable(harness, receive):
    root = await receive(harness, thread="", mid="om_first")
    reply = await receive(harness, thread="omt_later", mid="om_next", root="om_first")
    assert root.session_id == reply.session_id


async def test_manual_group_override_without_chat_permission(harness, receive):
    harness.client.im.v1.chat.aget.return_value = response(code=99991672)
    harness.plugin.config["topic_chat_ids"] = ["oc_group"]
    event = await receive(harness, thread="")
    assert event.session_id == "oc_group~ft~om_root"


async def test_missing_root_recovers_from_known_thread(harness, receive):
    await receive(harness, mid="om_known", thread="omt_known")
    reply = await receive(harness, mid="om_next", thread="omt_known", root="")
    assert reply.session_id == "oc_group~ft~om_known"


async def test_alias_and_native_cron_survive_restart(harness, receive):
    event = await receive(harness)
    event.role = "admin"
    results = [result async for result in harness.plugin.bind_topic(event, "日报")]
    assert "已绑定" in results[0].chain[0].text
    origin = event.unified_msg_origin
    await harness.plugin.terminate()
    await harness.plugin.initialize()
    cron = CronMessageEvent(
        context=harness.context, session=MessageSession.from_str(origin), message="定时测试"
    )
    await cron.send(MessageChain().message("重启后的定时提醒"))
    req = harness.client.im.v1.message.areply.call_args.args[0]
    assert req.message_id == "om_root" and req.body.reply_in_thread
    assert harness.client.im.v1.message.acreate.await_count == 0
    info = harness.plugin.bridge.event_context(cron)
    topic = await harness.plugin.store.resolve(info[1], "oc_group", "日报")
    assert topic.root_id == "om_root"
    run_context = NS(messages=[])
    await harness.plugin.enrich_scheduled_agent(cron, run_context)
    await harness.plugin.enrich_scheduled_agent(cron, run_context)
    assert len(run_context.messages) == 1
    assert "om_root" in run_context.messages[0].content[-1].text
    assert run_context.messages[0].content[-1].model_dump_for_context()["_no_save"]


async def test_topic_send_failures_never_fall_back_to_new_topic(harness, receive):
    event = await receive(harness)
    harness.client.im.v1.message.areply.side_effect = None
    harness.client.im.v1.message.areply.return_value = response(
        code=230050, msg="message is invisible"
    )
    with pytest.raises(RuntimeError, match="230050"):
        await harness.context.send_message(
            event.unified_msg_origin, MessageChain().message("不可投递")
        )
    harness.client.im.v1.message.acreate.assert_not_awaited()
    with pytest.raises(ValueError, match="没有这个"):
        await harness.context.send_message(
            "feishu:GroupMessage:oc_group~ft~om_unobserved", MessageChain().message("?")
        )


async def test_concurrent_topics_have_no_shared_send_state(harness, receive):
    a = await receive(harness, mid="om_a", thread="omt_a")
    b = await receive(harness, mid="om_b", thread="omt_b")
    await asyncio.gather(a.send(MessageChain().message("A")), b.send(MessageChain().message("B")))
    requests = [call.args[0] for call in harness.client.im.v1.message.areply.call_args_list]
    assert {req.message_id for req in requests} == {"om_a", "om_b"}
    assert all(req.body.reply_in_thread for req in requests)
    assert harness.adapter.lark_api is harness.client


async def test_streaming_card_uses_topic_and_rewrites_aggregated_name(harness, receive):
    event = await receive(harness)

    async def stream():
        yield MessageChain().message("你好 ou_")
        await asyncio.sleep(0.01)
        yield MessageChain().message("alice")

    await event.send_streaming(stream())
    req = harness.client.im.v1.message.areply.call_args.args[0]
    assert req.body.msg_type == "interactive" and req.body.reply_in_thread
    update = harness.client.cardkit.v1.card_element.acontent.call_args.args[0]
    assert update.body.content == "你好 小王"
    assert all(
        "ou_" not in call.args[0].body.content
        for call in harness.client.cardkit.v1.card_element.acontent.call_args_list
    )
    history = await harness.plugin.store.context(
        "feishu:cli_test", "oc_group", exclude="", limit=40, max_chars=12000
    )
    assert "你好 小王" in history


async def test_streaming_card_failure_fallback_stays_in_topic(harness, receive):
    event = await receive(harness)
    harness.client.cardkit.v1.card.acreate.return_value = response(code=99991672)

    async def stream():
        yield MessageChain().message("你好 ")
        yield MessageChain().message("ou_alice")

    await event.send_streaming(stream())
    req = harness.client.im.v1.message.areply.call_args.args[0]
    assert req.body.msg_type == "post" and req.body.reply_in_thread
    harness.client.im.v1.message.acreate.assert_not_awaited()


async def test_admin_only_and_cross_group_target_rejection(harness, receive):
    event = await receive(harness)
    result = await harness.plugin.send_tool(event, "om_root", "hello")
    assert "管理员" in result
    event.role = "admin"
    await receive(harness, chat="oc_other", mid="om_elsewhere")
    result = await harness.plugin.send_tool(event, "om_elsewhere", "hello")
    assert "发送失败" in result
    harness.client.im.v1.message.areply.assert_not_awaited()
    result = await harness.plugin.send_tool(event, "om_root", "hello with spaces")
    assert result == "已发送到指定话题。"


async def test_unload_restores_original_adapter_and_unique_builder(harness, receive):
    await receive(harness)
    await harness.plugin.terminate()
    assert "handle_msg" not in harness.adapter.__dict__
    assert "create_event" not in harness.adapter.__dict__
    assert "send_by_session" not in harness.adapter.__dict__
    event = await receive(harness, mid="om_after")
    assert event.session_id == "oc_group"
    assert build_unique_session_id(event) == "ou_alice%oc_group"


async def test_other_plugins_wrapper_is_preserved_on_unload(harness):
    previous = harness.adapter.send_by_session
    later = AsyncMock(side_effect=previous)
    harness.adapter.send_by_session = later
    await harness.plugin.terminate()
    assert harness.adapter.send_by_session is later


async def test_store_limits_isolation_dedup_and_forget(tmp_path):
    store = Store(tmp_path / "history.db", history_limit=3)
    await store.open()
    try:
        for i in range(5):
            await store.record(
                scope="a",
                chat="oc_test",
                message_id=f"om_{i}",
                root="om_root",
                sender="ou_alice",
                name="小王",
                role="user",
                text=f"msg{i}",
                sent=i,
            )
        assert not await store.record(
            scope="a",
            chat="oc_test",
            message_id="om_4",
            root="om_root",
            sender="ou_alice",
            name="小王",
            role="user",
            text="duplicate",
            sent=4,
        )
        history = json.loads(
            await store.context("a", "oc_test", exclude="", limit=20, max_chars=5000)
        )
        assert [row["text"] for row in history] == ["msg2", "msg3", "msg4"]
        assert await store.context("b", "oc_test", exclude="", limit=20, max_chars=5000) == "[]"
        assert len(await store.context("a", "oc_test", exclude="", limit=20, max_chars=200)) <= 200
        topic = Topic("oc_test", "om_root", "omt_root")
        await store.save_topic("a", topic, "话题")
        await store.bind("a", topic, "日报")
        with pytest.raises(ValueError):
            await store.resolve("b", "oc_test", "日报")
        await store.forget("a", "oc_test")
        with pytest.raises(ValueError):
            await store.resolve("a", "oc_test", "日报")
    finally:
        await store.close()


@pytest.mark.parametrize(
    "invalid", ["oc_a~ft~omt_thread", "oc_a~ft~om_b/attack", "oc_a~ft~om_b~ft~om_c", "oc_a~ft~"]
)
def test_malformed_routes_rejected(invalid):
    with pytest.raises(ValueError):
        parse_topic(invalid)


async def test_command_can_reach_response_stage_before_stopping(harness, receive):
    from astrbot.core.pipeline.context_utils import call_handler

    event = await receive(harness, "/ft_topic")
    async for _ in call_handler(event, harness.plugin.topic_info):
        # PipelineScheduler refuses to invoke RespondStage if already stopped.
        assert not event.is_stopped()
        await event.send(event.get_result())
    assert event.is_stopped()
    sent = harness.client.im.v1.message.areply.call_args.args[0]
    assert "显示姓名" in sent.body.content


async def test_runtime_platform_reload_uses_new_app_scope(harness):
    from asyncio import Queue

    from astrbot.core.platform.sources.lark.lark_adapter import LarkPlatformAdapter

    other = LarkPlatformAdapter(
        {"id": "feishu", "app_id": "cli_replaced", "app_secret": "fake"}, {}, Queue()
    )
    harness.context.platform_manager.platform_insts = [other]
    await harness.plugin.platform_loaded()
    cron = CronMessageEvent(
        context=harness.context,
        session=MessageSession.from_str("feishu:GroupMessage:oc_group~ft~om_root"),
        message="定时任务",
    )
    assert harness.plugin.bridge.event_context(cron)[1] == "feishu:cli_replaced"


async def test_stream_history_records_latest_successful_update(harness, receive):
    event = await receive(harness)

    async def stream():
        yield MessageChain().message("第一段")
        await asyncio.sleep(0.01)
        yield MessageChain().message("第二段")

    await event.send_streaming(stream())
    rows = json.loads(
        await harness.plugin.store.context(
            "feishu:cli_test",
            "oc_group",
            exclude="",
            limit=40,
            max_chars=12000,
        )
    )
    bot_messages = [row for row in rows if row["role"] == "assistant"]
    assert len(bot_messages) == 1
    assert bot_messages[0]["text"] == "第一段第二段"


async def test_direct_send_failure_is_propagated_without_context_record(harness, receive):
    event = await receive(harness)
    harness.client.im.v1.message.areply.side_effect = OSError("network unavailable")
    with pytest.raises(OSError, match="network unavailable"):
        await event.send(MessageChain().message("未发送内容"))
    history = await harness.plugin.store.context(
        "feishu:cli_test",
        "oc_group",
        exclude="",
        limit=40,
        max_chars=12000,
    )
    assert "未发送内容" not in history
    harness.client.im.v1.message.acreate.assert_not_awaited()
