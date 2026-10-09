from __future__ import annotations

import json
import os
import queue
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Y, StringVar, Tk, filedialog, messagebox, simpledialog
from tkinter import ttk

from core import (
    AML_MOD_ID,
    CompatibilityStore,
    DML_MOD_ID,
    DMF_MOD_ID,
    GAME_DOMAIN,
    ModManagerError,
    ModRecord,
    ProfileStore,
    RegistryStore,
    active_quarantines,
    adopt_untracked_installed_mods,
    analyze_recent_crash,
    apply_mod_profile,
    app_data_dir,
    backup_inventory,
    capture_mod_profile,
    create_diagnostic_bundle,
    create_mod_state_snapshot,
    compare_setup_manifest,
    cleanup_stale_download_parts,
    audit_mod_structure,
    detect_darktide_install,
    health_report,
    restore_latest_safe_mode,
    restore_latest_mod_state,
    load_setup_manifest,
    export_setup_manifest,
    enter_troubleshooting_safe_mode,
    enforce_active_quarantines,
    has_dml,
    install_archive,
    is_darktide_running,
    link_local_record_to_nexus,
    load_json,
    mods_dir,
    open_game_launcher,
    patch_loader,
    loader_patch_state,
    read_steam_build_id,
    repair_after_game_update,
    restore_latest_loader_backup,
    scan_installed_mods,
    prune_backups,
    quarantine_crash_candidate,
    quarantine_mod_folder,
    reconcile_installed_registry,
    modded_launch_preflight,
    restore_latest_removed_mod,
    rollback_mod_update,
    save_json,
    sync_registry_folders,
    toggle_mod_folder,
    unpatch_loader,
    validate_game_dir,
    verify_cached_archive,
    write_mod_load_order,
)
from nexus import (
    NexusClient,
    NxmLink,
    browser_authorization_required,
    choose_mirror,
    download_file,
    matching_files_for_version,
    parse_nexus_mod_reference,
    parse_nxm_url,
    unique_catalog_match,
)
from official_news import OFFICIAL_NEWS_URL, cached_official_news, fetch_official_news
from self_update import (
    ReleaseInfo,
    SelfUpdateError,
    download_installer,
    fetch_latest_release,
    is_newer_version,
    schedule_windows_installer,
    update_cache_dir,
    verify_installer,
)
from winutil import protect_secret, register_nxm_protocol, unprotect_secret
from version import __version__, RELEASE_NAME

APP_VERSION = __version__
INSTANCE_PORT = 47683
INSTANCE_MAGIC = "TERTIUM_MOD_MANAGER"
DEFAULT_INTERFACE_MODE = "simple"


def startup_interface_mode(_config: dict | None = None) -> str:
    """Always start in Simple Mode; Advanced Mode is an explicit session opt-in."""
    return DEFAULT_INTERFACE_MODE


CORE_NAMES = {
    DML_MOD_ID: "Darktide Mod Loader",
    DMF_MOD_ID: "Darktide Mod Framework",
    AML_MOD_ID: "Auto Mod Loading and Ordering",
}


def bundled_resource(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


class TertiumApp:
    def __init__(self, root: Tk, startup_nxm: str | None = None):
        self.root = root
        self.root.title(f"Tertium Mod Manager {APP_VERSION} — {RELEASE_NAME}")
        try:
            self.root.iconbitmap(str(bundled_resource("assets", "tertium.ico")))
        except Exception:
            pass
        self.root.geometry("1280x800")
        self.root.minsize(1040, 680)
        self.store = RegistryStore()
        self.compatibility = CompatibilityStore(self.store.root)
        self.profiles = ProfileStore(self.store.root)
        self.log_path = self.store.root / "tertium.log"
        self.crash_log_path = self.store.root / "tertium-crash.log"
        self.root.report_callback_exception = self._handle_tk_exception
        self.config_path = app_data_dir() / "config.json"
        self.config = self._load_config()
        self.game_dir = Path(self.config.get("game_dir", "")) if self.config.get("game_dir") else None
        self.api_key = unprotect_secret(self.config.get("api_key", ""))
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.startup_nxm = startup_nxm
        self.update_ids: set[int] = set()
        self.update_map: dict[int, tuple[ModRecord, dict]] = {}
        self.worker_active = False
        self.pending_nxm: list[str] = []
        self.guided_update_queue: list[tuple[ModRecord, dict]] = []
        self.guided_update_active = False
        self.guided_waiting_mod_id: int | None = None
        self.guided_installing_mod_id: int | None = None
        self.pending_existing_link_local_id: int | None = None
        self.pending_existing_link_nexus_mod_id: int | None = None
        self.auto_nexus_reconcile_active = False
        self.status = StringVar(value="Ready")
        self.profile_var = StringVar(value="")
        self.filter_var = StringVar(value="")
        # Tertium always opens in Simple Mode. Advanced Mode is deliberately
        # session-only so the next launch returns to the push-button experience.
        self.mode_var = StringVar(value=startup_interface_mode(self.config))
        self.interface_mode_text = StringVar(value="Simple Mode — everyday controls only")
        self.interface_mode_button_text = StringVar(value="ENTER ADVANCED MODE")
        self.crash_notice_var = StringVar(value="No recent mod-specific crash detected.")
        self.quarantine_text = StringVar(value="No active quarantines")
        self.recent_crash_finding: dict | None = None
        self.news_title_var = StringVar(value="Official Darktide Updates")
        self.news_date_var = StringVar(value="Checking Steam…")
        self.app_update_text = StringVar(value=f"Tertium v{APP_VERSION} · Check for Update")
        self.app_update_status = StringVar(value="Application updates: not checked")
        self.available_app_release: ReleaseInfo | None = None
        self.news_url = OFFICIAL_NEWS_URL
        self.mod_count_text = StringVar(value="0 mods")
        self.sort_reverse: dict[str, bool] = {}
        self.game_label = StringVar(value="Darktide: not configured")
        self.api_label = StringVar(value="Nexus: API key not set")
        self._build_ui()
        self.mode_var.trace_add("write", lambda *_args: self._change_interface_mode())
        self._apply_interface_mode()
        self.filter_var.trace_add("write", lambda *_args: self.refresh())
        self.root.bind("<F5>", lambda _e: self.refresh())
        self.root.bind("<Control-f>", lambda _e: self.filter_entry.focus_set())
        self._start_instance_listener()
        self.root.after(100, self._drain_queue)
        self._last_running_state = False
        self.root.after(250, self._initial_setup)
        self.root.after(900, self._refresh_official_news_async)
        self.root.after(1500, self._poll_game_session)

    def _handle_tk_exception(self, exc_type, exc, tb) -> None:
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            self.crash_log_path.write_text(detail, encoding="utf-8")
        except OSError:
            pass
        self.log_line(f"Unhandled UI error: {exc}")
        try:
            messagebox.showerror(
                "Tertium Mod Manager",
                "An unexpected interface error occurred. Tertium saved tertium-crash.log in its data folder.\n\n" + str(exc),
            )
        except Exception:
            pass

    def _start_instance_listener(self) -> None:
        def server() -> None:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(("127.0.0.1", INSTANCE_PORT))
                    sock.listen(5)
                    while True:
                        conn, _addr = sock.accept()
                        with conn:
                            conn.settimeout(2.0)
                            data = b""
                            while b"\n" not in data and len(data) < 65536:
                                part = conn.recv(4096)
                                if not part:
                                    break
                                data += part
                            try:
                                payload = json.loads(data.decode("utf-8", errors="replace").strip())
                            except Exception:
                                payload = {}
                            if payload.get("magic") != INSTANCE_MAGIC:
                                conn.sendall(b"NO\n")
                                continue
                            nxm = str(payload.get("nxm") or "")
                            self.queue.put(("external_instance", nxm))
                            conn.sendall(b"OK\n")
            except OSError as exc:
                self.queue.put(("log", f"Single-instance listener unavailable: {exc}"))

        threading.Thread(target=server, daemon=True).start()

    def _activate_window(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _process_pending_nxm(self) -> None:
        if self.worker_active or not self.pending_nxm:
            return
        url = self.pending_nxm.pop(0)
        self.handle_nxm(url)

    def _load_config(self) -> dict:
        data = load_json(self.config_path, {})
        return data if isinstance(data, dict) else {}

    def _save_config(self) -> None:
        data = dict(self.config)
        data["game_dir"] = str(self.game_dir) if self.game_dir else ""
        data["api_key"] = protect_secret(self.api_key) if self.api_key else ""
        save_json(self.config_path, data)

    def _build_ui(self) -> None:
        # Tertium intentionally stays dependency-free at runtime, so the launcher uses
        # a themed ttk interface rather than pulling in a third-party UI framework.
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        bg = "#0d1117"
        panel = "#161b22"
        panel2 = "#1f2630"
        text = "#e6edf3"
        muted = "#9aa7b5"
        accent = "#c89146"
        accent_dark = "#7d5a2b"
        good = "#86b386"
        danger = "#c96b6b"
        self.root.configure(bg=bg)
        style.configure("TFrame", background=bg)
        style.configure("Panel.TFrame", background=panel)
        style.configure("Subtle.TFrame", background=panel2)
        style.configure("TLabel", background=bg, foreground=text, font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=panel, foreground=text, font=("Segoe UI", 10))
        style.configure("Muted.Panel.TLabel", background=panel, foreground=muted, font=("Segoe UI", 9))
        style.configure("Hero.TLabel", background=panel, foreground=text, font=("Segoe UI Semibold", 22))
        style.configure("Section.TLabel", background=bg, foreground=text, font=("Segoe UI Semibold", 12))
        style.configure("StatusGood.TLabel", background=panel, foreground=good, font=("Segoe UI Semibold", 11))
        style.configure("StatusWarn.TLabel", background=panel, foreground=accent, font=("Segoe UI Semibold", 11))
        style.configure("TButton", padding=(10, 7), font=("Segoe UI", 9))
        style.configure("Primary.TButton", padding=(16, 10), font=("Segoe UI Semibold", 11), foreground="#101010", background=accent)
        style.map("Primary.TButton", background=[("active", "#daa45d"), ("pressed", accent_dark)])
        style.configure("Launch.TButton", padding=(18, 12), font=("Segoe UI Semibold", 13), foreground="#eefbf0", background="#285a31")
        style.map("Launch.TButton", background=[("active", "#347440"), ("pressed", "#1f4827")])
        style.configure("Secondary.TButton", padding=(14, 10), font=("Segoe UI Semibold", 11), foreground=text, background=panel2)
        style.map("Secondary.TButton", background=[("active", "#2d3743"), ("pressed", "#151b22")])
        style.configure("Update.TButton", padding=(14, 10), font=("Segoe UI Semibold", 11), foreground="#fff0d8", background="#6b461d")
        style.map("Update.TButton", background=[("active", "#855923"), ("pressed", "#4d3215")])
        style.configure("Danger.TButton", foreground=text, background="#552b2b")
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8), font=("Segoe UI Semibold", 10))
        style.configure("TLabelframe", background=bg, foreground=text)
        style.configure("TLabelframe.Label", background=bg, foreground=muted, font=("Segoe UI Semibold", 9))
        style.configure("Treeview", background=panel, fieldbackground=panel, foreground=text, rowheight=28, borderwidth=0)
        style.configure("Treeview.Heading", background=panel2, foreground=text, font=("Segoe UI Semibold", 9), padding=(6, 6))
        style.map("Treeview", background=[("selected", "#374151")], foreground=[("selected", text)])
        style.configure("TEntry", fieldbackground=panel2, foreground=text)
        style.configure("TCombobox", fieldbackground=panel2, foreground=text)

        self.dashboard_status = StringVar(value="Checking Darktide…")
        self.dashboard_mods = StringVar(value="Mods: —")
        self.dashboard_updates = StringVar(value="Updates: —")
        self.dashboard_build = StringVar(value="Steam build: —")

        header = ttk.Frame(self.root, style="Panel.TFrame", padding=(18, 14))
        header.pack(fill=X)
        titlebox = ttk.Frame(header, style="Panel.TFrame")
        titlebox.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(titlebox, text="TERTIUM MOD MANAGER", style="Hero.TLabel").pack(anchor="w")
        ttk.Label(
            titlebox,
            text=f"Darktide launcher & mod control  •  v{APP_VERSION} {RELEASE_NAME}",
            style="Muted.Panel.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        self.header_play_modded = ttk.Button(header, text="PLAY MODDED", style="Primary.TButton", command=self.repair_and_launch)
        self.header_play_modded.pack(side=RIGHT, padx=(8, 0))
        self.header_play_vanilla = ttk.Button(header, text="Play Vanilla", command=self.launch_vanilla)
        self.header_play_vanilla.pack(side=RIGHT, padx=(8, 0))

        strip = ttk.Frame(self.root, style="Subtle.TFrame", padding=(18, 8))
        self.status_strip = strip
        strip.pack(fill=X)
        ttk.Label(strip, textvariable=self.dashboard_status, style="Panel.TLabel").pack(side=LEFT)
        ttk.Label(strip, textvariable=self.dashboard_build, style="Panel.TLabel").pack(side=RIGHT)
        ttk.Label(strip, textvariable=self.dashboard_updates, style="Panel.TLabel").pack(side=RIGHT, padx=(0, 22))
        ttk.Label(strip, textvariable=self.dashboard_mods, style="Panel.TLabel").pack(side=RIGHT, padx=(0, 22))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=BOTH, expand=True, padx=12, pady=(10, 6))
        play_tab = ttk.Frame(notebook, padding=10)
        mods_tab = ttk.Frame(notebook, padding=10)
        recovery_tab = ttk.Frame(notebook, padding=10)
        tools_tab = ttk.Frame(notebook, padding=10)
        notebook.add(play_tab, text="  Play  ")
        notebook.add(mods_tab, text="  Mods  ")
        notebook.add(recovery_tab, text="  Profiles & Recovery  ")
        notebook.add(tools_tab, text="  Diagnostics & Settings  ")
        self.notebook = notebook
        self.play_tab = play_tab
        self.mods_tab = mods_tab
        self.recovery_tab = recovery_tab
        self.tools_tab = tools_tab

        # SIMPLE HOME (default)
        simple_home = ttk.Frame(play_tab)
        self.simple_home = simple_home

        simple_split = ttk.Panedwindow(simple_home, orient="horizontal")
        simple_split.pack(fill=BOTH, expand=True)
        art_side = ttk.Frame(simple_split, style="Panel.TFrame")
        action_side = ttk.Frame(simple_split, style="Panel.TFrame", padding=(14, 12))
        simple_split.add(art_side, weight=5)
        simple_split.add(action_side, weight=4)

        # Permanent art is original Tertium artwork, not redistributed Fatshark key art.
        tk = __import__("tkinter")
        art_path = bundled_resource("assets", "tertium_hive.png")
        try:
            self.simple_art_image = tk.PhotoImage(file=str(art_path))
            art_label = tk.Label(art_side, image=self.simple_art_image, bg="#090c10", borderwidth=0, highlightthickness=0)
            art_label.pack(fill=BOTH, expand=True)
        except Exception:
            self.simple_art_image = None
            art_fallback = tk.Frame(art_side, bg="#090c10")
            art_fallback.pack(fill=BOTH, expand=True)
            tk.Label(art_fallback, text="TERTIUM", bg="#090c10", fg="#e6edf3", font=("Segoe UI Semibold", 36)).pack(pady=(150, 4))
            tk.Label(art_fallback, text="MODS FOR THE EMPEROR", bg="#090c10", fg="#c89146", font=("Segoe UI Semibold", 14)).pack()

        art_caption = ttk.Frame(art_side, style="Panel.TFrame", padding=(14, 9))
        art_caption.pack(fill=X)
        ttk.Label(art_caption, text="MODS FOR THE EMPEROR", style="StatusWarn.TLabel").pack(side=LEFT)
        ttk.Label(art_caption, text="Unofficial community tool", style="Muted.Panel.TLabel").pack(side=RIGHT)

        ttk.Label(action_side, text="Deployment", style="Muted.Panel.TLabel").pack(anchor="w")
        ttk.Label(action_side, textvariable=self.dashboard_status, style="Hero.TLabel").pack(anchor="w", pady=(2, 10))

        ttk.Button(action_side, text="▶  PLAY MODDED", style="Launch.TButton", command=self.repair_and_launch).pack(fill=X, ipady=10, pady=(0, 8))
        ttk.Button(action_side, text="▷  PLAY VANILLA", style="Secondary.TButton", command=self.launch_vanilla).pack(fill=X, ipady=7, pady=(0, 8))
        ttk.Button(action_side, text="↻  UPDATE ALL", style="Update.TButton", command=self.update_all).pack(fill=X, ipady=7, pady=(0, 8))
        self.app_update_button = ttk.Button(
            action_side,
            textvariable=self.app_update_text,
            command=self.check_or_install_app_update,
        )
        self.app_update_button.pack(fill=X, ipady=5, pady=(0, 12))

        status_card = ttk.Frame(action_side, style="Panel.TFrame", padding=12)
        status_card.pack(fill=X, pady=(0, 8))
        ttk.Label(status_card, textvariable=self.dashboard_mods, style="StatusGood.TLabel").pack(anchor="w")
        ttk.Label(status_card, textvariable=self.dashboard_build, style="Muted.Panel.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(status_card, textvariable=self.dashboard_updates, style="Muted.Panel.TLabel").pack(anchor="w", pady=(2, 0))

        crash_card = ttk.LabelFrame(action_side, text=" Crash Guard ", padding=10)
        crash_card.pack(fill=X, pady=(0, 8))
        ttk.Label(crash_card, textvariable=self.crash_notice_var, wraplength=430, justify="left").pack(anchor="w")
        crash_actions = ttk.Frame(crash_card)
        crash_actions.pack(fill=X, pady=(7, 0))
        self.disable_retry_button = ttk.Button(crash_actions, text="Disable Suspect & Retry", command=self.disable_crash_suspect_and_retry)
        self.disable_retry_button.pack(side=LEFT, padx=(0, 6))
        self.disable_retry_button.state(["disabled"])
        self.launch_anyway_button = ttk.Button(crash_actions, text="Launch Anyway", command=self.launch_crash_anyway)
        self.launch_anyway_button.pack(side=LEFT, padx=(0, 6))
        self.launch_anyway_button.state(["disabled"])
        self.crash_details_button = ttk.Button(crash_actions, text="Advanced Details", command=self.show_crash_guard_details)
        self.crash_details_button.pack(side=LEFT)
        self.crash_details_button.state(["disabled"])
        ttk.Label(crash_actions, textvariable=self.quarantine_text).pack(side=RIGHT)

        news_card = ttk.LabelFrame(action_side, text=" Official Darktide Updates ", padding=10)
        news_card.pack(fill=X, pady=(0, 8))
        ttk.Label(news_card, textvariable=self.news_title_var, style="Panel.TLabel", wraplength=430, justify="left").pack(anchor="w")
        ttk.Label(news_card, textvariable=self.news_date_var, style="Muted.Panel.TLabel").pack(anchor="w", pady=(2, 6))
        news_row = ttk.Frame(news_card)
        news_row.pack(fill=X)
        ttk.Button(news_row, text="View Official Update", command=self._open_official_news).pack(side=LEFT)
        ttk.Button(news_row, text="Refresh", command=lambda: self._refresh_official_news_async(force=True)).pack(side=RIGHT)

        simple_links = ttk.Frame(action_side)
        simple_links.pack(fill=X, pady=(2, 0))
        ttk.Button(simple_links, text="Manage Mods", command=lambda: self.notebook.select(self.mods_tab)).pack(side=LEFT, fill=X, expand=True, padx=(0, 4))
        ttk.Button(simple_links, text="Discover Mods", command=self.open_nexus_catalog).pack(side=LEFT, fill=X, expand=True, padx=4)
        ttk.Button(simple_links, text="Profiles", command=self._open_profiles).pack(side=LEFT, fill=X, expand=True, padx=4)
        ttk.Button(simple_links, text="Settings", command=lambda: self.notebook.select(self.tools_tab)).pack(side=LEFT, fill=X, expand=True, padx=(4, 0))

        # ADVANCED PLAY DASHBOARD
        play_main = ttk.Panedwindow(play_tab, orient="horizontal")
        self.advanced_play = play_main
        play_main.pack(fill=BOTH, expand=True)
        play_left = ttk.Frame(play_main)
        play_right = ttk.Frame(play_main)
        play_main.add(play_left, weight=3)
        play_main.add(play_right, weight=2)

        readiness = ttk.Frame(play_left, style="Panel.TFrame", padding=18)
        readiness.pack(fill=X, pady=(0, 10))
        ttk.Label(readiness, text="Deployment status", style="Muted.Panel.TLabel").pack(anchor="w")
        ttk.Label(readiness, textvariable=self.dashboard_status, style="Hero.TLabel").pack(anchor="w", pady=(4, 8))
        ttk.Label(
            readiness,
            text="PLAY MODDED verifies/repairs the loader, synchronizes Tertium's enabled mods into the Darktide load order, then starts the game.",
            style="Muted.Panel.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w")
        launchrow = ttk.Frame(readiness, style="Panel.TFrame")
        launchrow.pack(fill=X, pady=(14, 0))
        ttk.Button(launchrow, text="PLAY MODDED", style="Primary.TButton", command=self.repair_and_launch).pack(side=LEFT)
        ttk.Button(launchrow, text="Launch Without Repair", command=self.launch_game).pack(side=LEFT, padx=(8, 0))
        ttk.Button(launchrow, text="Play Vanilla", command=self.launch_vanilla).pack(side=LEFT, padx=(8, 0))

        core = ttk.LabelFrame(play_left, text=" Core Darktide modding stack ", padding=12)
        core.pack(fill=X, pady=(0, 10))
        self.core_text = StringVar(value="Core status: not scanned")
        ttk.Label(core, textvariable=self.core_text, wraplength=730, justify="left").pack(anchor="w")
        corerow = ttk.Frame(core)
        corerow.pack(fill=X, pady=(8, 0))
        ttk.Button(corerow, text="DML", command=lambda: self.open_nexus(DML_MOD_ID)).pack(side=LEFT, padx=(0, 6))
        ttk.Button(corerow, text="DMF", command=lambda: self.open_nexus(DMF_MOD_ID)).pack(side=LEFT, padx=(0, 6))
        ttk.Button(corerow, text="AML", command=lambda: self.open_nexus(AML_MOD_ID)).pack(side=LEFT, padx=(0, 6))
        ttk.Button(corerow, text="Set Up Core Stack", command=self.setup_core_stack).pack(side=RIGHT, padx=(6, 0))
        ttk.Button(corerow, text="Repair Loader", command=self.repair_loader).pack(side=RIGHT)

        quick = ttk.LabelFrame(play_left, text=" Quick actions ", padding=12)
        quick.pack(fill=BOTH, expand=True)
        q1 = ttk.Frame(quick)
        q1.pack(fill=X, pady=(0, 7))
        ttk.Button(q1, text="Update All", command=self.update_all).pack(side=LEFT, fill=X, expand=True, padx=(0, 4))
        ttk.Button(q1, text="Check Updates", command=self.check_updates).pack(side=LEFT, fill=X, expand=True, padx=(4, 0))
        q2 = ttk.Frame(quick)
        q2.pack(fill=X, pady=(0, 7))
        ttk.Button(q2, text="Install from Nexus Link", command=self.prompt_nxm).pack(side=LEFT, fill=X, expand=True, padx=(0, 4))
        ttk.Button(q2, text="Install Local Archive", command=self.install_local_archive).pack(side=LEFT, fill=X, expand=True, padx=(4, 0))
        q3 = ttk.Frame(quick)
        q3.pack(fill=X)
        ttk.Button(q3, text="Health Check", command=self.show_health_check).pack(side=LEFT, fill=X, expand=True, padx=(0, 4))
        ttk.Button(q3, text="Troubleshoot Safe Mode", command=self.enter_safe_mode).pack(side=LEFT, fill=X, expand=True, padx=(4, 0))

        ttk.Label(play_right, text="Activity", style="Section.TLabel").pack(anchor="w")
        self.log = __import__("tkinter").Text(
            play_right,
            wrap="word",
            height=20,
            state="disabled",
            background=panel,
            foreground=text,
            insertbackground=text,
            relief="flat",
            padx=10,
            pady=10,
            font=("Consolas", 9),
        )
        self.log.pack(fill=BOTH, expand=True, pady=(6, 0))

        # MODS TAB
        listbar = ttk.Frame(mods_tab, padding=(0, 0, 0, 8))
        listbar.pack(fill=X)
        ttk.Label(listbar, text="Search mods:").pack(side=LEFT, padx=(0, 6))
        self.filter_entry = ttk.Entry(listbar, textvariable=self.filter_var)
        self.filter_entry.pack(side=LEFT, fill=X, expand=True)
        ttk.Button(listbar, text="Clear", command=lambda: self.filter_var.set("")).pack(side=LEFT, padx=(6, 0))
        ttk.Label(listbar, textvariable=self.mod_count_text).pack(side=RIGHT, padx=(12, 0))

        cols = ("enabled", "name", "version", "nexus", "folder", "update")
        self.tree = ttk.Treeview(mods_tab, columns=cols, show="headings", selectmode="browse")
        headers = {"enabled": "On", "name": "Mod", "version": "Version", "nexus": "Nexus ID", "folder": "Folder", "update": "Update"}
        widths = {"enabled": 48, "name": 300, "version": 100, "nexus": 80, "folder": 230, "update": 90}
        for c in cols:
            self.tree.heading(c, text=headers[c], command=lambda col=c: self._sort_tree(col))
            self.tree.column(c, width=widths[c], stretch=(c in {"name", "folder"}))
        self.tree.pack(fill=BOTH, expand=True)
        self.tree.bind("<Double-1>", self.on_double_click)

        actions = ttk.Frame(mods_tab, padding=(0, 8, 0, 0))
        actions.pack(fill=X)
        self.mod_toggle_button = ttk.Button(actions, text="Enable / Disable", command=self.toggle_selected)
        self.mod_toggle_button.pack(side=LEFT, padx=(0, 6))
        self.mod_nexus_button = ttk.Button(actions, text="Open on Nexus", command=self.open_selected_nexus)
        self.mod_nexus_button.pack(side=LEFT, padx=(0, 6))
        self.mod_link_button = ttk.Button(actions, text="Auto-Link Existing", command=lambda: self._start_auto_nexus_reconcile(announce=True))
        self.mod_link_button.pack(side=LEFT, padx=(0, 6))
        self.manual_link_button = ttk.Button(actions, text="Link Selected Manually", command=self.link_selected_existing_mod)
        self.manual_link_button.pack(side=LEFT, padx=(0, 6))
        ttk.Button(actions, text="Discover Mods", command=self.open_nexus_catalog).pack(side=LEFT, padx=(0, 6))
        self.mod_rollback_button = ttk.Button(actions, text="Rollback Update", command=self.rollback_selected_update)
        self.mod_rollback_button.pack(side=LEFT, padx=(0, 6))
        self.mod_remove_button = ttk.Button(actions, text="Remove", style="Danger.TButton", command=self.remove_selected)
        self.mod_remove_button.pack(side=LEFT, padx=(0, 6))
        ttk.Button(actions, text="Refresh", command=self.refresh).pack(side=RIGHT)

        profiles = ttk.LabelFrame(mods_tab, text=" Profiles ", padding=10)
        self.profiles_frame = profiles
        profiles.pack(fill=X, pady=(10, 0))
        ttk.Label(profiles, text="Profile:").pack(side=LEFT, padx=(0, 6))
        self.profile_combo = ttk.Combobox(profiles, textvariable=self.profile_var, state="readonly", width=28)
        self.profile_combo.pack(side=LEFT, padx=(0, 6))
        ttk.Button(profiles, text="Apply", command=self.apply_selected_profile).pack(side=LEFT, padx=(0, 6))
        ttk.Button(profiles, text="Save Current", command=self.save_profile).pack(side=LEFT, padx=(0, 6))
        ttk.Button(profiles, text="Delete", command=self.delete_selected_profile).pack(side=LEFT)

        # PROFILES / RECOVERY TAB
        self.recovery_content = ttk.Frame(recovery_tab)
        self.recovery_content.pack(fill=BOTH, expand=True)
        recovery_tab = self.recovery_content

        simple_profile_frame = ttk.LabelFrame(recovery_tab, text=" Profiles ", padding=12)
        self.simple_profile_frame = simple_profile_frame
        simple_profile_frame.pack(fill=X, pady=(0, 10))
        ttk.Label(simple_profile_frame, text="Profile:").pack(side=LEFT, padx=(0, 6))
        self.simple_profile_combo = ttk.Combobox(simple_profile_frame, textvariable=self.profile_var, state="readonly", width=32)
        self.simple_profile_combo.pack(side=LEFT, padx=(0, 6))
        ttk.Button(simple_profile_frame, text="Apply", command=self.apply_selected_profile).pack(side=LEFT, padx=(0, 6))
        ttk.Button(simple_profile_frame, text="Save Current", command=self.save_profile).pack(side=LEFT, padx=(0, 6))
        ttk.Button(simple_profile_frame, text="Delete", command=self.delete_selected_profile).pack(side=LEFT)

        recovery_intro = ttk.Frame(recovery_tab, style="Panel.TFrame", padding=16)
        self.recovery_intro = recovery_intro
        recovery_intro.pack(fill=X, pady=(0, 10))
        ttk.Label(recovery_intro, text="Recovery tools", style="Hero.TLabel").pack(anchor="w")
        ttk.Label(
            recovery_intro,
            text="Tertium snapshots mod state before risky batch operations. These tools are designed to get you back to a known state without manually moving folders.",
            style="Muted.Panel.TLabel",
            wraplength=900,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
        rec_grid = ttk.Frame(recovery_tab)
        self.recovery_grid = rec_grid
        rec_grid.pack(fill=X)
        ttk.Button(rec_grid, text="Troubleshoot Safe Mode", command=self.enter_safe_mode).grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=5)
        ttk.Button(rec_grid, text="Restore Last Mod State", command=self.restore_safe_mode).grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        ttk.Button(rec_grid, text="Restore Removed Mod", command=self.restore_removed_mod).grid(row=0, column=2, sticky="ew", padx=(5, 0), pady=5)
        ttk.Button(rec_grid, text="Rollback Loader Patch", command=self.rollback_loader).grid(row=1, column=0, sticky="ew", padx=(0, 5), pady=5)
        ttk.Button(rec_grid, text="Clean Old Backups", command=self.clean_backups).grid(row=1, column=1, sticky="ew", padx=5, pady=5)
        ttk.Button(rec_grid, text="Open Data Folder", command=self.open_data_folder).grid(row=1, column=2, sticky="ew", padx=(5, 0), pady=5)
        ttk.Button(rec_grid, text="Export Setup", command=self.export_setup).grid(row=2, column=0, sticky="ew", padx=(0, 5), pady=5)
        ttk.Button(rec_grid, text="Compare Setup", command=self.compare_setup).grid(row=2, column=1, sticky="ew", padx=5, pady=5)
        ttk.Button(rec_grid, text="Open Mods Folder", command=self.open_mods_folder).grid(row=2, column=2, sticky="ew", padx=(5, 0), pady=5)
        for i in range(3):
            rec_grid.columnconfigure(i, weight=1)

        # DIAGNOSTICS / SETTINGS TAB
        interface_box = ttk.LabelFrame(tools_tab, text=" Interface ", padding=12)
        interface_box.pack(fill=X, pady=(0, 10))
        interface_copy = ttk.Frame(interface_box)
        interface_copy.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(interface_copy, textvariable=self.interface_mode_text, style="Hero.TLabel").pack(anchor="w")
        ttk.Label(
            interface_copy,
            text="Tertium always starts in Simple Mode. Advanced Mode exposes diagnostics, recovery, loader controls and technical metadata for this session.",
            style="Muted.Panel.TLabel",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(3, 0))
        self.interface_mode_button = ttk.Button(
            interface_box,
            textvariable=self.interface_mode_button_text,
            command=self._toggle_interface_mode,
        )
        self.interface_mode_button.pack(side=RIGHT, padx=(12, 0), ipadx=8, ipady=4)

        settings_top = ttk.Frame(tools_tab)
        settings_top.pack(fill=X)
        game_box = ttk.LabelFrame(settings_top, text=" Darktide ", padding=12)
        game_box.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 5))
        ttk.Label(game_box, textvariable=self.game_label, wraplength=480, justify="left").pack(anchor="w")
        ttk.Button(game_box, text="Set Game Folder", command=self.choose_game_dir).pack(anchor="w", pady=(8, 0))
        nexus_box = ttk.LabelFrame(settings_top, text=" Nexus ", padding=12)
        nexus_box.pack(side=LEFT, fill=BOTH, expand=True, padx=(5, 0))
        ttk.Label(nexus_box, textvariable=self.api_label, wraplength=480, justify="left").pack(anchor="w")
        nexusrow = ttk.Frame(nexus_box)
        nexusrow.pack(fill=X, pady=(8, 0))
        ttk.Button(nexusrow, text="Set API Key", command=self.set_api_key).pack(side=LEFT, padx=(0, 6))
        ttk.Button(nexusrow, text="Register Nexus Links", command=self.register_nxm).pack(side=LEFT)

        app_update_box = ttk.LabelFrame(tools_tab, text=" Tertium Updates ", padding=12)
        app_update_box.pack(fill=X, pady=(10, 0))
        ttk.Label(app_update_box, textvariable=self.app_update_status, wraplength=900, justify="left").pack(side=LEFT, fill=X, expand=True)
        ttk.Button(app_update_box, textvariable=self.app_update_text, command=self.check_or_install_app_update).pack(side=RIGHT, padx=(12, 0))

        diag = ttk.LabelFrame(tools_tab, text=" Diagnostics & maintenance ", padding=12)
        self.advanced_diag = diag
        diag.pack(fill=X, pady=(10, 0))
        drow1 = ttk.Frame(diag)
        drow1.pack(fill=X, pady=(0, 6))
        ttk.Button(drow1, text="Health Check", command=self.show_health_check).pack(side=LEFT, padx=(0, 6))
        ttk.Button(drow1, text="Create Diagnostic ZIP", command=self.create_diagnostics).pack(side=LEFT, padx=(0, 6))
        ttk.Button(drow1, text="Repair Loader Only", command=self.repair_loader).pack(side=LEFT, padx=(0, 6))
        ttk.Button(drow1, text="Getting Started", command=self.show_getting_started).pack(side=LEFT)
        drow2 = ttk.Frame(diag)
        drow2.pack(fill=X)
        ttk.Button(drow2, text="Open DML on Nexus", command=lambda: self.open_nexus(DML_MOD_ID)).pack(side=LEFT, padx=(0, 6))
        ttk.Button(drow2, text="Open DMF on Nexus", command=lambda: self.open_nexus(DMF_MOD_ID)).pack(side=LEFT, padx=(0, 6))
        ttk.Button(drow2, text="Open AML on Nexus", command=lambda: self.open_nexus(AML_MOD_ID)).pack(side=LEFT, padx=(0, 6))
        ttk.Button(drow2, text="Adopt Installed Mods", command=self.adopt_existing_mods).pack(side=LEFT)

        notes = ttk.Frame(tools_tab, style="Panel.TFrame", padding=14)
        notes.pack(fill=X, pady=(10, 0))
        ttk.Label(notes, text="Free by design", style="StatusGood.TLabel").pack(anchor="w")
        ttk.Label(
            notes,
            text="Tertium has no paid tier. Update All uses direct Nexus downloads when the connected Nexus account permits them; otherwise it guides the required Nexus browser authorization and handles the install automatically after each Mod Manager Download click.",
            style="Muted.Panel.TLabel",
            wraplength=1000,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        bottom = ttk.Frame(self.root, style="Panel.TFrame", padding=(14, 8))
        bottom.pack(fill=X)
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill=X, side=LEFT, expand=True)
        ttk.Label(bottom, textvariable=self.status, style="Panel.TLabel", width=38).pack(side=RIGHT, padx=(12, 0))

    def _rotate_log_if_needed(self, max_bytes: int = 5 * 1024 * 1024) -> None:
        try:
            if not self.log_path.exists() or self.log_path.stat().st_size <= max_bytes:
                return
            older = self.log_path.with_suffix(".log.2")
            previous = self.log_path.with_suffix(".log.1")
            older.unlink(missing_ok=True)
            if previous.exists():
                previous.replace(older)
            self.log_path.replace(previous)
        except OSError:
            pass

    def log_line(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {text}"
        self.log.configure(state="normal")
        self.log.insert(END, line + "\n")
        self.log.see(END)
        self.log.configure(state="disabled")
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_log_if_needed()
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(time.strftime("%Y-%m-%d ") + line + "\n")
        except OSError:
            pass

    def _initial_setup(self) -> None:
        cleaned = cleanup_stale_download_parts(self.store)
        if cleaned.get("removed"):
            self.log_line(
                f"Cleaned {cleaned['removed']} stale partial download(s) "
                f"({cleaned['freed'] / 1024**2:.1f} MiB)."
            )
        if not self.game_dir or not validate_game_dir(self.game_dir)[0]:
            detected = detect_darktide_install()
            if detected:
                self.game_dir = detected
                self._save_config()
                self.log_line(f"Detected Darktide at {detected}")
        self.refresh()
        self._refresh_crash_guard()
        self.root.after(1800, lambda: self.check_self_update(announce=False))
        if self.api_key:
            nexus_name = str(self.config.get("nexus_name") or "account")
            self.api_label.set(f"Nexus: {nexus_name} · automatic + browser fallback")
            self.root.after(750, self._start_auto_nexus_reconcile)
        if not self.config.get("welcome_seen"):
            self.config["welcome_seen"] = True
            self._save_config()
            self.root.after(200, lambda: self.show_getting_started(auto=True))
        if self.startup_nxm:
            self.handle_nxm(self.startup_nxm)

    def _toggle_interface_mode(self) -> None:
        self.mode_var.set("advanced" if self.mode_var.get().strip().lower() != "advanced" else "simple")

    def _change_interface_mode(self) -> None:
        mode = self.mode_var.get().strip().lower()
        if mode not in {"simple", "advanced"}:
            mode = DEFAULT_INTERFACE_MODE
            self.mode_var.set(mode)
            return
        # Deliberately do not persist Advanced Mode. Every application launch
        # starts in Simple Mode unless the user opts into technical controls.
        self._apply_interface_mode()

    def _apply_interface_mode(self) -> None:
        """Keep everyday play intentionally sparse; expose tooling only in Advanced."""
        mode = self.mode_var.get().strip().lower()
        simple = mode != "advanced"
        try:
            self.interface_mode_text.set("Simple Mode — everyday controls only" if simple else "Advanced Mode — technical controls visible")
            self.interface_mode_button_text.set("ENTER ADVANCED MODE" if simple else "RETURN TO SIMPLE MODE")
        except Exception:
            pass
        try:
            self.simple_home.pack_forget()
            self.advanced_play.pack_forget()
            if simple:
                self.simple_home.pack(fill=BOTH, expand=True)
            else:
                self.advanced_play.pack(fill=BOTH, expand=True)
        except Exception:
            pass

        # The header already exists in Simple Mode; duplicate launch buttons and
        # the technical status strip only add noise there.
        try:
            self.header_play_modded.pack_forget()
            self.header_play_vanilla.pack_forget()
            self.status_strip.pack_forget()
            if not simple:
                self.header_play_modded.pack(side=RIGHT, padx=(8, 0))
                self.header_play_vanilla.pack(side=RIGHT, padx=(8, 0))
                self.status_strip.pack(fill=X, before=self.notebook)
        except Exception:
            pass

        try:
            self.notebook.tab(self.play_tab, text="  Play  ")
            self.notebook.tab(self.mods_tab, text="  Mods  ")
            self.notebook.tab(self.recovery_tab, text="  Profiles  " if simple else "  Profiles & Recovery  ")
            self.notebook.tab(self.tools_tab, text="  Settings  " if simple else "  Diagnostics & Settings  ")
        except Exception:
            pass

        # Simple mod management shows only the information most people need.
        try:
            self.tree.configure(displaycolumns=("enabled", "name", "version", "update") if simple else ("enabled", "name", "version", "nexus", "folder", "update"))
            if simple:
                self.profiles_frame.pack_forget()
                self.mod_rollback_button.pack_forget()
                self.mod_remove_button.pack_forget()
                self.manual_link_button.pack_forget()
                self.advanced_diag.pack_forget()
                self.recovery_intro.pack_forget()
                self.recovery_grid.pack_forget()
            else:
                if not self.profiles_frame.winfo_manager():
                    self.profiles_frame.pack(fill=X, pady=(10, 0))
                if not self.mod_rollback_button.winfo_manager():
                    self.mod_rollback_button.pack(side=LEFT, padx=(0, 6))
                if not self.mod_remove_button.winfo_manager():
                    self.mod_remove_button.pack(side=LEFT, padx=(0, 6))
                if not self.manual_link_button.winfo_manager():
                    self.manual_link_button.pack(side=LEFT, padx=(0, 6))
                if not self.advanced_diag.winfo_manager():
                    self.advanced_diag.pack(fill=X, pady=(10, 0))
                if not self.recovery_intro.winfo_manager():
                    self.recovery_intro.pack(fill=X, pady=(0, 10), after=self.simple_profile_frame)
                if not self.recovery_grid.winfo_manager():
                    self.recovery_grid.pack(fill=X, after=self.recovery_intro)
        except Exception:
            pass

    def _open_profiles(self) -> None:
        self.notebook.select(self.recovery_tab)

    def open_nexus_catalog(self) -> None:
        webbrowser.open(f"https://www.nexusmods.com/{GAME_DOMAIN}/mods/")

    def _refresh_official_news_async(self, force: bool = False) -> None:
        cache = self.store.root / "official-news.json"
        cached = cached_official_news(cache)
        if cached:
            self.news_title_var.set(str(cached.get("title") or "Official Darktide Updates"))
            self.news_date_var.set(str(cached.get("date") or cached.get("feed") or "Steam"))
            self.news_url = str(cached.get("url") or OFFICIAL_NEWS_URL)

        def worker() -> None:
            item = fetch_official_news(cache, force=force)
            self.queue.put(("official_news", item))

        threading.Thread(target=worker, daemon=True).start()

    def _open_official_news(self) -> None:
        webbrowser.open(self.news_url or OFFICIAL_NEWS_URL)

    @staticmethod
    def _primary_crash_candidate(finding: dict | None) -> dict | None:
        if not finding:
            return None
        candidates = finding.get("candidates") or []
        return dict(candidates[0]) if candidates and isinstance(candidates[0], dict) else None

    def _set_crash_guard_actions(
        self,
        candidate: dict | None = None,
        finding: dict | None = None,
        quarantined: bool = False,
    ) -> None:
        name = str((candidate or {}).get("logical_name") or "")
        confidence = str((candidate or {}).get("confidence") or "").lower()
        try:
            self.disable_retry_button.config(text=f"Disable {name} & Retry" if name else "Disable Suspect & Retry")
            if candidate and confidence == "high" and not quarantined:
                self.disable_retry_button.state(["!disabled"])
            else:
                self.disable_retry_button.state(["disabled"])
            if finding:
                self.launch_anyway_button.state(["!disabled"])
                self.crash_details_button.state(["!disabled"])
            else:
                self.launch_anyway_button.state(["disabled"])
                self.crash_details_button.state(["disabled"])
        except Exception:
            pass

    def _refresh_crash_guard(self) -> dict | None:
        if not self.game_dir or not validate_game_dir(self.game_dir)[0]:
            self.recent_crash_finding = None
            self.crash_notice_var.set("No recent mod-specific crash detected.")
            self.quarantine_text.set("No active quarantines")
            self._set_crash_guard_actions()
            return None

        quarantines = active_quarantines(self.game_dir, self.compatibility)
        self.quarantine_text.set(
            f"{len(quarantines)} quarantined" if quarantines else "No active quarantines"
        )
        finding = analyze_recent_crash(self.game_dir)
        if not finding:
            self.recent_crash_finding = None
            self.crash_notice_var.set("Last Darktide session did not expose a mod-specific crash.")
            self._set_crash_guard_actions()
            return None

        # Do not blame mods for a crash from a deliberately vanilla session.
        last_mode = str(self.config.get("last_launch_mode") or "")
        last_vanilla = float(self.config.get("last_vanilla_launch_at") or 0.0)
        if last_mode == "vanilla" and float(finding.get("mtime") or 0.0) >= max(0.0, last_vanilla - 30.0):
            self.recent_crash_finding = None
            self.crash_notice_var.set("Most recent crash was from a vanilla launch; no mod was quarantined.")
            self._set_crash_guard_actions()
            return None

        signature = str(finding.get("signature") or "")
        ignored = {str(x) for x in (self.config.get("dismissed_crash_signatures") or [])}
        if signature and signature in ignored:
            self.recent_crash_finding = None
            self.crash_notice_var.set("Previous crash notice dismissed. No new mod-specific crash detected.")
            self._set_crash_guard_actions()
            return None

        candidate = self._primary_crash_candidate(finding)
        self.recent_crash_finding = finding
        if candidate:
            name = str(candidate.get("logical_name") or "Unknown mod")
            confidence = str(candidate.get("confidence") or "low").lower()
            build = read_steam_build_id(self.game_dir)
            version = next(
                (
                    str(m.get("version") or "")
                    for m in scan_installed_mods(self.game_dir)
                    if str(m.get("logical_name") or "").lower() == name.lower()
                ),
                "",
            )
            if signature:
                self.compatibility.record_crash(
                    name,
                    build,
                    version,
                    signature,
                    str(finding.get("error") or ""),
                    str(finding.get("log_name") or ""),
                )
            active_names = {str(q.get("logical_name") or "").lower() for q in quarantines}
            quarantined = name.lower() in active_names
            if quarantined:
                self.crash_notice_var.set(
                    f"{name} is quarantined for this Darktide build/mod version after a recent crash."
                )
            elif confidence == "high":
                self.crash_notice_var.set(
                    f"Darktide crashed during the last modded session. {name} appears in the crash stack and is the likely cause."
                )
            else:
                self.crash_notice_var.set(
                    f"Darktide crashed. {name} is a {confidence}-confidence suspect; review details before disabling it."
                )
            self._set_crash_guard_actions(candidate, finding, quarantined=quarantined)
        else:
            self.crash_notice_var.set(
                "Darktide crashed, but Tertium could not identify a specific mod from the stack."
            )
            self._set_crash_guard_actions(None, finding)
        return finding

    def _remember_handled_crash(self, finding: dict | None) -> None:
        signature = str((finding or {}).get("signature") or "")
        if not signature:
            return
        rows = [str(x) for x in (self.config.get("dismissed_crash_signatures") or []) if str(x) != signature]
        rows.append(signature)
        self.config["dismissed_crash_signatures"] = rows[-24:]
        self._save_config()

    def _quarantine_candidate(self, logical_name: str, finding: dict | None) -> None:
        maintain = self.store.get(AML_MOD_ID) is None
        result = quarantine_crash_candidate(
            self.game_dir,
            self.store,
            self.compatibility,
            logical_name,
            finding=finding,
            maintain_load_order=maintain,
        )
        self._remember_handled_crash(finding)
        self.log_line(
            f"Crash Guard quarantined {logical_name} for Steam build {result.get('build_id') or 'unknown'} "
            f"at mod version {result.get('mod_version') or 'unknown'}."
        )
        self.refresh()
        self._refresh_crash_guard()

    def disable_crash_suspect_and_retry(self) -> None:
        finding = self.recent_crash_finding or self._refresh_crash_guard()
        candidate = self._primary_crash_candidate(finding)
        if not candidate:
            messagebox.showinfo("Crash Guard", "Tertium does not currently have a specific mod suspect to disable.")
            return
        if str(candidate.get("confidence") or "").lower() != "high":
            messagebox.showinfo(
                "Crash Guard",
                "The newest crash does not contain enough direct stack evidence for one-click quarantine. "
                "Use Advanced Details before changing the mod setup.",
            )
            return
        name = str(candidate.get("logical_name") or "")
        if not name:
            return
        try:
            self._quarantine_candidate(name, finding)
        except Exception as exc:
            messagebox.showerror("Crash Guard", str(exc))
            return
        self.log_line(f"Crash Guard retry: launching modded with {name} disabled.")
        self.repair_and_launch(skip_crash_guard=True)

    def launch_crash_anyway(self) -> None:
        finding = self.recent_crash_finding or self._refresh_crash_guard()
        if not finding:
            self.repair_and_launch(skip_crash_guard=True)
            return
        candidate = self._primary_crash_candidate(finding)
        name = str((candidate or {}).get("logical_name") or "")
        self._remember_handled_crash(finding)
        self.recent_crash_finding = None
        self._set_crash_guard_actions()
        self.log_line(
            "Crash Guard override: launching the current modded setup anyway"
            + (f" despite suspect {name}." if name else ".")
        )
        self.repair_and_launch(skip_crash_guard=True)

    def show_crash_guard_details(self) -> None:
        finding = self.recent_crash_finding or self._refresh_crash_guard()
        if not finding:
            messagebox.showinfo("Crash Guard details", "No current crash finding is available.")
            return
        self.mode_var.set("advanced")
        try:
            self.notebook.select(self.tools_tab)
        except Exception:
            pass
        candidates = finding.get("candidates") or []
        candidate_lines = []
        for item in candidates[:5]:
            if not isinstance(item, dict):
                continue
            candidate_lines.append(
                f"- {item.get('logical_name') or 'Unknown'}: {item.get('confidence') or 'unknown'} "
                f"confidence (score {item.get('score') or 0})"
            )
        body = [
            f"Log: {finding.get('log_name') or 'unknown'}",
            "",
            "Error:",
            str(finding.get("error") or "No script error text captured."),
            "",
            "Mod candidates:",
            *(candidate_lines or ["- No specific mod identified"]),
            "",
            "Advanced Mode is now open for Health Check, diagnostics, and recovery tools.",
        ]
        messagebox.showinfo("Crash Guard details", "\n".join(body))

    def dismiss_crash_notice(self) -> None:
        self._remember_handled_crash(self.recent_crash_finding)
        self.recent_crash_finding = None
        self.crash_notice_var.set("Crash notice dismissed. Tertium will alert you again for a new crash signature.")
        self._set_crash_guard_actions()

    def _poll_game_session(self) -> None:
        try:
            running = is_darktide_running()
            if self._last_running_state and not running:
                self.log_line("Darktide session ended; checking the newest session log.")
                self.refresh()
                self._refresh_crash_guard()
            self._last_running_state = running
        except Exception:
            pass
        finally:
            self.root.after(3000, self._poll_game_session)

    def adopt_existing_mods(self) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        try:
            result = adopt_untracked_installed_mods(self.game_dir, self.store)
            self.refresh()
            if result["count"]:
                messagebox.showinfo(
                    "Adopt installed mods",
                    f"Added {result['count']} existing mod folder(s) to Tertium's local registry. "
                    "No game files were moved or replaced. Nexus IDs are not guessed.",
                )
            else:
                messagebox.showinfo("Adopt installed mods", "All detected normal mods are already known to Tertium.")
        except Exception as exc:
            messagebox.showerror("Adopt installed mods", str(exc))

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not self.game_dir:
            self.game_label.set("Darktide: not configured")
            self.core_text.set("Core status: choose your Darktide folder")
            self.mod_count_text.set("0 mods")
            self.dashboard_status.set("Setup required")
            self.dashboard_mods.set("Mods: —")
            self.dashboard_updates.set("Updates: —")
            self.dashboard_build.set("Steam build: —")
            return
        ok, reason = validate_game_dir(self.game_dir)
        if not ok:
            self.game_label.set(f"Darktide: {reason}")
            self.dashboard_status.set("Darktide folder needs attention")
            self.dashboard_build.set("Steam build: —")
            return
        self.game_label.set(f"Darktide: {self.game_dir}")
        try:
            reconcile = reconcile_installed_registry(self.game_dir, self.store)
            if reconcile.get("adopted_count"):
                self.log_line(
                    "Automatically adopted existing mod(s): " + ", ".join(reconcile.get("adopted") or [])
                    + ". They are tracked locally until Nexus supplies a real mod/file ID."
                )
            if reconcile.get("pruned_count"):
                self.log_line("Removed stale local registry entr" + ("y: " if reconcile.get("pruned_count") == 1 else "ies: ") + ", ".join(reconcile.get("pruned") or []))
        except Exception as exc:
            self.log_line(f"Registry reconciliation warning: {exc}")
        tracked = {r.mod_id: r for r in self.store.all()}
        tracked_by_folder = {}
        for r in tracked.values():
            for f in r.folders or []:
                tracked_by_folder[f.lstrip("_").casefold()] = r
        scanned = scan_installed_mods(self.game_dir)
        query = self.filter_var.get().strip().lower()
        visible_count = 0
        for item in scanned:
            logical = item["logical_name"]
            rec = tracked_by_folder.get(logical.casefold())
            haystack = " ".join([
                logical, item["folder"], item.get("version", ""),
                rec.name if rec else "", rec.version if rec else "",
                str(rec.mod_id) if rec and rec.source == "nexus" else "",
            ]).lower()
            if query and query not in haystack:
                continue
            visible_count += 1
            self.tree.insert(
                "",
                END,
                iid=f"folder:{item['folder']}",
                values=(
                    "✓" if item["enabled"] else "—",
                    rec.name if rec else logical,
                    (rec.version if rec else item["version"]) or "",
                    rec.mod_id if rec and rec.source == "nexus" else "",
                    item["folder"],
                    (
                        "Available"
                        if rec and rec.mod_id in self.update_ids
                        else (
                            "Baseline ?"
                            if rec and rec.source == "nexus" and rec.file_id <= 0 and rec.mod_id not in CORE_NAMES
                            else ("Auto-link" if rec and rec.source == "local" and rec.mod_id not in CORE_NAMES else "")
                        )
                    ),
                ),
            )
        total = len(scanned)
        enabled_count = sum(1 for item in scanned if item["enabled"])
        disabled_count = total - enabled_count
        nexus_linked = sum(1 for r in tracked.values() if r.source == "nexus" and r.mod_id not in CORE_NAMES)
        local_only = sum(1 for r in tracked.values() if r.source == "local" and r.mod_id not in CORE_NAMES)
        count_prefix = f"{visible_count}/{total} mods" if query else f"{total} mods"
        self.mod_count_text.set(f"{count_prefix} · {nexus_linked} linked · {local_only} local")
        self.dashboard_mods.set(f"Mods: {enabled_count} on / {disabled_count} off")
        if self.update_ids:
            self.dashboard_updates.set(f"Updates: {len(self.update_ids)} available")
        elif local_only:
            self.dashboard_updates.set(f"Updates: none flagged · {local_only} local-only")
        else:
            self.dashboard_updates.set("Updates: none flagged")
        dml = "installed" if has_dml(self.game_dir) else "missing"
        patch_state = loader_patch_state(self.game_dir)
        patch_label = "enabled" if patch_state is True else ("disabled" if patch_state is False else "unknown")
        dmf = "installed" if (mods_dir(self.game_dir) / "dmf").exists() else "missing"
        aml_record = self.store.get(AML_MOD_ID)
        aml = "installed" if aml_record else "not tracked"
        build_id = read_steam_build_id(self.game_dir)
        self.dashboard_build.set(f"Steam build: {build_id}" if build_id else "Steam build: unknown")
        if not has_dml(self.game_dir):
            self.dashboard_status.set("Core loader setup required")
        elif patch_state is False:
            self.dashboard_status.set("Darktide update detected — repair ready")
        elif patch_state is not True:
            self.dashboard_status.set("Loader state needs verification")
        elif not (mods_dir(self.game_dir) / "dmf").exists():
            self.dashboard_status.set("DMF framework missing")
        else:
            self.dashboard_status.set("Ready for deployment")
        build_text = f"   |   Steam build: {build_id}" if build_id else ""
        last_repair_build = str(self.config.get("last_repair_build_id") or "")
        update_hint = ""
        if patch_state is False:
            update_hint = "   |   ACTION: loader patch required"
        elif build_id and last_repair_build and build_id != last_repair_build:
            update_hint = "   |   New Steam build detected"
        self.core_text.set(f"DML: {dml} ({patch_label})   |   DMF: {dmf}   |   AML: {aml}{build_text}{update_hint}")
        names = sorted(self.profiles.all(), key=str.lower)
        self.profile_combo["values"] = names
        self.simple_profile_combo["values"] = names
        if self.profile_var.get() not in names:
            self.profile_var.set(names[0] if names else "")

    def _sort_tree(self, column: str) -> None:
        rows = [(self.tree.set(item, column), item) for item in self.tree.get_children("")]
        reverse = self.sort_reverse.get(column, False)

        def key(pair):
            value = pair[0]
            if column == "nexus":
                try:
                    return (0, int(value))
                except (TypeError, ValueError):
                    return (1, str(value).lower())
            if column == "enabled":
                return 0 if value == "✓" else 1
            return str(value).lower()

        rows.sort(key=key, reverse=reverse)
        for index, (_value, item) in enumerate(rows):
            self.tree.move(item, "", index)
        self.sort_reverse[column] = not reverse

    def show_getting_started(self, auto: bool = False) -> None:
        game_ready = bool(self.game_dir and validate_game_dir(self.game_dir)[0])
        dml_ready = bool(game_ready and has_dml(self.game_dir))
        dmf_ready = bool(game_ready and (mods_dir(self.game_dir) / "dmf").exists())
        aml_ready = bool(self.store.get(AML_MOD_ID))
        nexus_ready = bool(self.api_key)
        lines = [
            f"Tertium Mod Manager {APP_VERSION} — {RELEASE_NAME}",
            "",
            "Setup status:",
            f"  Darktide folder: {'ready' if game_ready else 'needs selection'}",
            f"  Nexus account: {'configured' if nexus_ready else 'optional / not configured'}",
            f"  Darktide Mod Loader: {'installed' if dml_ready else 'not detected'}",
            f"  Darktide Mod Framework: {'installed' if dmf_ready else 'not detected'}",
            f"  Auto Mod Loading & Ordering: {'tracked' if aml_ready else 'optional / not tracked'}",
            "",
            "Recommended first setup:",
            "1. Confirm the Darktide folder.",
            "2. Configure your Nexus API key if you want Nexus integration.",
            "3. Install DML, then DMF, then AML (optional but recommended).",
            "4. Install normal mods through Mod Manager Download or Install Archive.",
            "5. Use Repair & Launch Darktide for normal play.",
            "",
            "Troubleshooting: Health Check finds common setup/dependency issues; Troubleshoot Safe Mode temporarily disables normal mods; Restore Last Mod State reverses the latest protected mod-state change.",
        ]
        if auto:
            lines.extend(["", "This welcome appears only on first run. You can reopen it with Getting Started."])
        messagebox.showinfo("Getting Started", "\n".join(lines))

    def check_or_install_app_update(self) -> None:
        release = self.available_app_release
        if release and is_newer_version(release.version, APP_VERSION):
            self.install_app_update(release)
        else:
            self.check_self_update(announce=True)

    def check_self_update(self, announce: bool = True) -> None:
        if self.worker_active:
            if announce:
                messagebox.showinfo("Tertium Update", "Tertium is busy with another task. Try the update check again when it finishes.")
            return

        def worker() -> None:
            try:
                release = fetch_latest_release()
                self.queue.put(("self_update_checked", {"release": release, "announce": announce}))
            except Exception as exc:
                self.queue.put(("self_update_check_error", {"error": str(exc), "announce": announce}))

        self._run_worker(worker, "Checking for Tertium update…")

    def install_app_update(self, release: ReleaseInfo | None = None) -> None:
        release = release or self.available_app_release
        if release is None or not is_newer_version(release.version, APP_VERSION):
            self.check_self_update(announce=True)
            return
        if os.name != "nt":
            messagebox.showerror("Tertium Update", "Automatic self-update is currently supported only on Windows.")
            return
        if not messagebox.askyesno(
            "Update Tertium",
            f"Update Tertium v{APP_VERSION} to v{release.version}?\n\n"
            "Tertium will download the official installer, verify SHA-256, close itself, install silently, and reopen automatically.",
        ):
            return

        def worker() -> None:
            target = update_cache_dir() / f"TertiumModManager-{release.version}-Setup-x64.exe"
            download_installer(
                release,
                target,
                lambda done, total: self.queue.put(("progress", (done, total))),
            )
            digest = verify_installer(target, release)
            self.queue.put(("self_update_downloaded", {"release": release, "path": str(target), "sha256": digest}))

        self._run_worker(worker, f"Downloading Tertium v{release.version}…")

    def choose_game_dir(self) -> None:
        path = filedialog.askdirectory(title="Choose Warhammer 40,000 DARKTIDE folder")
        if not path:
            return
        p = Path(path)
        ok, reason = validate_game_dir(p)
        if not ok:
            messagebox.showerror("Invalid folder", reason)
            return
        self.game_dir = p
        self._save_config()
        self.refresh()
        if self.api_key:
            self.root.after(100, self._start_auto_nexus_reconcile)

    def set_api_key(self) -> None:
        key = simpledialog.askstring(
            "Nexus API Key",
            "Paste your Nexus Mods personal API key. It is encrypted with Windows DPAPI before being stored locally.",
            show="*",
            initialvalue=self.api_key,
        )
        if key is None:
            return
        key = key.strip()
        if not key:
            self.api_key = ""
            for field in ("nexus_user_id", "nexus_name", "nexus_is_premium"):
                self.config.pop(field, None)
            self._save_config()
            self.api_label.set("Nexus: API key not set")
            return
        try:
            data = NexusClient(key).validate()
        except Exception as exc:
            messagebox.showerror("Nexus API", str(exc))
            return
        self.api_key = key
        name = data.get("name") or "account"
        self.config["nexus_user_id"] = str(data.get("user_id") or "")
        self.config["nexus_name"] = str(name)
        self.config["nexus_is_premium"] = bool(data.get("is_premium", False))
        self._save_config()
        premium = bool(data.get("is_premium", False))
        tier = "Premium · automatic downloads" if premium else "Free · automatic downloads restricted by Nexus"
        self.api_label.set(f"Nexus: {name} · {tier}")
        self.log_line(
            f"Nexus API key validated for {name}; account tier: {'Premium' if premium else 'Free'}. "
            "UPDATE ALL never opens Nexus pages automatically."
        )
        self.root.after(100, lambda: self._start_auto_nexus_reconcile(announce=True))

    def register_nxm(self) -> None:
        try:
            register_nxm_protocol()
            messagebox.showinfo("Nexus links", "Registered Tertium Mod Manager as the nxm:// handler for your Windows account.")
        except Exception as exc:
            messagebox.showerror("Nexus links", str(exc))

    def setup_core_stack(self) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        if not self.api_key:
            messagebox.showinfo(
                "Core setup",
                "Configure your Nexus API key first so Tertium can receive Mod Manager Download links and install the core stack safely.",
            )
            return
        if not has_dml(self.game_dir):
            target_id, name = DML_MOD_ID, "Darktide Mod Loader (DML)"
        elif not (mods_dir(self.game_dir) / "dmf").exists():
            target_id, name = DMF_MOD_ID, "Darktide Mod Framework (DMF)"
        elif self.store.get(AML_MOD_ID) is None:
            target_id, name = AML_MOD_ID, "Auto Mod Loading & Ordering (AML)"
        else:
            messagebox.showinfo("Core setup", "DML, DMF, and AML are already detected. Use PLAY MODDED to verify/repair the loader and launch Darktide.")
            return
        messagebox.showinfo(
            "Core setup",
            f"Next: {name}.\n\nTertium will open the Nexus files page. Click Mod Manager Download for the current main file; Tertium will receive the NXM link and install it. Then click Set Up Core Stack again for the next component.",
        )
        self.open_nexus(target_id)

    def open_nexus(self, mod_id: int) -> None:
        webbrowser.open(f"https://www.nexusmods.com/{GAME_DOMAIN}/mods/{mod_id}?tab=files")

    def _record_for_folder(self, folder: str) -> ModRecord | None:
        logical = folder.lstrip("_")
        for rec in self.store.all():
            if any(f.lstrip("_") == logical for f in (rec.folders or [])):
                return rec
        return None

    def open_selected_nexus(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        folder = str(values[4])
        rec = self._record_for_folder(folder)
        if not rec or rec.source != "nexus":
            messagebox.showinfo("Nexus", "This mod is not linked to a Nexus record.")
            return
        self.open_nexus(rec.mod_id)

    def _local_only_mod_records(self) -> list[ModRecord]:
        return [
            record
            for record in self.store.all()
            if record.source == "local" and record.mod_id not in CORE_NAMES
        ]

    def _load_nexus_catalog(self, client: NexusClient) -> list[dict]:
        cache_path = self.store.root / f"nexus-catalog-{GAME_DOMAIN}.json"
        cached = load_json(cache_path, {})
        if isinstance(cached, dict):
            try:
                fetched_at = float(cached.get("fetched_at") or 0.0)
            except (TypeError, ValueError):
                fetched_at = 0.0
            rows = cached.get("mods")
            if isinstance(rows, list) and rows and time.time() - fetched_at < 24 * 60 * 60:
                return [dict(row) for row in rows if isinstance(row, dict)]
        rows = client.game_mod_catalog(GAME_DOMAIN)
        save_json(cache_path, {"fetched_at": time.time(), "mods": rows})
        return rows

    def _start_auto_nexus_reconcile(self, announce: bool = False) -> None:
        if self.auto_nexus_reconcile_active or self.worker_active:
            return
        if not self.api_key or not self.game_dir or not validate_game_dir(self.game_dir)[0]:
            return
        local_records = self._local_only_mod_records()
        if not local_records:
            if announce:
                messagebox.showinfo("Auto-Link Existing", "All installed normal mods are already Nexus-linked.")
            return
        self.auto_nexus_reconcile_active = True

        def worker() -> None:
            try:
                self._auto_nexus_reconcile_worker(announce=announce)
            except Exception as exc:
                self.queue.put(("log", f"Automatic Nexus reconciliation warning: {exc}"))
                self.queue.put((
                    "auto_nexus_reconcile_complete",
                    {
                        "linked_exact": [],
                        "linked_page": [],
                        "unresolved": [record.name for record in local_records],
                        "errors": [str(exc)],
                        "announce": announce,
                    },
                ))

        self._run_worker(worker, f"Auto-linking {len(local_records)} existing Nexus mod(s)…")

    def _auto_nexus_reconcile_worker(self, announce: bool = False) -> None:
        client = NexusClient(self.api_key)
        catalog = self._load_nexus_catalog(client)
        linked_exact: list[str] = []
        linked_page: list[str] = []
        unresolved: list[str] = []
        errors: list[str] = []

        for local in self._local_only_mod_records():
            names = [local.name, *(folder.lstrip("_") for folder in (local.folders or []))]
            candidate = unique_catalog_match(names, catalog)
            if not candidate:
                unresolved.append(local.name)
                continue
            nexus_mod_id = int(candidate.get("mod_id") or 0)
            if nexus_mod_id <= 0:
                unresolved.append(local.name)
                continue
            existing = self.store.get(nexus_mod_id)
            if existing is not None and existing.mod_id != local.mod_id:
                unresolved.append(local.name)
                errors.append(
                    f"{local.name}: Nexus mod {nexus_mod_id} is already linked to {existing.name}."
                )
                continue
            try:
                mod_info = client.mod_info(nexus_mod_id)
                files_payload = client.mod_files(nexus_mod_id)
                exact = matching_files_for_version(files_payload, local.version)
                file_info = exact[0] if len(exact) == 1 else {}
                linked = link_local_record_to_nexus(
                    self.store,
                    local.mod_id,
                    nexus_mod_id,
                    mod_info,
                    file_info,
                )
                if linked.file_id > 0:
                    linked_exact.append(linked.name)
                    self.queue.put((
                        "log",
                        f"Auto-linked {local.name} → Nexus {linked.mod_id}, file {linked.file_id} "
                        f"({linked.version or 'version unknown'}).",
                    ))
                else:
                    linked_page.append(linked.name)
                    self.queue.put((
                        "log",
                        f"Auto-linked {local.name} → Nexus {linked.mod_id}; historical file baseline is unknown. "
                        "Update All will resolve it to the latest main file.",
                    ))
            except Exception as exc:
                unresolved.append(local.name)
                errors.append(f"{local.name}: {exc}")

        self.queue.put((
            "auto_nexus_reconcile_complete",
            {
                "linked_exact": linked_exact,
                "linked_page": linked_page,
                "unresolved": unresolved,
                "errors": errors,
                "announce": announce,
            },
        ))

    def link_selected_existing_mod(self) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        if not self.api_key:
            messagebox.showerror("Nexus", "Set your Nexus API key first.")
            return
        if is_darktide_running():
            messagebox.showerror(
                "Darktide is running",
                "Close Darktide before changing Tertium's mod tracking records.",
            )
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(
                "Link existing mod",
                "Automatic linking handles normal cases. In Advanced Mode, select an unresolved local-only mod and use Link Selected Manually only as a fallback.",
            )
            return
        values = self.tree.item(sel[0], "values")
        folder = str(values[4])
        rec = self._record_for_folder(folder)
        if not rec:
            messagebox.showerror("Link existing mod", "Tertium could not find the selected mod in its registry.")
            return
        if rec.source == "nexus":
            messagebox.showinfo(
                "Link existing mod",
                f"{rec.name} is already linked to Nexus mod {rec.mod_id}.",
            )
            return
        if rec.mod_id in CORE_NAMES:
            messagebox.showinfo(
                "Link existing mod",
                "Core framework components use their dedicated Nexus records and are not linked here.",
            )
            return

        self.pending_existing_link_local_id = None
        self.pending_existing_link_nexus_mod_id = None
        reference = simpledialog.askstring(
            "Manual Nexus link fallback",
            f"Local mod: {rec.name}\n"
            f"Installed version: {rec.version or 'unknown'}\n\n"
            "Automatic linking could not resolve this mod. Paste the Nexus mod page URL or numeric mod ID.\n\n"
            "Tertium will only change its tracking record; it will not download, reinstall, enable, disable, or move the mod.",
        )
        if reference is None:
            return
        try:
            nexus_mod_id = parse_nexus_mod_reference(reference)
        except Exception as exc:
            messagebox.showerror("Link existing mod", str(exc))
            return
        self._run_worker(
            lambda: self._prepare_existing_mod_link(rec.mod_id, nexus_mod_id),
            f"Checking Nexus match for {rec.name}…",
        )

    def _prepare_existing_mod_link(self, local_mod_id: int, nexus_mod_id: int) -> None:
        local = self.store.get(local_mod_id)
        if local is None or local.source != "local":
            raise ModManagerError("The selected mod is no longer a local-only Tertium record.")
        client = NexusClient(self.api_key)
        mod_info = client.mod_info(nexus_mod_id)
        files_payload = client.mod_files(nexus_mod_id)
        files = [
            dict(item)
            for item in files_payload.get("files", [])
            if isinstance(item, dict) and int(item.get("file_id") or 0) > 0
        ]
        if not files:
            raise ModManagerError("Nexus returned no usable files for that mod page.")
        exact = matching_files_for_version(files_payload, local.version)
        self.queue.put((
            "existing_link_ready",
            {
                "local_mod_id": local_mod_id,
                "nexus_mod_id": nexus_mod_id,
                "mod_info": dict(mod_info or {}),
                "files": files,
                "exact": exact,
            },
        ))

    def _prepare_existing_mod_link_from_nxm(self, local_mod_id: int, link: NxmLink) -> None:
        local = self.store.get(local_mod_id)
        if local is None or local.source != "local":
            raise ModManagerError("The selected mod is no longer a local-only Tertium record.")
        client = NexusClient(self.api_key)
        mod_info = client.mod_info(link.mod_id, link.domain)
        file_info = client.file_info(link.mod_id, link.file_id, link.domain)
        self.queue.put((
            "existing_link_ready",
            {
                "local_mod_id": local_mod_id,
                "nexus_mod_id": link.mod_id,
                "mod_info": dict(mod_info or {}),
                "files": [dict(file_info or {})],
                "exact": [],
                "chosen": dict(file_info or {}),
            },
        ))

    @staticmethod
    def _recent_nexus_files(files: list[dict], limit: int = 12) -> list[dict]:
        def key(item: dict) -> tuple[int, int]:
            try:
                uploaded = int(item.get("uploaded_timestamp") or 0)
            except (TypeError, ValueError):
                uploaded = 0
            try:
                file_id = int(item.get("file_id") or 0)
            except (TypeError, ValueError):
                file_id = 0
            return (uploaded, file_id)

        return sorted(files, key=key, reverse=True)[:max(1, limit)]

    def _finish_existing_mod_link(self, payload: dict) -> None:
        local = self.store.get(int(payload.get("local_mod_id") or 0))
        if local is None or local.source != "local":
            messagebox.showerror("Link existing mod", "The selected local mod record changed before linking completed.")
            return
        mod_info = dict(payload.get("mod_info") or {})
        files = [dict(item) for item in (payload.get("files") or []) if isinstance(item, dict)]
        exact = [dict(item) for item in (payload.get("exact") or []) if isinstance(item, dict)]
        nexus_mod_id = int(payload.get("nexus_mod_id") or 0)
        chosen: dict | None = None

        forced = payload.get("chosen")
        if isinstance(forced, dict) and int(forced.get("file_id") or 0) > 0:
            chosen = dict(forced)
        elif len(exact) == 1:
            chosen = exact[0]
        else:
            self.pending_existing_link_local_id = local.mod_id
            self.pending_existing_link_nexus_mod_id = nexus_mod_id
            reason = (
                f"Tertium found {len(exact)} Nexus files declaring installed version {local.version!r}, so it cannot choose one safely."
                if exact
                else (
                    f"Tertium could not uniquely match installed version {local.version!r} to a Nexus file."
                    if local.version
                    else "Tertium could not read an installed version for this mod."
                )
            )
            messagebox.showinfo(
                "Choose the installed Nexus file",
                reason
                + "\n\nTertium will open the Nexus Files page. Find the exact file/version you currently have installed "
                "and click Mod Manager Download. Tertium will capture that file ID and LINK ONLY — it will not download or reinstall the mod.",
            )
            self.open_nexus(nexus_mod_id)
            return

        nexus_name = str(mod_info.get("name") or f"Nexus mod {nexus_mod_id}")
        nexus_version = str(chosen.get("version") or chosen.get("mod_version") or "")
        file_id = int(chosen.get("file_id") or 0)
        if not messagebox.askyesno(
            "Confirm Nexus link",
            f"Link local mod:\n  {local.name} ({local.version or 'version unknown'})\n\n"
            f"to Nexus:\n  {nexus_name}\n"
            f"  Mod ID {nexus_mod_id} · File ID {file_id}"
            + (f" · Version {nexus_version}" if nexus_version else "")
            + "\n\nNo mod files will be downloaded or changed.",
        ):
            return

        try:
            linked = link_local_record_to_nexus(
                self.store,
                local.mod_id,
                nexus_mod_id,
                mod_info,
                chosen,
            )
            self.pending_existing_link_local_id = None
            self.pending_existing_link_nexus_mod_id = None
            self.update_ids.discard(local.mod_id)
            self.log_line(
                f"Linked existing local mod {local.name} to Nexus mod {linked.mod_id}, file {linked.file_id} "
                "without reinstalling it."
            )
            self.refresh()
            remaining = sum(
                1
                for record in self.store.all()
                if record.source == "local" and record.mod_id not in CORE_NAMES
            )
            messagebox.showinfo(
                "Nexus link complete",
                f"{linked.name} is now Nexus-linked and eligible for Update All.\n\n"
                f"{remaining} local-only mod(s) remain.",
            )
        except Exception as exc:
            messagebox.showerror("Link existing mod", str(exc))

    def prompt_nxm(self) -> None:
        value = simpledialog.askstring("Install Nexus Mod", "Paste an nxm://warhammer40kdarktide/... link:")
        if value:
            self.handle_nxm(value)

    def handle_nxm(self, url: str) -> None:
        if self.worker_active:
            self.pending_nxm.append(url)
            self.log_line(f"Queued Nexus download while another task is running ({len(self.pending_nxm)} queued).")
            return
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        if not self.api_key:
            messagebox.showerror("Setup", "Set your Nexus API key first.")
            return
        try:
            link = parse_nxm_url(url)
            if link.domain != GAME_DOMAIN:
                raise ModManagerError(f"This manager only accepts Darktide Nexus links ({GAME_DOMAIN}).")
        except Exception as exc:
            messagebox.showerror("NXM link", str(exc))
            return

        if self.pending_existing_link_local_id is not None:
            expected_mod_id = int(self.pending_existing_link_nexus_mod_id or 0)
            if expected_mod_id and link.mod_id != expected_mod_id:
                messagebox.showerror(
                    "Wrong Nexus mod",
                    f"Tertium is waiting to link Nexus mod {expected_mod_id}, but the received link is for mod {link.mod_id}. "
                    "Return to the opened Nexus page and click Mod Manager Download for the exact installed file.",
                )
                return
            local_id = int(self.pending_existing_link_local_id)
            self._run_worker(
                lambda: self._prepare_existing_mod_link_from_nxm(local_id, link),
                "Validating Nexus file for existing mod…",
            )
            return

        if self.guided_update_active and self.guided_update_queue and self.guided_waiting_mod_id == link.mod_id:
            old, successor = self.guided_update_queue[0]
            expected_file = int(successor.get("file_id") or 0)
            if expected_file and link.file_id != expected_file:
                messagebox.showerror(
                    "Wrong Nexus file",
                    f"Tertium is waiting for the queued update to {old.name} (file {expected_file}), but Nexus sent file {link.file_id}. "
                    "Return to the opened Nexus page and choose Mod Manager Download for the queued update file.",
                )
                return
            self.guided_installing_mod_id = link.mod_id
            self.guided_waiting_mod_id = None
            self.log_line(f"Guided Update All: authorized {old.name}; installing now.")

        self._run_worker(lambda: self._download_and_install(link), f"Downloading Nexus mod {link.mod_id}…")

    def _download_and_install(self, link: NxmLink) -> None:
        if is_darktide_running():
            raise ModManagerError("Close Darktide before downloading/installing a mod. Tertium will not modify a live game install.")
        configured_user = str(self.config.get("nexus_user_id") or "")
        if link.user_id and configured_user and str(link.user_id) != configured_user:
            raise ModManagerError(
                "This Nexus download link was generated for a different Nexus account than the API key configured in Tertium. "
                "Open Nexus while signed into the same account and click Mod Manager Download again."
            )
        client = NexusClient(self.api_key)
        mod_info = client.mod_info(link.mod_id, link.domain)
        file_info = client.file_info(link.mod_id, link.file_id, link.domain)
        urls = client.download_urls(link)
        url = choose_mirror(urls)
        file_name = str(file_info.get("file_name") or f"{link.mod_id}-{link.file_id}.zip")
        cache = self.store.cache_archive_path(link.mod_id, link.file_id, file_name)
        self.queue.put(("log", f"Downloading {mod_info.get('name', link.mod_id)} / {file_name}"))

        def prog(done: int, total: int | None) -> None:
            self.queue.put(("progress", (done, total)))

        download_file(url, cache, prog)
        record = ModRecord(
            mod_id=link.mod_id,
            file_id=link.file_id,
            name=str(mod_info.get("name") or file_info.get("name") or f"Nexus Mod {link.mod_id}"),
            version=str(file_info.get("version") or file_info.get("mod_version") or ""),
            file_name=file_name,
            category_id=file_info.get("category_id"),
        )
        self._install_record_archive(cache, record)
        self.queue.put(("log", f"Installed {record.name} {record.version}."))

    def _install_record_archive(self, archive: Path, record: ModRecord) -> None:
        if record.mod_id != DML_MOD_ID:
            install_archive(self.game_dir, archive, record, self.store, log=lambda m: self.queue.put(("log", m)))
            if record.mod_id not in {DMF_MOD_ID, AML_MOD_ID} and self.store.get(AML_MOD_ID) is None:
                enabled = [
                    m["logical_name"] for m in scan_installed_mods(self.game_dir)
                    if m["enabled"] and m["logical_name"].lower() != "dmf"
                ]
                write_mod_load_order(self.game_dir, enabled)
            return

        was_patched = has_dml(self.game_dir) and loader_patch_state(self.game_dir) is True
        if was_patched:
            self.queue.put(("log", "DML update detected: safely disabling the current loader patch before replacing loader files…"))
            code, output = unpatch_loader(self.game_dir)
            if output.strip():
                self.queue.put(("log", output.strip()))
            if code != 0:
                raise ModManagerError(f"Could not disable the existing DML patch before update (exit code {code}).")

        try:
            install_archive(self.game_dir, archive, record, self.store, log=lambda m: self.queue.put(("log", m)))
        except Exception:
            if was_patched and has_dml(self.game_dir):
                try:
                    patch_loader(self.game_dir)
                    self.queue.put(("log", "DML file install failed; restored the previous loader patch state."))
                except Exception as restore_exc:
                    self.queue.put(("log", f"WARNING: DML install failed and repatching the previous loader also failed: {restore_exc}"))
            raise

        self.queue.put(("log", "Enabling the installed DML patch…"))
        code, output = patch_loader(self.game_dir)
        if output.strip():
            self.queue.put(("log", output.strip()))
        if code != 0 or loader_patch_state(self.game_dir) is not True:
            raise ModManagerError(f"DML files were installed, but enabling/verifying the loader failed (exit code {code}).")
        self.queue.put(("log", "Re-applying AML from cache if available…"))
        self._reapply_aml_if_cached()

    def _reapply_aml_if_cached(self) -> None:
        aml = self.store.get(AML_MOD_ID)
        if not aml:
            return
        cached = self.store.cache_archive_path(aml.mod_id, aml.file_id, aml.file_name)
        if cached.exists():
            integrity = verify_cached_archive(aml, self.store)
            if integrity is False:
                raise ModManagerError("The cached AML archive failed its install-time SHA-256 check. Download AML again before updating DML.")
            install_archive(self.game_dir, cached, aml, self.store, log=lambda m: self.queue.put(("log", m)))
            self.queue.put(("log", "AML patch re-applied after DML update."))

    def install_local_archive(self) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        path = filedialog.askopenfilename(
            title="Choose Darktide mod archive",
            filetypes=[("Archives", "*.zip *.7z *.rar"), ("All files", "*.*")],
        )
        if not path:
            return
        mod_id = simpledialog.askinteger("Optional Nexus ID", "Nexus mod ID (leave blank/cancel for local-only install):", minvalue=1)
        if mod_id is None:
            mod_id = int(time.time())  # local-only synthetic record id
            name = Path(path).stem
            file_id = mod_id
            version = "local"
        else:
            name = CORE_NAMES.get(mod_id, f"Nexus Mod {mod_id}")
            file_id = int(time.time())
            version = "local"
        record = ModRecord(mod_id=mod_id, file_id=file_id, name=name, version=version, source="local")
        self._run_worker(
            lambda: self._install_record_archive(Path(path), record),
            f"Installing {Path(path).name}…",
        )

    def check_updates(self) -> None:
        if not self.api_key:
            messagebox.showerror("Nexus", "Set your Nexus API key first.")
            return
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        self.update_ids.clear()
        self._run_worker(self._check_updates_worker, "Checking Nexus for updates…")

    def _check_updates_worker(self) -> None:
        client = NexusClient(self.api_key)
        updates = []
        all_records = self.store.all()
        records = [r for r in all_records if r.source == "nexus" and r.mod_id < 1000000000]
        local_only = [r for r in all_records if r.source == "local" and r.mod_id not in CORE_NAMES]
        if local_only:
            self.queue.put((
                "log",
                f"{len(local_only)} adopted local-only mod(s) are not Nexus-linked yet. "
                "Tertium will automatically reconcile exact mod-name matches against the connected Nexus catalog.",
            ))
        for idx, rec in enumerate(records, 1):
            self.queue.put(("status", f"Checking {idx}/{len(records)}: {rec.name}"))
            try:
                successor = client.latest_successor(rec.mod_id, rec.file_id)
            except Exception as exc:
                self.queue.put(("log", f"Update check failed for {rec.name}: {exc}"))
                continue
            if successor:
                updates.append((rec, successor))
        if not updates:
            body = f"All {len(records)} Nexus-linked mod(s) are current."
            if local_only:
                body += (
                    f"\n\n{len(local_only)} installed mod(s) are still local-only. "
                    "Tertium auto-links only unique verified Nexus catalog matches; unresolved names are left untouched rather than guessed."
                )
            self.queue.put(("message", ("info", "Updates", body)))
            return
        lines = []
        for rec, succ in updates:
            lines.append(f"{rec.name}: {rec.version or rec.file_id} → {succ.get('version') or succ.get('file_id')}")
        self.queue.put(("updates", updates))
        note = (
            "Update All will try automatic download first and fall back to Nexus browser authorization "
            "only for files that require it."
        )
        if local_only:
            note += f"\n\n{len(local_only)} local-only mod(s) are not Nexus-linked yet."
        self.queue.put(("message", ("info", "Updates available", "\n".join(lines) + "\n\n" + note)))

    def repair_loader(self) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        self._run_worker(lambda: self._repair_worker(False), "Repairing Darktide after update…")

    def repair_and_launch(self, skip_crash_guard: bool = False) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        if not has_dml(self.game_dir):
            messagebox.showerror("Core setup", "Darktide Mod Loader is not installed yet. Install DML first.")
            return
        if is_darktide_running():
            messagebox.showinfo("Darktide is already running", "Darktide.exe is already running.")
            return

        # If the newest session produced a high-confidence mod stack, offer the
        # recovery path before launching the exact same setup again. Explicit
        # Crash Guard actions pass skip_crash_guard=True so they never prompt twice.
        finding = None if skip_crash_guard else self._refresh_crash_guard()
        candidate = self._primary_crash_candidate(finding)
        if not skip_crash_guard and candidate and candidate.get("confidence") == "high":
            name = str(candidate.get("logical_name") or "the suspected mod")
            choice = messagebox.askyesnocancel(
                "Crash Guard",
                f"The most recent modded session crashed and {name} appears directly in the crash stack.\n\n"
                f"Yes: disable {name}, quarantine it for this Darktide build/mod version, and launch.\n"
                "No: launch the current setup anyway.\n"
                "Cancel: do not launch.",
            )
            if choice is None:
                return
            if choice is True:
                try:
                    self._quarantine_candidate(name, finding)
                except Exception as exc:
                    messagebox.showerror("Crash Guard", str(exc))
                    return
        self._run_worker(lambda: self._repair_worker(True), "Checking loader and preparing Darktide…")

    def _repair_worker(self, launch_after: bool = False) -> None:
        report = repair_after_game_update(
            self.game_dir,
            self.store,
            reapply_aml=self._reapply_aml_if_cached,
            log=lambda m: self.queue.put(("log", m)),
        )
        if launch_after:
            preflight = modded_launch_preflight(self.game_dir, self.store)
            report["preflight"] = preflight
            reconcile = preflight.get("reconcile") or {}
            if reconcile.get("adopted_count"):
                self.queue.put(("log", "Preflight adopted existing mod(s): " + ", ".join(reconcile.get("adopted") or [])))
            for warning in preflight.get("warnings") or []:
                self.queue.put(("log", "Preflight warning: " + str(warning.get("message") or warning)))
            blockers = list(preflight.get("blockers") or [])
            if blockers:
                lines = []
                for issue in blockers[:8]:
                    subject = issue.get("mod") or issue.get("folder") or "Mods"
                    lines.append(f"- {subject}: {issue.get('message') or 'preflight error'}")
                more = len(blockers) - len(lines)
                detail = "\n".join(lines)
                if more > 0:
                    detail += f"\n- …and {more} more issue(s)"
                raise ModManagerError(
                    "Tertium repaired the loader, but did not launch because the mod preflight found a blocking issue:\n\n"
                    + detail
                    + "\n\nOpen Advanced Mode → Health Check for full details."
                )
        self.queue.put(("repair_complete", (report, launch_after)))

    def update_all(self) -> None:
        if is_darktide_running():
            messagebox.showerror("Darktide is running", "Close Darktide before updating mods.")
            return
        if not self.api_key:
            messagebox.showerror("Nexus", "Set your Nexus API key first.")
            return
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        if not messagebox.askyesno(
            "Update all tracked mods",
            "Tertium will check every tracked Nexus mod and create a protected state snapshot before changing anything.\n\n"
            "UPDATE ALL is browser-free: Tertium will only use download links Nexus authorizes directly through the API. "
            "It will not open Nexus pages or ask you to manually download files.\n\n"
            "Continue?",
        ):
            return
        self._run_worker(self._update_all_worker, "Finding and installing Nexus updates…")

    def _collect_guided_updates_worker(self) -> None:
        client = NexusClient(self.api_key)
        updates: list[tuple[ModRecord, dict]] = []
        all_records = self.store.all()
        records = [r for r in all_records if r.source == "nexus" and r.mod_id < 1000000000]
        local_only = [r for r in all_records if r.source == "local" and r.mod_id not in CORE_NAMES]
        if local_only:
            self.queue.put(("log", f"Update All: {len(local_only)} local-only mod(s) are adopted but not Nexus-linked yet."))
        for idx, rec in enumerate(records, 1):
            self.queue.put(("status", f"Checking {idx}/{len(records)}: {rec.name}"))
            try:
                successor = client.latest_successor(rec.mod_id, rec.file_id)
            except Exception as exc:
                self.queue.put(("log", f"Update check failed for {rec.name}: {exc}"))
                continue
            if successor:
                updates.append((rec, successor))
        if not updates:
            body = f"All {len(records)} Nexus-linked mod(s) are current."
            if local_only:
                body += (
                    f"\n\n{len(local_only)} local-only mod(s) are still unresolved. "
                    "Use Auto-Link Existing to retry catalog reconciliation; only unusual names should need the Advanced manual fallback."
                )
            self.queue.put(("message", ("info", "Updates", body)))
            return
        priority = {DML_MOD_ID: 0, DMF_MOD_ID: 1, AML_MOD_ID: 2}
        updates.sort(key=lambda pair: (priority.get(pair[0].mod_id, 10), pair[0].name.lower()))
        snapshot = create_mod_state_snapshot(
            self.game_dir,
            self.store,
            "pre-update-all-guided",
            extra={"updates": [{"mod_id": old.mod_id, "from_file_id": old.file_id, "to_file_id": int(succ.get("file_id") or 0)} for old, succ in updates]},
        )
        self.queue.put(("log", f"Saved pre-Update-All mod state snapshot: {snapshot.name}"))
        self.queue.put(("guided_updates", updates))

    def _begin_guided_updates(self, updates: list[tuple[ModRecord, dict]]) -> None:
        self.guided_update_queue = list(updates)
        self.guided_update_active = bool(updates)
        self.guided_waiting_mod_id = None
        self.guided_installing_mod_id = None
        if not updates:
            return
        messagebox.showinfo(
            "Update All — Nexus authorization",
            f"{len(updates)} remaining update(s) require Nexus browser authorization.\n\n"
            "Tertium will open each required Nexus page in order. Click Mod Manager Download once on that page; "
            "Tertium will receive the NXM link, install the update, and automatically continue.",
        )
        self._open_next_guided_update()

    def _open_next_guided_update(self) -> None:
        if not self.guided_update_active:
            return
        if self.worker_active or self.guided_installing_mod_id is not None:
            return
        if not self.guided_update_queue:
            self.guided_update_active = False
            self.guided_waiting_mod_id = None
            self.status.set("Ready")
            self.update_ids.clear()
            self.update_map.clear()
            self.refresh()
            messagebox.showinfo("Update All", "Guided update queue complete. Run Check Updates if you want to verify every tracked mod is current.")
            return
        old, successor = self.guided_update_queue[0]
        file_id = int(successor.get("file_id") or 0)
        self.guided_waiting_mod_id = old.mod_id
        self.status.set(f"Waiting for Nexus authorization: {old.name}")
        self.log_line(
            f"Guided Update All: opening {old.name} (target file {file_id}). Click Mod Manager Download on Nexus."
        )
        url = f"https://www.nexusmods.com/{GAME_DOMAIN}/mods/{old.mod_id}?tab=files"
        if file_id:
            url += f"&file_id={file_id}"
        webbrowser.open(url)

    def _update_all_worker(self) -> None:
        client = NexusClient(self.api_key)
        updates: list[tuple[ModRecord, dict]] = []
        all_records = self.store.all()
        records = [r for r in all_records if r.source == "nexus" and r.mod_id < 1000000000]
        local_only = [r for r in all_records if r.source == "local" and r.mod_id not in CORE_NAMES]
        if local_only:
            self.queue.put(("log", f"Update All: {len(local_only)} local-only mod(s) are adopted but not Nexus-linked yet."))
        for idx, rec in enumerate(records, 1):
            self.queue.put(("status", f"Checking {idx}/{len(records)}: {rec.name}"))
            successor = client.latest_successor(rec.mod_id, rec.file_id)
            if successor:
                updates.append((rec, successor))
        if not updates:
            body = f"All {len(records)} Nexus-linked mod(s) are current."
            if local_only:
                body += (
                    f"\n\n{len(local_only)} local-only mod(s) are still unresolved. "
                    "Use Auto-Link Existing to retry catalog reconciliation; only unusual names should need the Advanced manual fallback."
                )
            self.queue.put(("message", ("info", "Updates", body)))
            return
        priority = {DML_MOD_ID: 0, DMF_MOD_ID: 1, AML_MOD_ID: 2}
        updates.sort(key=lambda pair: (priority.get(pair[0].mod_id, 10), pair[0].name.lower()))
        snapshot = create_mod_state_snapshot(
            self.game_dir,
            self.store,
            "pre-update-all",
            extra={"updates": [{"mod_id": old.mod_id, "from_file_id": old.file_id, "to_file_id": int(succ.get("file_id") or 0)} for old, succ in updates]},
        )
        self.queue.put(("log", f"Saved pre-Update-All mod state snapshot: {snapshot.name}"))
        for idx, (old, successor) in enumerate(updates, 1):
            new_file_id = int(successor.get("file_id"))
            self.queue.put(("status", f"Updating {idx}/{len(updates)}: {old.name}"))
            try:
                urls = client.direct_download_urls(old.mod_id, new_file_id)
            except ModManagerError as exc:
                if browser_authorization_required(exc):
                    blocked = updates[idx - 1:]
                    names = ", ".join(item[0].name for item in blocked[:6])
                    if len(blocked) > 6:
                        names += f", and {len(blocked) - 6} more"
                    self.queue.put((
                        "log",
                        f"Nexus refused direct API download authorization for {old.name}. "
                        "Tertium will not open a browser or hand the update back to the user.",
                    ))
                    self.queue.put((
                        "message",
                        (
                            "error",
                            "Nexus blocked automatic download",
                            "Tertium found the update(s), but Nexus did not authorize a direct API download for this account.\n\n"
                            f"Blocked update(s): {names}\n\n"
                            "UPDATE ALL will not open Nexus or ask you to download files manually. "
                            "Nexus-hosted files can only be zero-click updated when the Nexus account/API session is permitted to request direct download links.",
                        ),
                    ))
                    return
                raise
            file_name = str(successor.get("file_name") or f"{old.mod_id}-{new_file_id}.zip")
            cache = self.store.cache_archive_path(old.mod_id, new_file_id, file_name)
            download_file(choose_mirror(urls), cache, lambda done, total: self.queue.put(("progress", (done, total))))
            record = ModRecord(
                mod_id=old.mod_id,
                file_id=new_file_id,
                name=old.name,
                version=str(successor.get("version") or successor.get("mod_version") or ""),
                file_name=file_name,
                category_id=successor.get("category_id"),
                source="nexus",
            )
            self._install_record_archive(cache, record)
            self.queue.put(("log", f"Updated {old.name} to {record.version or new_file_id}."))
        self.update_ids.clear()
        self.update_map.clear()
        suffix = f" {len(local_only)} local-only mod(s) remain adopted but unlinked." if local_only else ""
        self.queue.put(("message", ("info", "Updates complete", f"Updated {len(updates)} tracked mod(s)." + suffix)))

    def save_profile(self) -> None:
        if not self.game_dir:
            return
        name = simpledialog.askstring("Save profile", "Profile name:", initialvalue=self.profile_var.get() or "My Mods")
        if not name:
            return
        try:
            states = capture_mod_profile(self.game_dir)
            self.profiles.save(name, states)
            self.profile_var.set(name.strip())
            self.refresh()
            self.log_line(f"Saved mod profile '{name.strip()}' with {len(states)} mod entries.")
        except Exception as exc:
            messagebox.showerror("Profile", str(exc))

    def apply_selected_profile(self) -> None:
        if not self.game_dir:
            return
        name = self.profile_var.get().strip()
        if not name:
            messagebox.showinfo("Profiles", "Save a profile first.")
            return
        states = self.profiles.all().get(name)
        if states is None:
            messagebox.showerror("Profile", f"Profile not found: {name}")
            return
        try:
            current_states = capture_mod_profile(self.game_dir)
            if any(current_states.get(mod_name) != bool(desired) for mod_name, desired in states.items() if mod_name in current_states):
                snapshot = create_mod_state_snapshot(
                    self.game_dir,
                    self.store,
                    "pre-profile-apply",
                    extra={"profile": name},
                )
                self.log_line(f"Saved pre-profile state snapshot: {snapshot.name}")
            result = apply_mod_profile(
                self.game_dir,
                states,
                maintain_load_order=self.store.get(AML_MOD_ID) is None,
            )
            sync_registry_folders(self.game_dir, self.store)
            self.log_line(f"Applied profile '{name}'; changed {len(result['changed'])} mod(s).")
            if result["missing"]:
                self.log_line("Profile entries not installed: " + ", ".join(result["missing"]))
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Profile", str(exc))

    def delete_selected_profile(self) -> None:
        name = self.profile_var.get().strip()
        if not name:
            return
        if not messagebox.askyesno("Delete profile", f"Delete mod profile '{name}'?"):
            return
        self.profiles.delete(name)
        self.profile_var.set("")
        self.refresh()
        self.log_line(f"Deleted mod profile '{name}'.")

    def enter_safe_mode(self) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        if is_darktide_running():
            messagebox.showerror("Darktide is running", "Close Darktide before changing the active mod set.")
            return
        if not messagebox.askyesno(
            "Troubleshooting Safe Mode",
            "Disable all normal mods while keeping the core framework installed?\n\n"
            "Tertium will first save your current enabled/disabled state so Restore Last Mod State can put it back exactly.",
        ):
            return
        try:
            result = enter_troubleshooting_safe_mode(
                self.game_dir,
                self.store,
                maintain_load_order=self.store.get(AML_MOD_ID) is None,
            )
            self.log_line(f"Entered troubleshooting safe mode; changed {len(result['changed'])} mod(s).")
            self.refresh()
            messagebox.showinfo(
                "Safe mode ready",
                "All normal mods are disabled. Launch Darktide to test the core loader/framework.\n\n"
                "Use Restore Last Mod State when you are finished troubleshooting.",
            )
        except Exception as exc:
            messagebox.showerror("Troubleshooting Safe Mode", str(exc))

    def restore_safe_mode(self) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        if is_darktide_running():
            messagebox.showerror("Darktide is running", "Close Darktide before restoring the previous mod set.")
            return
        try:
            result = restore_latest_mod_state(
                self.game_dir,
                self.store,
                maintain_load_order=self.store.get(AML_MOD_ID) is None,
            )
            self.log_line(
                f"Restored mod-state snapshot ({result.get('reason') or 'unknown'}); "
                f"changed {len(result['changed'])} mod(s)."
            )
            if result.get("missing"):
                self.log_line("Snapshot entries no longer installed: " + ", ".join(result["missing"]))
            self.refresh()
            messagebox.showinfo(
                "Mod set restored",
                f"Restored the saved enabled/disabled state. Changed {len(result['changed'])} mod(s)."
                + (f"\nMissing: {', '.join(result['missing'])}" if result.get("missing") else ""),
            )
        except Exception as exc:
            messagebox.showerror("Restore Last Mod State", str(exc))

    def export_setup(self) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        default_name = f"tertium-darktide-setup-{time.strftime('%Y%m%d')}.json"
        path = filedialog.asksaveasfilename(
            title="Export Darktide mod setup",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("Tertium setup", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            result = export_setup_manifest(self.game_dir, self.store, self.profiles, Path(path), APP_VERSION)
            self.log_line(f"Exported secret-free setup manifest: {result}")
            messagebox.showinfo(
                "Setup exported",
                "Saved a shareable mod/setup manifest. It contains no Nexus API key.\n\n" + str(result),
            )
        except Exception as exc:
            messagebox.showerror("Export Setup", str(exc))

    def compare_setup(self) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        path = filedialog.askopenfilename(
            title="Compare a Tertium setup manifest",
            filetypes=[("Tertium setup", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            manifest = load_setup_manifest(Path(path))
            result = compare_setup_manifest(self.game_dir, self.store, manifest)
            lines = [
                f"Manifest: {Path(path).name}",
                f"Manifest Tertium: {manifest.get('tertium_version') or 'unknown'}",
                f"Manifest Steam build: {manifest.get('steam_build_id') or 'unknown'}",
                "",
                f"Missing folders: {len(result['missing_folders'])}",
                f"Extra folders: {len(result['extra_folders'])}",
                f"Enabled-state differences: {len(result['state_differences'])}",
                f"Missing tracked Nexus mods: {len(result['missing_nexus'])}",
                f"Different Nexus file versions: {len(result['nexus_differences'])}",
            ]
            detail: list[str] = []
            if result["missing_folders"]:
                detail.append("Missing: " + ", ".join(result["missing_folders"][:12]))
            if result["extra_folders"]:
                detail.append("Extra: " + ", ".join(result["extra_folders"][:12]))
            if result["missing_nexus"]:
                detail.append("Missing Nexus: " + ", ".join(x["name"] for x in result["missing_nexus"][:12]))
            if result["nexus_differences"]:
                detail.append(
                    "Version differences: "
                    + ", ".join(
                        f"{x['name']} ({x['current_file_id']} vs {x['manifest_file_id']})"
                        for x in result["nexus_differences"][:12]
                    )
                )
            messagebox.showinfo("Setup comparison", "\n".join(lines + (["", *detail] if detail else ["", "Current setup matches the manifest at this level."])))
            self.log_line(
                "Compared setup manifest: "
                f"missing={len(result['missing_folders'])}, extra={len(result['extra_folders'])}, "
                f"state_diff={len(result['state_differences'])}, nexus_diff={len(result['nexus_differences'])}."
            )
        except Exception as exc:
            messagebox.showerror("Compare Setup", str(exc))

    def create_diagnostics(self) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        default = f"Tertium-Diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        path = filedialog.asksaveasfilename(
            title="Save diagnostic bundle",
            defaultextension=".zip",
            initialfile=default,
            filetypes=[("ZIP archive", "*.zip")],
        )
        if not path:
            return
        try:
            result = create_diagnostic_bundle(self.game_dir, self.store, Path(path), APP_VERSION)
            self.log_line(f"Created diagnostic bundle: {result}")
            messagebox.showinfo(
                "Diagnostics created",
                "Diagnostic ZIP created. It contains Tertium status, your installed mod list, AML/load-order information, and recent Darktide console logs when available. The Nexus API key is not included.",
            )
        except Exception as exc:
            messagebox.showerror("Diagnostics", str(exc))

    def show_health_check(self) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        try:
            report = health_report(self.game_dir, self.store)
            if not report.get("game_valid"):
                messagebox.showerror("Health Check", str(report.get("reason") or "Darktide folder is invalid."))
                return
            patch = report.get("dml_patch_state")
            patch_text = "enabled" if patch is True else ("disabled" if patch is False else "unknown")
            aml_state = report.get("aml_patch_state")
            aml_text = "verified" if aml_state is True else ("needs reapply" if aml_state is False else ("tracked/unverified" if report.get("aml_tracked") else "not tracked"))
            free_gb = float(report.get("free_bytes") or 0) / 1024**3
            writable = report.get("game_writable")
            writable_text = "not tested (Darktide is running)" if writable is None else ("OK" if writable else "BLOCKED")
            lines = [
                f"Steam build: {report.get('build_id') or 'unknown'}",
                f"Game running: {'yes' if report.get('darktide_running') else 'no'}",
                f"Game folder write access: {writable_text}",
                f"Free space: {free_gb:.1f} GiB",
                f"DML: {'present' if report.get('dml_present') else 'MISSING'} / patch {patch_text}",
                f"DMF: {'present' if report.get('dmf_present') else 'MISSING'}",
                f"AML: {aml_text}",
                f"Mods: {report.get('enabled_mods', 0)} enabled / {report.get('disabled_mods', 0)} disabled",
            ]
            if report.get("cache_corrupt"):
                lines.append("Corrupt cached archives: " + ", ".join(report["cache_corrupt"]))
            if report.get("cache_missing"):
                lines.append("Missing tracked caches: " + ", ".join(report["cache_missing"]))
            if report.get("stale_partial_downloads"):
                lines.append(f"Stale partial downloads: {report['stale_partial_downloads']}")
            structure_issues = report.get("structure_issues") or []
            if structure_issues:
                errors = sum(1 for item in structure_issues if item.get("severity") == "error")
                warnings = len(structure_issues) - errors
                lines.append(f"Mod-folder audit: {errors} error(s), {warnings} warning(s)")
                for item in structure_issues[:5]:
                    lines.append(f"  - {item.get('folder')}: {item.get('message')}")
                if len(structure_issues) > 5:
                    lines.append(f"  - ...and {len(structure_issues) - 5} more")
            else:
                lines.append("Mod-folder audit: no structural issues detected")
            dependency_issues = report.get("dependency_issues") or []
            if dependency_issues:
                dep_errors = sum(1 for item in dependency_issues if item.get("severity") == "error")
                dep_warnings = len(dependency_issues) - dep_errors
                lines.append(f"Dependency/order audit: {dep_errors} error(s), {dep_warnings} warning(s)")
                for item in dependency_issues[:5]:
                    lines.append(f"  - {item.get('mod')}: {item.get('message')}")
                if len(dependency_issues) > 5:
                    lines.append(f"  - ...and {len(dependency_issues) - 5} more")
            else:
                lines.append("Dependency/order audit: no declared-rule issues detected")
            if report.get("mod_state_restore_available") or report.get("safe_mode_restore_available"):
                lines.append("Protected mod-state snapshot: restore available")
            if report.get("aml_log_errors"):
                lines.append(f"AML log: {len(report['aml_log_errors'])} recent error-like line(s)")
            elif report.get("aml_log_warnings"):
                lines.append(f"AML log: {len(report['aml_log_warnings'])} recent warning line(s)")
            else:
                lines.append("AML log: no recent error/warning markers detected")
            healthy = (
                report.get("game_writable")
                and report.get("dml_present")
                and patch is True
                and report.get("dmf_present")
                and not any(item.get("severity") == "error" for item in (report.get("structure_issues") or []))
                and not any(item.get("severity") == "error" for item in (report.get("dependency_issues") or []))
            )
            title = "Darktide looks ready" if healthy else "Darktide needs attention"
            messagebox.showinfo(title, "\n".join(lines))
        except Exception as exc:
            messagebox.showerror("Health Check", str(exc))

    def open_mods_folder(self) -> None:
        if not self.game_dir:
            return
        path = mods_dir(self.game_dir)
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                webbrowser.open(path.as_uri())
        except Exception as exc:
            messagebox.showerror("Mods folder", str(exc))

    def restore_removed_mod(self) -> None:
        if not self.game_dir:
            return
        try:
            result = restore_latest_removed_mod(self.game_dir, self.store)
            if self.store.get(AML_MOD_ID) is None:
                enabled = [
                    m["logical_name"] for m in scan_installed_mods(self.game_dir)
                    if m["enabled"] and m["logical_name"].lower() != "dmf"
                ]
                write_mod_load_order(self.game_dir, enabled)
            self.log_line(f"Restored removed mod {result['logical_name']}.")
            self.refresh()
            messagebox.showinfo("Mod restored", f"Restored {result['logical_name']} to the Darktide mods folder.")
        except Exception as exc:
            messagebox.showerror("Restore removed mod", str(exc))

    def open_data_folder(self) -> None:
        path = self.store.root
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                webbrowser.open(path.as_uri())
        except Exception as exc:
            messagebox.showerror("Data folder", str(exc))

    def clean_backups(self) -> None:
        items = backup_inventory(self.store)
        if len(items) <= 20:
            messagebox.showinfo("Backups", f"You have {len(items)} backup set(s); nothing needs cleanup.")
            return
        total = sum(int(x["size"]) for x in items[20:])
        if not messagebox.askyesno(
            "Clean old backups",
            f"Keep the newest 20 backup sets and remove {len(items) - 20} older set(s), freeing about {total / 1024 / 1024:.1f} MB?",
        ):
            return
        result = prune_backups(self.store, keep=20)
        messagebox.showinfo("Backups", f"Removed {result['removed']} old backup set(s); freed {result['freed'] / 1024 / 1024:.1f} MB.")

    def rollback_loader(self) -> None:
        if not self.game_dir:
            return
        if not messagebox.askyesno(
            "Rollback loader patch",
            "Restore the newest compatible pre-repair bundle database for this Steam build?\n\n"
            "This disables mod loading for the current build. Tertium will first make a safety copy of the current database.",
        ):
            return
        try:
            backup = restore_latest_loader_backup(self.game_dir, self.store, log=self.log_line)
            self.log_line(f"Restored loader backup: {backup}")
            self.refresh()
            messagebox.showinfo("Rollback complete", "The pre-repair loader database was restored. Mod loading should now be disabled until you repair again.")
        except Exception as exc:
            messagebox.showerror("Rollback", str(exc))

    def toggle_selected(self) -> None:
        if not self.game_dir:
            return
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        folder = str(values[4])
        if folder.lstrip("_").lower() == "dmf":
            messagebox.showinfo("Core framework", "Darktide Mod Framework is a core dependency and cannot be toggled from the normal mod list.")
            return
        enabled_now = values[0] == "✓"
        try:
            new_name = toggle_mod_folder(self.game_dir, folder, not enabled_now)
            sync_registry_folders(self.game_dir, self.store)
            self.log_line(f"{'Enabled' if not enabled_now else 'Disabled'} {new_name.lstrip('_')}")
            # If AML is not tracked, maintain mod_load_order.txt too.
            if not self.store.get(AML_MOD_ID):
                enabled = [
                    m["logical_name"] for m in scan_installed_mods(self.game_dir)
                    if m["enabled"] and m["logical_name"].lower() != "dmf"
                ]
                write_mod_load_order(self.game_dir, enabled)
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Toggle mod", str(exc))

    def rollback_selected_update(self) -> None:
        if not self.game_dir:
            return
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        name = str(values[1])
        folder = str(values[4])
        rec = self._record_for_folder(folder)
        if not rec:
            messagebox.showinfo("Rollback update", "This mod is not tracked by Tertium, so there is no managed update backup to restore.")
            return
        if rec.mod_id in {DML_MOD_ID, DMF_MOD_ID, AML_MOD_ID}:
            messagebox.showinfo("Rollback update", "Core DML/DMF/AML components use the dedicated loader recovery workflow rather than per-mod rollback.")
            return
        if not messagebox.askyesno(
            "Rollback mod update",
            f"Restore the newest previous Tertium-managed version of {name}?\n\n"
            "The currently installed version will be safety-backed up first.",
        ):
            return
        try:
            result = rollback_mod_update(self.game_dir, self.store, rec.mod_id)
            if self.store.get(AML_MOD_ID) is None:
                enabled = [
                    m["logical_name"] for m in scan_installed_mods(self.game_dir)
                    if m["enabled"] and m["logical_name"].lower() != "dmf"
                ]
                write_mod_load_order(self.game_dir, enabled)
            self.log_line(f"Rolled back {name} to {result.get('version') or 'the previous tracked version'}.")
            self.refresh()
            messagebox.showinfo("Rollback complete", f"Restored {name} to the previous Tertium-managed version.")
        except Exception as exc:
            messagebox.showerror("Rollback update", str(exc))

    def remove_selected(self) -> None:
        if not self.game_dir:
            return
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        name = str(values[1])
        folder = str(values[4])
        if folder.lstrip("_").lower() == "dmf":
            messagebox.showinfo("Core framework", "Darktide Mod Framework is a core dependency and is protected from removal here.")
            return
        if not messagebox.askyesno("Remove mod", f"Remove {name}?\n\nTertium will move the mod folder into a reversible backup instead of deleting it permanently."):
            return
        try:
            rec = self._record_for_folder(folder)
            backup = quarantine_mod_folder(self.game_dir, folder, self.store, record=rec)
            if rec:
                self.store.remove_mod(rec.mod_id)
            if self.store.get(AML_MOD_ID) is None:
                enabled = [
                    m["logical_name"] for m in scan_installed_mods(self.game_dir)
                    if m["enabled"] and m["logical_name"].lower() != "dmf"
                ]
                write_mod_load_order(self.game_dir, enabled)
            self.log_line(f"Removed {name} to reversible backup: {backup.name}")
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Remove mod", str(exc))

    def on_double_click(self, _event) -> None:
        self.toggle_selected()

    def launch_vanilla(self) -> None:
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        if is_darktide_running():
            messagebox.showinfo("Darktide is already running", "Darktide.exe is already running.")
            return
        if not messagebox.askyesno(
            "Launch without mods",
            "Temporarily disable the Darktide loader patch and launch the game without mods?\n\n"
            "Your mod files and profiles are not removed. Use Repair & Launch next time to re-enable mod loading.",
        ):
            return

        def worker() -> None:
            if has_dml(self.game_dir) and loader_patch_state(self.game_dir) is True:
                code, output = unpatch_loader(self.game_dir)
                if output.strip():
                    self.queue.put(("log", output.strip()))
                if code != 0 or loader_patch_state(self.game_dir) is not False:
                    raise ModManagerError("Could not verify that the loader patch was disabled for vanilla launch.")
            self.config["last_vanilla_launch_at"] = time.time()
            self.config["last_launch_mode"] = "vanilla"
            self._save_config()
            open_game_launcher(self.game_dir)
            self.queue.put(("log", "Launched Darktide with the mod-loader patch disabled."))

        self._run_worker(worker, "Preparing vanilla Darktide…")

    def launch_game(self) -> None:
        if is_darktide_running():
            messagebox.showinfo("Darktide is already running", "Darktide.exe is already running.")
            return
        if not self.game_dir:
            messagebox.showerror("Setup", "Choose your Darktide game folder first.")
            return
        if has_dml(self.game_dir) and loader_patch_state(self.game_dir) is False:
            if messagebox.askyesno(
                "Mods are disabled",
                "Darktide's loader patch is currently disabled, usually because the game updated. Repair it and launch now?",
            ):
                self.repair_and_launch()
            return
        try:
            self.config["last_modded_launch_at"] = time.time()
            self.config["last_launch_mode"] = "modded"
            self._save_config()
            open_game_launcher(self.game_dir)
        except Exception as exc:
            messagebox.showerror("Launch", str(exc))

    def _run_worker(self, func, status: str) -> None:
        if self.worker_active:
            messagebox.showinfo("Tertium is busy", "Another install/update/repair task is already running. Nexus links will be queued automatically.")
            return
        self.worker_active = True
        self.status.set(status)
        self.progress["value"] = 0
        self.progress["maximum"] = 100

        def runner():
            try:
                func()
                self.queue.put(("done", None))
            except Exception as exc:
                self.queue.put(("log", "Worker exception:\n" + traceback.format_exc()))
                self.queue.put(("error", str(exc)))

        threading.Thread(target=runner, daemon=True).start()

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.log_line(str(payload))
                elif kind == "progress":
                    done, total = payload
                    if total:
                        self.progress["maximum"] = total
                        self.progress["value"] = done
                        self.status.set(f"Downloading {done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB")
                    else:
                        self.progress["maximum"] = max(done, 1)
                        self.progress["value"] = done
                        self.status.set(f"Downloading {done / 1024 / 1024:.1f} MB")
                elif kind == "status":
                    self.status.set(str(payload))
                elif kind == "done":
                    self.worker_active = False
                    self.status.set("Ready")
                    self.progress["value"] = 0
                    if self.guided_installing_mod_id is not None:
                        installed_id = self.guided_installing_mod_id
                        if self.guided_update_queue and self.guided_update_queue[0][0].mod_id == installed_id:
                            finished, _successor = self.guided_update_queue.pop(0)
                            self.log_line(f"Guided Update All: completed {finished.name}.")
                        self.guided_installing_mod_id = None
                    self.refresh()
                    self.root.after(50, self._process_pending_nxm)
                    if self.guided_update_active and self.guided_installing_mod_id is None:
                        self.root.after(250, self._open_next_guided_update)
                elif kind == "error":
                    self.worker_active = False
                    self.status.set("Error")
                    if self.guided_installing_mod_id is not None:
                        self.log_line("Guided Update All paused because the current update failed. Run Update All again after resolving the error.")
                        self.guided_update_active = False
                        self.guided_installing_mod_id = None
                        self.guided_waiting_mod_id = None
                    messagebox.showerror("Tertium Mod Manager", str(payload))
                    self.root.after(50, self._process_pending_nxm)
                elif kind == "message":
                    level, title, body = payload
                    getattr(messagebox, f"show{level}")(title, body)
                elif kind == "updates":
                    self.update_ids = {r.mod_id for r, _ in payload}
                    self.update_map = {r.mod_id: (r, succ) for r, succ in payload}
                    self.refresh()
                elif kind == "official_news":
                    item = dict(payload or {})
                    self.news_title_var.set(str(item.get("title") or "Official Darktide Updates"))
                    date = str(item.get("date") or "")
                    feed = str(item.get("feed") or "Steam")
                    self.news_date_var.set((date + " · " + feed).strip(" ·"))
                    self.news_url = str(item.get("url") or OFFICIAL_NEWS_URL)
                elif kind == "external_instance":
                    self._activate_window()
                    if payload:
                        self.handle_nxm(str(payload))
                elif kind == "guided_updates":
                    self._begin_guided_updates(list(payload))
                elif kind == "existing_link_ready":
                    self._finish_existing_mod_link(dict(payload or {}))
                elif kind == "self_update_checked":
                    info = dict(payload or {})
                    release = info.get("release")
                    announce = bool(info.get("announce"))
                    if isinstance(release, ReleaseInfo):
                        if is_newer_version(release.version, APP_VERSION):
                            self.available_app_release = release
                            self.app_update_text.set(f"UPDATE TERTIUM → v{release.version}")
                            self.app_update_status.set(
                                f"Tertium v{release.version} is available. The launcher can download, verify, install, and restart automatically."
                            )
                            if announce:
                                if messagebox.askyesno(
                                    "Tertium Update Available",
                                    f"Tertium v{release.version} is available.\n\nInstall it now?",
                                ):
                                    self.root.after(100, lambda rel=release: self.install_app_update(rel))
                        else:
                            self.available_app_release = None
                            self.app_update_text.set(f"Tertium v{APP_VERSION} · Check for Update")
                            self.app_update_status.set(f"Tertium v{APP_VERSION} is current.")
                            if announce:
                                messagebox.showinfo("Tertium Update", f"Tertium v{APP_VERSION} is already current.")
                elif kind == "self_update_check_error":
                    info = dict(payload or {})
                    error = str(info.get("error") or "Unknown update-check error.")
                    self.app_update_status.set(f"Application update check unavailable: {error}")
                    if info.get("announce"):
                        if messagebox.askyesno(
                            "Tertium Update",
                            error + "\n\nOpen the Tertium Releases page in your browser?",
                        ):
                            webbrowser.open("https://github.com/Tangodwn/TertiumModManager/releases")
                elif kind == "self_update_downloaded":
                    info = dict(payload or {})
                    release = info.get("release")
                    path = Path(str(info.get("path") or ""))
                    if not isinstance(release, ReleaseInfo) or not path.exists():
                        messagebox.showerror("Tertium Update", "The verified update installer could not be prepared.")
                    else:
                        try:
                            schedule_windows_installer(path)
                            self.log_line(
                                f"Verified Tertium v{release.version} update ({info.get('sha256')}); "
                                "closing for silent installer handoff."
                            )
                            self.app_update_status.set(f"Installing Tertium v{release.version}…")
                            self.root.after(300, self.root.destroy)
                        except Exception as exc:
                            messagebox.showerror("Tertium Update", str(exc))
                elif kind == "auto_nexus_reconcile_complete":
                    report = dict(payload or {})
                    self.auto_nexus_reconcile_active = False
                    self.refresh()
                    exact = list(report.get("linked_exact") or [])
                    page = list(report.get("linked_page") or [])
                    unresolved = list(report.get("unresolved") or [])
                    linked_count = len(exact) + len(page)
                    if linked_count:
                        self.log_line(
                            f"Automatic Nexus reconciliation linked {linked_count} mod(s): "
                            f"{len(exact)} exact file match(es), {len(page)} mod-page match(es)."
                        )
                    if report.get("announce") or linked_count:
                        body = [
                            f"Linked automatically: {linked_count}",
                            f"  Exact Nexus file: {len(exact)}",
                            f"  Mod page linked / baseline unknown: {len(page)}",
                            f"Still unresolved: {len(unresolved)}",
                        ]
                        if page:
                            body.extend([
                                "",
                                "Mods with an unknown historical file baseline are still Nexus-linked. "
                                "Update All will offer the latest main file and establish exact tracking after that update.",
                            ])
                        if unresolved:
                            body.extend([
                                "",
                                "Unresolved mods were left untouched rather than guessed. "
                                "Advanced Mode keeps a manual-link fallback for unusual names.",
                            ])
                        messagebox.showinfo("Nexus Auto-Link", "\n".join(body))
                elif kind == "repair_complete":
                    report, launch_after = payload
                    build = report.get("build_id") or read_steam_build_id(self.game_dir) or "unknown"
                    self.config["last_repair_build_id"] = "" if build == "unknown" else str(build)
                    self.config["last_repair_at"] = time.time()
                    self._save_config()
                    details = [f"Steam build: {build}", "DML patch: enabled"]
                    details.append("DMF: present" if report.get("dmf_present") else "DMF: MISSING")
                    if report.get("aml_reapplied"):
                        details.append("AML: re-applied")
                    if report.get("backup"):
                        details.append("Rollback backup: created")
                    self.log_line("Repair complete: " + "; ".join(details))
                    self.refresh()
                    if launch_after:
                        try:
                            # Tertium owns mod_load_order.txt even when AML is installed.
                            changed = enforce_active_quarantines(self.game_dir, self.store, self.compatibility, maintain_load_order=True)
                            if changed:
                                self.log_line("Crash Guard kept quarantined mod(s) disabled: " + ", ".join(changed))
                            self.config["last_modded_launch_at"] = time.time()
                            self.config["last_launch_mode"] = "modded"
                            self._save_config()
                            open_game_launcher(self.game_dir)
                        except Exception as exc:
                            messagebox.showerror("Launch", str(exc))
                    else:
                        messagebox.showinfo(
                            "Darktide ready",
                            "Repair completed successfully.\n\n" + "\n".join(details),
                        )
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)


def _forward_to_existing_instance(startup_nxm: str | None) -> bool:
    payload = json.dumps({"magic": INSTANCE_MAGIC, "nxm": startup_nxm or ""}).encode("utf-8") + b"\n"
    try:
        with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=0.35) as sock:
            sock.settimeout(0.75)
            sock.sendall(payload)
            reply = sock.recv(32).decode("ascii", errors="ignore").strip()
            return reply == "OK"
    except OSError:
        return False


def main() -> None:
    startup_nxm = None
    for arg in sys.argv[1:]:
        if arg.lower().startswith("nxm://"):
            startup_nxm = arg
            break
    if _forward_to_existing_instance(startup_nxm):
        return
    root = Tk()
    TertiumApp(root, startup_nxm=startup_nxm)
    root.mainloop()


def _main_with_crash_log() -> None:
    try:
        main()
    except Exception:
        detail = traceback.format_exc()
        try:
            crash_path = app_data_dir() / "tertium-crash.log"
            crash_path.write_text(detail, encoding="utf-8")
        except OSError:
            pass
        raise


if __name__ == "__main__":
    _main_with_crash_log()
