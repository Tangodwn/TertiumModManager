# Tertium Mod Manager v0.8.33 — Launch State & Update Health

This release improves the everyday one-button experience by making launch and updater state visible instead of opaque.

- Adds a live launch-state line to the Play dashboard:
  - Darktide running
  - Darktide launcher open
  - ready
  - game folder not configured
- Keeps the state synchronized with the existing Darktide session monitor.
- Reads the most recent in-app updater log at startup and summarizes whether the previous update succeeded, failed, rolled back, or appears incomplete.
- Adds **Open Update Log** in Settings for direct access to the updater log.
- Adds **Clean Update Cache** to remove stale update ZIPs/scripts/staging folders while preserving the latest update log.
- Retains the v0.8.32 path-aware Darktide launcher reuse and duplicate-launch protection.
