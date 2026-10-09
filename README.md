# Tertium Mod Manager

Tertium is a free, unofficial Windows mod manager and launcher for **Warhammer 40,000: Darktide**.

Current development baseline: **v0.8.4 — Guardian Release Automation**

## Goals

Tertium is designed around a simple default experience:

- **PLAY MODDED**
- **PLAY VANILLA**
- **UPDATE ALL**
- automatic Darktide loader repair after game updates
- automatic adoption of already-installed mods
- crash analysis and build-aware mod quarantine
- profiles, backup/rollback, diagnostics, and troubleshooting tools behind **Advanced Mode**
- Nexus integration without mirroring or rehosting mod files

Tertium is intended to remain completely free. There are no paid tiers, subscriptions, or artificially gated features.

## Simple Mode

Tertium always starts in Simple Mode. Advanced tools are available from Settings for users who need diagnostics, loader controls, manual metadata, recovery functions, or detailed mod-management information.

## Windows releases

Normal users should eventually only need:

`TertiumModManager-Setup-x64.exe`

Tagged releases are configured to build and test the Windows installer automatically using GitHub Actions. The release pipeline verifies the tag against `VERSION`, runs the automated test suite on Windows, builds the PyInstaller application and Inno Setup installer, generates SHA-256, and publishes the installer assets.

## Development

Requirements:

- Python 3.11+
- Windows for native installer builds

Run tests:

```text
python -m pytest -q
```

Build the streamlined installer on Windows:

```text
MAKE_WINDOWS_INSTALLER.cmd
```

## Nexus Mods

Tertium uses Nexus as the source for mod files rather than acting as a mirror. Direct downloads are used only where the user's Nexus account/API permissions allow them. Otherwise Tertium can guide the unavoidable Nexus Mod Manager Download interaction and handle the resulting `nxm://` link.

Before a public community release, Tertium should be registered with Nexus as a public-facing application rather than requiring users to manage personal API keys indefinitely.

## Disclaimer

Tertium Mod Manager is an **unofficial community project** and is not affiliated with or endorsed by Fatshark or Games Workshop.

Permanent Tertium branding and launcher artwork should be original project artwork. Official Darktide news/update material should remain linked to its official source rather than redistributed as Tertium branding.

## License

Source code is currently provided under the MIT License. Third-party projects, mods, trademarks, artwork, and game content remain the property of their respective owners.
