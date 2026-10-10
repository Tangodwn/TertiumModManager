# Tertium Mod Manager v0.8.34 — Crash Context Ranking

This release improves Crash Guard when a Darktide crash does not contain a direct mod path.

- Keeps direct stack-path matches as the strongest evidence.
- Keeps learned same-build/same-mod-version crash signatures as strong evidence.
- Adds recently installed or updated enabled mods as conservative fallback context.
- Recent-change evidence is time-bounded to the two hours before the crash and never outranks direct or learned evidence.
- Recent-change suspects are medium/low confidence only, so Tertium does not automatically blame or quarantine a mod on timing alone.
- Crash Guard Advanced Details now shows the evidence source behind each ranked suspect.
- User-facing crash notices explicitly distinguish timing correlation from stronger crash attribution.
