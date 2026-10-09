# Tertium Mod Manager v0.8.19 — Crash Learning

This release fixes a blind spot where Darktide could crash with a generic base-game Lua stack and Tertium would fail to flag the mod that had already caused the same error before.

## Learned crash detection

- Crash Guard now keeps prior crash evidence even after the user explicitly re-enables a quarantined mod.
- Re-enabling a mod also re-arms its prior crash signatures for testing instead of leaving them permanently dismissed.
- If a new crash has no direct mod path in the Lua stack, Tertium compares the normalized error against prior crash evidence for the same Darktide build and the same installed mod version.
- A unique match becomes a high-confidence learned suspect.
- Multiple learned matches remain medium confidence and are not auto-quarantined.

This is designed for cases like Numeric UI where the visible stack ends in a Fatshark player_husk_data_extension.lua error rather than a path under mods/NumericUI.

The goal is that a repeated crash with the same build/version/error can now be flagged and automatically quarantined even when the current stack itself is generic.
