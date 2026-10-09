# Tertium Mod Manager v0.8.16 — Reliable Self Update

This release fixes the in-app updater disappearing after confirmation without reopening Tertium.

## Self-update hardening

- The updater handoff now runs from Tertium's update-cache directory instead of inheriting the installed application directory as its working directory.
- The update no longer renames/moves the live Tertium installation directory.
- The verified GitHub package is expanded to staging, the existing install is backed up, and updated files are copied in place after Tertium exits.
- Tertium is then restarted explicitly from the installed path.
- The updater writes `last-update.log` in the update cache so any future handoff failure has a concrete reason instead of silently disappearing.
- Rollback copies are retained during the handoff and restored if file replacement fails.

## Retained from v0.8.15

- Free Nexus accounts use the guided Update All queue when Nexus requires Mod Manager Download / Slow Download authorization.
- Premium/direct-authorized accounts continue to update automatically.
- No manual ZIP extraction or load-order editing is required.
