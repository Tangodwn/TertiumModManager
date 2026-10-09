# Tertium Mod Manager v0.8.17 — Play Isolation Fix

This release fixes a case where clicking **PLAY MODDED** could unexpectedly open a Nexus page.

## Fix

The guided Nexus update queue used by free accounts was able to remain armed after a previous Update All attempt. Because Tertium used the same generic worker-complete handler for repairs and installs, finishing the PLAY MODDED repair worker could accidentally advance that pending Nexus queue.

v0.8.17 changes that behavior:

- PLAY MODDED cancels any pending guided Nexus authorization queue before launch preparation.
- The guided queue only advances after a guided NXM install actually completes.
- Unrelated workers, including loader repair and PLAY MODDED, can no longer trigger Nexus browser pages.

Expected behavior is now strict:

**PLAY MODDED → repair/check loader → synchronize load order → launch Darktide.**

Nexus pages may only open as part of an explicit free-account Update All authorization flow or an explicit Nexus action.
