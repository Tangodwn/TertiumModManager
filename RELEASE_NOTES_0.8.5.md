# Tertium Mod Manager v0.8.5 — Guardian Recovery

## Release focus

Guardian Recovery tightens the two everyday workflows that should require the least thought: recovering from a mod-caused crash and updating tracked Nexus mods.

## Crash Guard

- High-confidence stack suspects now get an explicit **Disable <mod> & Retry** action.
- The one-click retry path no longer prompts twice after Tertium has already quarantined the suspect.
- **Launch Anyway** is available when the user deliberately wants to retry the current setup.
- **Advanced Details** opens Advanced Mode and shows the captured error, source log, and ranked mod candidates.
- One-click quarantine remains limited to high-confidence direct stack evidence.
- Quarantine remains scoped to the exact Darktide build and installed mod version, so a game or mod update naturally expires the block.
- Tertium still analyzes only the newest Darktide session so an older crash does not keep poisoning a later clean session.

## Nexus Update All

- Update All no longer makes the user choose a workflow based on account tier.
- Tertium now tries the supported automatic Nexus download path for each update.
- If Nexus requires browser authorization, Tertium automatically switches the remaining queue to guided authorization instead of failing and asking the user to restart Update All.
- The browser path still uses Nexus as the file source: the user clicks **Mod Manager Download**, Tertium receives the `nxm://` authorization, installs the update, and advances to the next required file.
- Local-only adopted mods remain untouched until they are legitimately linked through a Nexus install/download.
- The protected pre-update state snapshot remains in place before batch changes.

## Release / safety

- Simple Mode remains the default on every launch.
- The native Windows process check remains in place; no recurring `tasklist.exe` polling is reintroduced.
- Windows CI continues to run the Python test matrix plus the exact installer-only release builder, verifying both the installer and its SHA-256 file before merge.
- Tertium remains a free unofficial community project and does not mirror or rehost third-party mods.

Tertium Mod Manager is not affiliated with or endorsed by Fatshark, Games Workshop, or Nexus Mods.
