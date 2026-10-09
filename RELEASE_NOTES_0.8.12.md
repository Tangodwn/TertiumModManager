# Tertium Mod Manager v0.8.12 — One-Button Recovery

## PLAY MODDED is now an automatic recovery path

The goal is simple: press PLAY MODDED and get into Darktide.

- Tertium still repairs DML, synchronizes the enabled mod set, enforces quarantines, and launches the game.
- If the previous modded session crashed and a high-confidence mod appears in the crash stack, Tertium automatically quarantines that mod before launch instead of asking the user to diagnose it.
- If the previous crash has no reliable single culprit, Tertium preserves the current state, switches to a core-only safe set, and launches rather than repeating the same broken combination.
- If a freshly launched modded session crashes while Tertium is still running, Tertium automatically performs one recovery attempt and relaunches:
  - high-confidence culprit -> quarantine that mod;
  - no reliable culprit -> core-only safe set.
- Automatic recovery is deliberately limited to one relaunch attempt so a base-game/core failure cannot create an infinite launch loop.
- Crash recovery always keeps mod_load_order.txt synchronized.

## UPDATE ALL

The v0.8.11 browser-free Update All behavior is retained:
- no automatic Nexus-page opening;
- no manual-download handoff from Update All;
- direct API download when Nexus authorizes it;
- one clear failure when Nexus refuses direct download authorization.

## Crash report addressed

The recovery path is intended to handle script crashes like:
`player_husk_data_extension.lua:277: attempt to index local 'field' (a nil value)`
without requiring the player to inspect Lua stacks or manually toggle mods.

Tertium still preserves snapshots and quarantine metadata so the user's normal mod set can be restored or investigated later.
