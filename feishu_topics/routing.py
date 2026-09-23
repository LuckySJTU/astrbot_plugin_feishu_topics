"""Stable, restart-safe topic addresses; IDs remain protocol identifiers."""

import re
from dataclasses import dataclass

MARKER = "~ft~"
ID = re.compile(r"(?:oc|om|omt|ou)_[A-Za-z0-9_-]+\Z")


@dataclass(frozen=True)
class Topic:
    chat_id: str
    root_id: str
    thread_id: str = ""

    def __post_init__(self):
        for value, prefix in ((self.chat_id, "oc_"), (self.root_id, "om_")):
            if not value.startswith(prefix) or not ID.fullmatch(value):
                raise ValueError(f"Invalid {prefix} identifier")

    @property
    def session_id(self) -> str:
        return f"{self.chat_id}{MARKER}{self.root_id}"

    def origin(self, platform_id: str) -> str:
        return f"{platform_id}:GroupMessage:{self.session_id}"


def parse_topic(session_id: str) -> Topic | None:
    """Also accepts AstrBot's per-user `open_id%chat_id` session prefix."""
    if MARKER not in session_id:
        return None
    address = session_id.rsplit("%", 1)[-1]
    chat_id, root_id = address.split(MARKER, 1)
    return Topic(chat_id, root_id)


def get_field(obj, name: str, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)
