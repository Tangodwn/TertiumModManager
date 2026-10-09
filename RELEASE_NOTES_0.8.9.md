# Tertium Mod Manager v0.8.9 — One-Stop Launch

## Release focus

v0.8.9 makes Tertium authoritative for the Darktide mod load order during normal play. The goal is the one-stop workflow: manage mods in Tertium, click **PLAY MODDED**, and let the launcher prepare Darktide without handing the user back to manual load-order maintenance.

## One-stop modded launch

- **PLAY MODDED** now rebuilds `mods/mod_load_order.txt` from the mods that are enabled in Tertium before launch.
- Existing relative order and comments are preserved where possible.
- Disabled, removed, and stale load-order entries are removed automatically.
- DMF/base are not written as normal load-order entries.
- The load-order file is maintained even when AML is installed, missing, stale, or cannot be fingerprint-verified.
- Crash Guard quarantine changes also keep the load order synchronized.

## Existing automation retained

- Darktide Mod Loader repair after game updates.
- Guardian Auto-Link for existing mods.
- Nexus Update All with browser fallback when Nexus authorization is required.
- Guardian crash analysis/quarantine.
- Tertium application self-update support introduced in v0.8.8.

## Update-channel note

The in-launcher updater requires a release endpoint that the installed application can reach without embedding private GitHub credentials. A private GitHub repository therefore still needs either public repository visibility or a separate public release channel for true zero-touch application updates.

Tertium Mod Manager remains a free, unofficial community project. It is not affiliated with or endorsed by Fatshark, Games Workshop, Nexus Mods, or GitHub.
