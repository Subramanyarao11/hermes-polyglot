"""Offline tests: fake gateway, fake model, fake Sarvam. Run: python -m pytest tests -q"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from dataclasses import dataclass, field
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


class MessageType(Enum):
    TEXT = "text"
    VOICE = "voice"


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

    def transcribe(self, path: str):
        return "ನಮಸ್ಕಾರ, ಊಟ ಆಯ್ತಾ?", languages.by_sarvam_code("kn-IN")


class FakeAdapter:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, chat_id: str, content: str, reply_to=None, metadata=None):
        self.sent.append({"chat_id": chat_id, "content": content, "reply_to": reply_to, "metadata": metadata})


@dataclass
class Source:
    user_id: str
    user_name: str
    chat_id: str = "-100"
    chat_type: str = "group"
    platform: Platform = Platform.TELEGRAM
    thread_id: Optional[str] = None
    is_bot: bool = False


@dataclass
class Event:
    text: str
    source: Source
    message_id: str = "m1"
    internal: bool = False
    message_type: MessageType = MessageType.TEXT
    media_urls: list = field(default_factory=list)


def make_plugin(llm: Optional[FakeLlm] = None, sarvam: Optional[FakeSarvam] = None):
    ctx = SimpleNamespace(state=FakeState(), llm=llm or FakeLlm(), get_config=lambda key, default=None: default)
    plugin = polyglot.Polyglot(ctx)
    plugin._sarvam = lambda: sarvam  # type: ignore[method-assign]
    adapter = FakeAdapter()
    gateway = SimpleNamespace(adapters={Platform.TELEGRAM: adapter})
    return plugin, gateway, adapter


def run(coro):
    return asyncio.run(coro)


ASHA = Source("1", "Asha")
RAVI = Source("2", "Ravi")
MARIA = Source("3", "Maria")


def setup_chat(plugin, gateway):
    run(plugin.polyglot_command("on", source=ASHA))
    for src, lang in ((ASHA, "kannada"), (RAVI, "english"), (MARIA, "español")):
        run(plugin.on_gateway_dispatch(Event(f"/lang {lang}", src), gateway))


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

def test_does_nothing_until_enabled():
    plugin, gateway, adapter = make_plugin()
    assert run(plugin.on_gateway_dispatch(Event("hello all", ASHA), gateway)) is None
    assert adapter.sent == []


def test_ignores_direct_messages():
    plugin, gateway, adapter = make_plugin()
    dm = Source("1", "Asha", chat_type="dm")
    run(plugin.polyglot_command("on", source=ASHA))
    assert run(plugin.on_gateway_dispatch(Event("hello", dm), gateway)) is None


def test_lang_command_is_handled_before_auth_in_enabled_chats():
    plugin, gateway, adapter = make_plugin()
    run(plugin.polyglot_command("on", source=ASHA))
    out = run(plugin.on_gateway_dispatch(Event("/lang@hermes_bot kannada", RAVI), gateway))
    assert out == {"action": "skip", "reason": "polyglot-lang"}
    assert "ಕನ್ನಡ" in adapter.sent[-1]["content"]


def test_translates_for_every_other_language_and_skips_agent_turn():
    llm = FakeLlm(source_language="English")
    plugin, gateway, adapter = make_plugin(llm)
    setup_chat(plugin, gateway)
    adapter.sent.clear()
    out = run(plugin.on_gateway_dispatch(Event("Dinner at 8?", RAVI, message_id="42"), gateway))
    assert out["action"] == "skip"
    reply = adapter.sent[-1]
    assert reply["reply_to"] == "42"
    assert "[kn] Dinner at 8?" in reply["content"] and "[es] Dinner at 8?" in reply["content"]
    assert "[en]" not in reply["content"]  # nobody needs English → English
    assert "task" not in llm.calls[-1]  # no aux slot registered in this fake ctx → main model
    plugin.use_task = True
    run(plugin.on_gateway_dispatch(Event("See you soon", RAVI), gateway))
    assert llm.calls[-1]["task"] == translate.AUX_TASK


def test_declared_language_is_only_a_hint():
    """Asha reads Kannada but writes in English: Kannada readers must still get a translation."""
    llm = FakeLlm(source_language="English")
    plugin, gateway, adapter = make_plugin(llm)
    setup_chat(plugin, gateway)
    run(plugin.on_gateway_dispatch(Event("I'm running late", ASHA), gateway))
    assert "[kn] I'm running late" in adapter.sent[-1]["content"]


def test_wake_word_and_mentions_go_to_hermes():
    plugin, gateway, adapter = make_plugin()
    setup_chat(plugin, gateway)
    for text in ("hermes, what's the weather?", "Hermes: book a table", "@hermes_bot summarise this"):
        assert run(plugin.on_gateway_dispatch(Event(text, RAVI), gateway)) is None
    assert run(plugin.on_gateway_dispatch(Event("hermeneutics is fun", RAVI), gateway))["action"] == "skip"


def test_other_slash_commands_pass_through():
    plugin, gateway, _ = make_plugin()
    setup_chat(plugin, gateway)
    assert run(plugin.on_gateway_dispatch(Event("/polyglot off", ASHA), gateway)) is None
    assert run(plugin.on_gateway_dispatch(Event("/help", ASHA), gateway)) is None


def test_emoji_and_links_are_not_translated():
    llm = FakeLlm()
    plugin, gateway, adapter = make_plugin(llm)
    setup_chat(plugin, gateway)
    adapter.sent.clear()
    for text in ("😂😂", "+1", "https://example.com/x"):
        assert run(plugin.on_gateway_dispatch(Event(text, RAVI), gateway))["action"] == "skip"
    assert adapter.sent == [] and llm.calls == []


def test_sarvam_handles_indian_pairs_and_model_handles_the_rest():
    llm, sarvam = FakeLlm(source_language="Kannada"), FakeSarvam(detected="kn-IN")
    plugin, gateway, adapter = make_plugin(llm, sarvam)
    setup_chat(plugin, gateway)
    llm.calls.clear()
    run(plugin.on_gateway_dispatch(Event("ಊಟ ಆಯ್ತಾ?", ASHA), gateway))
    content = adapter.sent[-1]["content"]
    assert sarvam.translated == [("kn", "en")]
    assert "<sarvam en>" in content and "[es]" in content and "[kn]" not in content
    assert list(llm.calls[-1]["json_schema"]["properties"]["translations"]["properties"]) == ["es"]


def test_voice_note_is_transcribed_then_translated():
    llm, sarvam = FakeLlm(), FakeSarvam(detected="kn-IN")
    plugin, gateway, adapter = make_plugin(llm, sarvam)
    setup_chat(plugin, gateway)
    voice = Event("", ASHA, message_type=MessageType.VOICE, media_urls=["/tmp/v.ogg"])
    run(plugin.on_gateway_dispatch(voice, gateway))
    content = adapter.sent[-1]["content"]
    assert content.startswith("🎙 ನಮಸ್ಕಾರ") and "<sarvam en>" in content


def test_translation_failure_is_silent_and_never_raises():
    class Broken(FakeLlm):
        async def acomplete_structured(self, **kwargs):
            raise translate.TranslationError("provider down")
    plugin, gateway, adapter = make_plugin(Broken())
    setup_chat(plugin, gateway)
    adapter.sent.clear()
    assert run(plugin.on_gateway_dispatch(Event("hello there", RAVI), gateway))["reason"] == "polyglot-failed"
    assert adapter.sent == []


def test_polyglot_command_in_dm_explains():
    plugin, _, _ = make_plugin()
    assert "group" in run(plugin.polyglot_command("on", source=Source("1", "Asha", chat_type="dm")))


# ---- learning languages automatically ------------------------------------------------------------------

def test_first_message_sets_the_senders_language_without_any_command():
    llm = FakeLlm(source_language="English")
    plugin, gateway, adapter = make_plugin(llm)
    run(plugin.polyglot_command("on", source=RAVI))
    key = store.chat_key("telegram", "-100")

    out = run(plugin.on_gateway_dispatch(Event("Is everyone coming tomorrow?", RAVI), gateway))
    assert out["action"] == "skip"
    assert plugin.store.member_language(key, "2").code == "en"
    first = adapter.sent[-1]["content"]
    assert first.startswith("👋 Ravi") and "English" in first and "🌐" not in first  # nobody to translate for yet

    llm.source_language = "Kannada"
    run(plugin.on_gateway_dispatch(Event("ನಾಳೆ ಹಬ್ಬಕ್ಕೆ ಎಲ್ಲರೂ ಬರ್ತೀರಾ?", ASHA), gateway))
    assert plugin.store.member_language(key, "1").code == "kn"
    second = adapter.sent[-1]["content"]
    assert "[en] ನಾಳೆ" in second and "👋 Asha" in second and "ಕನ್ನಡ" in second

    llm.source_language = "English"
    run(plugin.on_gateway_dispatch(Event("Yes, landing at six", RAVI), gateway))
    third = adapter.sent[-1]["content"]
    assert "[kn] Yes, landing at six" in third and "👋" not in third  # the note is shown only once


def test_short_first_messages_do_not_decide_a_language():
    llm = FakeLlm()
    plugin, gateway, adapter = make_plugin(llm)
    run(plugin.polyglot_command("on", source=RAVI))
    run(plugin.on_gateway_dispatch(Event("hi all", RAVI), gateway))
    assert plugin.store.member_language(store.chat_key("telegram", "-100"), "2") is None
    assert adapter.sent == [] and llm.calls == []


def test_automatic_languages_can_be_switched_off():
    llm = FakeLlm()
    plugin, gateway, adapter = make_plugin(llm)
    plugin.ctx.get_config = lambda key, default=None: False if key == "auto_language" else default
    run(plugin.polyglot_command("on", source=RAVI))
    run(plugin.on_gateway_dispatch(Event("Is everyone coming tomorrow?", RAVI), gateway))
    assert adapter.sent == [] and llm.calls == []


def test_polyglot_status_when_off_says_how_to_start():
    plugin, _, _ = make_plugin()
    assert "/polyglot on" in run(plugin.polyglot_command("", source=RAVI))
