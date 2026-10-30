"""
The Find screen's left panel: where to search, and what to keep.

Turns its controls into a finder.FindOptions and does nothing else - it never
starts a search or touches the queue. The defaults are what a first search
should use without anyone touching anything.

Public: OptionsPanel

Imports services and ui only.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from services import finder

from .. import theme

# Each choice row: shown text -> value handed to the search.
_MIN_LENGTH = {"20m": 20, "30m": 30, "45m": 45, "60m": 60}
_MAX_LENGTH = {"Any": 0, "90m": 90, "2h": 120, "3h": 180}
_UPLOADED = {"Week": 7, "Month": 31, "Year": 365, "Any": 0}
_LIKES = {"Any": 0, "10+": 10, "50+": 50, "250+": 250}
_PER_ARTIST = {"1": 1, "2": 2, "3": 3, "5": 5}
_NEW_TO_YOU = {"Any": None, "Once at most": 1, "Never": 0}
_DEPTH = {"Quick": "quick", "Normal": "normal", "Deep": "deep"}
_EXCLUDE = "podcast, radio show, episode"


def _words(text: str) -> tuple:
    return tuple(w.strip() for w in text.split(",") if w.strip())


class OptionsPanel(ctk.CTkScrollableFrame):
    def __init__(self, parent):
        super().__init__(parent, width=292, fg_color=theme.SURFACE,
                         corner_radius=14, border_width=1,
                         border_color=theme.BORDER)
        self._sources = {}
        self._build_sources()
        self._new_to_you = self._choice(
            "ARTIST ALREADY IN YOUR LIBRARY", _NEW_TO_YOU, "Any",
            hint="In a b2b, one new performer is enough.")
        self._seeds = self._slider("SEARCH FROM YOUR TOP", 5, 60, 25, "artists")
        self._depth = self._choice(
            "SEARCH DEPTH", _DEPTH, "Normal",
            hint="Deep reads more of every source - more sets, about twice "
                 "as long.")
        self._min_length = self._choice("SHORTEST SET", _MIN_LENGTH, "20m")
        self._max_length = self._choice("LONGEST SET", _MAX_LENGTH, "Any")
        self._uploaded = self._choice("UPLOADED IN THE LAST", _UPLOADED, "Year")
        self._likes = self._choice("LIKES ON SOUNDCLOUD", _LIKES, "Any")
        self._count = self._slider("HOW MANY TO FIND", 10, 100, 30, "sets")
        self._per_artist = self._choice("MOST FROM ONE ARTIST", _PER_ARTIST, "2")
        self._include = self._entry("MUST MENTION ONE OF",
                                    "e.g. b2b, live, dubstep", "")
        self._exclude = self._entry("MUST NOT MENTION", "", _EXCLUDE)
        self._skip_stop = self._switch(
            "Skip artists on your stop list", True, top=18)
        self._playable = self._switch("Full tracks only, no previews", True)

    # -- reading -----------------------------------------------------------

    def options(self) -> finder.FindOptions:
        filters = finder.Filters(
            min_minutes=_MIN_LENGTH[self._min_length.get()],
            max_minutes=_MAX_LENGTH[self._max_length.get()],
            within_days=_UPLOADED[self._uploaded.get()],
            min_likes=_LIKES[self._likes.get()],
            include_words=_words(self._include.get()),
            exclude_words=_words(self._exclude.get()),
            playable_only=self._playable.get(),
            max_results=int(self._count.get()),
            per_artist=_PER_ARTIST[self._per_artist.get()],
            max_known_sets=_NEW_TO_YOU[self._new_to_you.get()],
        )
        return finder.FindOptions(
            sources=frozenset(k for k, v in self._sources.items() if v.get()),
            seed_artists=int(self._seeds.get()),
            skip_stop_list=self._skip_stop.get(),
            depth=_DEPTH[self._depth.get()],
            filters=filters)

    # -- building ----------------------------------------------------------

    def _heading(self, text: str, top: int = 18) -> None:
        ctk.CTkLabel(self, text=text, anchor="w", height=18,
                     text_color=theme.TEXT_FAINT, font=theme.font(11, "bold"),
                     ).pack(fill=tk.X, padx=14, pady=(top, 6))

    def _build_sources(self) -> None:
        self._heading("SEARCH IN", top=12)
        for key, (label, description) in finder.SOURCES.items():
            var = tk.BooleanVar(value=True)
            self._sources[key] = var
            ctk.CTkSwitch(self, text=label, variable=var,
                          font=theme.font(13, "bold")).pack(
                              anchor="w", padx=14, pady=(4, 0))
            ctk.CTkLabel(self, text=description, anchor="w", justify=tk.LEFT,
                         wraplength=200, text_color=theme.TEXT_FAINT,
                         font=theme.font(12)).pack(fill=tk.X, padx=(56, 14))

    def _choice(self, heading: str, choices: dict, default: str,
                hint: str = ""):
        self._heading(heading)
        control = ctk.CTkSegmentedButton(self, values=list(choices),
                                         font=theme.font(12, "bold"), height=30)
        control.set(default)
        control.pack(fill=tk.X, padx=14)
        if hint:
            ctk.CTkLabel(self, text=hint, anchor="w", justify=tk.LEFT,
                         wraplength=236, text_color=theme.TEXT_FAINT,
                         font=theme.font(12)).pack(fill=tk.X, padx=14,
                                                   pady=(4, 0))
        return control

    def _slider(self, heading: str, low: int, high: int, default: int,
                unit: str) -> tk.IntVar:
        self._heading(heading)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill=tk.X, padx=14)
        value = tk.IntVar(value=default)
        shown = ctk.CTkLabel(row, text=f"{default} {unit}", width=74, anchor="e",
                             font=theme.font(13, "bold"))
        ctk.CTkSlider(row, from_=low, to=high, number_of_steps=(high - low) // 5,
                      variable=value,
                      command=lambda v: shown.configure(text=f"{int(v)} {unit}"),
                      ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        shown.pack(side=tk.LEFT)
        return value

    def _entry(self, heading: str, placeholder: str, default: str):
        self._heading(heading)
        entry = ctk.CTkEntry(self, height=34, font=theme.font(13),
                             placeholder_text=placeholder or None)
        if default:
            entry.insert(0, default)
        entry.pack(fill=tk.X, padx=14)
        return entry

    def _switch(self, text: str, default: bool, top: int = 8) -> tk.BooleanVar:
        var = tk.BooleanVar(value=default)
        ctk.CTkSwitch(self, text=text, variable=var, font=theme.font(13)).pack(
            anchor="w", padx=14, pady=(top, 0))
        return var
