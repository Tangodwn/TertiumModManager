# Tertium Mod Manager v0.8.10 — Public Update Channel

## Release focus

v0.8.10 completes the launcher-side move to a public binary update channel while keeping the development source repository private.

## Public application update channel

- Tertium now checks `Tangodwn/TertiumModManager-Releases` for application updates.
- The public release repository contains only Windows installers, SHA-256 checksums, and release notes.
- The private development repository remains the source/build system.
- The Windows release workflow is wired to publish installer assets to the public release repository.
- The launcher still verifies SHA-256 before handing the installer to the silent updater.

## One-stop launch retained

- PLAY MODDED continues to synchronize enabled mods into `mods/mod_load_order.txt` before launch.
- Disabled and stale load-order entries are removed automatically.
- Crash Guard keeps the managed load order synchronized.
- Darktide loader repair, Guardian Auto-Link, Nexus updates, profiles, rollback, and diagnostics remain available.

## One-time bootstrap

Users upgrading from builds that still point at the private development release feed may need to install v0.8.10 manually once. After v0.8.10 is installed, future Tertium releases can be discovered and installed from inside the launcher.

Tertium Mod Manager remains a free, unofficial community project.
