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


def test_crash_guard_exposes_delete_suspect_action():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    assert 'text="Delete Suspect"' in source
    assert "def delete_crash_suspect" in source
    method = source[source.index("    def delete_crash_suspect"):source.index("    def launch_crash_anyway")]
    assert "quarantine_mod_folder" in method
    assert "self.store.remove_mod" in method
    assert "self.compatibility.clear_quarantine" in method
    assert "write_mod_load_order(self.game_dir, enabled)" in method


def test_mod_install_always_updates_load_order():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    method = source[source.index("    def _install_record_archive"):source.index("    def _reapply_aml_if_cached")]
    assert "self.store.get(AML_MOD_ID) is None" not in method
    assert 'if record.mod_id not in {DMF_MOD_ID, AML_MOD_ID}:' in method
    assert 'write_mod_load_order(self.game_dir, enabled)' in method
    assert 'Updated mod_load_order.txt' in method


def test_reenabled_mod_ignores_stale_crash_until_fresh_failure():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    assert "def _arm_crash_retest" in source
    assert "def _crash_retest_cutoff" in source
    refresh = source[source.index("    def _refresh_crash_guard"):source.index("    def _remember_handled_crash")]
    assert "was explicitly re-enabled for testing" in refresh
    assert "float(finding.get(\"mtime\") or 0.0) <= retest_after" in refresh
    toggle = source[source.index("    def toggle_selected"):source.index("    def rollback_selected_update")]
    assert "self._arm_crash_retest(logical_name)" in toggle
    quarantine = source[source.index("    def _quarantine_candidate"):source.index("    def disable_crash_suspect_and_retry")]
    assert "self._clear_crash_retest(logical_name)" in quarantine


def test_hybrid_visual_polish_has_borders_and_brand_graphic():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    assert 'style.configure("Border.TFrame"' in source
    assert 'style.configure("AccentBorder.TFrame"' in source
    assert 'style.configure("AccentCard.TLabelframe"' in source
    assert 'style.configure("DangerCard.TLabelframe"' in source
    assert "brand_mark = tk.Canvas(" in source
    assert "brand_mark.create_polygon(" in source
    assert 'text="T"' in source
    assert 'style="DangerCard.TLabelframe"' in source
    assert 'style="AccentCard.TLabelframe"' in source
    assert 'tree_shell = ttk.Frame(mods_tab, style="Border.TFrame"' in source


def test_main_tabs_have_restrained_themed_banners():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    assert "def _build_tab_banner" in source
    assert '"DEPLOYMENT"' in source
    assert '"MOD CONTROL"' in source
    assert '"RECOVERY & CRASH GUARD"' in source
    assert '"MACHINE SETTINGS & TOOLS"' in source
    assert '"mods",' in source
    assert '"recovery",' in source
    assert '"mechanicus",' in source
    assert "Ogryn-inspired heavy logistics silhouette" in source
    assert "Arbites-inspired shield / containment marks" in source
    assert "Mechanicus-inspired cog, cables and machine-console blocks" in source


def test_tab_art_asset_is_packaged():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")
    spec = (root / "TertiumModManager.spec").read_text(encoding="utf-8")
    assert "tab_art_sprite.png" in source
    assert "tab_art_sprite.png" in spec
    assert (root / "assets" / "tab_art_sprite.png").exists()
