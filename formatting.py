"""Chat-facing text: translation replies, the language roster, help."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from .languages import Language, by_code
from .translate import Translation

WELCOME = (
    "🌐 Polyglot is on. Just chat as usual, in any language.\n"
    "I learn each person's language from their first message and translate the chat for everyone else. "
    "Voice notes work too.\n"
    "• Prefer another language? Send /lang and its name, e.g. /lang hindi\n"
    "• To ask me something, start with “hermes,”"
)

HELP = (
    "Polyglot commands\n"
    "/polyglot on: start interpreting this group\n"
    "/polyglot off: stop (language choices are kept)\n"
    "/polyglot status: who reads which language\n"
    "/lang <language>: the language you read this group in\n"
    "/lang off: stop receiving translations\n"
    "/lang: show everyone's languages"
)


def render(result: Translation, targets: Sequence[Language], *, transcript: Optional[str] = None) -> Optional[str]:
    """One reply per message: a line per language that needed a translation, in roster order."""
    lines: list[str] = []
    if transcript:
        lines.append(f"🎙 {transcript}")
    rendered = [(lang, result.texts[lang.code]) for lang in targets if result.texts.get(lang.code)]
    if not rendered:
        return "\n".join(lines) if lines else None
    if len(rendered) == 1 and not transcript:
        lang, text = rendered[0]
        return f"🌐 {lang.native}: {text}"
    lines.extend(f"🌐 {lang.native}: {text}" for lang, text in rendered)
    return "\n".join(lines)


def roster(members: dict[str, Any], mine: Optional[Language]) -> str:
    rows = []
    for entry in members.values():
        if not isinstance(entry, dict):
            continue
        lang = by_code(str(entry.get("lang") or ""))
        if lang is not None:
            rows.append(f"• {entry.get('name') or 'someone'}: {lang.native}")
    head = "Languages in this chat:" if rows else "Nobody has chosen a language yet."
    tail = f"You read in {mine.native}." if mine else "Choose yours with /lang <language>, e.g. /lang english."
    return "\n".join([head, *rows, tail])


def access_hint(platform: str, chat_id: str) -> str:
    """Hermes drops group messages from people who are not allowed to use it before any plugin sees
    them. Tell the admin how to let this group's members through."""
    if platform != "telegram":
        return ("\n\nIf other members' messages are not translated, allow this chat's members in your "
                "Hermes gateway settings.")
    return ("\n\nIf other members' messages are not translated, Hermes is ignoring them because they are "
            f"not Hermes users. Allow this group: add TELEGRAM_GROUP_ALLOWED_CHATS={chat_id} to Hermes's "
            ".env and restart the gateway. Every member of the group can then also talk to Hermes here.")


def learned(name: str, lang: Language) -> str:
    """Shown once, the first time someone writes: what was picked for them and how to change it."""
    return (f"👋 {name}, I'll show you this chat in {lang.native}. "
            "Want another language? Send /lang and its name, e.g. /lang hindi")
