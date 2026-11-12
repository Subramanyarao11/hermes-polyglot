"""Chat-facing text: translation replies, the language roster, help."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from .languages import Language, by_code
from .translate import Translation

WELCOME = (
    "🌐 Polyglot is on. Just chat as usual, in any language.\n"
    "I learn each person's language from their first message and translate the chat for everyone else.\n"
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


def render(result: Translation, targets: Sequence[Language]) -> Optional[str]:
    """One reply per message: a line per language that needed a translation, in roster order."""
    rendered = [(lang, result.texts[lang.code]) for lang in targets if result.texts.get(lang.code)]
    if not rendered:
        return None
    return "\n".join(f"🌐 {lang.native}: {text}" for lang, text in rendered)


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
    """Only people Hermes admits are translated. Point the admin at the gateway's own allowlists."""
    if platform != "telegram":
        return ("\n\nOnly messages from people Hermes admits are translated. To include another member, "
                "add them to your gateway's allowed users (see the Hermes messaging docs for this platform).")
    return ("\n\nOnly messages from people Hermes admits are translated. To include another member, add "
            "their Telegram user ID to TELEGRAM_GROUP_ALLOWED_USERS (group-only access) or "
            "TELEGRAM_ALLOWED_USERS in Hermes's .env and restart the gateway. Admitted members can also "
            "talk to Hermes here, so only add people you trust.")


def learned(name: str, lang: Language) -> str:
    """Shown once, the first time someone writes: what was picked for them and how to change it."""
    return (f"👋 {name}, I'll show you this chat in {lang.native}. "
            "Want another language? Send /lang and its name, e.g. /lang hindi")
