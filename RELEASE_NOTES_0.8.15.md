# Tertium Mod Manager v0.8.15 — Free Nexus Guided Updates

This release restores the free-account Update All path.

## Update All behavior

- Tertium first checks every tracked Nexus mod for updates.
- If Nexus authorizes direct API downloads, Tertium downloads and installs them automatically.
- If Nexus requires free-user browser authorization, Tertium automatically moves the remaining updates into a guided queue.
- Tertium opens the exact Nexus file page for the next queued mod.
- The user clicks **Mod Manager Download / Slow Download** once.
- The resulting NXM authorization is captured by Tertium.
- Tertium downloads, backs up, installs, preserves enable state, and continues to the next queued update automatically.
- No manual ZIP extraction, folder replacement, or load-order editing is required.

Premium remains optional. It removes the authorization clicks, but the free-user workflow remains supported.

## Retained

- In-app GitHub package updates for Tertium itself.
- One-button PLAY MODDED repair/recovery.
- Automatic load-order ownership and crash quarantine.
