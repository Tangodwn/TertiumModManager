from pathlib import Path


def test_play_modded_auto_recovery_contract():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")

    assert "self.auto_recovery_armed = False" in source
    assert "self.auto_recovery_attempted = False" in source
    assert "Automatic Crash Recovery" in source
    assert "enter_troubleshooting_safe_mode(" in source
    assert "self.root.after(750, lambda: self.repair_and_launch(skip_crash_guard=True))" in source
    assert "maintain_load_order=True" in source


def test_update_all_uses_guided_free_account_fallback():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")

    worker = source[source.index("    def _update_all_worker"):source.index("    def save_profile")]
    assert 'webbrowser.open(' not in worker
    assert 'self.queue.put(("guided_updates", remaining))' in worker
    assert "guided free-account queue" in worker
    assert "will not open Nexus" not in worker


def test_play_modded_never_advances_guided_nexus_queue():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")

    repair = source[source.index("    def repair_and_launch"):source.index("    def _repair_worker")]
    assert "self.guided_update_active = False" in repair
    assert "self.guided_update_queue.clear()" in repair
    assert "self.guided_waiting_mod_id = None" in repair
    assert "self.guided_installing_mod_id = None" in repair

    drain = source[source.index('                elif kind == "done":'):source.index('                elif kind == "error":')]
    assert "completed_guided_update" in drain
    assert "if completed_guided_update and self.guided_update_active" in drain
