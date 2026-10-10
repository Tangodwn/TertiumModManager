# Tertium Mod Manager v0.8.31 — Launcher Guard & Load Order Hardening

This release focuses on launcher reliability and Tertium-owned load-order consistency.

- Prevents Tertium from starting a second Fatshark Darktide Launcher instance when one is already running.
- Blocks the duplicate-launch path that can cause `darktide_launcher.log` sharing violations and `System.IO.IOException`.
- Keeps using the existing native Windows process enumeration; no visible tasklist console is spawned.
- Tertium now maintains `mod_load_order.txt` after profile apply, troubleshooting safe mode, state restore, removed-mod restore, and mod-update rollback even when AML is installed.
- This removes remaining load-order ownership edge cases and keeps PLAY MODDED consistent with the active Tertium state.
