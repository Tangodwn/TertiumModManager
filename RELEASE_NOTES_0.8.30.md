# Tertium Mod Manager v0.8.30 — Desktop Icon Cache Repair

This release targets the remaining Windows desktop-icon problem shown after v0.8.29.

What changed:
- Added a uniquely named desktop icon asset (`tertium_desktop_v3.ico`) to bypass Windows Explorer's stale icon cache.
- Packages that icon at the application root so the shortcut path is always valid in PyInstaller one-folder builds.
- Installer-created Desktop and Start Menu shortcuts now point to that unique icon file.
- Tertium now repairs existing Desktop and Start Menu shortcuts on every startup.
- Startup repair also updates the shortcut target and working directory to the currently running Tertium installation.
- Triggers a Windows shell icon refresh after rewriting shortcuts.
- In-app self-update uses the same cache-busting icon path.

The actual T badge artwork from v0.8.29 remains the icon content.
