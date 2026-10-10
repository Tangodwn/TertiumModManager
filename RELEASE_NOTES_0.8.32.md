# Tertium Mod Manager v0.8.32 — Smart Launcher Reuse

This release further hardens Darktide launch behavior.

- Detects the running Fatshark launcher by executable path, not only by the generic `Launcher.exe` filename.
- Avoids false positives from unrelated applications that also use a process named Launcher.exe.
- If the matching Darktide launcher is already open, Tertium reuses it instead of spawning a second copy.
- Best-effort brings the existing launcher window to the foreground.
- Continues preventing the duplicate-launch condition that can lock `darktide_launcher.log` and trigger Fatshark launcher `System.IO.IOException`.
- Uses native Windows APIs only; no visible console helper is spawned.
