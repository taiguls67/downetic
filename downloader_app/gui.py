from __future__ import annotations

import os
import queue
import sqlite3
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.parse import urlsplit

from downloader_app.links import parse_urls
from downloader_app.localization import available_locales, load_locale
from downloader_app.manager import DownloadManager
from downloader_app.media_links import MediaLink, ScanProgress, scan_media_pages
from downloader_app.models import DownloadRecord
from downloader_app.storage import Storage, default_data_directory

MAX_EVENTS_PER_TICK = 100
COLORS = {
    "window": "#f0f0f0",
    "panel": "#ffffff",
    "header": "#d7e7f0",
    "header_dark": "#b7c9d3",
    "list": "#ffffff",
    "text": "#202020",
    "muted": "#77838a",
    "blue": "#3d83b4",
    "blue_dark": "#245a7c",
    "selection": "#4a86c5",
    "yellow": "#f2c94c",
    "yellow_light": "#fff4c7",
    "yellow_hover": "#ffeb9c",
    "white": "#ffffff",
    "green": "#36804b",
    "orange": "#d48a22",
    "red": "#a83a35",
}


def format_size(size: int | None, unknown: str = "Unknown") -> str:
    if size is None:
        return unknown
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return unknown


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


class DownloadManagerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Downetic")
        self.root.geometry("1000x700")
        self.root.minsize(800, 500)
        self._app_icon = tk.PhotoImage(
            file=str(Path(__file__).parent / "assets" / "icons" / "main_ico.png")
        )
        self.root.iconphoto(True, self._app_icon)
        aero_icons = Path(__file__).parent / "assets" / "aero"
        self.dialog_icons = {
            kind: tk.PhotoImage(file=str(aero_icons / filename)).subsample(3, 3)
            for kind, filename in {
                "info": "media-playback-start.png",
                "warning": "media-playback-pause.png",
                "error": "media-playback-stop.png",
            }.items()
        }
        self._configure_style()
        self.storage = Storage()
        self.storage.recover_interrupted_downloads()
        self.locales = available_locales()
        saved_locale = self.storage.get_setting("language", "en")
        self.locale = saved_locale if saved_locale in self.locales else "en"
        self.translations = load_locale(self.locale)
        self._localized_updates: list[Callable[[], None]] = []
        self.download_directory = self.storage.get_setting(
            "download_directory", str(Path.home() / "Downloads")
        )
        self.simultaneous = int(self.storage.get_setting("simultaneous", "3"))
        self.output_format = self.storage.get_setting("output_format", "original")
        if self.output_format not in {"original", "mp3", "mp4", "webm"}:
            self.output_format = "original"
        self.events: queue.Queue[dict[str, object]] = queue.Queue()
        self.manager = DownloadManager(self.events, self.simultaneous)
        self.records: dict[int, DownloadRecord] = {}
        self.download_rows: dict[int, str] = {}
        self.history_rows: dict[int, str] = {}
        self.media_candidates: dict[str, MediaLink] = {}
        self.visible_media_ids: set[str] = set()
        self.selected_media_ids: set[str] = set()
        self.media_hosts: list[str] = []
        self._closing = False
        self.empty_labels: dict[str, ttk.Label] = {}
        self.empty_details: dict[str, ttk.Label] = {}
        self.empty_icons: dict[str, tk.Canvas] = {}
        self.empty_states_visible: dict[str, bool] = {}
        self._context_tree: ttk.Treeview | None = None
        self.toolbar_icons = self._load_toolbar_icons()
        self._build_menu()
        self._build_layout()
        self._load_existing()
        self._refresh_status()
        self._show_selected_progress()
        self.root.after(150, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        self.root.configure(background=COLORS["window"])
        style.configure(
            ".",
            font=("Tahoma", 8),
            background=COLORS["window"],
            foreground=COLORS["text"],
        )
        style.configure(
            "TFrame",
            background=COLORS["window"],
        )
        style.configure(
            "Toolbar.TFrame",
            background=COLORS["window"],
        )
        style.configure(
            "TLabel",
            background=COLORS["window"],
            foreground=COLORS["text"],
        )
        style.configure(
            "Panel.TLabel",
            background=COLORS["panel"],
            foreground=COLORS["muted"],
        )
        style.configure(
            "TEntry",
            fieldbackground=COLORS["white"],
            foreground=COLORS["text"],
            bordercolor=COLORS["header_dark"],
            padding=4,
        )
        style.configure(
            "TCombobox",
            fieldbackground=COLORS["white"],
            background=COLORS["header"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["blue_dark"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", COLORS["white"])],
            selectbackground=[("readonly", COLORS["selection"])],
            selectforeground=[("readonly", COLORS["white"])],
        )
        style.configure(
            "TButton",
            padding=(7, 3),
            background=COLORS["header"],
            foreground=COLORS["text"],
            bordercolor=COLORS["header_dark"],
        )
        style.map(
            "TButton",
            background=[("pressed", COLORS["header_dark"]), ("active", COLORS["white"])],
            foreground=[("disabled", COLORS["muted"])],
        )
        style.configure(
            "Accent.TButton",
            padding=(9, 4),
            background=COLORS["blue"],
            foreground=COLORS["white"],
            bordercolor=COLORS["blue_dark"],
        )
        style.map(
            "Accent.TButton",
            background=[("pressed", COLORS["blue_dark"]), ("active", "#347db9")],
            foreground=[("active", COLORS["white"])],
        )
        style.configure(
            "TNotebook",
            background=COLORS["window"],
            bordercolor=COLORS["header_dark"],
            tabmargins=(2, 3, 2, 0),
        )
        style.configure(
            "TNotebook.Tab",
            padding=(12, 5),
            background=COLORS["header"],
            foreground=COLORS["text"],
            bordercolor=COLORS["header_dark"],
        )
        style.map(
            "TNotebook.Tab",
            background=[
                ("selected", COLORS["yellow_light"]),
                ("active", COLORS["yellow_hover"]),
            ],
            foreground=[("selected", COLORS["text"])],
            bordercolor=[
                ("selected", COLORS["yellow"]),
                ("active", COLORS["yellow"]),
            ],
        )
        style.configure(
            "Treeview",
            background=COLORS["list"],
            fieldbackground=COLORS["list"],
            foreground=COLORS["text"],
            rowheight=21,
            bordercolor=COLORS["header_dark"],
            lightcolor=COLORS["header_dark"],
            darkcolor=COLORS["header_dark"],
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", COLORS["selection"])],
            foreground=[("selected", COLORS["white"])],
        )
        style.configure(
            "Treeview.Heading",
            background=COLORS["header"],
            foreground=COLORS["text"],
            padding=(5, 4),
            relief="raised",
            bordercolor=COLORS["header_dark"],
            font=("Tahoma", 8),
        )
        style.map(
            "Treeview.Heading",
            background=[("active", COLORS["yellow_light"])],
        )
        style.configure(
            "Horizontal.TProgressbar",
            background="#28a745",
            troughcolor=COLORS["white"],
            bordercolor="#9aa9ae",
            lightcolor="#72d982",
            darkcolor="#168437",
            thickness=14,
        )
        style.configure(
            "TLabelframe",
            background=COLORS["panel"],
            bordercolor=COLORS["header_dark"],
        )
        style.configure(
            "TLabelframe.Label",
            background=COLORS["panel"],
            foreground=COLORS["blue_dark"],
            font=("Tahoma", 8, "bold"),
        )
        style.configure("ToolbarAction.TFrame", background=COLORS["window"])
        style.configure("ToolbarActionHover.TFrame", background=COLORS["yellow_light"])
        style.configure(
            "ToolbarAction.TLabel",
            background=COLORS["window"],
            foreground=COLORS["text"],
        )
        style.configure(
            "ToolbarActionHover.TLabel",
            background=COLORS["yellow_light"],
            foreground=COLORS["text"],
        )

    def _t(self, key: str, **values: object) -> str:
        text = self.translations.get(key)
        if text is None:
            raise KeyError(f"Missing translation key: {key}")
        return text.format(**values) if values else text

    def _register_localized(self, update: Callable[[], None]) -> None:
        self._localized_updates.append(update)
        update()

    def _localize_widget(self, widget: tk.Widget, key: str) -> None:
        self._register_localized(
            lambda: widget.configure(text=self._t(key))
        )

    def _localize_menu_entry(self, menu: tk.Menu, index: int, key: str) -> None:
        self._register_localized(
            lambda: menu.entryconfigure(index, label=self._t(key))
        )

    def _localize_tab(self, tab: ttk.Frame, key: str) -> None:
        self._register_localized(
            lambda: self.tabs.tab(tab, text=self._t(key))
        )

    def _show_message(self, title: str, message: str, kind: str) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.iconphoto(True, self._app_icon)

        content = ttk.Frame(dialog, padding=16)
        content.pack(fill="both", expand=True)
        ttk.Label(content, image=self.dialog_icons[kind]).pack(
            side="left", padx=(0, 14)
        )
        ttk.Label(
            content,
            text=message,
            justify="left",
            wraplength=440,
        ).pack(side="left", fill="x", expand=True)

        buttons = ttk.Frame(content)
        buttons.pack(side="bottom", fill="x", pady=(16, 0))
        close_button = ttk.Button(
            buttons,
            text="OK",
            command=dialog.destroy,
            style="TButton",
            width=10,
        )
        close_button.pack(side="right")
        dialog.bind("<Return>", lambda _event: close_button.invoke())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        self.root.update_idletasks()
        dialog.update_idletasks()
        width = dialog.winfo_reqwidth()
        height = dialog.winfo_reqheight()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()
        close_button.focus_set()
        dialog.wait_window()

    def _show_info(self, title: str, message: str) -> None:
        self._show_message(title, message, "info")

    def _show_warning(self, title: str, message: str) -> None:
        self._show_message(title, message, "warning")

    def _show_error(self, title: str, message: str) -> None:
        self._show_message(title, message, "error")

    def _set_language(self, locale: str) -> None:
        if locale not in self.locales:
            raise ValueError(f"Unsupported language: {locale}")
        translations = load_locale(locale)
        self.storage.set_setting("language", locale)
        previous_media_type = self._media_kind_value(self.media_type_var.get())
        previous_engine = self._engine_value(self.engine_var.get())
        previous_format = self._output_format_value(self.output_format_var.get())
        previous_all_hosts = self._t("grabber.all_hosts")
        self.locale = locale
        self.translations = translations
        self.language_var.set(locale)
        self.engine_var.set(self._engine_label(previous_engine))
        self.output_format_var.set(self._output_format_label(previous_format))
        if previous_media_type is None:
            self.media_type_var.set(self._t("grabber.all_types"))
        else:
            self.media_type_var.set(self._media_kind_label(previous_media_type))
        if self.media_host_var.get() == previous_all_hosts:
            self.media_host_var.set(self._t("grabber.all_hosts"))
        for update in self._localized_updates:
            update()
        for record in self.records.values():
            self._upsert_row(record)
        self._refresh_status()
        self._show_selected_progress()

    def _build_menu(self) -> None:
        self._register_localized(lambda: self.root.title(self._t("app.title")))
        menu = tk.Menu(self.root, tearoff=False)
        menu.configure(
            background=COLORS["header"],
            foreground=COLORS["text"],
            activebackground=COLORS["selection"],
            activeforeground=COLORS["white"],
            borderwidth=0,
        )
        file_menu = tk.Menu(
            menu, tearoff=False, background=COLORS["panel"],
            activebackground=COLORS["selection"], activeforeground=COLORS["white"],
        )
        self.file_menu = file_menu
        file_menu.add_command(label="Add links...", command=self._focus_grabber)
        file_menu.add_command(
            label="Add links from clipboard", command=self._add_clipboard_links
        )
        file_menu.add_separator()
        file_menu.add_command(label="Open download folder", command=self._open_directory)
        file_menu.add_command(label="Open data folder", command=self._open_data_directory)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._close, accelerator="Ctrl+Q")
        for index, key in enumerate((
            "menu.add_links",
            "toolbar.add_clipboard",
            None,
            "menu.open_download_folder",
            "menu.open_data_folder",
            None,
            "menu.exit",
        )):
            if key:
                self._localize_menu_entry(file_menu, index, key)
        settings = tk.Menu(
            menu, tearoff=False, background=COLORS["panel"],
            activebackground=COLORS["selection"], activeforeground=COLORS["white"],
        )
        self.settings_menu = settings
        settings.add_command(label="Download folder...", command=self._choose_directory)
        self._localize_menu_entry(settings, 0, "settings.download_folder")
        simultaneous_menu = tk.Menu(
            settings, tearoff=False, background=COLORS["panel"],
            activebackground=COLORS["selection"], activeforeground=COLORS["white"],
        )
        self.menu_simultaneous_var = tk.IntVar(self.root, value=self.simultaneous)
        for count in range(1, 9):
            simultaneous_menu.add_radiobutton(
                label=str(count),
                value=count,
                variable=self.menu_simultaneous_var,
                command=lambda selected=count: self._set_simultaneous_count(selected),
            )
        settings.add_cascade(label="Simultaneous downloads", menu=simultaneous_menu)
        self._localize_menu_entry(settings, 1, "settings.simultaneous_downloads")
        format_menu = tk.Menu(
            settings, tearoff=False, background=COLORS["panel"],
            activebackground=COLORS["selection"], activeforeground=COLORS["white"],
        )
        self.format_menu = format_menu
        self.menu_format_var = tk.StringVar(self.root, value=self.output_format)
        for output_format, label in (
            ("original", "Original"),
            ("mp3", "MP3"),
            ("mp4", "MP4"),
            ("webm", "WebM"),
        ):
            format_menu.add_radiobutton(
                label=label,
                value=output_format,
                variable=self.menu_format_var,
                command=lambda selected=output_format: self._set_default_format(selected),
            )
        self._localize_menu_entry(format_menu, 0, "format.original")
        settings.add_cascade(label="Default format", menu=format_menu)
        self._localize_menu_entry(settings, 2, "settings.default_format")
        language_menu = tk.Menu(
            settings, tearoff=False, background=COLORS["panel"],
            activebackground=COLORS["selection"], activeforeground=COLORS["white"],
        )
        self.language_var = tk.StringVar(self.root, value=self.locale)
        for locale, language in self.locales.items():
            language_menu.add_radiobutton(
                label=language,
                value=locale,
                variable=self.language_var,
                command=lambda selected=locale: self._set_language(selected),
            )
        settings.add_cascade(label="Language", menu=language_menu)
        self.language_menu = language_menu
        language_entry = settings.index("end")
        self._register_localized(
            lambda: settings.entryconfigure(
                language_entry, label=self._t("settings.language")
            )
        )
        tools = tk.Menu(
            menu, tearoff=False, background=COLORS["panel"],
            activebackground=COLORS["selection"], activeforeground=COLORS["white"],
        )
        self.tools_menu = tools
        tools.add_command(label="Start / Resume", command=self._resume_selected)
        tools.add_command(label="Pause", command=self._pause_selected)
        tools.add_command(label="Stop", command=self._cancel_selected)
        tools.add_separator()
        tools.add_command(label="Add links from clipboard", command=self._add_clipboard_links)
        tools.add_command(label="Copy selected URLs", command=self._copy_selected_links)
        for index, key in enumerate((
            "menu.start_resume",
            "toolbar.pause",
            "toolbar.stop",
            None,
            "toolbar.add_clipboard",
            "menu.copy_selected_urls",
        )):
            if key:
                self._localize_menu_entry(tools, index, key)
        help_menu = tk.Menu(
            menu, tearoff=False, background=COLORS["panel"],
            activebackground=COLORS["selection"], activeforeground=COLORS["white"],
        )
        self.help_menu = help_menu
        help_menu.add_command(label="About", command=self._about)
        help_menu.add_command(label="History", command=lambda: self._select_tab("history"))
        self._localize_menu_entry(help_menu, 0, "menu.about")
        self._localize_menu_entry(help_menu, 1, "tabs.history")
        menu.add_cascade(label="File", menu=file_menu)
        menu.add_cascade(label="Settings", menu=settings)
        menu.add_cascade(label="Tools", menu=tools)
        menu.add_cascade(label="Help", menu=help_menu)
        for index, key in enumerate(
            ("menu.file", "menu.settings", "menu.tools", "menu.help")
        ):
            self._localize_menu_entry(menu, index, key)
        self.root.config(menu=menu)

    def _build_layout(self) -> None:
        toolbar = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(4, 3))
        toolbar.pack(fill="x")
        action_groups = (
            (("add", "Add Links", "toolbar.add_links", self._focus_grabber),),
            (
                ("start", "Start", "toolbar.start", self._resume_selected),
                ("pause", "Pause", "toolbar.pause", self._pause_selected),
                ("stop", "Stop", "toolbar.stop", self._cancel_selected),
            ),
            (
                ("move_up", "Move Up", "toolbar.move_up", self._raise_selected),
                ("move_down", "Move Down", "toolbar.move_down", self._lower_selected),
            ),
            (("retry", "Retry", "toolbar.retry", self._retry_selected),),
            (
                (
                    "clipboard",
                    "Add from Clipboard",
                    "toolbar.add_clipboard",
                    self._add_clipboard_links,
                ),
            ),
        )
        for group_index, groups in enumerate(action_groups):
            if group_index:
                ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=4)
            for icon, label, translation_key, command in groups:
                self._add_toolbar_action(
                    toolbar, icon, label, command, translation_key
                )

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=2, pady=(0, 1))
        self.downloads_tab = ttk.Frame(self.tabs)
        self.grabber_tab = ttk.Frame(self.tabs)
        self.contribute_tab = ttk.Frame(self.tabs)
        self.history_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.downloads_tab, text="Downloads")
        self.tabs.add(self.grabber_tab, text="Link Grabber")
        self.tabs.add(self.contribute_tab, text="Contribute")
        self.tabs.add(
            self.history_tab,
            text=self._t("tabs.history_count", count=0),
        )
        self._localize_tab(self.downloads_tab, "tabs.downloads")
        self._localize_tab(self.grabber_tab, "tabs.link_grabber")
        self._localize_tab(self.contribute_tab, "tabs.contribute")
        self._register_localized(
            lambda: self.tabs.tab(
                self.history_tab,
                text=self._t(
                    "tabs.history_count", count=len(self.history_rows)
                ),
            )
        )
        self.tabs.hide(self.history_tab)
        self._build_contribute_page()
        self._build_grabber_page()
        self.download_tree = self._build_tree(self.downloads_tab, "downloads")
        self.history_tree = self._build_tree(self.history_tab, "history")
        self.download_tree.bind("<<TreeviewSelect>>", self._show_selected_progress)
        self.history_tree.bind("<<TreeviewSelect>>", self._show_selected_progress)
        self.download_tree.bind("<Button-3>", self._show_download_context_menu)
        self.history_tree.bind("<Button-3>", self._show_download_context_menu)
        self.download_tree.bind("<Button-1>", self._clear_context_tree, add="+")
        self.history_tree.bind("<Button-1>", self._clear_context_tree, add="+")
        self.tabs.bind("<<NotebookTabChanged>>", self._clear_context_tree)
        self.context_menu = tk.Menu(
            self.root,
            tearoff=False,
            background=COLORS["panel"],
            foreground=COLORS["text"],
            activebackground=COLORS["selection"],
            activeforeground=COLORS["white"],
        )
        context_labels = (
            "menu.start_resume",
            "toolbar.pause",
            "toolbar.stop",
            None,
            "context.open_download_folder",
            "context.open_url",
            "context.copy_url",
            None,
            "context.rename",
            "context.remove",
            "context.delete_downloaded_file",
        )
        self.context_menu.add_command(label="Start / Resume", command=self._resume_selected)
        self.context_menu.add_command(label="Pause", command=self._pause_selected)
        self.context_menu.add_command(label="Stop", command=self._cancel_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Open download folder", command=self._open_selected_folder
        )
        self.context_menu.add_command(label="Open URL", command=self._open_selected_links)
        self.context_menu.add_command(label="Copy URL", command=self._copy_selected_links)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Rename...", command=self._rename_selected)
        self.context_menu.add_command(label="Remove from list", command=self._remove_selected)
        self.context_menu.add_command(
            label="Delete downloaded file...", command=self._delete_selected_file
        )
        for index, key in enumerate(context_labels):
            if key:
                self._localize_menu_entry(self.context_menu, index, key)

        self._build_information_panel()
        self._build_quick_add()
        self.status_var = tk.StringVar()
        ttk.Label(
            self.root, textvariable=self.status_var, relief="sunken", anchor="w", padding=(4, 1)
        ).pack(side="bottom", fill="x")

    def _create_empty_state(
        self,
        parent: ttk.Frame,
        key: str,
        title_key: str,
        detail_key: str,
    ) -> None:
        icon = tk.Canvas(
            parent,
            width=72,
            height=72,
            background=COLORS["list"],
            highlightthickness=0,
            takefocus=False,
        )
        icon.configure(width=72, height=72)
        ink = "#292929"
        icon.create_rectangle(
            22, 8, 50, 42, fill="#ffffff", outline=ink, width=2
        )
        icon.create_line(41, 8, 50, 17, fill=ink, width=2)
        icon.create_line(41, 8, 41, 17, fill=ink, width=2)
        icon.create_line(41, 17, 50, 17, fill=ink, width=2)
        icon.create_line(36, 20, 36, 34, fill=ink, width=3)
        icon.create_line(30, 29, 36, 35, 42, 29, fill=ink, width=3)
        icon.create_line(16, 46, 16, 56, 56, 56, 56, 46, fill=ink, width=3)
        icon.create_line(
            16, 46, 28, 46, 32, 51, 40, 51, 44, 46, 56, 46,
            fill=ink, width=3,
        )
        icon.place(relx=0.5, rely=0.38, anchor="center")

        title = ttk.Label(parent, text="", style="Panel.TLabel", anchor="center")
        self._localize_widget(title, title_key)
        title.configure(font=("Tahoma", 10, "bold"))
        title.place(relx=0.5, rely=0.53, anchor="center")

        detail = ttk.Label(
            parent,
            text="",
            style="Panel.TLabel",
            anchor="center",
            justify="center",
            wraplength=460,
        )
        self._localize_widget(detail, detail_key)
        detail.place(relx=0.5, rely=0.60, anchor="center")

        self.empty_icons[key] = icon
        self.empty_labels[key] = title
        self.empty_details[key] = detail
        self.empty_states_visible[key] = True

    def _set_empty_state_visible(self, key: str, visible: bool) -> None:
        if self.empty_states_visible.get(key) == visible:
            return
        if visible:
            self.empty_icons[key].place(
                relx=0.5, rely=0.38, anchor="center"
            )
            self.empty_labels[key].place(
                relx=0.5, rely=0.53, anchor="center"
            )
            self.empty_details[key].place(
                relx=0.5, rely=0.60, anchor="center"
            )
        else:
            for widget in (
                self.empty_icons[key],
                self.empty_labels[key],
                self.empty_details[key],
            ):
                widget.place_forget()
        self.empty_states_visible[key] = visible

    def _build_contribute_page(self) -> None:
        page = ttk.Frame(self.contribute_tab, padding=(28, 22))
        page.pack(fill="both", expand=True)
        heading = ttk.Label(page, text="", font=("Tahoma", 16, "bold"))
        self.contribute_heading = heading
        self._localize_widget(heading, "contribute.title")
        heading.pack(anchor="w", pady=(0, 4))
        intro = ttk.Label(
            page,
            text="",
            style="Panel.TLabel",
            wraplength=760,
            justify="left",
        )
        self.contribute_intro = intro
        self._localize_widget(intro, "contribute.intro")
        intro.pack(anchor="w", pady=(0, 18))

        cards = ttk.Frame(page)
        cards.pack(fill="both", expand=True)
        card_content = (
            ("contribute.test_title", "contribute.test_body"),
            ("contribute.feedback_title", "contribute.feedback_body"),
            ("contribute.translate_title", "contribute.translate_body"),
        )
        for index, (title_key, body_key) in enumerate(card_content):
            card = ttk.LabelFrame(cards, text="", padding=(14, 12))
            self._localize_widget(card, title_key)
            row, column = divmod(index, 2)
            card.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)
            description = ttk.Label(
                card,
                text="",
                style="Panel.TLabel",
                wraplength=340,
                justify="left",
            )
            self._localize_widget(description, body_key)
            description.pack(fill="x")
            cards.columnconfigure(column, weight=1)
            cards.rowconfigure(row, weight=1)
            if title_key == "contribute.translate_title":
                open_folder = ttk.Button(
                    card,
                    text="",
                    command=self._open_locale_directory,
                )
                self._localize_widget(open_folder, "contribute.open_locales")
                open_folder.pack(anchor="w", pady=(12, 0))

        template_button = ttk.Button(
            page,
            text="",
            command=self._copy_contribution_template,
        )
        self._localize_widget(template_button, "contribute.copy_template")
        template_button.pack(anchor="e", padx=6, pady=(10, 0))

    def _open_locale_directory(self) -> None:
        try:
            os.startfile(str(Path(__file__).parent / "locales"))
        except OSError as exc:
            self._show_error(self._t("messages.folder_error"), str(exc))

    def _copy_contribution_template(self) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._t("contribute.report_template"))
            self.root.update_idletasks()
        except tk.TclError as exc:
            self._show_error(self._t("messages.clipboard_error"), str(exc))
            return
        self.status_var.set(self._t("contribute.template_copied"))

    def _add_toolbar_action(
        self,
        parent: ttk.Frame,
        icon_name: str,
        label: str,
        command: Callable[[], None],
        translation_key: str | None = None,
    ) -> None:
        action = ttk.Frame(parent, style="ToolbarAction.TFrame", padding=(5, 3))
        action.pack(side="left", padx=1, pady=1)
        canvas = tk.Canvas(
            action,
            width=34,
            height=34,
            highlightthickness=0,
            background=COLORS["window"],
            takefocus=False,
        )
        canvas.pack(side="left", padx=(0, 4))
        canvas.create_image(17, 17, image=self.toolbar_icons[icon_name])
        caption = ttk.Label(action, text=label, style="ToolbarAction.TLabel")
        caption.pack(side="left")
        if translation_key:
            self._localize_widget(caption, translation_key)

        def invoke(_event: tk.Event | None = None) -> str:
            command()
            return "break"

        def set_hover(active: bool) -> None:
            action.configure(
                style="ToolbarActionHover.TFrame" if active else "ToolbarAction.TFrame"
            )
            canvas.configure(
                background=COLORS["yellow_light"] if active else COLORS["window"]
            )
            caption.configure(
                style="ToolbarActionHover.TLabel" if active else "ToolbarAction.TLabel"
            )

        for widget in (action, canvas, caption):
            widget.bind("<Button-1>", invoke)
            widget.bind("<Enter>", lambda _event: set_hover(True))
            widget.bind("<Leave>", lambda _event: set_hover(False))
        action.bind("<Return>", invoke)
        action.bind("<space>", invoke)
        action.configure(takefocus=True, cursor="hand2")

    def _load_toolbar_icons(self) -> dict[str, tk.PhotoImage]:
        assets = Path(__file__).parent / "assets" / "aero"
        icon_files = {
            "add": "list-add-user.png",
            "start": "media-playback-start.png",
            "pause": "media-playback-pause.png",
            "stop": "media-playback-stop.png",
            "move_up": "go-up.png",
            "move_down": "go-down.png",
            "retry": "system-reboot.png",
            "clipboard": "list-add-user.png",
        }
        return {
            name: tk.PhotoImage(file=str(assets / filename)).subsample(5, 5)
            for name, filename in icon_files.items()
        }

    def _build_grabber_page(self) -> None:
        body = ttk.Frame(self.grabber_tab)
        body.pack(fill="both", expand=True)
        sidebar = ttk.Frame(body, width=245, relief="solid", borderwidth=1)
        sidebar.pack(side="right", fill="y")
        sidebar.pack_propagate(False)
        views_label = ttk.Label(
            sidebar, text="Views", anchor="w", padding=(8, 5),
            background=COLORS["header"], relief="raised",
        )
        self._localize_widget(views_label, "grabber.views")
        views_label.pack(fill="x")
        search_row = ttk.Frame(sidebar, padding=(6, 5))
        search_row.pack(fill="x")
        search_label = ttk.Label(search_row, text="")
        self._localize_widget(search_label, "grabber.search")
        search_label.pack(anchor="w")
        self.media_search_var = tk.StringVar()
        ttk.Entry(search_row, textvariable=self.media_search_var).pack(fill="x", pady=(3, 0))
        self.media_search_var.trace_add("write", self._apply_media_filters)

        self.selected_only_var = tk.BooleanVar(value=False)
        selected_only_check = ttk.Checkbutton(
            sidebar,
            text="",
            variable=self.selected_only_var,
            command=self._apply_media_filters,
        )
        self._localize_widget(selected_only_check, "grabber.show_selected")
        selected_only_check.pack(anchor="w", padx=6, pady=5)
        self.media_type_var = tk.StringVar(value=self._t("grabber.all_types"))
        self.media_host_var = tk.StringVar(value=self._t("grabber.all_hosts"))
        for label, variable, values in (
            (
                "File type",
                self.media_type_var,
                (
                    self._t("grabber.all_types"),
                    self._t("media.kind.video"),
                    self._t("media.kind.audio"),
                    self._t("media.kind.stream"),
                ),
            ),
            ("Host", self.media_host_var, (self._t("grabber.all_hosts"),)),
        ):
            row = ttk.Frame(sidebar, padding=(6, 5))
            row.pack(fill="x")
            label_widget = ttk.Label(row, text="")
            self._localize_widget(
                label_widget,
                "grabber.file_type" if label == "File type" else "grabber.host",
            )
            label_widget.pack(anchor="w")
            combo = ttk.Combobox(
                row, textvariable=variable, values=values, state="readonly"
            )
            combo.pack(fill="x", pady=(3, 0))
            combo.bind("<<ComboboxSelected>>", self._apply_media_filters)
            if label == "Host":
                self.media_host_combo = combo
            else:
                self.media_type_combo = combo
        self._register_localized(self._update_media_filter_options)
        select_visible_button = ttk.Button(
            sidebar, text="", command=self._select_visible_media
        )
        self._localize_widget(select_visible_button, "grabber.select_visible")
        select_visible_button.pack(fill="x", padx=6, pady=(8, 3))
        clear_selection_button = ttk.Button(
            sidebar, text="", command=self._clear_media_selection
        )
        self._localize_widget(clear_selection_button, "grabber.clear_selection")
        clear_selection_button.pack(fill="x", padx=6, pady=3)

        media_actions = ttk.Frame(body, padding=(4, 3))
        media_actions.pack(fill="x")
        self.media_selection_var = tk.StringVar(value="0 selected")
        ttk.Label(
            media_actions,
            textvariable=self.media_selection_var,
            style="Panel.TLabel",
        ).pack(side="left", padx=(2, 8))
        self.queue_media_button = ttk.Button(
            media_actions,
            text="",
            command=self._queue_selected_media,
            state="disabled",
        )
        self._localize_widget(self.queue_media_button, "media.download_selected")
        self.queue_media_button.pack(side="left", padx=2)
        self.open_media_button = ttk.Button(
            media_actions,
            text="",
            command=self._open_selected_media,
            state="disabled",
        )
        self._localize_widget(self.open_media_button, "media.open_link")
        self.open_media_button.pack(side="left", padx=2)
        self.copy_media_button = ttk.Button(
            media_actions,
            text="",
            command=self._copy_selected_media,
            state="disabled",
        )
        self._localize_widget(self.copy_media_button, "media.copy_link")
        self.copy_media_button.pack(side="left", padx=2)

        self.grabber_tree = self._build_capture_tree(body)
        self._register_localized(self._update_media_selection_actions)
        self.media_context_menu = tk.Menu(
            self.root,
            tearoff=False,
            background=COLORS["panel"],
            foreground=COLORS["text"],
            activebackground=COLORS["selection"],
            activeforeground=COLORS["white"],
        )
        for key, label, command in (
            ("media.download_selected", "Download selected", self._queue_selected_media),
            ("media.open_link", "Open direct link", self._open_selected_media),
            ("media.copy_link", "Copy direct link", self._copy_selected_media),
        ):
            self.media_context_menu.add_command(label=label, command=command)
            index = self.media_context_menu.index("end")
            self._localize_menu_entry(self.media_context_menu, index, key)
        input_frame = ttk.Frame(self.grabber_tab)
        self.input_frame = input_frame
        self.link_text = tk.Text(input_frame, height=14, wrap="word", undo=True)
        paste_hint = ttk.Label(
            input_frame,
            text="",
        )
        self._localize_widget(paste_hint, "grabber.paste_hint")
        paste_hint.pack(anchor="w", pady=(0, 5))
        self.link_text.pack(fill="both", expand=True)
        options = ttk.Frame(input_frame)
        options.pack(fill="x", pady=(7, 0))
        type_label = ttk.Label(options, text="")
        self._localize_widget(type_label, "grabber.file_type")
        type_label.pack(side="left")
        self.engine_var = tk.StringVar(value=self._engine_label("auto"))
        self.engine_combo = ttk.Combobox(
            options,
            textvariable=self.engine_var,
            values=(),
            state="readonly",
            width=9,
        )
        self.engine_combo.pack(side="left", padx=5)
        format_label = ttk.Label(options, text="")
        self._localize_widget(format_label, "media.format")
        format_label.pack(side="left", padx=(8, 2))
        self.output_format_var = tk.StringVar(
            value=self._output_format_label(self.output_format)
        )
        self.output_format_combo = ttk.Combobox(
            options,
            textvariable=self.output_format_var,
            values=(),
            state="readonly",
            width=9,
        )
        self.output_format_combo.pack(side="left", padx=3)
        self._register_localized(self._update_language_dependent_options)
        self.scan_media_button = ttk.Button(
            options, text="", command=self._scan_media_pages
        )
        self._localize_widget(self.scan_media_button, "grabber.find_media")
        self.scan_media_button.pack(side="right", padx=(5, 0))
        add_selected_button = ttk.Button(
            options, text="", command=self._queue_selected_media
        )
        self._localize_widget(add_selected_button, "grabber.add_selected")
        add_selected_button.pack(side="right", padx=(5, 0))
        add_links_button = ttk.Button(
            options, text="", command=self._add_links
        )
        self._localize_widget(add_links_button, "grabber.add_links")
        add_links_button.pack(side="right")
        self.media_scan_status = tk.StringVar()
        ttk.Label(
            input_frame, textvariable=self.media_scan_status, style="Panel.TLabel"
        ).pack(anchor="w", pady=(4, 0))
        self.media_scan_progress = ttk.Progressbar(
            input_frame, mode="determinate", maximum=1
        )
        self.media_scan_progress.pack(fill="x", pady=(3, 0))
        self._apply_media_filters()

    def _build_capture_tree(self, parent: ttk.Frame) -> ttk.Treeview:
        capture_area = ttk.Frame(parent)
        capture_area.pack(fill="both", expand=True)
        columns = ("name", "type", "format", "host", "url", "page")
        heading_keys = (
            "media.name",
            "media.type",
            "media.format",
            "media.host",
            "media.direct_url",
            "media.page",
        )
        tree = ttk.Treeview(
            capture_area, columns=columns, show="headings", selectmode="extended"
        )
        for index, (column, heading, width) in enumerate((
            ("name", "Name", 125),
            ("type", "Type", 55),
            ("format", "Format", 55),
            ("host", "Host", 95),
            ("url", "Direct media URL", 250),
            ("page", "Page", 110),
        )):
            tree.heading(column, text=heading)
            self._register_localized(
                lambda col=column, key=heading_keys[index]: tree.heading(
                    col, text=self._t(key)
                )
            )
            tree.column(column, width=width, minwidth=55, anchor="w", stretch=True)
        tree.bind("<<TreeviewSelect>>", self._on_media_selection_changed)
        tree.bind("<Double-1>", self._queue_media_on_double_click)
        tree.bind("<Button-3>", self._show_media_context_menu)
        vertical = ttk.Scrollbar(capture_area, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(capture_area, orient="horizontal", command=tree.xview)
        tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )
        horizontal.pack(side="bottom", fill="x")
        vertical.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        self._create_empty_state(
            capture_area,
            "grabber",
            "empty.grabber.title",
            "empty.grabber.detail",
        )
        self.grabber_x_scrollbar = horizontal
        return tree

    def _build_information_panel(self) -> None:
        panel = ttk.LabelFrame(
            self.root, text="Download information", padding=(5, 2)
        )
        self._localize_widget(panel, "details.panel")
        panel.pack(fill="x", padx=2, pady=(0, 1))

        self.detail_text = tk.StringVar()
        ttk.Label(
            panel,
            textvariable=self.detail_text,
            style="Panel.TLabel",
            anchor="w",
        ).pack(fill="x", pady=(0, 2))
        self.detail_progress = ttk.Progressbar(
            panel,
            maximum=100,
            style="Horizontal.TProgressbar",
            mode="determinate",
        )
        self.detail_progress.pack(fill="x", pady=(0, 3))

        stats = ttk.Frame(panel)
        stats.pack(fill="x")
        self.stats_vars = {
            key: tk.StringVar()
            for key in (
                "links", "total", "downloaded", "remaining", "speed", "eta",
                "active", "connections", "online", "offline", "download_count",
            )
        }
        rows = (
            ("links", "total", "downloaded", "eta", "active"),
            ("connections", "speed", "remaining", "download_count", "online", "offline"),
        )
        for row_index, keys in enumerate(rows):
            for column_index, key in enumerate(keys):
                ttk.Label(
                    stats, textvariable=self.stats_vars[key], style="Panel.TLabel"
                ).grid(row=row_index, column=column_index, sticky="w", padx=(0, 12), pady=1)

    def _build_quick_add(self) -> None:
        quick_add = ttk.Frame(self.root, padding=(3, 2))
        quick_add.pack(fill="x", padx=1, pady=(0, 1))
        self._add_toolbar_action(
            quick_add, "add", "Add new links...", self._focus_grabber,
            "quick.add_links",
        )
        self._add_toolbar_action(
            quick_add, "clipboard", "Clipboard", self._add_clipboard_links,
            "quick.clipboard",
        )
        self._add_toolbar_action(
            quick_add, "retry", "Retry", self._retry_selected, "toolbar.retry"
        )
        sort_combo = ttk.Combobox(
            quick_add, values=("Filename", "Host", "Status"),
            state="readonly", width=13,
        )
        self._register_localized(
            lambda: sort_combo.configure(values=tuple(
                self._t(key) for key in (
                    "quick.sort_filename",
                    "grabber.host",
                    "download.column.status",
                )
            ))
        )
        sort_combo.pack(side="left", padx=(1, 3))
        self.quick_link_var = tk.StringVar()
        paste_url_label = ttk.Label(quick_add, text="Paste URL:")
        self._localize_widget(paste_url_label, "quick.paste_url")
        paste_url_label.pack(side="left", padx=(2, 4))
        self.quick_link_entry = ttk.Entry(
            quick_add,
            textvariable=self.quick_link_var,
        )
        self.quick_link_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.quick_link_entry.bind("<Return>", self._add_quick_link)
        start_all_button = ttk.Button(
            quick_add, text="Start all downloads", command=self._resume_all
        )
        self._localize_widget(start_all_button, "quick.start_all")
        start_all_button.pack(side="left")

    def _build_tree(self, parent: ttk.Frame, mode: str) -> ttk.Treeview:
        columns = (
            "name", "size", "host", "connection", "status", "speed",
            "eta", "downloaded", "progress", "save_to", "comment",
        )
        body = ttk.Frame(parent)
        body.pack(fill="both", expand=True)
        tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="extended")
        heading_keys = {
            "name": "download.column.name",
            "size": "download.column.size",
            "host": "grabber.host",
            "connection": "download.column.connection",
            "status": "download.column.status",
            "speed": "download.column.speed",
            "eta": "download.column.eta",
            "downloaded": "download.column.downloaded",
            "progress": "download.column.progress",
            "save_to": "download.column.save_to",
            "comment": "download.column.comments",
        }
        widths = {
            "name": 170, "size": 75, "host": 85, "connection": 95, "status": 75,
            "speed": 85, "eta": 65, "downloaded": 80, "progress": 85,
            "save_to": 105, "comment": 75,
        }
        for column in columns:
            key = heading_keys[column]
            tree.heading(column, text=self._t(key))
            self._register_localized(
                lambda col=column, translation_key=key: tree.heading(
                    col, text=self._t(translation_key)
                )
            )
            tree.column(
                column,
                width=widths[column],
                minwidth=45,
                anchor="w",
                stretch=True,
            )
        tree.column("progress", anchor="center")
        vertical = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.tag_configure("queued", foreground=COLORS["muted"])
        tree.tag_configure("downloading", foreground=COLORS["blue_dark"])
        tree.tag_configure("completed", foreground=COLORS["green"])
        tree.tag_configure("paused", foreground=COLORS["orange"])
        tree.tag_configure("error", foreground=COLORS["red"])
        tree.tag_configure("cancelled", foreground=COLORS["muted"])
        tree.pack(side="left", fill="both", expand=True)
        vertical.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        key = "downloads" if mode == "downloads" else "history"
        self._create_empty_state(
            body,
            key,
            f"empty.{key}.title",
            f"empty.{key}.detail",
        )
        return tree

    def _load_existing(self) -> None:
        for record in self.storage.get_downloads():
            self.records[record.id] = record
            self.manager.add(record)
            self._upsert_row(record)
        for record in self.storage.get_downloads(history=True):
            self.records[record.id] = record
            if record.status in {"cancelled", "error"}:
                self.manager.add(record)
            self._upsert_row(record)

    def _add_links(self) -> None:
        urls = parse_urls(self.link_text.get("1.0", "end"))
        if not urls:
            self._show_warning(
                self._t("messages.no_links_title"),
                self._t("messages.no_links_text"),
            )
            return
        self._queue_links(urls)
        self.link_text.delete("1.0", "end")
        self.input_frame.pack_forget()
        self.tabs.select(self.downloads_tab)

    def _scan_media_pages(self) -> None:
        page_urls = parse_urls(self.link_text.get("1.0", "end"))
        if not page_urls:
            self._show_warning(
                self._t("scan.no_page_urls_title"),
                self._t("scan.no_urls"),
            )
            return
        self.scan_media_button.configure(state="disabled")
        self._media_scan_total = len(page_urls)
        self.media_scan_progress.configure(maximum=len(page_urls), value=0)
        scan_errors: list[ScanProgress] = []

        def report_progress(progress: ScanProgress) -> None:
            if progress.error:
                scan_errors.append(progress)
            if self._closing:
                return
            try:
                self.root.after(
                    0,
                    self._update_media_scan_progress,
                    progress,
                    len(scan_errors),
                )
            except tk.TclError:
                pass

        def scan() -> None:
            candidates = scan_media_pages(page_urls, report_progress)
            if self._closing:
                return
            try:
                self.root.after(
                    0, self._show_media_candidates, candidates, scan_errors.copy()
                )
            except tk.TclError:
                pass

        threading.Thread(target=scan, name="media-link-scan", daemon=True).start()

    def _update_media_scan_progress(
        self, progress: ScanProgress, error_count: int
    ) -> None:
        self.media_scan_progress.configure(
            maximum=max(progress.total, 1), value=progress.completed
        )
        page = urlsplit(progress.page_url).hostname or progress.page_url
        if progress.page_done:
            status = self._t(
                "scan.progress",
                completed=progress.completed,
                total=progress.total,
                found=progress.found,
                errors=error_count,
            )
        else:
            status = self._t(
                "scan.scanning_page",
                page=page,
                completed=progress.completed + 1,
                total=progress.total,
            )
        self.media_scan_status.set(status)

    def _show_media_candidates(
        self, candidates: list[MediaLink], errors: list[ScanProgress]
    ) -> None:
        self.scan_media_button.configure(state="normal")
        self.media_candidates.clear()
        self.selected_media_ids.clear()
        for index, candidate in enumerate(candidates):
            item_id = str(index)
            self.media_candidates[item_id] = candidate
            self.selected_media_ids.add(item_id)
        hosts = sorted({
            (urlsplit(candidate.url).hostname or "").lower()
            for candidate in candidates
        })
        self.media_hosts = hosts
        host_values = (self._t("grabber.all_hosts"), *hosts)
        self.media_host_combo.configure(values=host_values)
        if self.media_host_var.get() not in host_values:
            self.media_host_var.set(self._t("grabber.all_hosts"))
        self._apply_media_filters()
        self.media_scan_progress.configure(
            maximum=max(self._media_scan_total, 1),
            value=self._media_scan_total,
        )
        if errors:
            self.media_scan_status.set(self._t("scan.errors", count=len(errors)))
            details = "\n".join(
                f"{urlsplit(item.page_url).hostname or item.page_url}: {item.error}"
                for item in errors
            )
            self._show_warning(self._t("scan.errors_title"), details)
        elif candidates:
            self.media_scan_status.set(
                self._t("scan.result", count=len(candidates))
            )
        else:
            self.media_scan_status.set(self._t("scan.no_media"))

    def _update_media_filter_options(self) -> None:
        self.media_type_combo.configure(
            values=(
                self._t("grabber.all_types"),
                self._t("media.kind.video"),
                self._t("media.kind.audio"),
                self._t("media.kind.stream"),
            )
        )
        self.media_host_combo.configure(
            values=(self._t("grabber.all_hosts"), *self.media_hosts)
        )

    def _media_kind_label(self, kind: str) -> str:
        translation_keys = {
            "Video": "media.kind.video",
            "Audio": "media.kind.audio",
            "Stream": "media.kind.stream",
        }
        key = translation_keys.get(kind)
        return self._t(key) if key else kind

    def _media_kind_value(self, label: str) -> str | None:
        if label == self._t("grabber.all_types"):
            return None
        for kind in ("Video", "Audio", "Stream"):
            if label == self._media_kind_label(kind):
                return kind
        return label

    def _engine_label(self, engine: str) -> str:
        if engine in {"auto", "http", "video"}:
            return self._t(f"engine.{engine}")
        return engine

    def _engine_value(self, label: str) -> str:
        for engine in ("auto", "http", "video"):
            if label == self._engine_label(engine):
                return engine
        return label

    def _output_format_label(self, output_format: str) -> str:
        if output_format in {"original", "mp3", "mp4", "webm"}:
            return self._t(f"format.{output_format}")
        return output_format

    def _output_format_value(self, label: str) -> str:
        for output_format in ("original", "mp3", "mp4", "webm"):
            if label == self._output_format_label(output_format):
                return output_format
        return label

    def _update_language_dependent_options(self) -> None:
        self.engine_combo.configure(
            values=tuple(self._engine_label(engine) for engine in ("auto", "http", "video"))
        )
        self.output_format_combo.configure(
            values=tuple(
                self._output_format_label(output_format)
                for output_format in ("original", "mp3", "mp4", "webm")
            )
        )
        for index, output_format in enumerate(("original", "mp3", "mp4", "webm")):
            self.format_menu.entryconfigure(
                index, label=self._output_format_label(output_format)
            )

    def _apply_media_filters(self, *_args: object) -> None:
        if not hasattr(self, "grabber_tree"):
            return
        search = self.media_search_var.get().strip().casefold()
        selected_type = self._media_kind_value(self.media_type_var.get())
        selected_host = self.media_host_var.get()
        selected_only = self.selected_only_var.get()
        self.grabber_tree.delete(*self.grabber_tree.get_children())
        self.visible_media_ids.clear()
        for item_id, candidate in self.media_candidates.items():
            host = (urlsplit(candidate.url).hostname or "").lower()
            if (
                selected_type is not None
                and candidate.kind != selected_type
            ):
                continue
            if (
                selected_host != self._t("grabber.all_hosts")
                and host != selected_host
            ):
                continue
            if search and search not in candidate.name.casefold() and search not in host:
                continue
            if selected_only and item_id not in self.selected_media_ids:
                continue
            self.visible_media_ids.add(item_id)
            self.grabber_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    candidate.name,
                    self._media_kind_label(candidate.kind),
                    candidate.format,
                    host,
                    candidate.url,
                    urlsplit(candidate.page_url).hostname or "",
                ),
            )
            if item_id in self.selected_media_ids:
                self.grabber_tree.selection_add(item_id)
        self._update_media_selection_actions()

    def _on_media_selection_changed(self, _event: object = None) -> None:
        selected = set(self.grabber_tree.selection())
        for item_id in self.visible_media_ids:
            if item_id in selected:
                self.selected_media_ids.add(item_id)
            else:
                self.selected_media_ids.discard(item_id)
        if self.selected_only_var.get():
            self._apply_media_filters()
        else:
            self._update_media_selection_actions()

    def _update_media_selection_actions(self) -> None:
        count = len(self.selected_media_ids)
        self.media_selection_var.set(
            self._t(
                "grabber.selected_count",
                selected=count,
                total=len(self.media_candidates),
            )
        )
        state = "normal" if count else "disabled"
        self.queue_media_button.configure(state=state)
        self.open_media_button.configure(state=state)
        self.copy_media_button.configure(state=state)
        self._set_empty_state_visible(
            "grabber", not bool(self.grabber_tree.get_children())
        )

    def _select_visible_media(self) -> None:
        self.selected_media_ids.update(self.visible_media_ids)
        self._apply_media_filters()

    def _clear_media_selection(self) -> None:
        self.selected_media_ids.clear()
        self._apply_media_filters()

    def _selected_media_urls(self) -> list[str]:
        return [
            self.media_candidates[item].url
            for item in self.media_candidates
            if item in self.selected_media_ids
        ]

    def _queue_media_on_double_click(self, event: tk.Event) -> str:
        item_id = self.grabber_tree.identify_row(event.y)
        if item_id and item_id not in self.selected_media_ids:
            self.selected_media_ids.add(item_id)
            self.grabber_tree.selection_add(item_id)
        self._queue_selected_media()
        return "break"

    def _show_media_context_menu(self, event: tk.Event) -> None:
        item_id = self.grabber_tree.identify_row(event.y)
        if not item_id:
            return
        if item_id not in self.grabber_tree.selection():
            self.grabber_tree.selection_set(item_id)
        self.media_context_menu.tk_popup(event.x_root, event.y_root)

    def _open_selected_media(self) -> None:
        urls = self._selected_media_urls()
        if not urls:
            return
        if len(urls) > 1:
            self._show_info(
                self._t("messages.open_media_title"),
                self._t("messages.one_media_link"),
            )
            return
        if not webbrowser.open(urls[0]):
            self._show_error(
                self._t("messages.browser_error"),
                self._t("messages.browser_media_error"),
            )

    def _copy_selected_media(self) -> None:
        urls = self._selected_media_urls()
        if not urls:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(urls))
        except tk.TclError as exc:
            self._show_error(self._t("messages.clipboard_error"), str(exc))

    def _queue_selected_media(self) -> None:
        candidates = [
            self.media_candidates[item]
            for item in self.media_candidates
            if item in self.selected_media_ids
        ]
        if not candidates:
            self._show_info(
                "No media selected",
                self._t("messages.no_media_selected"),
            )
            return
        self._queue_links([candidate.url for candidate in candidates])
        self.input_frame.pack_forget()
        self.tabs.select(self.downloads_tab)

    def _add_quick_link(self, _event: object = None) -> None:
        urls = parse_urls(self.quick_link_var.get())
        if not urls:
            self._show_warning(
                self._t("messages.invalid_link"),
                self._t("messages.invalid_url"),
            )
            return
        self._queue_links(urls)
        self.quick_link_var.set("")

    def _queue_links(self, urls: list[str]) -> None:
        engine = self._engine_value(self.engine_var.get())
        output_format = self._output_format_value(self.output_format_var.get())
        self.output_format = output_format
        self.menu_format_var.set(output_format)
        self.storage.set_setting("output_format", output_format)
        for url in urls:
            download_id = self.storage.add_download(
                url, self.download_directory, engine, output_format
            )
            record = DownloadRecord(
                id=download_id,
                url=url,
                filename=Path(url.split("?", 1)[0]).name or "download",
                status="queued",
                progress=0,
                total=None,
                speed=0.0,
                eta=None,
                destination=self.download_directory,
                engine=engine,
                error="",
                created_at="",
                output_format=output_format,
            )
            self.records[download_id] = record
            self.manager.add(record)
            self._upsert_row(record)
        self._refresh_status()

    def _upsert_row(self, record: DownloadRecord) -> None:
        try:
            host = urlsplit(record.url).netloc
        except ValueError:
            host = ""
        status_key = f"download.status.{record.status}"
        status_label = self._t(status_key) if record.status in {
            "queued", "downloading", "paused", "completed", "cancelled", "error"
        } else record.status.title()
        comment = record.error
        if not comment and record.output_format != "original":
            comment = record.output_format.upper()
        values = (
            record.filename,
            format_size(record.total, self._t("common.unknown")),
            host,
            "-",
            status_label,
            f"{format_size(int(record.speed), self._t('common.unknown'))}/s"
            if record.speed > 0
            else "-",
            format_duration(record.eta),
            format_size(record.progress, self._t("common.unknown")),
            self._progress_bar(record),
            record.destination,
            comment,
        )
        tags = (record.status,)
        self._upsert_tree_row(
            self.download_tree,
            self.download_rows,
            record.id,
            values,
            tags,
        )
        if record.status in {"completed", "cancelled", "error"}:
            self._upsert_tree_row(
                self.history_tree,
                self.history_rows,
                record.id,
                values,
                tags,
            )
        elif record.id in self.history_rows:
            self.history_tree.delete(self.history_rows.pop(record.id))

    @staticmethod
    def _upsert_tree_row(
        tree: ttk.Treeview,
        rows: dict[int, str],
        download_id: int,
        values: tuple[str, ...],
        tags: tuple[str, ...],
    ) -> None:
        row = rows.get(download_id)
        if row and tree.exists(row):
            tree.item(row, values=values, tags=tags)
        else:
            row = tree.insert("", "end", iid=str(download_id), values=values, tags=tags)
            rows[download_id] = row

    @staticmethod
    def _progress_bar(record: DownloadRecord) -> str:
        percent = int(record.progress * 100 / record.total) if record.total else 0
        bars = min(10, percent // 10)
        return f"[{'#' * bars}{'-' * (10 - bars)}] {percent}%"

    def _poll_events(self) -> None:
        changed = False
        for _ in range(MAX_EVENTS_PER_TICK):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                download_id = int(event["id"])
                record = self.records.get(download_id)
                if not record:
                    continue
                for key, value in event.items():
                    if key != "id" and hasattr(record, key):
                        setattr(record, key, value)
                updates = {key: value for key, value in event.items() if key != "id"}
                self.storage.update_download(download_id, **updates)
                self._upsert_row(record)
                changed = True
            except (KeyError, TypeError, ValueError) as exc:
                self.status_var.set(
                    self._t("status.update_error", error=exc)
                )
        if changed:
            self._refresh_status()
            self._show_selected_progress()
        self.root.after(150, self._poll_events)

    def _selected_ids(self) -> list[int]:
        tree = self._context_tree or self._active_tree()
        return [int(item) for item in tree.selection() if item.isdigit()]

    def _clear_context_tree(self, _event: object = None) -> None:
        self._context_tree = None

    def _show_download_context_menu(self, event: tk.Event) -> str:
        tree = event.widget
        if not isinstance(tree, ttk.Treeview):
            return "break"
        row = tree.identify_row(event.y)
        if not row:
            return "break"
        if row not in tree.selection():
            tree.selection_set(row)
        tree.focus(row)
        self._context_tree = tree
        selected = self._selected_ids()
        records = [self.records[item] for item in selected if item in self.records]
        can_rename = len(records) == 1 and records[0].status in {
            "paused", "cancelled", "error", "completed",
        }
        can_delete = bool(records) and all(
            record.status == "completed"
            and (Path(record.destination) / record.filename).is_file()
            for record in records
        )
        self.context_menu.entryconfigure(
            0,
            state="normal"
            if any(
                record.status in {"paused", "cancelled", "error"}
                for record in records
            )
            else "disabled",
        )
        for index in (1, 2):
            self.context_menu.entryconfigure(
                index,
                state="normal"
                if any(
                    record.status in {"queued", "downloading", "paused"}
                    for record in records
                )
                else "disabled",
            )
        for index in (5, 6, 9):
            self.context_menu.entryconfigure(
                index, state="normal" if records else "disabled"
            )
        self.context_menu.entryconfigure(8, state="normal" if can_rename else "disabled")
        self.context_menu.entryconfigure(10, state="normal" if can_delete else "disabled")
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()
        return "break"

    def _resume_selected(self) -> None:
        for download_id in self._selected_ids():
            self.manager.resume(download_id)

    def _pause_selected(self) -> None:
        for download_id in self._selected_ids():
            self.manager.pause(download_id)

    def _cancel_selected(self) -> None:
        for download_id in self._selected_ids():
            self.manager.cancel(download_id)

    def _open_selected_folder(self) -> None:
        selected = self._selected_ids()
        if not selected or selected[0] not in self.records:
            return
        folder = self.records[selected[0]].destination
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        except OSError as exc:
            self._show_error(self._t("messages.folder_error"), str(exc))

    def _rename_selected(self) -> None:
        selected = self._selected_ids()
        if len(selected) != 1 or selected[0] not in self.records:
            return
        record = self.records[selected[0]]
        new_name = simpledialog.askstring(
            self._t("messages.rename_title"),
            self._t("messages.rename_prompt"),
            initialvalue=record.filename,
            parent=self.root,
        )
        if new_name is None:
            return
        try:
            self._rename_download(record.id, new_name)
        except (OSError, ValueError) as exc:
            self._show_error(self._t("messages.rename_failed"), str(exc))

    def _rename_download(self, download_id: int, new_name: str) -> None:
        record = self.records[download_id]
        new_name = new_name.strip()
        if (
            not new_name
            or new_name in {".", ".."}
            or "/" in new_name
            or "\\" in new_name
            or "\0" in new_name
        ):
            raise ValueError(self._t("messages.invalid_filename"))
        if record.status not in {"paused", "cancelled", "error", "completed"}:
            raise ValueError(self._t("messages.pause_before_rename"))
        old_path = Path(record.destination) / record.filename
        new_path = Path(record.destination) / new_name
        if old_path == new_path:
            return
        if new_path.exists():
            raise FileExistsError(
                self._t("messages.filename_exists", filename=new_name)
            )

        partial_path = Path(f"{old_path}.part")
        new_partial_path = Path(f"{new_path}.part")
        renamed_path: Path | None = None
        if record.status == "completed":
            if not old_path.is_file():
                raise FileNotFoundError(
                    self._t("messages.downloaded_file_missing", path=old_path)
                )
            old_path.rename(new_path)
            renamed_path = new_path
        elif partial_path.exists():
            if new_partial_path.exists():
                raise FileExistsError(
                    self._t(
                        "messages.partial_file_exists",
                        filename=new_partial_path.name,
                    )
                )
            partial_path.rename(new_partial_path)
            renamed_path = new_partial_path

        try:
            self.storage.update_download(download_id, filename=new_name)
        except sqlite3.Error:
            if renamed_path is not None:
                renamed_path.rename(
                    old_path if record.status == "completed" else partial_path
                )
            raise
        record.filename = new_name
        self._upsert_row(record)
        self._show_selected_progress()

    def _remove_selected(self) -> None:
        selected = self._selected_ids()
        if not selected:
            return
        if not messagebox.askyesno(
            self._t("messages.remove_title"),
            self._t("messages.remove_confirm", count=len(selected)),
            parent=self.root,
        ):
            return
        self._remove_downloads(selected)

    def _remove_downloads(self, download_ids: list[int]) -> None:
        for download_id in download_ids:
            record = self.records.get(download_id)
            if not record:
                continue
            try:
                self.storage.delete_download(download_id)
            except sqlite3.Error as exc:
                self._show_error(
                    self._t("messages.remove_failed"),
                    self._t(
                        "messages.remove_error",
                        filename=record.filename,
                        error=exc,
                    ),
                )
                continue
            self.manager.remove(download_id)
            self.records.pop(download_id, None)
            for rows, tree in (
                (self.download_rows, self.download_tree),
                (self.history_rows, self.history_tree),
            ):
                item = rows.pop(download_id, None)
                if item and tree.exists(item):
                    tree.delete(item)
        self._refresh_status()
        self._show_selected_progress()

    def _delete_selected_file(self) -> None:
        selected = self._selected_ids()
        if not selected:
            return
        records = [self.records[item] for item in selected if item in self.records]
        paths = [Path(record.destination) / record.filename for record in records]
        if not records or any(
            record.status != "completed" or not path.is_file()
            for record, path in zip(records, paths)
        ):
            self._show_error(
                self._t("messages.delete_title"),
                self._t("messages.delete_only_completed"),
            )
            return
        if not messagebox.askyesno(
            self._t("messages.delete_title"),
            self._t("messages.delete_confirm", count=len(paths)),
            parent=self.root,
        ):
            return
        try:
            for path in paths:
                path.unlink()
        except OSError as exc:
            self._show_error(
                self._t("messages.delete_failed"), str(exc)
            )
            return
        self._remove_downloads(selected)

    def _retry_selected(self) -> None:
        self._resume_selected()

    def _resume_all(self) -> None:
        for download_id, record in self.records.items():
            if record.status not in {"completed", "downloading"}:
                self.manager.resume(download_id)

    def _pause_all(self) -> None:
        for download_id in self.records:
            self.manager.pause(download_id)

    def _set_simultaneous(self) -> None:
        try:
            count = int(self.concurrent_var.get())
        except ValueError:
            self.concurrent_var.set(str(self.simultaneous))
            return
        self._set_simultaneous_count(count)

    def _set_simultaneous_count(self, count: int) -> None:
        if not 1 <= count <= 8:
            self.menu_simultaneous_var.set(self.simultaneous)
            self.concurrent_var.set(str(self.simultaneous))
            return
        self.simultaneous = count
        self.menu_simultaneous_var.set(count)
        self.concurrent_var.set(str(count))
        self.manager.set_simultaneous(count)
        self.storage.set_setting("simultaneous", str(count))

    def _set_default_format(self, output_format: str) -> None:
        if output_format not in {"original", "mp3", "mp4", "webm"}:
            return
        self.output_format = output_format
        self.menu_format_var.set(output_format)
        self.output_format_var.set(self._output_format_label(output_format))
        self.storage.set_setting("output_format", output_format)

    def _choose_directory(self) -> None:
        selected = filedialog.askdirectory(
            title=self._t("settings.download_folder"),
            initialdir=self.download_directory,
        )
        if selected:
            self.download_directory = selected
            self.storage.set_setting("download_directory", selected)
            self._refresh_status()

    def _add_clipboard_links(self) -> None:
        try:
            clipboard_text = self.root.clipboard_get()
        except tk.TclError:
            self._show_warning(
                self._t("messages.clipboard_empty_title"),
                self._t("messages.clipboard_empty_text"),
            )
            return
        urls = parse_urls(clipboard_text)
        if not urls:
            self._show_warning(
                self._t("messages.no_clipboard_links_title"),
                self._t("messages.no_clipboard_links_text"),
            )
            return
        self._queue_links(urls)
        self.tabs.select(self.downloads_tab)

    def _focus_grabber(self) -> None:
        self.tabs.select(self.grabber_tab)
        self.input_frame.pack(fill="x", padx=5, pady=5)
        self.link_text.focus_set()
        try:
            self.link_text.event_generate("<<Paste>>")
        except tk.TclError:
            self.link_text.focus_set()

    def _select_tab(self, tab: str) -> None:
        tab_frame = {
            "downloads": self.downloads_tab,
            "grabber": self.grabber_tab,
            "history": self.history_tab,
            "contribute": self.contribute_tab,
        }[tab]
        if tab == "history" and self.tabs.tab(tab_frame, "state") == "hidden":
            self.tabs.add(
                tab_frame,
                text=self._t("tabs.history_count", count=len(self.history_rows)),
            )
        self.tabs.select(tab_frame)

    def _active_tree(self) -> ttk.Treeview:
        return (
            self.history_tree
            if self.tabs.select() == str(self.history_tab)
            else self.download_tree
        )

    def _select_all(self) -> None:
        self._active_tree().selection_set(self._active_tree().get_children())

    def _raise_selected(self) -> None:
        self._move_selected(-1)

    def _lower_selected(self) -> None:
        self._move_selected(1)

    def _move_selected(self, offset: int) -> None:
        tree = self._active_tree()
        children = list(tree.get_children())
        selected = set(tree.selection())
        for index in range(len(children)):
            item = children[index] if offset < 0 else children[-index - 1]
            if item not in selected:
                continue
            current_index = children.index(item)
            target_index = current_index + offset
            if 0 <= target_index < len(children):
                tree.move(item, "", target_index)

    def _copy_selected_links(self) -> None:
        urls = [
            self.records[download_id].url
            for download_id in self._selected_ids()
            if download_id in self.records
        ]
        if not urls:
            self._show_info(
                self._t("messages.copy_urls_title"),
                self._t("messages.select_download"),
            )
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(urls))
            self.root.update_idletasks()
        except tk.TclError as exc:
            self._show_error(self._t("messages.clipboard_error"), str(exc))

    def _open_selected_links(self) -> None:
        urls = [
            self.records[download_id].url
            for download_id in self._selected_ids()
            if download_id in self.records
        ]
        if not urls:
            self._show_info(
                self._t("messages.open_urls_title"),
                self._t("messages.select_download"),
            )
            return
        failed = [url for url in urls if not webbrowser.open(url)]
        if failed:
            self._show_error(
                self._t("messages.browser_error"),
                self._t("messages.browser_urls_error")
                + "\n"
                + "\n".join(failed),
            )

    def _show_selected_progress(self, _event: object = None) -> None:
        selected = self._selected_ids()
        records = [self.records[item] for item in selected if item in self.records]
        if not records:
            self.detail_text.set(self._t("details.none"))
            self.detail_progress.configure(value=0)
            return
        total = sum(record.total or 0 for record in records)
        downloaded = sum(record.progress for record in records)
        percent = downloaded * 100 / total if total else 0
        self.detail_progress.configure(value=percent)
        if len(records) == 1:
            record = records[0]
            self.detail_text.set(
                self._t(
                    "details.single",
                    filename=record.filename,
                    status=self._t(f"download.status.{record.status}")
                    if record.status in {
                        "queued", "downloading", "paused", "completed",
                        "cancelled", "error",
                    }
                    else record.status.title(),
                    downloaded=format_size(
                        record.progress, self._t("common.unknown")
                    ),
                    total=format_size(record.total, self._t("common.unknown")),
                    percent=f"{percent:.0f}",
                )
            )
        else:
            self.detail_text.set(
                self._t(
                    "details.multiple",
                    count=len(records),
                    downloaded=format_size(
                        downloaded, self._t("common.unknown")
                    ),
                    total=format_size(total, self._t("common.unknown")),
                    percent=f"{percent:.0f}",
                )
            )

    def _refresh_status(self) -> None:
        records = list(self.records.values())
        active_records = [record for record in records if record.status == "downloading"]
        active = len(active_records)
        queued = sum(record.status in {"queued", "paused"} for record in records)
        total = sum(record.total or 0 for record in records)
        downloaded = sum(record.progress for record in records)
        remaining = max(total - downloaded, 0)
        speed = sum(record.speed for record in active_records)
        eta = remaining / speed if speed > 0 else None
        self.stats_vars["links"].set(self._t("stats.packages", count=len(records)))
        self.stats_vars["total"].set(
            self._t(
                "stats.total_size",
                size=format_size(total, self._t("common.unknown")),
            )
        )
        self.stats_vars["downloaded"].set(
            self._t(
                "stats.downloaded",
                size=format_size(downloaded, self._t("common.unknown")),
            )
        )
        self.stats_vars["remaining"].set(
            self._t(
                "stats.remaining",
                size=format_size(remaining, self._t("common.unknown")),
            )
        )
        self.stats_vars["speed"].set(
            self._t(
                "stats.download_speed",
                speed=f"{format_size(int(speed), self._t('common.unknown'))}/s",
            )
        )
        self.stats_vars["eta"].set(
            self._t("stats.time_left", time=format_duration(eta))
        )
        self.stats_vars["active"].set(
            self._t("stats.open_connections", count=active)
        )
        self.stats_vars["connections"].set(
            self._t("stats.links", count=len(records))
        )
        self.stats_vars["download_count"].set(
            self._t("stats.downloads_in_progress", count=active)
        )
        self.stats_vars["online"].set(self._t("stats.online", count=active))
        self.stats_vars["offline"].set(self._t("stats.offline", count=0))
        self.tabs.tab(
            self.history_tab,
            text=self._t("tabs.history_count", count=len(self.history_rows)),
        )
        for key, rows in (
            ("downloads", self.download_rows),
            ("history", self.history_rows),
        ):
            has_records = bool(rows) or (
                key == "downloads" and bool(self.download_tree.get_children())
            )
            self._set_empty_state_visible(key, not has_records)
        self.status_var.set(
            self._t(
                "status.summary",
                active=active,
                queued=queued,
                folder=self.download_directory,
            )
        )

    def _open_directory(self) -> None:
        try:
            Path(self.download_directory).mkdir(parents=True, exist_ok=True)
            os.startfile(self.download_directory)
        except OSError as exc:
            self._show_error(self._t("messages.folder_error"), str(exc))

    def _open_data_directory(self) -> None:
        try:
            data_directory = default_data_directory()
            data_directory.mkdir(parents=True, exist_ok=True)
            os.startfile(str(data_directory))
        except OSError as exc:
            self._show_error(self._t("messages.data_folder_error"), str(exc))

    def _about(self) -> None:
        self._show_info(
            self._t("about.title"),
            self._t("about.body"),
        )

    def _close(self) -> None:
        self._closing = True
        self.manager.close()
        self.storage.close()
        self.root.destroy()
