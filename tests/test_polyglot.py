"""Offline tests: fake gateway, fake model, fake Sarvam. Run: python -m pytest tests -q"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("polyglot", ROOT / "__init__.py",
                                               submodule_search_locations=[str(ROOT)])
polyglot = importlib.util.module_from_spec(_spec)
sys.modules["polyglot"] = polyglot
_spec.loader.exec_module(polyglot)

from polyglot import languages, store, translate  # noqa: E402


# ---- fakes ------------------------------------------------------------------------------------------

class Platform(Enum):
    TELEGRAM = "telegram"


class FakeState:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


class FakeLlm:
    """Pretends to be ctx.llm: 'translates' by tagging the text with the target code."""

    def __init__(self, source_language: str = "English") -> None:
        self.calls: list[dict[str, Any]] = []
        self.source_language = source_language

    async def acomplete_structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        codes = list(kwargs["json_schema"]["properties"]["translations"]["properties"])
        text = kwargs["input"][0]["text"].split("<<<\n", 1)[1].rsplit("\n>>>", 1)[0]
        return SimpleNamespace(content_type="json", text="", parsed={
            "source_language": self.source_language,
            "translations": {code: f"[{code}] {text}" for code in codes},
        })


class FakeSarvam:
    def __init__(self, detected: Optional[str] = "kn-IN") -> None:
        self.detected = detected
        self.translated: list[tuple[str, str]] = []

    def identify(self, text: str):
        return languages.by_sarvam_code(self.detected)

    def translate_one(self, text: str, source, target) -> str:
        self.translated.append((source.code, target.code))
        return f"<sarvam {target.code}> {text}"


@dataclass
class Source:
    user_id: str
    user_name: str
    chat_id: str = "-100"
    chat_type: str = "group"
    platform: Platform = Platform.TELEGRAM
    thread_id: Optional[str] = None
    is_bot: bool = False


def make_plugin(llm: Optional[FakeLlm] = None, sarvam: Optional[FakeSarvam] = None):
    ctx = SimpleNamespace(state=FakeState(), llm=llm or FakeLlm(), get_config=lambda key, default=None: default)
    plugin = polyglot.Polyglot(ctx)
    plugin._sarvam = lambda: sarvam  # type: ignore[method-assign]
    return plugin


def admit(plugin, text: str, src: "Source", message_id: str = "m1"):
    """Call the hook the way the gateway does: keyword payload, ``source`` as a plain dict."""
    snapshot = {"platform": src.platform.value, "chat_id": src.chat_id, "chat_type": src.chat_type,
                "user_id": src.user_id, "user_name": src.user_name}
    if src.thread_id:
        snapshot["thread_id"] = src.thread_id
    if src.is_bot:
        snapshot["is_bot"] = True
    return run(plugin.on_admitted_message(session_key="k", platform=src.platform.value, source=snapshot,
                                          message_id=message_id, text=text))


def reply(out):
    return (out or {}).get("reply") or ""


def run(coro):
    return asyncio.run(coro)


ASHA = Source("1", "Asha")
RAVI = Source("2", "Ravi")
MARIA = Source("3", "Maria")


def setup_chat(plugin):
    run(plugin.polyglot_command("on", source=ASHA))
    for src, lang in ((ASHA, "kannada"), (RAVI, "english"), (MARIA, "español")):
        run(plugin.lang_command(lang, source=src))


# ---- languages ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("typed,code", [
    ("kannada", "kn"), ("ಕನ್ನಡ", "kn"), ("KN", "kn"), ("hindi", "hi"), ("हिंदी", "hi"),
    ("español", "es"), ("Spanish", "es"), ("oriya", "or"), ("kn-IN", "kn"),
])
def test_parse_known_languages(typed, code):
    assert languages.parse(typed).code == code


def test_parse_freeform_language_and_rejects_junk():
    tulu = languages.parse("Tulu")
    assert tulu.code == "x-tulu" and languages.by_code("x-tulu").name == "Tulu"
    assert languages.parse("") is None
    assert languages.parse("rm -rf /") is None
    assert languages.parse("x" * 80) is None


def test_sarvam_model_choice():
    kn, en, ur, es = (languages.parse(x) for x in ("kannada", "english", "urdu", "spanish"))
    assert languages.sarvam_pair_model(kn, en) == "mayura:v1"
    assert languages.sarvam_pair_model(kn, ur) == "sarvam-translate:v1"
    assert languages.sarvam_pair_model(kn, es) is None


# ---- store -------------------------------------------------------------------------------------------

def test_store_roundtrip_and_distinct_languages():
    s = store.ChatStore(FakeState())
    key = store.chat_key("telegram", "-100", thread_id="7")
    assert key == "telegram:-100"
    s.set_enabled(key, True)
    s.set_member_language(key, "1", "Asha", languages.parse("kannada"))
    s.set_member_language(key, "2", "Ravi", languages.parse("english"))
    s.set_member_language(key, "4", "Dev", languages.parse("english"))
    assert [lang.code for lang in s.languages(key)] == ["kn", "en"]
    s.set_member_language(key, "1", "Asha", None)
    assert s.member_language(key, "1") is None


# ---- gateway hook ------------------------------------------------------------------------------------

def test_registers_the_post_admission_hook_only():
    hooks, commands = [], []
    ctx = SimpleNamespace(state=FakeState(), llm=FakeLlm(), get_config=lambda key, default=None: default,
                          register_hook=lambda name, fn: hooks.append(name),
                          register_command=lambda name, **kw: commands.append(name))
    polyglot.register(ctx)
    assert hooks == ["post_gateway_admission"] and commands == ["polyglot", "lang"]


def test_does_nothing_until_enabled():
    plugin = make_plugin()
    assert admit(plugin, "hello all", ASHA) is None


def test_ignores_direct_messages_bots_and_bad_payloads():
    plugin = make_plugin()
    run(plugin.polyglot_command("on", source=ASHA))
    assert admit(plugin, "hello", Source("1", "Asha", chat_type="dm")) is None
    assert admit(plugin, "hello there", Source("9", "Relay", is_bot=True)) is None
    assert run(plugin.on_admitted_message(source=None, text="hello")) is None


def test_lang_command_sets_only_the_senders_language():
    plugin = make_plugin()
    run(plugin.polyglot_command("on", source=ASHA))
    assert "ಕನ್ನಡ" in run(plugin.lang_command("kannada", source=RAVI))
    key = store.chat_key("telegram", "-100")
    assert plugin.store.member_language(key, "2").code == "kn"
    assert plugin.store.member_language(key, "1") is None


def test_translates_for_every_other_language_and_skips_agent_turn():
    llm = FakeLlm(source_language="English")
    plugin = make_plugin(llm)
    setup_chat(plugin)
    out = admit(plugin, "Dinner at 8?", RAVI, message_id="42")
    assert out["action"] == "handled"
    assert "[kn] Dinner at 8?" in out["reply"] and "[es] Dinner at 8?" in out["reply"]
    assert "[en]" not in out["reply"]  # nobody needs English → English
    assert "task" not in llm.calls[-1]  # no aux slot registered in this fake ctx → main model
    plugin.use_task = True
    admit(plugin, "See you soon", RAVI)
    assert llm.calls[-1]["task"] == translate.AUX_TASK


def test_declared_language_is_only_a_hint():
    """Asha reads Kannada but writes in English: Kannada readers must still get a translation."""
    plugin = make_plugin(FakeLlm(source_language="English"))
    setup_chat(plugin)
    assert "[kn] I'm running late" in reply(admit(plugin, "I'm running late", ASHA))


def test_wake_word_and_mentions_go_to_hermes():
    plugin = make_plugin()
    setup_chat(plugin)
    for text in ("hermes, what's the weather?", "Hermes: book a table", "@hermes_bot summarise this"):
        assert admit(plugin, text, RAVI) is None
    assert admit(plugin, "hermeneutics is fun", RAVI)["action"] == "handled"


def test_slash_commands_pass_through():
    plugin = make_plugin()
    setup_chat(plugin)
    assert admit(plugin, "/polyglot off", ASHA) is None
    assert admit(plugin, "/help", ASHA) is None


def test_emoji_links_and_empty_messages_are_consumed_silently():
    llm = FakeLlm()
    plugin = make_plugin(llm)
    setup_chat(plugin)
    for text in ("😂😂", "+1", "https://example.com/x", ""):
        assert admit(plugin, text, RAVI) == {"action": "handled"}
    assert llm.calls == []


def test_sarvam_handles_indian_pairs_and_model_handles_the_rest():
    llm, sarvam = FakeLlm(source_language="Kannada"), FakeSarvam(detected="kn-IN")
    plugin = make_plugin(llm, sarvam)
    setup_chat(plugin)
    llm.calls.clear()
    content = reply(admit(plugin, "ಊಟ ಆಯ್ತಾ?", ASHA))
    assert sarvam.translated == [("kn", "en")]
    assert "<sarvam en>" in content and "[es]" in content and "[kn]" not in content
    assert list(llm.calls[-1]["json_schema"]["properties"]["translations"]["properties"]) == ["es"]


def test_translation_failure_is_silent_and_never_becomes_an_agent_turn():
    class Broken(FakeLlm):
        async def acomplete_structured(self, **kwargs):
            raise translate.TranslationError("provider down")
    plugin = make_plugin(Broken())
    setup_chat(plugin)
    assert admit(plugin, "hello there", RAVI) == {"action": "handled"}


def test_polyglot_command_in_dm_explains():
    plugin = make_plugin()
    assert "group" in run(plugin.polyglot_command("on", source=Source("1", "Asha", chat_type="dm")))


def test_status_explains_access_without_widening_it():
    plugin = make_plugin()
    run(plugin.polyglot_command("on", source=ASHA))
    status = run(plugin.polyglot_command("status", source=ASHA))
    assert "Only messages from people Hermes admits are translated" in status
    assert "TELEGRAM_GROUP_ALLOWED_USERS" in status and "TELEGRAM_GROUP_ALLOWED_CHATS" not in status


# ---- learning languages automatically ------------------------------------------------------------------

def test_first_message_sets_the_senders_language_without_any_command():
    llm = FakeLlm(source_language="English")
    plugin = make_plugin(llm)
    run(plugin.polyglot_command("on", source=RAVI))
    key = store.chat_key("telegram", "-100")

    out = admit(plugin, "Is everyone coming tomorrow?", RAVI)
    assert out["action"] == "handled"
    assert plugin.store.member_language(key, "2").code == "en"
    first = out["reply"]
    assert first.startswith("👋 Ravi") and "English" in first and "🌐" not in first  # nobody to translate for yet

    llm.source_language = "Kannada"
    second = reply(admit(plugin, "ನಾಳೆ ಹಬ್ಬಕ್ಕೆ ಎಲ್ಲರೂ ಬರ್ತೀರಾ?", ASHA))
    assert plugin.store.member_language(key, "1").code == "kn"
    assert "[en] ನಾಳೆ" in second and "👋 Asha" in second and "ಕನ್ನಡ" in second

    llm.source_language = "English"
    third = reply(admit(plugin, "Yes, landing at six", RAVI))
    assert "[kn] Yes, landing at six" in third and "👋" not in third  # the note is shown only once


def test_short_first_messages_do_not_decide_a_language():
    llm = FakeLlm()
    plugin = make_plugin(llm)
    run(plugin.polyglot_command("on", source=RAVI))
    assert admit(plugin, "hi all", RAVI) == {"action": "handled"}
    assert plugin.store.member_language(store.chat_key("telegram", "-100"), "2") is None
    assert llm.calls == []


def test_automatic_languages_can_be_switched_off():
    llm = FakeLlm()
    plugin = make_plugin(llm)
    plugin.ctx.get_config = lambda key, default=None: False if key == "auto_language" else default
    run(plugin.polyglot_command("on", source=RAVI))
    assert admit(plugin, "Is everyone coming tomorrow?", RAVI) == {"action": "handled"}
    assert llm.calls == []


def test_polyglot_status_when_off_says_how_to_start():
    plugin = make_plugin()
    assert "/polyglot on" in run(plugin.polyglot_command("", source=RAVI))
