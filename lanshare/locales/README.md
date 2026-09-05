# Web UI translations

Each file here is one language for the recipient-facing web UI (the file browser, login page,
and error messages) — a flat JSON object of `"key": "translated string"`.

## Adding a language

1. Copy `en.json` to `<code>.json`, using a short language code (e.g. `fr.json`, `pt-br.json`).
2. Translate the values. Leave the keys untouched.
3. You don't have to translate every key — any key you skip just falls back to English, so a
   partial translation is still useful and safe to submit.
4. Some strings contain `{placeholders}` like `{name}`, `{n}`, or `{sec}` — keep those exactly as
   they are, just move them to wherever they read naturally in your language.
5. That's it — no code changes needed. The new file is picked up automatically and becomes an
   option in the language toggle in the app's header and on the login page.

`en.json` is the reference: it must stay complete, since every other language falls back to it.
