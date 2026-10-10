# Tertium Mod Manager v0.8.35 — Resumable Update Queue

This release hardens the free-user Nexus Update All workflow so partially completed guided updates survive failures and app restarts.

- Persists the guided Nexus update queue into Tertium's config.
- Restores paused guided updates after restarting Tertium.
- UPDATE ALL now offers to resume the saved queue instead of rescanning and starting over.
- Shows queue progress on the Play dashboard, including the next mod waiting for authorization.
- Guided installs save progress after each completed mod.
- If a guided install fails, the remaining queue is preserved and marked paused instead of discarded.
- Resuming continues from the failed/current mod rather than reopening already-completed updates.
- Normal PLAY MODDED behavior remains isolated from the Nexus browser queue.
