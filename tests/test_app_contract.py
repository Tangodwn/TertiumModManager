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


def test_user_can_override_or_remove_crash_quarantine():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")

    toggle = source[source.index("    def toggle_selected"):source.index("    def rollback_selected_update")]
    assert "self.compatibility.clear_quarantine(logical_name)" in toggle
    assert "write_mod_load_order(self.game_dir, enabled)" in toggle

    remove_start = source.index("    def remove_selected")
    remove_end = source.find("\n    def ", remove_start + 8)
    remove = source[remove_start: remove_end if remove_end != -1 else len(source)]
    assert "self.compatibility.clear_quarantine(logical_name)" in remove
    assert "write_mod_load_order(self.game_dir, enabled)" in remove
    assert "self._refresh_crash_guard()" in remove


def test_reenabled_quarantine_rearms_crash_detection():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    assert "def _undismiss_crash_signatures_for_mod" in source
    assert "def _learned_crash_candidate" in source
    refresh = source[source.index("    def _refresh_crash_guard"):source.index("    def _remember_handled_crash")]
    assert "learned-crash-signature" in refresh
    toggle = source[source.index("    def toggle_selected"):source.index("    def rollback_selected_update")]
    assert "_undismiss_crash_signatures_for_mod(logical_name)" in toggle
