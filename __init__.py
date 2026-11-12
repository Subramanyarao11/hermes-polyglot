"""Polyglot: a live interpreter for Hermes group chats.

Add Hermes to a Telegram / Discord / Slack / WhatsApp group and run ``/polyglot on``. Each member's
reading language is learned from their first message (or set with ``/lang``). From then on every message
from someone Hermes admits is translated for everyone else in the group, in their own language. Hermes
still answers as an assistant when someone addresses it ("hermes, ..." or an @mention of the bot).

Surfaces: the ``post_gateway_admission`` hook (runs after Hermes's authorization; it consumes each
interpreted group message and returns the translation as the reply, so translation never costs an agent
turn), two slash commands, a registered auxiliary model slot (``auxiliary.polyglot_translate``) and
``ctx.llm`` for the translation call.
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace
from typing import Any, Optional

from . import formatting
from .languages import Language, parse
from .store import ChatStore, chat_key
from .translate import AUX_TASK, Interpreter, ModelTranslator, SarvamClient, TranslationError

logger = logging.getLogger(__name__)

ENGLISH = parse("english")
AUTO_LANGUAGE_MIN_LETTERS = 8  # shorter messages ("ok", "hi all") say too little about someone's language
GROUP_CHAT_TYPES = frozenset({"group", "channel", "thread", "forum", "supergroup"})
_BOT_MENTION = re.compile(r"(?<![\w@])@\w{2,}bot\b|<@!?\d+>", re.I)
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_URL = re.compile(r"https?://\S+")


class Polyglot:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._store: Optional[ChatStore] = None
        self.use_task = False

    @property
    def store(self) -> ChatStore:
        # Built on first use, not in register(): state is only touched when a message arrives.
        if self._store is None:
            self._store = ChatStore(self.ctx.state)
        return self._store

    # ---- settings ---------------------------------------------------------------------------------
    def _setting(self, key: str, default: Any) -> Any:
        try:
            value = self.ctx.get_config(key, default=default)
        except Exception:  # noqa: BLE001 - settings are optional
            return default
        return default if value is None else value

    def _sarvam(self) -> Optional[SarvamClient]:
        try:
            from agent.secret_scope import get_secret
        except ImportError:
            return None
        key = (get_secret("SARVAM_API_KEY") or "").strip()
        if not key:
            return None
        return SarvamClient(key, mode=str(self._setting("sarvam_mode", "modern-colloquial")))

    def interpreter(self) -> Interpreter:
        return Interpreter(ModelTranslator(self.ctx.llm, use_task=self.use_task), self._sarvam())

    # ---- gateway hook -----------------------------------------------------------------------------
    async def on_admitted_message(self, session_key: str = "", platform: str = "", source: Any = None,
                                  message_id: Any = None, text: str = "", **_: Any) -> Optional[dict]:
        """``post_gateway_admission``: runs only for messages Hermes already admitted (authorized
        senders, after slash commands were dispatched), so nobody Hermes would refuse can spend the
        owner's model or Sarvam key, or write to the roster."""
        src = _source(source, platform)
        if src is None or src.is_bot:
            return None
        if str(src.chat_type or "").lower() not in GROUP_CHAT_TYPES:
            return None
        key = chat_key(_platform_name(src), str(src.chat_id))
        if not self.store.is_enabled(key):
            return None  # Polyglot only acts in chats an authorized user switched on

        text = (text or "").strip()
        if text.startswith("/") or self._addressed_to_assistant(text):
            return None  # commands and questions for Hermes take the normal agent turn

        try:
            reply = await self._interpret(key, src, text)
        except TranslationError as exc:
            logger.warning("polyglot: translation failed in %s: %s", key, exc)
            return {"action": "handled"}
        except Exception:  # noqa: BLE001 - fail closed: a broken translation never becomes an agent turn
            logger.exception("polyglot: unexpected error in %s", key)
            return {"action": "handled"}
        # Group chatter in an interpreted chat never becomes an agent turn, translated or not.
        return {"action": "handled", "reply": reply} if reply else {"action": "handled"}

    def _addressed_to_assistant(self, text: str) -> bool:
        if _BOT_MENTION.search(text):
            return True
        wake = str(self._setting("wake_word", "hermes") or "").strip().casefold()
        if not wake:
            return False
        head = text.casefold()
        return head == wake or (head.startswith(wake) and not head[len(wake):len(wake) + 1].isalnum())

    async def _interpret(self, key: str, source: Any, text: str) -> Optional[str]:
        user_id = str(getattr(source, "user_id", "") or "")
        targets = self.store.languages(key)
        speaker = self.store.member_language(key, user_id)
        # Someone who has not chosen a language yet: learn it from what they write, so nobody has to
        # run a command before the group works for them.
        newcomer = speaker is None and bool(user_id) and bool(self._setting("auto_language", True))
        if not targets and not newcomer:
            return None
        if not _worth_translating(text):
            return None
        learn = newcomer and _letters(text) >= AUTO_LANGUAGE_MIN_LETTERS
        if not targets and not learn:
            return None
        # With nobody to translate for yet, the call is still made (into English) to detect the language.
        result = await self.interpreter().translate(text, targets or [ENGLISH], speaker_hint=speaker)
        reply = formatting.render(result, targets)
        if learn and result.source is not None:
            name = str(getattr(source, "user_name", "") or "you")
            try:
                self.store.set_member_language(key, user_id, name, result.source)
            except ValueError:  # the chat is full; translating still works
                return reply
            return "\n".join(filter(None, [reply, formatting.learned(name, result.source)]))
        return reply

    # ---- commands ---------------------------------------------------------------------------------
    def set_language(self, key: str, source: Any, args: str) -> str:
        name = str(getattr(source, "user_name", "") or "you")
        user_id = str(getattr(source, "user_id", "") or "")
        if not args:
            return formatting.roster(self.store.members(key), self.store.member_language(key, user_id))
        if args.casefold() in {"off", "none", "stop", "clear"}:
            self.store.set_member_language(key, user_id, name, None)
            return f"OK {name}, you won't get translations in this chat any more."
        if not user_id:
            return "I can't tell who sent that, so I can't save a language for it."
        lang = parse(args)
        if lang is None:
            return f"I don't recognise “{args[:40]}” as a language. Try /lang english, /lang kannada, /lang español…"
        try:
            self.store.set_member_language(key, user_id, name, lang)
        except ValueError as exc:
            return str(exc)
        label = lang.native if lang.native == lang.name else f"{lang.native} ({lang.name})"
        return f"✓ {name} will read this chat in {label}."

    async def lang_command(self, raw_args: str, *, source: Any = None, **_: Any) -> str:
        if source is None or str(getattr(source, "chat_type", "")).lower() not in GROUP_CHAT_TYPES:
            return "Use /lang inside a group chat where Polyglot is on."
        key = chat_key(_platform_name(source), str(source.chat_id))
        if not self.store.is_enabled(key):
            return "Polyglot is off in this chat. Turn it on with /polyglot on."
        return self.set_language(key, source, (raw_args or "").strip())

    async def polyglot_command(self, raw_args: str, *, source: Any = None, **_: Any) -> str:
        action = (raw_args or "").strip().casefold() or "status"
        if source is None or str(getattr(source, "chat_type", "")).lower() not in GROUP_CHAT_TYPES:
            return ("Polyglot interprets group chats. Add me to a group, then send /polyglot on there.\n"
                    + formatting.HELP)
        key = chat_key(_platform_name(source), str(source.chat_id))
        if action in {"on", "start", "enable"}:
            try:
                self.store.set_enabled(key, True)
            except ValueError as exc:
                return str(exc)
            return formatting.WELCOME
        if action in {"off", "stop", "disable"}:
            self.store.set_enabled(key, False)
            return "Polyglot is off in this chat. Language choices are kept for next time."
        if action == "help":
            return formatting.HELP
        if not self.store.is_enabled(key):
            return "Polyglot is off in this chat. Send /polyglot on to start translating it."
        state = "on"
        engine = "Sarvam AI for Indian languages + your Hermes model" if self._sarvam() else "your Hermes model"
        return (f"Polyglot is {state} here. Translating with {engine}.\n"
                + formatting.roster(self.store.members(key), None)
                + formatting.access_hint(_platform_name(source), str(source.chat_id)))


# ---- helpers --------------------------------------------------------------------------------------

def _platform_name(source: Any) -> str:
    platform = getattr(source, "platform", None)
    return str(getattr(platform, "value", platform) or "unknown")


def _source(source: Any, platform: str) -> Optional[SimpleNamespace]:
    """The hook's ``source`` is a plain dict snapshot (``SessionSource.to_dict()``)."""
    if not isinstance(source, dict) or not source.get("chat_id"):
        return None
    return SimpleNamespace(
        platform=str(source.get("platform") or platform or "unknown"), chat_id=str(source["chat_id"]),
        chat_type=str(source.get("chat_type") or "dm"), user_id=source.get("user_id"),
        user_name=source.get("user_name"), thread_id=source.get("thread_id"), is_bot=bool(source.get("is_bot")))


def _letters(text: str) -> int:
    return len(_LETTER.findall(_URL.sub("", text)))


def _worth_translating(text: str) -> bool:
    """Skip messages with nothing to translate: emoji, links, "ok", "+1"."""
    return _letters(text) >= 2


def register(ctx: Any) -> None:
    plugin = Polyglot(ctx)
    register_task = getattr(ctx, "register_auxiliary_task", None)
    if callable(register_task):
        try:
            register_task(
                AUX_TASK,
                display_name="Polyglot translation",
                description="Model used to translate group-chat messages. Defaults to your main model; "
                            "a fast, cheap model is usually enough.",
            )
            plugin.use_task = True
        except Exception:  # noqa: BLE001 - older Hermes: fall back to the main model
            logger.info("polyglot: auxiliary task slot unavailable; translating with the main model")
    ctx.register_hook("post_gateway_admission", plugin.on_admitted_message)
    ctx.register_command("polyglot", handler=plugin.polyglot_command,
                         description="Live interpreter for this group: /polyglot on | off | status | help",
                         args_hint="on|off|status|help")
    ctx.register_command("lang", handler=plugin.lang_command,
                         description="Choose the language you read this group in: /lang kannada",
                         args_hint="<language>|off")
