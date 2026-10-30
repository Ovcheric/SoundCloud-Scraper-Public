"""
The window: a sidebar, four screens and one task runner - and the way in.

main() is the first-launch path too: no credentials opens the connect window
(ui/onboarding.py), a brand-new library gets its starter lists, and an empty
one opens the guide.

Holds the things the screens share and nothing else. Any logic that belongs to
a pipeline lives in services/; this file only wires. The look lives in
ui/theme.py.

There is no shared database connection, on purpose - see App.

Public: App, main

Run with:  .venv/Scripts/python.exe -m src.ui.app
"""

from __future__ import annotations

import sys
import tkinter as tk
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import auth, client  # noqa: E402
from data import db, starter, tracks as track_store  # noqa: E402
from ui import onboarding, theme  # noqa: E402
from ui.sidebar import Sidebar  # noqa: E402
from ui.tabs.add import AddTab  # noqa: E402
from ui.tabs.cleanup import CleanupTab  # noqa: E402
from ui.tabs.find import FindTab  # noqa: E402
from ui.tabs.taste import TasteTab  # noqa: E402
from ui.task import TaskRunner  # noqa: E402

TITLE = "SoundCloud Discovery"

# The screens, in sidebar order: (Segoe Fluent Icons glyph, name).
_SCREENS = (("", "Add Sets"), ("", "Find Sets"),
            ("", "Clean Up"), ("", "Taste"))
_CLEAN_UP = 2


def _opening_size(root) -> str:
    """
    As big as the review panes want, but never past the screen. CTk scales
    geometry by the display's DPI, so the screen is measured in its units.
    """
    scale = ctk.ScalingTracker.get_window_scaling(root)
    width = min(1500, int(root.winfo_screenwidth() / scale) - 80)
    height = min(900, int(root.winfo_screenheight() / scale) - 120)
    return f"{max(width, 1100)}x{max(height, 660)}"


class App:
    """
    The window, and the things every tab needs.

    There is deliberately no shared connection. SQLite objects are bound to the
    thread that made them, and the pipelines run on worker threads, so a single
    long-lived connection fails the moment Add or Clean Up does any work. Each
    caller opens its own through db() instead - opening a local SQLite file
    costs microseconds, and it removes the whole class of bug.
    """

    def __init__(self, root: tk.Tk, connect):
        self.root = root
        self._connect = connect          # zero-arg -> a NEW connection
        self.runner = TaskRunner(root)
        self._credentials: auth.Credentials | None = None

        theme.apply(root)
        root.title(TITLE)
        root.geometry(_opening_size(root))
        root.minsize(1100, 660)
        theme.dress_window(root)

        self.sidebar = Sidebar(root, list(_SCREENS), on_select=self.show,
                               on_help=self.show_guide)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        ctk.CTkFrame(root, width=1, corner_radius=0,
                     fg_color=theme.BORDER).pack(side=tk.LEFT, fill=tk.Y)
        content = ctk.CTkFrame(root, fg_color=theme.BG, corner_radius=0)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.add_tab = AddTab(content, self)
        self.find_tab = FindTab(content, self)
        self.cleanup_tab = CleanupTab(content, self)
        self.taste_tab = TasteTab(content, self)
        self._screens = (self.add_tab, self.find_tab, self.cleanup_tab,
                         self.taste_tab)
        self.show(0)

        self.runner.start()
        self.taste_tab.refresh()
        self.refresh_title()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def show(self, index: int) -> None:
        """Swap the visible screen. Hidden ones keep their state."""
        for i, screen in enumerate(self._screens):
            if i == index:
                screen.pack(fill=tk.BOTH, expand=True, padx=32, pady=(26, 20))
            else:
                screen.pack_forget()
        self.sidebar.select(index)

    def show_guide(self) -> None:
        """How the app works - and, if asked, import liked sets."""
        if onboarding.show_guide(self.root) == "import":
            self.show(_CLEAN_UP)
            self.cleanup_tab.import_likes()

    def welcome_if_new(self) -> None:
        """
        Open the guide by itself while the library is empty - a first launch.
        Never from __init__: the guide is modal, and tests build an App.
        """
        with self.db() as conn:
            empty = not sum(track_store.verdict_counts(conn).values())
        if empty:
            self.show_guide()

    @contextmanager
    def db(self):
        """
        A connection scoped to whoever asks, committed and closed on the way
        out. Safe to call from a worker thread; the shared one was not.
        """
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def api(self) -> client.SoundCloudClient:
        """
        A client with fresh credentials.

        Credentials are cached for the session but re-checked for expiry on
        every call, so a long-running window cannot go stale mid-evening.
        """
        if self._credentials is None or self._credentials.is_expired():
            self._credentials = auth.load_fresh()
        return client.SoundCloudClient(self._credentials)

    def refresh_title(self) -> None:
        with self.db() as conn:
            counts = track_store.verdict_counts(conn)
        total = sum(counts.values())
        self.root.title(
            f"{TITLE} — {total} sets, {counts.get('liked', 0)} kept")
        self.sidebar.show_counts(counts)

    def _on_close(self) -> None:
        # Nothing to close: every connection is short-lived by design.
        self.runner.stop()
        self.root.destroy()


def _sign_in(root: tk.Tk) -> bool:
    """
    Working credentials, walking a first launch through setting them up.

    Nothing else in the app opens a browser - auth.load_fresh() raises instead -
    so the connect window is the one place one can appear, and only after the
    user presses Connect.
    """
    try:
        auth.load_fresh()
        return True
    except auth.MissingCredentials:
        stored = None
    except auth.ReauthRequired:
        stored = auth.load()
    except auth.AuthError as exc:
        messagebox.showerror("Cannot start", str(exc), parent=root)
        return False
    return onboarding.ask_to_connect(root, onboarding.SignIn(
        save=lambda client_id, secret: auth.save(
            auth.Credentials(client_id, secret)),
        connect=lambda: auth.authorize_interactively(auth.load()),
        redirect_uri=auth.REDIRECT_URI,
        apps_page=auth.APPS_PAGE,
        needs_app=stored is None,
        signed_in_before=bool(stored and stored.refresh_token),
        client_id=stored.client_id if stored else "",
    ))


def _open_library() -> None:
    """Fail here, with a dialog, not later - and give a new library its lists."""
    conn = db.connect()
    try:
        starter.seed_new_library(conn)
        conn.commit()
    finally:
        conn.close()


CRASH_LOG = Path(__file__).resolve().parents[2] / "crash.log"


def _report_crash(root: tk.Tk, error: BaseException) -> None:
    """
    Make a startup failure visible.

    run.bat launches this with pythonw.exe so no console window sits behind the
    app - which also means an unhandled exception would vanish and the app
    would simply appear not to start. Write it down and say so.
    """
    detail = "".join(
        traceback.format_exception(type(error), error, error.__traceback__))
    try:
        CRASH_LOG.write_text(
            f"{datetime.now():%Y-%m-%d %H:%M:%S}\n\n{detail}", encoding="utf-8")
        where = f"\n\nWritten to {CRASH_LOG.name}"
    except OSError:
        where = ""
    messagebox.showerror(
        "SoundCloud Discovery could not start",
        f"{type(error).__name__}: {error}{where}",
        parent=root,
    )


def main() -> int:
    root = ctk.CTk()            # a tk.Tk that also darkens the title bar
    root.withdraw()

    try:
        theme.apply(root)             # the connect window is themed too
        if not _sign_in(root):
            root.destroy()
            return 1

        _open_library()
        app = App(root, db.connect)
        # Shown from inside the loop, not before it: CTk's mainloop() hides a
        # window that was withdrawn before it started, and never shows it
        # again. Calling deiconify() here would leave the app invisible.
        root.after(0, root.deiconify)
        root.after(400, app.welcome_if_new)
        root.mainloop()
        return 0
    except Exception as exc:  # noqa: BLE001 - last resort, see _report_crash
        _report_crash(root, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
