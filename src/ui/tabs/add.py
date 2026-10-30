"""
The Add screen: paste a playlist URL, review what will be added, commit.

No questions mid-flow. services/discovery.py decides every duplicate and
artist, and the screen shows the result twice over - the playlist as
uploaded beside what will be stored (ui/tabs/add_review.py). Where the app
got it wrong, the user corrects it: mark a reposter or an official account,
group copies, split a pair, skip a set. Each correction re-plans at once from
the playlist already fetched, so the right-hand side is always the answer.

Public: AddTab

Imports services and ui only. Never reaches into core, data or api directly.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from services import discovery, finder, review

from .. import confidence, theme
from ..dialogs import ask_artist, ask_dismissal, show_error
from ..widgets import Button, PillRow, StatusBar, card, fmt_duration, page_header
from .add_review import STAR, ReviewPanes


class AddTab(ctk.CTkFrame):
    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, fg_color="transparent")
        self._app = app
        self._plan: discovery.AddPlan | None = None
        self._task = None
        self._working = False
        self._build()

    # -- layout ------------------------------------------------------------

    def _build(self) -> None:
        page_header(
            self, "Add Sets",
            "Paste a SoundCloud playlist. The long sets you have not heard go "
            "into a playlist called “discovery” in your SoundCloud account."
            ).pack(fill=tk.X)
        self._build_entry()
        self._summary = ctk.CTkLabel(
            self, text="", anchor="w", font=theme.font(17, "bold"))
        self._summary.pack(fill=tk.X, pady=(18, 0))
        self._pills = PillRow(self)
        self._pills.pack(fill=tk.X, pady=(6, 12))
        self._build_actions()
        # Packed after the action row, so a short window squeezes the tables
        # rather than clipping the buttons off the bottom.
        self._panes = ReviewPanes(self, on_action=self._on_action,
                                  on_edit_artist=self._on_edit_artist,
                                  on_select=self._sync_toolbar)
        self._panes.pack(fill=tk.BOTH, expand=True)
        self._tree, self._raw = self._panes.parsed, self._panes.raw
        self._legend = self._panes.legend
        self._legend_labels = self._legend.labels
        self._show_summary(None)

    def _build_entry(self) -> None:
        entry_card = card(self)
        entry_card.pack(fill=tk.X, pady=(20, 0))
        self._url = ctk.CTkEntry(
            entry_card, height=42, border_width=0, font=theme.font(14),
            placeholder_text="https://soundcloud.com/…/sets/…")
        self._url.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 10),
                       pady=12)
        self._url.bind("<Return>", lambda _event: self._on_check())
        self._check_button = Button(entry_card, "Check", self._on_check,
                                    kind="secondary", width=110, height=42)
        self._check_button.pack(side=tk.LEFT, padx=(0, 12), pady=12)

    def _build_actions(self) -> None:
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill=tk.X, side=tk.BOTTOM, pady=(14, 0))
        self._add_button = Button(
            actions, "Add to Discovery", self._on_add, state=tk.DISABLED,
            width=170)
        self._add_button.pack(side=tk.LEFT)
        self._clear_button = Button(
            actions, "Clear", self._reset, kind="ghost", state=tk.DISABLED,
            width=90)
        self._clear_button.pack(side=tk.LEFT, padx=10)
        self._status = StatusBar(actions)
        self._status.pack(side=tk.RIGHT)

    def _show_summary(self, plan: discovery.AddPlan | None) -> None:
        """The playlist's name, then one pill per number the plan reports."""
        if plan is None:
            self._summary.configure(text="Nothing checked yet",
                                    text_color=theme.TEXT_FAINT)
            self._pills.show([])
            return
        self._summary.configure(text=plan.playlist_title or "Untitled playlist",
                                text_color=theme.TEXT)
        pills = [(f"{plan.add_count} to add", "accent"),
                 (f"{plan.already_known} already in library", "neutral"),
                 (f"{plan.too_short} too short", "neutral"),
                 (f"{len(plan.skipped)} duplicates skipped", "neutral")]
        if plan.dismissed_before:
            pills.append((f"{plan.dismissed_before} turned down before",
                          "neutral"))
        if plan.needs_review:
            pills.append((f"{len(plan.needs_review)} artist(s) need a look",
                          "neutral"))
        self._pills.show(pills)

    # -- showing a plan ----------------------------------------------------

    def _show(self, plan: discovery.AddPlan | None) -> None:
        """Draw both panes from `plan`. Every correction comes back here."""
        self._plan = plan
        self._panes.quietly(lambda: (self._show_parsed(plan),
                                     self._panes.show_raw(plan)))
        self._legend.show(plan)
        self._show_summary(plan)
        self._set_working(False)

    def _show_parsed(self, plan: discovery.AddPlan | None) -> None:
        """Least trustworthy first, so a review can stop at the green rows."""
        self._tree.delete(*self._tree.get_children())
        uploads = {r.track_id: r for r in (plan.rows if plan else [])}
        for track in sorted(plan.to_add if plan else [], key=self._review_order):
            tid = track["track_id"]
            fixed = tid in plan.corrections
            raw = uploads.get(tid)
            uploader = track.get("uploader", "") + (
                " · reposter" if raw and raw.reposter else "")
            self._tree.insert(
                "", tk.END, iid=tid,
                tags=("fixed" if fixed else confidence.level_of(plan, tid),),
                values=(STAR if tid in plan.official else "",
                        plan.corrections.get(tid, track["real_artists"]),
                        track["title"], uploader,
                        fmt_duration(track["duration_ms"])))

    def _review_order(self, track: dict) -> tuple:
        """Sort key: worst confidence first, then by title."""
        level = (confidence.level_of(self._plan, track["track_id"])
                 if self._plan else discovery.CERTAIN)
        return (confidence.RANK.get(level, len(confidence.RANK)),
                track["title"].lower())

    # -- checking ----------------------------------------------------------

    def _on_check(self) -> None:
        url = self._url.get().strip()
        if not url:
            return
        self._set_working(True)
        self._status.busy("Fetching playlist…")
        self._task = self._app.runner.run(
            lambda report: self._check_work(url, report),
            on_done=self._on_checked,
            on_error=self._on_error,
            on_progress=self._status.busy,
            name="plan_add",
        )

    def _check_work(self, url: str, report) -> discovery.AddPlan:
        report("Resolving playlist…")
        api = self._app.api()
        report("Reading tracks and checking for duplicates…")
        # Its own connection: this runs on a worker thread, and a SQLite
        # object may only be used by the thread that created it.
        with self._app.db() as conn:
            return discovery.plan_add(url, conn, api)

    def _on_checked(self, plan: discovery.AddPlan) -> None:
        self._show(plan)
        self._status.idle(
            "Nothing new in that playlist." if not plan.to_add
            else f"Ready to add {plan.add_count}.")

    # -- correcting --------------------------------------------------------

    def _on_action(self, action: str) -> None:
        if not self._plan or self._working:
            return
        {"reposter": lambda: self._mark_account("reposter"),
         "official": lambda: self._mark_account("official"),
         "same": self._group, "different": self._split,
         "skip": self._skip}[action]()

    def _uploader_flags(self) -> dict:
        """Is every selected uploader already a reposter / already official?"""
        rows = [r for r in (self._plan.rows if self._plan else [])
                if r.track_id in set(self._panes.selected())]
        return {"reposter": bool(rows) and all(r.reposter for r in rows),
                "official": bool(rows) and all(r.official for r in rows)}

    def _sync_toolbar(self) -> None:
        self._panes.sync_toolbar(self._plan, self._uploader_flags(), self._working)

    def _mark_account(self, kind: str) -> None:
        """Saved straight away: it is a fact about the account, not this add."""
        names = review.uploaders(self._plan, self._panes.selected())
        undo = self._uploader_flags()[kind]
        mark = review.mark_reposter if kind == "reposter" else review.mark_official
        with self._app.db() as conn:                # quick, so on the UI thread
            mark(conn, names, not undo)
        what = "a reposter" if kind == "reposter" else "official"
        self._replan(f"{', '.join(names)} {'no longer' if undo else 'now'} "
                     f"marked {what} - saved for every playlist.")

    def _group(self) -> None:
        ids = self._panes.selected()
        review.mark_same(self._plan, ids)
        self._replan(f"Marked {len(ids)} tracks as one set - the best copy stays.")

    def _split(self) -> None:
        with self._app.db() as conn:
            split = review.mark_different(self._plan, conn, self._panes.selected())
        self._replan(f"Split {split} pair{'' if split == 1 else 's'} - both "
                     "copies will be added.")

    def _skip(self) -> None:
        adding = {t["track_id"]: t for t in self._plan.to_add}
        ids = [i for i in self._panes.selected() if i in adding]
        names = list(dict.fromkeys(
            n.strip() for i in ids for n in self._plan.corrections.get(
                i, adding[i]["real_artists"]).split(",") if n.strip()))
        answer = ask_dismissal(self, len(ids), finder.DISMISS_REASONS,
                               finder.LEARNS, names, finder.NOT_NOW)
        if answer is None:
            return
        reason, note = answer
        if reason == finder.NOT_NOW:
            self._plan.held_back |= set(ids)
        else:
            with self._app.db() as conn:
                review.skip(self._plan, conn, ids, reason, note)
        self._replan(f"Skipped {len(ids)} - "
                     f"{finder.DISMISS_REASONS.get(reason, 'for this add').lower()}.")

    def _replan(self, message: str) -> None:
        """Work the plan out again on a worker - triage reads the library."""
        plan = self._plan
        self._set_working(True)
        self._status.busy("Updating…")
        self._task = self._app.runner.run(
            lambda report: self._refresh_work(plan),
            on_done=lambda new: (self._show(new), self._status.idle(message)),
            on_error=self._on_error, name="refresh_add")

    def _refresh_work(self, plan: discovery.AddPlan) -> discovery.AddPlan:
        with self._app.db() as conn:
            return discovery.refresh(plan, conn)

    def _on_edit_artist(self, _event=None) -> None:
        """
        Double-click a row to correct its artist before it is added.

        Fixing it here means it lands right the first time, rather than
        becoming a one-off artist that has to be found again later.
        """
        if not self._plan:
            return
        track_id = self._tree.focus()
        track = next(
            (t for t in self._plan.to_add if t["track_id"] == track_id), None)
        if track is None:
            return
        parsed = track["real_artists"]
        current = self._plan.corrections.get(track_id, parsed)
        always = []
        corrected = ask_artist(
            self, track["title"], current, confidence.explain(self._plan, track_id),
            alias_of=parsed if "," not in parsed else "", on_always=always.append)
        if not corrected:
            return
        if always:
            with self._app.db() as conn:
                review.always_read_as(conn, parsed, corrected)
        else:
            self._plan.corrections[track_id] = corrected
            self._tree.item(track_id, tags=("fixed",))
            self._tree.set(track_id, "artist", corrected)
        # A new artist can make this a copy of something - judge it again.
        if self._plan.raw:
            self._replan(f"“{parsed}” is now read as “{corrected}”"
                         + (" in every playlist." if always else "."))

    # -- adding ------------------------------------------------------------

    def _on_add(self) -> None:
        if not self._plan or not self._plan.to_add:
            return
        self._set_working(True)
        self._status.busy("Adding to Discovery…")
        plan = self._plan
        self._task = self._app.runner.run(
            lambda report: self._add_work(plan),
            on_done=self._on_added,
            on_error=self._on_error,
            on_progress=self._status.busy,
            name="apply_add",
        )

    def _add_work(self, plan: discovery.AddPlan) -> discovery.AddResult:
        """Runs on a worker thread, so it opens its own connection."""
        with self._app.db() as conn:
            return discovery.apply_add(plan, conn, self._app.api())

    def _on_added(self, result: discovery.AddResult) -> None:
        self._reset()
        message = (f"Added {result.added_to_library} to the library and "
                   f"{result.added_to_playlist} to Discovery.")
        if result.corrected:
            message += f" {result.corrected} artist(s) corrected."
        self._status.idle(message)
        self._app.refresh_title()

    # -- shared ------------------------------------------------------------

    def _on_error(self, error: Exception) -> None:
        self._set_working(False)
        self._status.idle("Failed.")
        show_error(self, "Add failed", str(error))

    def _set_working(self, working: bool) -> None:
        """Lock every button while a fetch, re-plan or add is running."""
        self._working = working
        ready = bool(self._plan and self._plan.to_add) and not working
        self._check_button.configure(state=tk.DISABLED if working else tk.NORMAL)
        self._add_button.configure(state=tk.NORMAL if ready else tk.DISABLED)
        self._clear_button.configure(
            state=tk.NORMAL if self._plan and not working else tk.DISABLED)
        self._sync_toolbar()

    def _reset(self) -> None:
        self._url.delete(0, tk.END)
        self._show(None)
