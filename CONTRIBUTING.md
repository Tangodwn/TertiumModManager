# Contributing

Tertium is currently under active pre-release development.

Before proposing code changes:

1. Keep Simple Mode simple.
2. Do not add paid tiers, subscriptions, or feature gating.
3. Do not bundle or rehost third-party mods.
4. Preserve rollback/recovery behavior for any destructive operation.
5. Keep background Windows processes non-focus-stealing.
6. Add or update automated tests for behavior changes.
7. Do not commit API keys, user-specific paths, game files, or third-party copyrighted assets.

Run the test suite with:

```
python -m pytest -q
```

Windows-specific changes should also be validated on a real Windows Darktide installation when practical.
