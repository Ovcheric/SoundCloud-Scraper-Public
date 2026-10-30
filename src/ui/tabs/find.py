"""
The Find screen: search SoundCloud for unheard sets, review them, add them.

Each search adds to one queue, which lives until it is added or cleared.
Reviewing it works like Add Sets - rows coloured by how sure the artist is,
double-click to correct one - plus removing any number of rows with a
reason, which services/finder.py records so they are never offered again.
"Add to Discovery" is the Add Sets commit, unchanged.

Public: FindTab

Imports services and ui only. Never reaches into core, data or api directly.
"""

from __future__ import annotations

import threading
import tkinter as tk

import customtkinter as ctk

from services import finder

from .. import confidence, theme
from ..dialogs import ask_artist, ask_dismissal, ask_yes_no, show_error
from ..widgets import (Button, PillRow, StatusBar, fmt_duration, make_tree,
                       page_header)
from .find_options import OptionsPanel

_EMPTY_HINT = ("Pick where to look on the left, then press Search.\n"
               "Searches start from the artists you keep, so they find more "
               "once your library has some.")


class FindTab(ctk.CTkFrame):
    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, fg_color="transparent")
        self._app = app
        self._plan = finder.FindPlan()
        self._stop = threading.Event()
        self._busy = False               # a search or an add is running
        self._build()
        self._refresh()

    # -- layout ------------------------------------------------------------

    def _build(self) -> None:
        page_header(
            self, "Find Sets",
            "Search SoundCloud for sets you have not heard, starting from the "
            "artists you keep.").pack(fill=tk.X)
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill=tk.BOTH, expand=True, pady=(20, 0))
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=0, column=0, sticky="ns", padx=(0, 18))
        self._panel = OptionsPanel(left)
        self._panel.pack(fill=tk.Y, expand=True)
        self._search_button = Button(left, "Search", self._on_search, height=42)
        self._search_button.pack(fill=tk.X, pady=(12, 0))
        self._stop_button = Button(left, "Stop", self._on_stop, kind="ghost",
                                   height=42)

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        self._build_queue(right)

    def _build_queue(self, parent) -> None:
        self._summary = ctk.CTkLabel(parent, text="", anchor="w",
                                     font=theme.font(17, "bold"))
        self._summary.pack(fill=tk.X)
        self._pills = PillRow(parent)
        self._pills.pack(fill=tk.X, pady=(6, 0))
        self._legend = confidence.Legend(parent)
        self._legend.pack(fill=tk.X, pady=(10, 0))
        self._notes = ctk.CTkLabel(parent, text="", anchor="w", justify=tk.LEFT,
                                   wraplength=640, text_color=theme.TEXT_DIM,
                                   font=theme.font(12))
        self._notes.pack(fill=tk.X, pady=(6, 0))

        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.pack(fill=tk.X, side=tk.BOTTOM, pady=(14, 0))
        self._add_button = Button(actions, "Add to Discovery", self._on_add,
                                  state=tk.DISABLED, width=170)
        self._add_button.pack(side=tk.LEFT)
        self._remove_button = Button(actions, "Remove…", self._on_remove,
                                     kind="secondary", state=tk.DISABLED, width=110)
        self._remove_button.pack(side=tk.LEFT, padx=10)
        self._clear_button = Button(actions, "Clear", self._on_clear,
                                    kind="ghost", state=tk.DISABLED, width=80)
        self._clear_button.pack(side=tk.LEFT)
        self._status = StatusBar(actions)
        self._status.pack(side=tk.RIGHT)

        table_area = ctk.CTkFrame(parent, fg_color="transparent")
        table_area.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self._tree = make_tree(table_area, [
            ("artist", "Artist", 150, tk.W),
            ("title", "Title", 230, tk.W),
            ("length", "Length", 70, tk.E),
            ("via", "Found because", 210, tk.W),
        ], hint=_EMPTY_HINT, selectmode="extended")
        confidence.style_rows(self._tree)
        self._tree.bind("<Double-1>", self._on_edit_artist)
        self._tree.bind("<Delete>", lambda _e: self._on_remove())
        self._tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_buttons())

    # -- showing the queue -------------------------------------------------

    def _order(self, row: dict) -> tuple:
        """Least sure artists first, then the strongest finds."""
        tid = row["track_id"]
        level = confidence.level_of(self._plan.queue, tid)
        return (confidence.RANK.get(level, len(confidence.RANK)),
                -self._plan.scores.get(tid, 0), row["title"].lower())

    def _refresh(self) -> None:
        queue = self._plan.queue
        self._tree.delete(*self._tree.get_children())
        for row in sorted(queue.to_add, key=self._order):
            tid = row["track_id"]
            fixed = tid in queue.corrections
            self._tree.insert("", tk.END, iid=tid, tags=(
                "fixed" if fixed else confidence.level_of(queue, tid),),
                values=(queue.corrections.get(tid, row["real_artists"]),
                        row["title"], fmt_duration(row["duration_ms"]),
                        self._plan.found_via.get(tid, "")))
        self._legend.show(queue if queue.to_add else None)
        count = len(queue.to_add)
        self._summary.configure(
            text=f"{count} set{'' if count == 1 else 's'} waiting to be added"
            if count else "Nothing queued yet",
            text_color=theme.TEXT if count else theme.TEXT_FAINT)
        self._show_last_search()
        self._sync_buttons()

    def _show_last_search(self) -> None:
        """What the last search did, including everything it left out."""
        plan = self._plan
        if not plan.requests:
            self._pills.show([])
            self._notes.configure(text="")
            return
        self._pills.show([(f"{len(plan.queue.to_add)} in the queue", "accent"),
                          (f"{plan.seen} looked at", "neutral")])
        lines = []
        if plan.queued_by_source:
            lines.append("Queued from: " + "  ·  ".join(
                f"{finder.SOURCES[key][0]} {n}"
                for key, n in plan.queued_by_source.most_common()))
        if plan.left_out:
            lines.append("Left out: " + "  ·  ".join(
                f"{n} {why}" for why, n in plan.left_out.most_common()))
        if plan.stopped:
            lines.append(f"Stopped early ({plan.stopped}) - kept what was found.")
        if plan.unmatched:
            extra = len(plan.unmatched) - 6
            more = f" and {extra} more" if extra > 0 else ""
            lines.append("No SoundCloud account found for "
                         f"{', '.join(plan.unmatched[:6])}{more}.")
        lines.append(f"Searched from {len(plan.seeds)} of your artists in "
                     f"{plan.requests} requests. Double-click a row to fix its "
                     "artist; Delete removes the selected rows.")
        self._notes.configure(text="\n".join(lines))

    def _sync_buttons(self) -> None:
        """Add and Clear wait for a running job; Remove works any time."""
        ready = bool(self._plan.queue.to_add) and not self._busy
        self._add_button.configure(state=tk.NORMAL if ready else tk.DISABLED)
        self._clear_button.configure(state=tk.NORMAL if ready else tk.DISABLED)
        self._search_button.configure(
            state=tk.DISABLED if self._busy else tk.NORMAL)
        self._remove_button.configure(
            state=tk.NORMAL if self._tree.selection() else tk.DISABLED)

    # -- searching ---------------------------------------------------------

    def _on_search(self) -> None:
        options = self._panel.options()
        if not options.sources:
            self._status.idle("Turn on at least one place to search.")
            return
        self._stop.clear()
        self._set_busy(True, searching=True)
        self._status.busy("Starting the search…")
        queued = list(self._plan.queue.to_add)
        self._app.runner.run(
            lambda report: self._search_work(options, queued, report),
            on_done=self._on_found, on_error=self._on_error,
            on_progress=self._status.busy, name="plan_find")

    def _search_work(self, options, queued: list, report) -> finder.FindPlan:
        """Runs on a worker thread, so it opens its own connection."""
        api = self._app.api()
        with self._app.db() as conn:
            return finder.plan_find(options, conn, api, report=report,
                                    should_stop=self._stop.is_set, queued=queued)

    def _on_stop(self) -> None:
        self._stop.set()
        self._status.busy("Stopping after the current request…")

    def _on_found(self, found: finder.FindPlan) -> None:
        self._plan = finder.merge(self._plan, found)
        self._set_busy(False)
        self._refresh()
        self._status.idle(self._found_message(found))

    @staticmethod
    def _found_message(found: finder.FindPlan) -> str:
        """Say plainly when a search came up short, and what to widen."""
        added = len(found.queue.to_add)
        if not added:
            return "Nothing new this time - try Deep, or wider filters."
        if added < found.asked and not found.stopped:
            return (f"Found {added} of the {found.asked} asked for - everything "
                    "else was left out (see below). Deep searches further.")
        return f"Found {added} new set{'' if added == 1 else 's'}."

    def _set_busy(self, busy: bool, searching: bool = False) -> None:
        self._busy = busy
        if searching:
            self._stop_button.pack(fill=tk.X, pady=(8, 0))
        else:
            self._stop_button.pack_forget()
        self._sync_buttons()

    # -- reviewing ---------------------------------------------------------

    def _on_edit_artist(self, _event=None) -> None:
        """Double-click a row to correct its artist, exactly as in Add Sets."""
        queue = self._plan.queue
        track_id = self._tree.focus()
        row = next((r for r in queue.to_add if r["track_id"] == track_id), None)
        if row is None:
            return
        current = queue.corrections.get(track_id, row["real_artists"])
        corrected = ask_artist(self, row["title"], current,
                               confidence.explain(queue, track_id))
        if corrected:
            queue.corrections[track_id] = corrected
            self._refresh()

    def _on_remove(self) -> None:
        ids = list(self._tree.selection())
        if not ids:
            return
        queue = self._plan.queue
        performers = []
        for row in queue.to_add:
            if row["track_id"] in ids:
                artist = queue.corrections.get(row["track_id"], row["real_artists"])
                performers += [p.strip() for p in artist.split(",")
                               if p.strip() and p.strip() not in performers]
        answer = ask_dismissal(self, len(ids), finder.DISMISS_REASONS,
                               finder.LEARNS, performers, finder.NOT_NOW)
        if answer is None:
            return
        reason, note = answer
        with self._app.db() as conn:          # quick, so on the UI thread
            removed = finder.dismiss(self._plan, conn, ids, reason, note)
        self._refresh()
        why = finder.DISMISS_REASONS.get(reason, "for now")
        self._status.idle(f"Removed {removed} - {why.lower()}.")

    def _on_clear(self) -> None:
        count = len(self._plan.queue.to_add)
        if count and not ask_yes_no(
                self, "Clear the queue",
                f"Take all {count} sets out of the queue? Nothing is "
                "remembered, so a later search may find them again.",
                confirm="Clear"):
            return
        self._plan = finder.FindPlan()
        self._refresh()
        self._status.idle("Queue cleared.")

    # -- adding ------------------------------------------------------------

    def _on_add(self) -> None:
        if not self._plan.queue.to_add:
            return
        plan = self._plan
        self._set_busy(True)
        self._status.busy("Adding to Discovery…")
        self._app.runner.run(
            lambda report: self._add_work(plan), on_done=self._on_added,
            on_error=self._on_error, name="apply_find")

    def _add_work(self, plan: finder.FindPlan):
        """Runs on a worker thread, so it opens its own connection."""
        with self._app.db() as conn:
            return finder.apply_find(plan, conn, self._app.api())

    def _on_added(self, result) -> None:
        self._plan = finder.FindPlan()
        self._set_busy(False)
        self._refresh()
        message = (f"Added {result.added_to_library} to the library and "
                   f"{result.added_to_playlist} to Discovery, sorted by title.")
        if result.corrected:
            message += f" {result.corrected} artist(s) corrected."
        self._status.idle(message)
        self._app.refresh_title()

    def _on_error(self, error: Exception) -> None:
        self._set_busy(False)
        self._status.idle("Failed.")
        show_error(self, "Find failed", str(error))
