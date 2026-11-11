"""Language table: names people type, the label shown in chat, and Sarvam's code where it has one."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Language:
    code: str  # stable key stored in state: ISO 639 code, or "x-<slug>" for a free-form language
    name: str  # English name, what the model is asked to translate into
    native: str  # label shown in chat, in the language's own script
    sarvam: Optional[str] = None  # Sarvam AI language code, when Sarvam supports it
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Sarvam text translation: mayura:v1 covers these 11; sarvam-translate:v1 covers all with a sarvam code.
MAYURA_CODES = frozenset({
    "bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN",
})

_LANGUAGES: tuple[Language, ...] = (
    # Languages Sarvam supports (22 scheduled languages of India plus English)
    Language("en", "English", "English", "en-IN", ("eng", "inglish")),
    Language("hi", "Hindi", "हिन्दी", "hi-IN", ("hindi", "हिंदी")),
    Language("kn", "Kannada", "ಕನ್ನಡ", "kn-IN", ("kannad", "canarese")),
    Language("ta", "Tamil", "தமிழ்", "ta-IN", ("tamizh",)),
    Language("te", "Telugu", "తెలుగు", "te-IN", ()),
    Language("ml", "Malayalam", "മലയാളം", "ml-IN", ()),
    Language("mr", "Marathi", "मराठी", "mr-IN", ()),
    Language("bn", "Bengali", "বাংলা", "bn-IN", ("bangla",)),
    Language("gu", "Gujarati", "ગુજરાતી", "gu-IN", ()),
    Language("pa", "Punjabi", "ਪੰਜਾਬੀ", "pa-IN", ("panjabi",)),
    Language("or", "Odia", "ଓଡ଼ିଆ", "od-IN", ("oriya", "od")),
    Language("ur", "Urdu", "اردو", "ur-IN", ()),
    Language("as", "Assamese", "অসমীয়া", "as-IN", ()),
    Language("ne", "Nepali", "नेपाली", "ne-IN", ()),
    Language("kok", "Konkani", "कोंकणी", "kok-IN", ()),
    Language("sa", "Sanskrit", "संस्कृतम्", "sa-IN", ()),
    Language("mai", "Maithili", "मैथिली", "mai-IN", ()),
    Language("ks", "Kashmiri", "कॉशुर", "ks-IN", ()),
    Language("sd", "Sindhi", "سنڌي", "sd-IN", ()),
    Language("doi", "Dogri", "डोगरी", "doi-IN", ()),
    Language("brx", "Bodo", "बड़ो", "brx-IN", ()),
    Language("sat", "Santali", "ᱥᱟᱱᱛᱟᱲᱤ", "sat-IN", ()),
    Language("mni", "Manipuri", "মৈতৈলোন্", "mni-IN", ("meitei",)),
    # Widely spoken world languages (translated by the user's own model)
    Language("es", "Spanish", "Español", None, ("espanol", "castellano")),
    Language("fr", "French", "Français", None, ("francais",)),
    Language("de", "German", "Deutsch", None, ()),
    Language("pt", "Portuguese", "Português", None, ("portugues",)),
    Language("it", "Italian", "Italiano", None, ()),
    Language("nl", "Dutch", "Nederlands", None, ()),
    Language("pl", "Polish", "Polski", None, ()),
    Language("ru", "Russian", "Русский", None, ()),
    Language("uk", "Ukrainian", "Українська", None, ()),
    Language("el", "Greek", "Ελληνικά", None, ()),
    Language("tr", "Turkish", "Türkçe", None, ()),
    Language("ar", "Arabic", "العربية", None, ()),
    Language("he", "Hebrew", "עברית", None, ()),
    Language("fa", "Persian", "فارسی", None, ("farsi",)),
    Language("sw", "Swahili", "Kiswahili", None, ()),
    Language("zh", "Chinese (Simplified)", "中文", None, ("chinese", "mandarin", "zh-cn")),
    Language("ja", "Japanese", "日本語", None, ()),
    Language("ko", "Korean", "한국어", None, ()),
    Language("id", "Indonesian", "Bahasa Indonesia", None, ("bahasa",)),
    Language("ms", "Malay", "Bahasa Melayu", None, ()),
    Language("vi", "Vietnamese", "Tiếng Việt", None, ()),
    Language("th", "Thai", "ไทย", None, ()),
    Language("tl", "Filipino", "Filipino", None, ("tagalog",)),
    Language("si", "Sinhala", "සිංහල", None, ("sinhalese",)),
)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().casefold()


_INDEX: dict[str, Language] = {}
for _lang in _LANGUAGES:
    for _key in (_lang.code, _lang.name, _lang.native, *(_lang.aliases)):
        _INDEX.setdefault(_norm(_key), _lang)
    if _lang.sarvam:
        _INDEX.setdefault(_norm(_lang.sarvam), _lang)

_FREEFORM = re.compile(r"^[^\W\d_][^\W\d_ ()'-]*(?:[ '-][^\W\d_]+){0,3}$")


def by_code(code: str) -> Optional[Language]:
    """The language stored under ``code`` (known code, or a free-form ``x-`` language)."""
    found = _INDEX.get(_norm(code))
    if found is not None and found.code == code:
        return found
    if code.startswith("x-"):
        name = code[2:].replace("-", " ").title()
        return Language(code, name, name)
    return None


def parse(text: str) -> Optional[Language]:
    """Resolve what a person typed ("kannada", "ಕನ್ನಡ", "kn", "Hinglish"...) to a Language.

    Unknown but plausible language names are accepted as free-form languages, so any language the
    user's model can translate works, not just the ones listed here.
    """
    query = _norm(text)
    if not query:
        return None
    found = _INDEX.get(query)
    if found is not None:
        return found
    if len(query) <= 40 and _FREEFORM.match(query):
        slug = re.sub(r"[\s']+", "-", query)
        name = " ".join(part.capitalize() for part in query.split())
        return Language(f"x-{slug}", name, name)
    return None


def sarvam_pair_model(source: Language, target: Language) -> Optional[str]:
    """The Sarvam model that can translate ``source`` → ``target``, or None when Sarvam can't."""
    if not (source.sarvam and target.sarvam):
        return None
    if source.sarvam in MAYURA_CODES and target.sarvam in MAYURA_CODES:
        return "mayura:v1"
    return "sarvam-translate:v1"


def by_sarvam_code(code: Optional[str]) -> Optional[Language]:
    if not code:
        return None
    found = _INDEX.get(_norm(code))
    return found if found is not None and found.sarvam == code else None


def known_languages() -> tuple[Language, ...]:
    return _LANGUAGES
