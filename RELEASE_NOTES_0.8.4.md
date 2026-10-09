# Tertium Mod Manager v0.8.4 — Guardian Release Automation

## Release focus

This release keeps the validated v0.8.3 Guardian Automation behavior while adding the infrastructure needed to publish normal Windows installer releases instead of developer build kits.

## Highlights

- Simple Mode remains the default startup experience.
- Advanced Mode remains available from Settings for diagnostics, repair, loader, Nexus metadata, and recovery tools.
- The native Windows process check from v0.8.1 remains in place, preventing recurring `tasklist.exe` focus-stealing while Darktide is running.
- Existing manually installed Darktide mods can be adopted into Tertium's registry without being moved or reinstalled.
- Nexus-managed and local-only mods remain distinguished so Update All does not pretend local-only mods can already be updated automatically.
- Crash Guard and build-aware quarantine behavior from the Guardian series are retained.
- Official Darktide update handling remains metadata/link based; Fatshark promotional artwork is not bundled into Tertium.

## Release automation

The repository now includes:

- Windows CI across supported Python versions.
- PyInstaller packaging.
- Inno Setup installer generation.
- SHA-256 generation.
- A tag-driven GitHub Actions release workflow.
- A streamlined local `MAKE_WINDOWS_INSTALLER.cmd` developer path.

Tagged releases must match the canonical `VERSION` file. A matching version tag can build the Windows application on a real Windows GitHub runner and publish:

- `TertiumModManager-Setup-x64.exe`
- `TertiumModManager-Setup-x64.exe.sha256`

## Project principles

Tertium remains a free, unofficial community tool with no paid tiers or artificial feature gating. Nexus remains the source of mod files; Tertium does not mirror or rehost third-party mods.

Tertium Mod Manager is not affiliated with or endorsed by Fatshark or Games Workshop.
