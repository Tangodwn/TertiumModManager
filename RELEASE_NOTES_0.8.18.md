# Tertium Mod Manager v0.8.18 — Quarantine Control

This release gives the player final control over a mod that Crash Guard previously disabled.

## Re-enable behavior

If a user explicitly re-enables a crash-quarantined mod, Tertium now treats that as an intentional override:

- the mod is enabled;
- matching crash quarantine flags are cleared;
- the enabled state is written back into `mod_load_order.txt`;
- Tertium will no longer silently disable that same mod again on the next launch solely because of the previous quarantine.

Crash evidence is retained for diagnostics, but the quarantine itself is removed.

## Remove behavior

Removing a mod now also clears its crash quarantine state, removes it from the active Darktide mod set, updates the load order immediately, and refreshes Crash Guard.

The removed mod is still moved into Tertium's reversible backup instead of being destroyed, so a mistaken removal can be recovered.

This specifically fixes the Numeric UI case where Crash Guard had disabled the mod and attempts to re-enable it appeared to revert.
