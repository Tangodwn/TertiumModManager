# Tertium Mod Manager v0.8.6 — Guardian Linker

## Release focus

Guardian Linker adds a safe way to convert manually installed/adopted local mods into Nexus-managed records without reinstalling or moving the mod files.

## Link Existing Mods

- Local-only mods now show **Link Nexus** in the Mods list.
- Select a local-only mod and click **Link Existing to Nexus**.
- Paste the Nexus mod page URL or numeric mod ID.
- Tertium validates the Nexus mod and tries to match the currently installed version to an exact Nexus file version.
- If there is one exact match, Tertium can link that file directly after confirmation.
- If the version is missing or ambiguous, Tertium opens the Nexus Files page and waits for the user to click **Mod Manager Download** on the exact file currently installed.
- In that linking mode, the received `nxm://` link is used only to identify the Nexus file. Tertium does **not** download, reinstall, enable, disable, or move the mod.
- The local placeholder record is then promoted to a real Nexus mod/file record so future Update All checks use the correct file-update lineage.
- Folder names, current enabled/disabled state, and installed timestamp are preserved.
- A registry backup is written before the local placeholder is replaced.
- Tertium refuses ambiguous duplicate mappings rather than merging two installed mods automatically.

## Mod list visibility

The Mods tab now shows linked/local counts, making it easy to work through an older manually installed setup and see how many mods still need linking.

## Existing behavior retained

- Simple Mode remains the default.
- Guardian Recovery crash handling remains one-click for high-confidence suspects.
- Update All still tries automatic Nexus downloads first, then falls back to browser authorization only when Nexus requires it.
- Windows CI runs the supported Python matrix and the exact installer-only release builder before merge.

Tertium Mod Manager remains a free, unofficial community project. It is not affiliated with or endorsed by Fatshark, Games Workshop, or Nexus Mods.
