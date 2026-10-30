"""
The Add screen's review: the playlist as uploaded, beside what will be added.

Left, every track in upload order, highlighted by what became of it - red in
the library, blue a duplicate, yellow from a known reposter - with a toolbar
to correct the app: mark an account, group copies, split a pair, skip a set.
Right, the parsed result exactly as it will be stored, coloured by how sure
the parser is. A star marks an upload from the performer's own account.

Draws and reports selections only; the Add screen decides what an action
does. Public: ReviewPanes, FATES

Imports services and ui only.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from services import discovery

from .. import confidence, theme
from ..widgets import Button, fmt_duration, make_tree

STAR = "★"

# Left-pane highlight per fate: (legend words, text, row tint). The order is
# the precedence when a row is several things - a reposter's copy that is
# already stored reads red, not yellow. Tints sit on theme.SURFACE;
# tests/test_add_tab.py checks every pair stays readable.
FATES = {
    discovery.LIBRARY: ("in library", "#f28b82", "#403232"),
    discovery.DUPLICATE: ("duplicate", "#8ab4f8", "#353a42"),
    "reposter": ("reposter", "#ffd54f", "#433e2e"),
    # Dimmer than any live row, but still readable: theme.TEXT_FAINT is 4:1.
    "faint": ("song or skipped", "#9a9a9a", ""),
}

# Toolbar: (action, label, label when it would undo). The screen gets the
# action name; "reposter" and "official" flip when every selected uploader
# already is one.
_ACTIONS = (("reposter", "Reposter", "Not reposter"),
            ("official", "Official", "Not official"),
            ("same", "Same set", None),
            ("different", "Not the same", None),
            ("skip", "Skip…", None))


def fate_tag(row: discovery.RawRow) -> str:
    if row.status in (discovery.SHORT, discovery.DISMISSED):
        return "faint"
    if row.status in (discovery.LIBRARY, discovery.DUPLICATE):
        return row.status
    return "reposter" if row.reposter else "add"


class ReviewPanes(ctk.CTkFrame):
    def __init__(self, parent, *, on_action, on_edit_artist, on_select):
        super().__init__(parent, fg_color="transparent")
        self._on_action = on_action
        self._on_select = on_select
        self._echo = None              # the pane whose next select event we caused
        self._quiet = False            # redrawing - select events are side effects
        panes = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=theme.BG, bd=0,
                               sashwidth=14, sashrelief=tk.FLAT, opaqueresize=True)
        panes.pack(fill=tk.BOTH, expand=True)
        self._build_raw(self._column(panes, 600, "AS UPLOADED",
                                     "Select rows, then correct the app below."))
        self._build_parsed(self._column(
            panes, 420, "WHAT WILL BE ADDED",
            "Double-click a row to fix its artist."), on_edit_artist)
        self.raw.bind("<<TreeviewSelect>>",
                      lambda _e: self._selected(self.raw, self.parsed))
        self.parsed.bind("<<TreeviewSelect>>",
                         lambda _e: self._selected(self.parsed, self.raw))

    # -- layout ------------------------------------------------------------

    @staticmethod
    def _column(panes, width: int, heading: str, hint: str) -> ctk.CTkFrame:
        column = ctk.CTkFrame(panes, fg_color=theme.BG, corner_radius=0)
        top = ctk.CTkFrame(column, fg_color="transparent")
        top.pack(fill=tk.X, pady=(0, 4))
        ctk.CTkLabel(top, text=heading, anchor="w", text_color=theme.TEXT_FAINT,
                     font=theme.font(11, "bold"), height=18).pack(side=tk.LEFT)
        ctk.CTkLabel(top, text=hint, anchor="e", text_color=theme.TEXT_FAINT,
                     font=theme.font(12), height=18).pack(side=tk.RIGHT)
        panes.add(column, stretch="always", minsize=320, width=width)
        return column

    def _build_raw(self, column) -> None:
        self.fate_labels = {}
        legend = ctk.CTkFrame(column, fg_color="transparent", height=20)
        legend.pack(fill=tk.X, pady=(0, 6))
        for fate, (words, colour, _tint) in FATES.items():
            self.fate_labels[fate] = ctk.CTkLabel(
                legend, text="", height=20, text_color=colour,
                font=theme.font(12, "bold"))
        self.fate_labels["star"] = ctk.CTkLabel(
            legend, text="", height=20, text_color=theme.TEXT_DIM,
            font=theme.font(12, "bold"))

        toolbar = ctk.CTkFrame(column, fg_color="transparent")
        toolbar.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0))
        self.buttons = {}
        for action, label, _undo in _ACTIONS:
            self.buttons[action] = Button(
                toolbar, label, lambda a=action: self._on_action(a),
                kind="secondary", height=32, width=96, state=tk.DISABLED,
                font=theme.font(12, "bold"))
            self.buttons[action].pack(side=tk.LEFT, padx=(0, 6))

        area = ctk.CTkFrame(column, fg_color="transparent")
        area.pack(fill=tk.BOTH, expand=True)
        self.raw = make_tree(area, [
            ("star", STAR, 30, tk.CENTER),
            ("uploader", "Uploader", 120, tk.W),
            ("title", "Title", 210, tk.W),
            ("length", "Length", 66, tk.E),
            ("fate", "What happened", 170, tk.W),
        ], hint="The playlist appears here, exactly as uploaded.",
            selectmode="extended")
        for fate, (_words, colour, tint) in FATES.items():
            self.raw.tag_configure(
                fate, foreground=colour, **({"background": tint} if tint else {}))

    def _build_parsed(self, column, on_edit_artist) -> None:
        self.legend = confidence.Legend(column)
        self.legend.pack(fill=tk.X, pady=(0, 6))
        area = ctk.CTkFrame(column, fg_color="transparent")
        area.pack(fill=tk.BOTH, expand=True)
        self.parsed = make_tree(area, [
            ("star", STAR, 30, tk.CENTER),
            ("artist", "Artist", 140, tk.W),
            ("title", "Title", 200, tk.W),
            ("uploader", "Uploader", 110, tk.W),
            ("length", "Length", 66, tk.E),
        ], hint="Paste a playlist URL above and press Check.",
            selectmode="extended")
        confidence.style_rows(self.parsed)
        self.parsed.bind("<Double-1>", on_edit_artist)

    # -- showing a plan ----------------------------------------------------

    def show_raw(self, plan) -> None:
        """Every playlist track, upload order, highlighted by its fate."""
        kept = set(self.raw.selection())
        self.raw.delete(*self.raw.get_children())
        counts: dict = {}
        for row in (plan.rows if plan else []):
            tag = fate_tag(row)
            counts[tag] = counts.get(tag, 0) + 1
            self.raw.insert("", tk.END, iid=row.track_id, tags=(tag,), values=(
                STAR if row.official else "", row.uploader, row.title,
                fmt_duration(row.duration_ms), row.note or "will be added"))
        self.raw.selection_set([i for i in kept if self.raw.exists(i)])
        stars = sum(r.official for r in (plan.rows if plan else []))
        for fate, label in self.fate_labels.items():
            count = stars if fate == "star" else counts.get(fate, 0)
            if not count:
                label.pack_forget()
                continue
            words = ("own upload" if fate == "star"
                     else FATES[fate][0])
            mark = STAR if fate == "star" else "●"
            label.configure(text=f"{mark}  {count} {words}")
            label.pack(side=tk.LEFT, padx=(0, 16))

    def selected(self) -> list:
        return list(self.raw.selection())

    def quietly(self, draw) -> None:
        """
        Run `draw` with mirroring off until Tk has delivered the select events
        it caused. Deleting selected rows fires one too, and arriving after the
        redraw it would wipe the selection the redraw just restored. Idle
        callbacks run only once the event queue is empty, so this is after.
        """
        self._quiet = True
        try:
            draw()
            wanted = [i for i in self.raw.selection() if self.parsed.exists(i)]
            self.parsed.selection_set(wanted)
        finally:
            self.after_idle(self._wake)

    def _wake(self) -> None:
        self._quiet, self._echo = False, None

    def sync_toolbar(self, plan, uploader_flags: dict, busy: bool) -> None:
        """
        Enable what applies to the selection. `uploader_flags` is
        {"reposter": bool, "official": bool} - True when every selected
        uploader already is one, so the button offers to undo it.
        """
        rows = {r.track_id: r for r in (plan.rows if plan else [])}
        chosen = [rows[i] for i in self.raw.selection() if i in rows]
        skipped = plan.skipped if plan else []
        dupes = ({s.track_id for s in skipped}
                 | {s.duplicate_of for s in skipped})
        groupable = [r for r in chosen if r.status in (
            discovery.ADD, discovery.DUPLICATE, discovery.LIBRARY)]
        able = {
            "reposter": bool(chosen), "official": bool(chosen),
            "same": len(groupable) >= 2,
            "different": any(r.track_id in dupes for r in chosen),
            "skip": any(r.status == discovery.ADD for r in chosen),
        }
        for action, label, undo in _ACTIONS:
            text = undo if undo and uploader_flags.get(action) else label
            self.buttons[action].configure(
                text=text, state=tk.NORMAL if able[action] and not busy
                else tk.DISABLED)

    def _selected(self, source, target) -> None:
        """
        Show the same tracks selected in the other pane.

        Tk fires a select event for every selection_set, even one made here,
        so the echo is ignored once - otherwise picking two copies on the left
        (which have no row on the right) would clear itself straight away.
        """
        if self._quiet:
            return
        if self._echo is source:
            self._echo = None
        else:
            wanted = [i for i in source.selection() if target.exists(i)]
            self._echo = target
            target.selection_set(wanted)
            if wanted:
                target.see(wanted[0])
        self._on_select()
