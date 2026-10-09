# Tertium Mod Manager v0.8.14 — In-App Package Updates

This release changes Tertium's own updater so normal application updates come from the launcher instead of repeatedly running Windows installers.

## New update path

- Tertium checks the public GitHub Releases feed.
- It looks for the versioned package `TertiumModManager-<version>-Portable-x64.zip`.
- Tertium downloads that package itself.
- It verifies the package SHA-256 against the GitHub release asset/checksum.
- Tertium closes itself.
- A local update handoff waits for Tertium to exit, expands the verified package, replaces the installed application directory, and restarts Tertium.
- The downloaded Inno Setup installer is not executed during normal in-app updates.

The release pipeline now publishes both:
- the versioned ZIP used by the launcher updater; and
- the installer, retained only as a bootstrap/manual installation path.

## Why

Repeated installer downloads were the wrong normal workflow and were also hitting antivirus/temp-execution blocks. After a launcher with this update system is installed, future Tertium versions should be delivered through Tertium itself.
