# Tertium Mod Manager v0.8.11 — Browser-Free Updates

## Update All behavior

- UPDATE ALL no longer opens Nexus pages automatically.
- Tertium first checks tracked Nexus mods, snapshots the current mod state, and requests authorized direct download URLs through the Nexus API.
- When Nexus grants direct download access, Tertium downloads and installs the updates without browser handoff.
- When Nexus refuses direct API download authorization, Tertium stops cleanly with one explanation instead of opening file pages or asking the user to manually download each mod.
- The Nexus account label now shows whether the configured API key reports a Premium or Free account.

## Why this change

The previous guided fallback was not the one-stop experience Tertium is intended to provide. v0.8.11 makes the boundary explicit: UPDATE ALL is an automatic in-app operation or it does not proceed.

Nexus-hosted files still require whatever download authorization Nexus grants to the configured account. Tertium does not scrape or automate the Nexus website to bypass that authorization.
