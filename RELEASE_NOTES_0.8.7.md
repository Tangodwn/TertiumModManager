# Tertium Mod Manager v0.8.7 — Guardian Auto-Link

## Release focus

Guardian Auto-Link removes the normal-user requirement to manually attach previously installed Darktide mods to Nexus after connecting a Nexus account.

## Automatic existing-mod reconciliation

- When a valid Nexus API key and Darktide installation are present, Tertium automatically checks adopted local-only mods against the Darktide Nexus catalog.
- Matching is conservative: Tertium auto-links only a unique exact normalized mod-name/folder-name match.
- Names such as `animation_events`, `true_level`, and `ForTheEmperor` can match Nexus display names such as “Animation Events,” “True Level,” and “For the Emperor!” without user input.
- The Nexus catalog is cached locally for 24 hours to avoid unnecessary repeated catalog requests.
- After a successful Nexus account connection, reconciliation runs automatically.
- It also runs automatically on later launches while unresolved local-only mods remain.
- The Mods tab provides **Auto-Link Existing** to retry the process manually if desired.

## Exact file matching and unknown baselines

- If the installed mod reports a version and exactly one Nexus file declares that version, Tertium records the exact Nexus file ID immediately.
- If the mod page is identified confidently but the historical file version cannot be established, Tertium links the Nexus mod page with an **unknown file baseline** rather than forcing the user to reconstruct old downloads.
- Unknown-baseline mods are still fully Nexus-linked.
- **Update All** treats an unknown baseline as needing the newest primary/main Nexus file. After that update, Tertium has an exact file ID and normal update lineage continues.
- Tertium never silently substitutes an optional file when selecting the latest baseline update.

## Safety

- Automatic reconciliation changes only Tertium's tracking metadata. It does not move, reinstall, enable, disable, or delete installed mods.
- Existing folder names, enabled/disabled state, and installed timestamp are preserved.
- A registry backup is created before a local placeholder is promoted to Nexus tracking.
- Ambiguous catalog matches are left unresolved rather than guessed.
- The old per-mod URL/file workflow remains available only as an Advanced Mode fallback for unusual mod names.

## Existing behavior retained

- Simple Mode remains the default.
- Guardian Recovery crash handling remains one-click for high-confidence suspects.
- Update All still attempts automatic Nexus downloads first and falls back to browser authorization only when Nexus requires it.
- Windows CI validates Python 3.11, 3.12, and 3.13 plus the exact Windows installer build before merge.

Tertium Mod Manager remains a free, unofficial community project. It is not affiliated with or endorsed by Fatshark, Games Workshop, or Nexus Mods.
