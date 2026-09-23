import asyncio
import json
import logging
from types import SimpleNamespace as NS

from conftest import response
from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
from topic_plugin.feishu_topics.client import client_view
from topic_plugin.feishu_topics.identity import Directory


async def test_contact_negative_cache_and_mentions(harness):
    client = harness.client
    client.contact.v3.user.aget.side_effect = None
    client.contact.v3.user.aget.return_value = response(code=99991672)
    directory = Directory(client, logging.getLogger("test"), member_fallback=False)
    assert await directory.resolve("ou_missing") == "未知成员"
    assert await directory.resolve("ou_missing") == "未知成员"
    assert client.contact.v3.user.aget.await_count == 1
    directory.remember("ou_missing", "后来得知的名字")
    assert await directory.resolve("ou_missing") == "后来得知的名字"


async def test_paginated_member_fallback(harness):
    client = harness.client
    client.contact.v3.user.aget.side_effect = None
    client.contact.v3.user.aget.return_value = response(code=99991672)
    client.im.v1.chat_members.aget.side_effect = [
        response(
            NS(items=[NS(member_id="ou_bob", name="小李")], has_more=True, page_token="page2")
        ),
        response(
            NS(items=[NS(member_id="ou_alice", name="小王")], has_more=False, page_token=None)
        ),
    ]
    directory = Directory(client, logging.getLogger("test"))
    assert await directory.resolve("ou_alice", "oc_group") == "小王"
    assert await directory.resolve("ou_bob", "oc_group") == "小李"
    assert client.im.v1.chat_members.aget.call_args.args[0].page_token == "page2"


async def test_lookup_timeout_degrades_without_id_exposure(harness):
    async def slow(req):
        await asyncio.sleep(10)

    harness.client.contact.v3.user.aget.side_effect = slow
    directory = Directory(
        harness.client, logging.getLogger("test"), timeout=0.01, member_fallback=False
    )
    assert await directory.resolve("ou_timeout") == "未知成员"


async def test_name_lookup_stampede_is_coalesced(harness):
    directory = Directory(harness.client, logging.getLogger("test"))
    assert await asyncio.gather(*(directory.resolve("ou_alice") for _ in range(8))) == ["小王"] * 8
    assert harness.client.contact.v3.user.aget.await_count == 1


async def test_sdk_view_does_not_mutate_shared_request_or_mention_ids(harness):
    directory = Directory(harness.client, logging.getLogger("test"))
    directory.remember("ou_alice", "小王")
    payload = {
        "zh_cn": {
            "content": [
                [
                    {"tag": "at", "user_id": "ou_alice", "user_name": "小王"},
                    {
                        "tag": "text",
                        "text": 'ou_alice <at user_id="ou_alice">小王</at> https://example.com/ou_alice',
                    },
                ]
            ]
        }
    }
    request = (
        ReplyMessageRequest.builder()
        .message_id("om_root")
        .request_body(
            ReplyMessageRequestBody.builder()
            .content(json.dumps(payload))
            .msg_type("post")
            .reply_in_thread(False)
            .build(),
        )
        .build()
    )
    view = client_view(harness.client, directory, in_thread=True)
    await view.im.v1.message.areply(request)
    assert not request.body.reply_in_thread
    assert json.loads(request.body.content) == payload
    sent = harness.client.im.v1.message.areply.call_args.args[0]
    parsed = json.loads(sent.body.content)["zh_cn"]["content"][0]
    assert parsed[0]["user_id"] == "ou_alice"
    assert parsed[1]["text"] == '小王 <at user_id="ou_alice">小王</at> https://example.com/ou_alice'
