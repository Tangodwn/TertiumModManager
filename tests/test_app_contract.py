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


def test_update_all_does_not_browser_fallback():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")

    worker = source[source.index("    def _update_all_worker"):source.index("    def save_profile")]
    assert 'webbrowser.open(' not in worker
    assert 'self.queue.put(("guided_updates",' not in worker
    assert "switching the remaining" not in worker
    assert "will not open Nexus" in worker
