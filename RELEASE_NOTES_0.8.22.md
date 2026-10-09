# Tertium Mod Manager v0.8.22 — Crash Retest Control

This release fixes an issue where a mod that the user explicitly re-enabled could be immediately disabled again when PLAY MODDED re-read the previous crash log.

## Behavior

When a user explicitly re-enables a previously quarantined mod:

- Tertium clears the active quarantine.
- Prior crash evidence is retained.
- Prior signatures are re-armed for testing.
- A retest timestamp is recorded.

PLAY MODDED will ignore crash evidence older than that explicit re-enable action. The mod will stay enabled for the test launch.

If the game crashes again after the re-enable timestamp, that new crash is fresh evidence and Crash Guard may quarantine the mod again.

This prevents stale crash history from instantly undoing the user's explicit re-enable before they can test an updated mod.
