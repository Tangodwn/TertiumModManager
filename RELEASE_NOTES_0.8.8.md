# Tertium Mod Manager v0.8.8 — Guardian Self-Update

## Release focus

Guardian Self-Update moves Tertium toward the intended install-once workflow: normal users should not have to keep visiting GitHub and manually replacing the launcher.

## Tertium application updates

- Simple Mode now includes a dedicated Tertium update control separate from Darktide mod **Update All**.
- Tertium checks the canonical release feed shortly after launch.
- When a newer Tertium version is available, the button changes to **UPDATE TERTIUM → vX.Y.Z**.
- The launcher can download the official Windows installer into its local update cache.
- The downloaded installer is SHA-256 verified before execution.
- Tertium then hands off to a hidden updater process, exits, installs the update silently, and reopens automatically.
- Manual update checks are also available from Settings.
- If the release feed is unavailable, Tertium reports the reason and can open the Releases page instead of silently failing.

## Desktop shortcut

- The Windows installer now selects **Create a desktop shortcut** by default.
- Silent in-launcher upgrades also use the normal installer defaults, so a missing desktop shortcut can be created during the upgrade.

## Release-channel requirement

The self-updater intentionally does not embed GitHub credentials. The canonical release endpoint therefore needs to be publicly reachable for zero-touch updates. While the development repository is private, the launcher reports that limitation rather than weakening authentication or storing a GitHub token.

## Existing behavior retained

- Guardian Auto-Link still reconciles older local-only Darktide mods against Nexus.
- Guardian Recovery still provides one-click crash-suspect quarantine/retry.
- Mod Update All remains separate from Tertium application updates.
- Windows CI validates supported Python versions and the exact installer build before merge.

Tertium Mod Manager remains a free, unofficial community project. It is not affiliated with or endorsed by Fatshark, Games Workshop, Nexus Mods, or GitHub.
