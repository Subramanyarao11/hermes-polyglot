"""Per-chat interpreter settings kept in the plugin's own profile-scoped state."""

from __future__ import annotations

import threading
from typing import Any, Optional

from .languages import Language, by_code

_STATE_KEY = "chats"
_MAX_CHATS = 500
_MAX_MEMBERS = 200


def chat_key(platform: str, chat_id: str, thread_id: Optional[str] = None) -> str:
    """Stable key for one conversation. Forum topics / threads share their parent chat's settings."""
    del thread_id  # one language roster per group, whatever topic people post in
    return f"{platform}:{chat_id}"


class ChatStore:
    """Small JSON-backed registry: which chats are interpreted, and who reads which language.

    ``state`` is any object with ``get(key, default)`` / ``set(key, value)`` (Hermes's PluginState).
    """

    def __init__(self, state: Any) -> None:
        self._state = state
        self._lock = threading.Lock()

    # ---- reading ---------------------------------------------------------------------------------
    def _all(self) -> dict[str, Any]:
        data = self._state.get(_STATE_KEY, default={})
        return data if isinstance(data, dict) else {}

    def chat(self, key: str) -> dict[str, Any]:
        entry = self._all().get(key)
        return entry if isinstance(entry, dict) else {}

    def is_enabled(self, key: str) -> bool:
        return bool(self.chat(key).get("enabled"))

    def members(self, key: str) -> dict[str, dict[str, str]]:
        members = self.chat(key).get("members")
        return members if isinstance(members, dict) else {}

    def member_language(self, key: str, user_id: str) -> Optional[Language]:
        entry = self.members(key).get(str(user_id)) or {}
        code = entry.get("lang")
        return by_code(code) if isinstance(code, str) else None

    def languages(self, key: str) -> list[Language]:
        """Distinct languages read in this chat, in the order members chose them."""
        seen: dict[str, Language] = {}
        for entry in self.members(key).values():
            code = entry.get("lang") if isinstance(entry, dict) else None
            lang = by_code(code) if isinstance(code, str) else None
            if lang is not None and lang.code not in seen:
                seen[lang.code] = lang
        return list(seen.values())

    # ---- writing ---------------------------------------------------------------------------------
    def _update(self, key: str, mutate) -> dict[str, Any]:
        with self._lock:
            data = dict(self._all())
            entry = dict(data.get(key) or {})
            mutate(entry)
            if entry:
                data[key] = entry
            else:
                data.pop(key, None)
            if len(data) > _MAX_CHATS:
                raise ValueError("Polyglot is already on in too many chats; turn it off somewhere first.")
            self._state.set(_STATE_KEY, data)
            return entry

    def set_enabled(self, key: str, enabled: bool) -> None:
        def mutate(entry: dict[str, Any]) -> None:
            entry["enabled"] = bool(enabled)
            entry.setdefault("members", {})
        self._update(key, mutate)

    def set_member_language(self, key: str, user_id: str, name: str, lang: Optional[Language]) -> None:
        def mutate(entry: dict[str, Any]) -> None:
            members = dict(entry.get("members") or {})
            if lang is None:
                members.pop(str(user_id), None)
            else:
                if str(user_id) not in members and len(members) >= _MAX_MEMBERS:
                    raise ValueError("This chat already has the maximum number of members with a language.")
                members[str(user_id)] = {"lang": lang.code, "name": (name or "")[:64]}
            entry["members"] = members
        self._update(key, mutate)
