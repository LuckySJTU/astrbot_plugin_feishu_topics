"""Upgrade from the published v0.1.0 bridge, including failed/partial installs."""

import importlib.util
import inspect
from functools import wraps
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from astrbot.api.event import MessageChain
from astrbot.core.platform.sources.lark.lark_adapter import LarkPlatformAdapter
from conftest import build_unique_session_id, response
from lark_oapi.api.im.v1 import GetMessageResponseBody
from topic_plugin.feishu_topics.bridge import Bridge
from topic_plugin.feishu_topics.store import Store


def legacy_bridge_class(version):
    # Frozen, unmodified release source: no Git/network access needed in CI.
    path = Path(__file__).parent / "fixtures" / f"{version}_bridge.py"
    spec = importlib.util.spec_from_file_location("topic_plugin.feishu_topics.bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Bridge


@pytest.mark.parametrize(
    "versions",
    [("v0_1_0",), ("v0_1_0", "v0_1_0"), ("v0_1_0", "v0_1_1"), ("v0_1_1",)],
)
async def test_upgrade_removes_legacy_wrappers_and_preserves_quote(harness, receive, versions):
    await harness.plugin.terminate()
    legacy_store = Store(harness.tmp / "legacy.sqlite3")
    await legacy_store.open()
    legacy_bridges = []
    try:
        for version in versions:
            old = legacy_bridge_class(version)(
                harness.context, {}, legacy_store, harness.plugin.logger
            )
            legacy_bridges.append(old)
            # On 4.29.4 this fails AFTER installing all three adapter wrappers.
            try:
                old.install()
            except ImportError as exc:
                assert "UNIQUE_SESSION_ID_BUILDERS" in str(exc)
                assert len(old.patches) == 3
            old.prepare = AsyncMock(wraps=old.prepare)

        await harness.plugin.initialize()
        harness.plugin.bridge.prepare = AsyncMock(wraps=harness.plugin.bridge.prepare)
        harness.client.im.v1.message.aget.return_value = response(
            GetMessageResponseBody(
                {
                    "items": [
                        {
                            "message_id": "om_quoted",
                            "chat_id": "oc_group",
                            "msg_type": "post",
                            "sender": {"id": "ou_bob"},
                            "create_time": "1800000000000",
                            "body": {
                                "content": '{"title":"引用","content":[[{"tag":"text","text":"内容"}]]}'
                            },
                        }
                    ]
                }
            )
        )
        event = await receive(harness, mid="om_new", root="om_root", parent="om_quoted")
        if "quoted_post_id" in inspect.signature(LarkPlatformAdapter.handle_msg).parameters:
            assert event.get_extra("lark_quoted_post_id") == "om_quoted"
        assert event.session_id == "oc_group~ft~om_root"
        assert build_unique_session_id(event) == "ou_alice%oc_group~ft~om_root"
        harness.plugin.bridge.prepare.assert_awaited_once()
        for old in legacy_bridges:
            assert not old.active
            old.prepare.assert_not_awaited()

        await harness.context.send_message(
            event.unified_msg_origin, MessageChain().message("你好 ou_alice")
        )
        harness.client.im.v1.message.areply.assert_awaited_once()
        request = harness.client.im.v1.message.areply.call_args.args[0]
        assert request.message_id == "om_root" and request.body.reply_in_thread
        assert "小王" in request.body.content

        await harness.plugin.terminate()
        for attr in ("handle_msg", "create_event", "send_by_session"):
            assert attr not in harness.adapter.__dict__
        event = await receive(harness, mid="om_after")
        assert build_unique_session_id(event) == "ou_alice%oc_group"
    finally:
        await harness.plugin.terminate()
        for old in reversed(legacy_bridges):
            old.uninstall()
        await legacy_store.close()


async def test_initialize_twice_reuses_bridge_and_database(harness, receive):
    bridge = harness.plugin.bridge
    store = harness.plugin.store
    connection = store.db
    handler = harness.adapter.handle_msg
    await harness.plugin.initialize()
    assert harness.plugin.bridge is bridge
    assert harness.plugin.store is store and store.db is connection
    assert harness.adapter.handle_msg is handler
    await receive(harness)
    await harness.plugin.terminate()
    assert store.db is None
    assert "handle_msg" not in harness.adapter.__dict__


async def test_replacement_preserves_other_plugin_and_late_unload(harness, receive):
    previous = harness.plugin
    old_bridge = previous.bridge
    old_bridge.prepare = AsyncMock(wraps=old_bridge.prepare)
    original = harness.adapter.handle_msg
    observed = []

    @wraps(original)
    async def third_party(abm, *args, **kwargs):
        observed.append(abm.message_id)
        return await original(abm, *args, **kwargs)

    harness.adapter.handle_msg = third_party
    current = type(previous)(harness.context, {})
    harness.plugin = current
    try:
        await current.initialize()
        assert not old_bridge.active
        installed = harness.adapter.handle_msg
        # A delayed old lifecycle hook must not install wrappers over the replacement.
        await previous.platform_loaded()
        assert harness.adapter.handle_msg is installed
        await previous.terminate()
        assert harness.adapter.handle_msg is installed
        event = await receive(harness)
        assert observed == ["om_root"]
        assert event.session_id == "oc_group~ft~om_root"
        old_bridge.prepare.assert_not_awaited()
        await current.terminate()
        assert harness.adapter.handle_msg is third_party
        event = await receive(harness, mid="om_after")
        assert observed == ["om_root", "om_after"]
        assert event.session_id == "oc_group"
    finally:
        await current.terminate()
        await previous.terminate()


async def test_failed_initialization_rolls_back_and_can_retry(harness, receive, monkeypatch):
    await harness.plugin.terminate()
    install_adapter = Bridge._install_adapter
    stores = []

    def fail_after_patching(bridge, adapter):
        stores.append(bridge.store)
        install_adapter(bridge, adapter)
        raise RuntimeError("injected initialization failure")

    with monkeypatch.context() as patch:
        patch.setattr(Bridge, "_install_adapter", fail_after_patching)
        with pytest.raises(RuntimeError, match="injected initialization failure"):
            await harness.plugin.initialize()
        assert stores[0].db is None
        assert harness.plugin.bridge is None and harness.plugin.store is None
        for attr in ("handle_msg", "create_event", "send_by_session"):
            assert attr not in harness.adapter.__dict__
        event = await receive(harness, mid="om_failed")
        assert build_unique_session_id(event) == "ou_alice%oc_group"

    await harness.plugin.initialize()
    event = await receive(harness, mid="om_retried")
    assert event.session_id == "oc_group~ft~om_retried"
