# Tertium Mod Manager v0.8.20 — Crash Guard Delete

This release adds a direct delete/remove action to Crash Guard for a mod that has been identified as the crash suspect.

## Crash Guard action

When Tertium identifies a specific suspect, the simple Play dashboard now shows a **Delete <mod>** button alongside the existing disable/retry controls.

Choosing it:

- removes the suspect mod from the active Darktide mod installation;
- removes it from Tertium's tracked active registry;
- clears its active crash quarantine;
- rewrites `mod_load_order.txt` immediately;
- refreshes Crash Guard;
- keeps a reversible Tertium safety backup instead of permanently destroying the folder.

The mod will not load again unless it is restored or reinstalled.

This is intended for cases like Numeric UI where the player no longer wants the crashing mod present at all.
