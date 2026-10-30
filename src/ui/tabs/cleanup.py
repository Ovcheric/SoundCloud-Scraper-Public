"""
The Clean Up screen: learn what was listened to, then tidy the playlist.

Loading is what records the verdicts - it syncs likes and snapshots Discovery
before anything is removed, because removal is the signal being read. Removing
is optional and separate. So is importing liked sets that never came through
Discovery, which is how a new library gets artists for Find to start from.

Public: CleanupTab

Imports services and ui only.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from services import cleanup, library

from .. import theme
from ..dialogs import ask_yes_no, show_error
from ..widgets import Button, PillRow, StatusBar, card, make_tree, page_header

# How each verdict reads in the table: (shown as, colour). Liked is orange
# because that is the colour of SoundCloud's own heart. Pending is most of
# the queue, so it stays plain and the hearts stand out against it.
_VERDICTS = {
    "liked": ("♥  liked", theme.ORANGE_SOFT),
    "pending": ("○  pending", theme.TEXT),
    "skipped": ("–  skipped", theme.TEXT_DIM),
    "unknown": ("?  unknown", theme.TEXT_FAINT),
}


class CleanupTab(ctk.CTkFrame):
    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, fg_color="transparent")
        self._app = app
        self._plan: cleanup.CleanupPlan | None = None
        self._build()

    def _build(self) -> None:
        page_header(
            self, "Clean Up",
            "After listening in SoundCloud, record what you kept and what "
            "you finished, then tidy the Discovery playlist."
        ).pack(fill=tk.X)

        top = card(self)
        top.pack(fill=tk.X, pady=(20, 0))
        self._load_button = Button(top, "Check Discovery", self._on_load,
                                   width=170, height=42)
        self._load_button.pack(side=tk.LEFT, padx=12, pady=12)
        ctk.CTkLabel(
            top, anchor="w", text_color=theme.TEXT_DIM, font=theme.font(13),
            text="Syncs your likes and records what you have worked through.",
        ).pack(side=tk.LEFT, padx=(4, 12))
        self._import_button = Button(top, "Import liked sets", self.import_likes,
                                     kind="secondary", width=170, height=42)
        self._import_button.pack(side=tk.RIGHT, padx=12, pady=12)

        self._summary = ctk.CTkLabel(
            self, text="Not checked yet", anchor="w",
            text_color=theme.TEXT_FAINT, font=theme.font(17, "bold"))
        self._summary.pack(fill=tk.X, pady=(18, 0))
        self._pills = PillRow(self)
        self._pills.pack(fill=tk.X, pady=(6, 0))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill=tk.X, side=tk.BOTTOM, pady=(14, 0))
        self._remove_button = Button(
            actions, "Remove liked from Discovery", self._on_remove,
            kind="secondary", state=tk.DISABLED, width=230)
        self._remove_button.pack(side=tk.LEFT)
        ctk.CTkLabel(
            actions, anchor="w", text_color=theme.TEXT_FAINT, font=theme.font(12),
            text="Only liked sets are removed — anything else may not be "
                 "played yet.",
        ).pack(side=tk.LEFT, padx=14)
        self._status = StatusBar(actions)
        self._status.pack(side=tk.RIGHT)

        table_area = ctk.CTkFrame(self, fg_color="transparent")
        table_area.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        self._tree = make_tree(table_area, [
            ("verdict", "Verdict", 120, tk.W),
            ("artist", "Artist", 200, tk.W),
            ("title", "Title", 460, tk.W),
        ], hint="Check Discovery to sync your likes and see what is queued.")
        for verdict, (_, colour) in _VERDICTS.items():
            self._tree.tag_configure(verdict, foreground=colour)

    # -- loading -----------------------------------------------------------

    def _on_load(self) -> None:
        self._load_button.configure(state=tk.DISABLED)
        self._status.busy("Syncing likes and reading Discovery…")
        self._app.runner.run(
            lambda report: self._load_work(),
            on_done=self._on_loaded,
            on_error=self._on_error,
            on_progress=self._status.busy,
            name="plan_cleanup",
        )

    def _load_work(self) -> cleanup.CleanupPlan:
        """Runs on a worker thread, so it opens its own connection."""
        with self._app.db() as conn:
            return cleanup.plan_cleanup(conn, self._app.api())

    def _on_loaded(self, plan: cleanup.CleanupPlan) -> None:
        self._plan = plan
        self._tree.delete(*self._tree.get_children())
        for queued in plan.queued:
            shown = _VERDICTS.get(queued.verdict, (queued.verdict, None))[0]
            self._tree.insert("", tk.END, iid=queued.track_id, tags=(queued.verdict,),
                              values=(shown, queued.artist, queued.title))

        self._summary.configure(text="Discovery, as of just now",
                                text_color=theme.TEXT)
        self._pills.show([
            (f"{plan.queued_count} in Discovery", "accent"),
            (f"{plan.likes_added} new likes", "neutral"),
            (f"{plan.likes_cleared} un-liked", "neutral"),
            (f"{plan.newly_listened} newly marked listened", "neutral"),
        ])
        self._load_button.configure(state=tk.NORMAL)
        self._remove_button.configure(
            state=tk.NORMAL if plan.removable else tk.DISABLED,
            text=f"Remove {len(plan.removable)} liked from Discovery"
            if plan.removable else "Remove liked from Discovery",
        )
        self._status.idle(
            "Discovery is empty — add some sets." if not plan.queued
            else "Recorded.")
        self._app.refresh_title()

    # -- removing ----------------------------------------------------------

    def _on_remove(self) -> None:
        if not self._plan or not self._plan.removable:
            return
        count = len(self._plan.removable)
        if not ask_yes_no(
            self, "Remove from Discovery",
            f"Remove {count} liked set(s) from the Discovery playlist?\n\n"
            "They stay in your library and in your SoundCloud likes.",
            confirm="Remove",
        ):
            return

        plan = self._plan
        self._remove_button.configure(state=tk.DISABLED)
        self._status.busy(f"Removing {count}…")
        self._app.runner.run(
            lambda report: self._remove_work(plan),
            on_done=self._on_removed,
            on_error=self._on_error,
            name="apply_removals",
        )

    def _remove_work(self, plan: cleanup.CleanupPlan) -> int:
        """Runs on a worker thread, so it opens its own connection."""
        with self._app.db() as conn:
            return cleanup.apply_removals(plan, conn, self._app.api(), plan.removable)

    def _on_removed(self, removed: int) -> None:
        self._status.idle(f"Removed {removed}. Re-check to refresh.")
        self._remove_button.configure(state=tk.DISABLED)
        self._tree.delete(*self._tree.get_children())
        self._plan = None
        self._summary.configure(
            text="Removed — check again to see the current queue.",
            text_color=theme.TEXT_DIM)
        self._pills.show([])

    def _on_error(self, error: Exception) -> None:
        self._load_button.configure(state=tk.NORMAL)
        self._status.idle("Failed.")
        show_error(self, "Clean up failed", str(error))

    # -- importing liked sets ----------------------------------------------

    def import_likes(self) -> None:
        """Find liked sets the library lacks, then offer to bring them in."""
        self._import_button.configure(state=tk.DISABLED)
        self._status.busy("Reading your SoundCloud likes…")
        self._app.runner.run(
            lambda report: self._plan_import_work(),
            on_done=self._on_import_planned,
            on_error=self._on_import_error,
            name="plan_import_likes",
        )

    def _plan_import_work(self) -> library.ImportPlan:
        """Runs on a worker thread, so it opens its own connection."""
        with self._app.db() as conn:
            return library.plan_import_likes(conn, self._app.api())

    def _on_import_planned(self, plan: library.ImportPlan) -> None:
        self._import_button.configure(state=tk.NORMAL)
        if not plan.add_count:
            self._status.idle(
                f"Nothing to import: none of your {plan.total_likes} likes is "
                "20 minutes or longer." if not plan.sets else
                "Nothing to import: every liked set is already in your library.")
            return
        if not ask_yes_no(
            self, "Import liked sets",
            f"Your SoundCloud likes hold {plan.add_count} set(s) of 20 minutes "
            "or more that are not in your library yet.\n\nThey are saved as "
            "heard and kept, so Find Sets and Taste can use them. Nothing is "
            "added to your Discovery playlist.",
            confirm=f"Import {plan.add_count}",
        ):
            self._status.idle("Nothing imported.")
            return
        self._import_button.configure(state=tk.DISABLED)
        self._status.busy(f"Importing {plan.add_count}…")
        self._app.runner.run(
            lambda report: self._apply_import_work(plan),
            on_done=self._on_imported,
            on_error=self._on_import_error,
            name="apply_import",
        )

    def _apply_import_work(self, plan: library.ImportPlan) -> library.ImportResult:
        """Runs on a worker thread, so it opens its own connection."""
        with self._app.db() as conn:
            return library.apply_import(plan, conn)

    def _on_imported(self, result: library.ImportResult) -> None:
        self._import_button.configure(state=tk.NORMAL)
        self._status.idle(f"Imported {result.added} liked set(s).")
        self._app.refresh_title()
        self._app.taste_tab.refresh()

    def _on_import_error(self, error: Exception) -> None:
        self._import_button.configure(state=tk.NORMAL)
        self._status.idle("Import failed.")
        show_error(self, "Import failed", str(error))
