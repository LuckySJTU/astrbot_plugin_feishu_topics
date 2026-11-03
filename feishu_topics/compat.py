"""Locate the session-isolation hook across supported AstrBot releases."""


def unique_session_builders():
    # 4.28 applies isolation in WakingCheckStage; 4.29 moves it to EventBus,
    # before pipeline registration. Patch the table actually used by the host.
    from astrbot.core.pipeline.waking_check import stage

    registry = getattr(stage, "UNIQUE_SESSION_ID_BUILDERS", None)
    if registry is None:
        from astrbot.core.event_bus import UNIQUE_SESSION_ID_BUILDERS

        registry = UNIQUE_SESSION_ID_BUILDERS
    return registry
