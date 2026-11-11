# Polyglot: a live interpreter for Hermes group chats

Grandma writes in Kannada. Her grandson in Toronto reads English. The cousin in Madrid reads Spanish.
With Polyglot, they're all in one group with Hermes, and each of them reads every message (and every
voice note) in their own language.

```
Asha:   ನಾಳೆ ಹಬ್ಬಕ್ಕೆ ಎಲ್ಲರೂ ಬರ್ತೀರಾ?
Hermes: 🌐 English: Is everyone coming for the festival tomorrow?
        🌐 Español: ¿Vienen todos a la fiesta mañana?

Ravi:   Yes! Landing at 6pm 🎉
Hermes: 🌐 ಕನ್ನಡ: ಹೌದು! ಸಂಜೆ 6 ಗಂಟೆಗೆ ಇಳಿಯುತ್ತೇನೆ 🎉
        🌐 Español: ¡Sí! Aterrizo a las 6 de la tarde 🎉
```

## Setup (two minutes)

1. Install: `hermes plugins install polyglot`, then restart the gateway (`hermes gateway restart`).
2. Add your Hermes bot to a group. On Telegram, turn the bot's **privacy mode off** in @BotFather
   (`/setprivacy` → Disable) so it can read group messages.
3. In the group, an authorized Hermes user sends **`/polyglot on`**.
4. Let the group's members through. Hermes ignores group messages from people who are not on its
   allow-list, before any plugin sees them. Send **`/polyglot status`** to get the group's id, add it to
   Hermes's `.env` as `TELEGRAM_GROUP_ALLOWED_CHATS=<id>` (or `group_allowed_chats` in `config.yaml`),
   and restart the gateway. Skip this if everyone in the group is already a Hermes user.
5. Everyone just chats. Polyglot learns each person's language from the first message they write
   and tells them what it picked. To read in a different language, send **`/lang <language>`**:
   `/lang english`, `/lang kannada`, `/lang हिन्दी`, `/lang español`… Native names, English names and
   codes all work, and any language your model knows is accepted.

That's it. It uses the model Hermes already runs on, so there is nothing else to configure.

**Optional: Sarvam AI for Indian languages.** Add a [Sarvam AI](https://www.sarvam.ai) key in
Desktop → Settings → Plugins → Polyglot (or `SARVAM_API_KEY` in Hermes's secrets). Polyglot then uses
Sarvam for translation between Indian languages and English (all 22 scheduled languages) and for voice
notes, including code-mixed speech like Hinglish and Kanglish. Everything else still goes to your model.

**Optional: a cheaper model for translation.** Polyglot registers its own model slot, *Polyglot
translation*. Pick a fast model for it in `hermes model` → auxiliary models (or Desktop → Settings →
Models → Auxiliary). By default it uses your main model.

## Commands

| Command | Who | What |
|---|---|---|
| `/polyglot on` / `off` | Authorized Hermes users | Start or stop interpreting this group |
| `/polyglot status` | Authorized Hermes users | Which engine is used, and who reads what |
| `/lang <language>` | Anyone in a group where Polyglot is on | Change the language *you* read the group in (it is picked automatically from your first message) |
| `/lang off` | Anyone | Stop receiving translations |
| `/lang` | Anyone | Show everyone's languages |

Talking to Hermes still works: start a message with **"hermes,"** (configurable) or @mention the bot,
and Hermes answers as usual instead of translating.

## How it works

Polyglot uses Hermes's `pre_gateway_dispatch` hook, which sees each incoming group message before the
agent does. In a group where Polyglot is on, it:

1. skips messages with nothing to translate (emoji, links, "+1"), commands, and messages addressed to Hermes;
2. transcribes voice notes (Sarvam if configured, otherwise Hermes's own speech-to-text);
3. works out which languages the group reads, excluding the language the message is actually written in
   (a member's chosen language is only a hint: people switch languages mid-chat);
4. translates into all of them in **one** model call (or with Sarvam for Indian language pairs), with
   instructions to keep names, emoji, tone and code-mixing, and never to act on the message's content;
5. replies to the original message with one line per language, and **does not start an agent turn**.

## Privacy, cost and what this plugin does (disclosure)

- **Network calls:** message text goes to the model provider Hermes is configured with (through Hermes's
  own `ctx.llm`). If you add a Sarvam key, message text and voice-note audio for Indian languages also go to
  `https://api.sarvam.ai`. No other services are contacted. No telemetry.
- **Which chats and people:** Polyglot acts only in groups where an authorized Hermes user ran
  `/polyglot on`, and only on messages Hermes lets through. On Telegram that means members on Hermes's
  allow-list, or every member once you add the group to `group_allowed_chats`. Allowing a group is
  Hermes's own setting and also lets those members talk to Hermes there ("hermes, ..."), so only allow
  groups you trust. `/lang` only changes the sender's own reading language. Each translated message
  costs one model call (or Sarvam calls), so turn Polyglot off in groups that don't need it.
- **Data kept:** per group, the members' chosen languages and display names, stored in Hermes's plugin
  state (`<hermes home>/plugin-data/`). Messages and translations are not stored by the plugin.
- **Files read:** voice-note audio files that Hermes's gateway has already downloaded to its cache.
- **No shell commands, no background processes, no files outside the plugin's own state.**
- **Isolation:** the hook needs the live gateway to post replies, so run with the default
  `plugins.isolation: in_process`.

## Develop

```bash
uv run --no-project --with pytest python -m pytest tests -q   # offline tests, fake gateway + model
hermes plugins validate .                                     # the check the catalog runs
```

MIT licensed.
