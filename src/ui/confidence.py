"""
How sure the parser was about each artist, drawn the same way on every screen.

Add Sets and Find both show a plan whose rows are coloured green to red by
how the artist was worked out, with a legend and a "where did this name come
from" explanation. This is the one copy of that, so the two screens cannot
come to disagree about what a colour means. The levels themselves come from
discovery.CONFIDENCE_ORDER - nothing here decides confidence.

Public: CONFIDENCE, FIXED, RANK, level_of, style_rows, explain, Legend

Imports services and ui only.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from services import discovery

from . import theme

# (legend label, text, row tint, explanation) per level. Each level says what
# the parser DID, never how the name reads. Across the 968 tracks already in
# the library the share a human later had to correct rises cleanly down this
# list - 2%, 25%, 41%, 100% - which is what makes the colours worth trusting.
# See docs/DOMAIN.md. Tints sit on theme.SURFACE, the table's own background;
# tests/test_add_tab.py checks every pair stays readable.
CONFIDENCE = {
    discovery.CERTAIN: (
        "uploader's own name", "#81c784", "",
        "The account that posted this is the artist - nothing was parsed. "
        "Green means the app did not guess, NOT that the name was checked. "
        "The one way it is wrong is an unregistered channel that posts other "
        "people's sets; if that is what this is, register it once with "
        "scripts/manage.py repost \"NAME\" and every future set from it gets "
        "parsed properly."),
    discovery.LIKELY: (
        "read off the title", "#ffd54f", "#433e2e",
        "A registered repost account posted this, so the performer was cut "
        "out of the title."),
    discovery.UNSURE: (
        "b2b split from title", "#ffa94d", "#43382d",
        "The title looked like a b2b, so it was split into several "
        "performers. This is the least reliable split the parser makes."),
    discovery.GUESS: (
        "couldn't parse", "#f28b82", "#403232",
        "The parser found nothing it could use. The name shown is a "
        "placeholder and is almost certainly wrong."),
}
# A row you have typed yourself outranks anything the parser decided.
FIXED = ("#a5d6a7", "#3d453d")

# Worst first, which is both the legend order and the row order.
RANK = {level: i for i, level in enumerate(discovery.CONFIDENCE_ORDER)}


def level_of(plan, track_id: str) -> str:
    """A track missing from the plan's grades is drawn as CERTAIN, not lost."""
    return plan.confidence.get(track_id, discovery.CERTAIN)


def style_rows(tree) -> None:
    """Give a table one tag per level, plus 'fixed' for a typed correction."""
    for level, (_, colour, tint, _explain) in CONFIDENCE.items():
        tree.tag_configure(
            level, foreground=colour, **({"background": tint} if tint else {}))
    tree.tag_configure("fixed", foreground=FIXED[0], background=FIXED[1])


def explain(plan, track_id: str) -> str:
    """
    Where a row's artist came from, whatever colour it is. On a red row that
    is the whole question; on a green one it is the reassurance that there
    is nothing to second-guess.
    """
    reason = CONFIDENCE[level_of(plan, track_id)][3]
    flagged = next((u.reason for u in plan.needs_review
                    if u.track_id == track_id), "")
    return f"{reason}  ({flagged})" if flagged else reason


class Legend(ctk.CTkFrame):
    """
    A swatch per level with its count, hidden while there is nothing to count.

    Colour alone would be a code the user has to remember, and on a red row
    the whole point is knowing why before deciding whether to retype it.
    """

    def __init__(self, parent):
        # Fixed height, and swatches hidden rather than the row, so the layout
        # does not reshuffle every time a plan arrives.
        super().__init__(parent, fg_color="transparent", height=20)
        self.labels = {
            level: ctk.CTkLabel(self, text="", height=20,
                                font=theme.font(12, "bold"),
                                text_color=CONFIDENCE[level][1])
            for level in discovery.CONFIDENCE_ORDER}

    def show(self, plan) -> None:
        counts: dict = {}
        for track in (plan.to_add if plan else []):
            level = level_of(plan, track["track_id"])
            counts[level] = counts.get(level, 0) + 1
        for level, label in self.labels.items():
            count = counts.get(level, 0)
            if not count:            # a zero is noise; hide the swatch instead
                label.pack_forget()
                continue
            label.configure(text=f"●  {count} {CONFIDENCE[level][0]}")
            label.pack(side=tk.LEFT, padx=(0, 20))
