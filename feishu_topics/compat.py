"""Locate host hooks and recognize our own wrappers across module reloads."""

import inspect


def bridge_owner(callback):
    """Identify only recorded plugin patches, including unmarked v0.1.0/0.1.1 ones."""
    if not inspect.isfunction(callback):
        return None
    owner = getattr(callback, "__feishu_topics_owner__", None)
    if owner is None:
        if not callback.__module__.endswith(".feishu_topics.bridge"):
            return None
        if callback.__qualname__ not in {
            "Bridge._install_adapter.<locals>.handle",
            "Bridge._install_adapter.<locals>.create",
            "Bridge._install_adapter.<locals>.send",
            "Bridge.install.<locals>.topic_unique_session",
        }:
            return None
        owner = inspect.getclosurevars(callback).nonlocals.get("self")
    # functools.wraps can copy the marker onto ANOTHER plugin's wrapper.
    # Require identity with an installed patch before touching its owner.
    if any(patch[-1] is callback for patch in getattr(owner, "patches", ())):
        return owner
    unique = getattr(owner, "unique_patch", None)
    return owner if unique and unique[-1] is callback else None


def unique_session_builders():
    # 4.28 applies isolation in WakingCheckStage; 4.29 moves it to EventBus,
    # before pipeline registration. Patch the table actually used by the host.
    from astrbot.core.pipeline.waking_check import stage

    registry = getattr(stage, "UNIQUE_SESSION_ID_BUILDERS", None)
    if registry is None:
        from astrbot.core.event_bus import UNIQUE_SESSION_ID_BUILDERS

        registry = UNIQUE_SESSION_ID_BUILDERS
    return registry
