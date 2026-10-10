# Tertium Mod Manager v0.8.28 — Shortcut Icon Repair

This release fixes existing Windows shortcuts that kept showing the old icon after an in-app ZIP update.

- Added a dedicated packaged `tertium_t.ico` asset for shortcut use.
- Installer-created Desktop and Start Menu shortcuts explicitly use the Tertium T icon.
- In-app self-update now refreshes existing Tertium Desktop and Start Menu shortcuts after replacing application files.
- Existing shortcuts are updated in place; users do not need to delete and recreate them manually.
- v0.8.27 professional Settings / Tools tab split remains unchanged.
