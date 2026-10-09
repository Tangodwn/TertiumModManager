# Tertium Mod Manager v0.8.13 — Clean Bootstrap

## Installer fix

This release fixes the manual/bootstrap installer error:

`CreateProcess failed; code 5. Access is denied.`

The installer no longer tries to auto-launch Tertium from Inno Setup after files are written. That final auto-launch step was the part failing on some systems with security software.

- The installation itself can finish cleanly.
- Manual/bootstrap installs are launched from the normal Tertium shortcut after setup exits.
- In-app self-updates continue to download, verify, silently install, and restart Tertium through Tertium's updater handoff.
- No Nexus or Darktide settings are removed by the update.

## Retained from v0.8.12

- One-button PLAY MODDED recovery.
- Automatic crash suspect quarantine.
- Core-only safe fallback when no reliable culprit is identified.
- One automatic recovery/relaunch attempt after a crash.
- Browser-free UPDATE ALL behavior.
