# Tertium Mod Manager v0.8.21 — Auto Load Order

This release makes Tertium populate `mod_load_order.txt` automatically after a normal mod is installed or updated.

## Behavior

After Tertium installs or updates a normal Darktide mod:

- it scans the currently enabled mod folders;
- excludes core `dmf` / `base` entries;
- preserves the existing relative order where possible;
- appends newly enabled mods that are not yet in the file;
- removes disabled/removed active entries;
- writes the result to `mods/mod_load_order.txt`.

This happens even when Auto Mod Loading and Ordering is installed, because Tertium is the authoritative owner of the load-order file.

Users should not need to open or manually edit `mod_load_order.txt` after downloading a mod through Tertium.
