"""
The Taste screen: what you keep, and who to stop queueing.

Two queries and no modelling. This replaces a 366-line Insights tab that tried
to infer preference from data that could not support it; see docs/DECISIONS.md.

Public: TasteTab

Imports data and ui only.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from data import tracks as track_store

from .. import theme
from ..widgets import Button, StatTile, make_tree, page_header


class TasteTab(ctk.CTkFrame):
    def __init__(self, parent: tk.Misc, app):
        super().__init__(parent, fg_color="transparent")
        self._app = app
        self._build()

    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill=tk.X)
        page_header(header, "Taste",
                    "Who you keep, and who to stop queueing.").pack(
                        side=tk.LEFT, fill=tk.X, expand=True)
        Button(header, "Refresh", self.refresh, kind="ghost", width=100).pack(
            side=tk.RIGHT, anchor=tk.S)

        tiles = ctk.CTkFrame(self, fg_color="transparent")
        tiles.pack(fill=tk.X, pady=(20, 0))
        self._kept_tile = StatTile(tiles, "artists you have kept", theme.ORANGE_SOFT)
        self._rate_tile = StatTile(tiles, "keep rate")
        self._skip_tile = StatTile(tiles, "artists to stop queueing")
        for i, tile in enumerate(
                (self._kept_tile, self._rate_tile, self._skip_tile)):
            tile.grid(row=0, column=i, sticky="nsew",
                      padx=(0 if i == 0 else 7, 0 if i == 2 else 7))
            tiles.grid_columnconfigure(i, weight=1, uniform="tile")

        ctk.CTkLabel(
            self, anchor="w", text_color=theme.TEXT_FAINT, font=theme.font(12),
            text="A name here that looks wrong is probably an unregistered "
                 "festival account, not an artist you dislike.",
        ).pack(fill=tk.X, side=tk.BOTTOM, pady=(12, 0))

        # Classic PanedWindow rather than ttk: it takes a background colour,
        # so the sash between the tables is just a gap in the dark.
        panes = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=theme.BG, bd=0,
                               sashwidth=16, sashrelief=tk.FLAT, opaqueresize=True)
        panes.pack(fill=tk.BOTH, expand=True, pady=(16, 0))
        kept = self._column(panes, "Sets you kept")
        skip = self._column(panes, "Heard repeatedly, never kept")

        self._kept = make_tree(kept, [
            ("liked", "Kept", 70, tk.E),
            ("heard", "Heard", 70, tk.E),
            ("artist", "Artist", 220, tk.W),
        ], hint="Nothing kept yet. Heart sets in SoundCloud, then press\n"
                "Check Discovery on Clean Up.")
        self._skip = make_tree(skip, [
            ("heard", "Heard", 70, tk.E),
            ("artist", "Artist", 220, tk.W),
        ], hint="No one heard often enough to judge yet.")

    @staticmethod
    def _column(panes: tk.PanedWindow, title: str) -> ctk.CTkFrame:
        """One side of the split: a small heading over a table."""
        column = ctk.CTkFrame(panes, fg_color=theme.BG, corner_radius=0)
        ctk.CTkLabel(column, text=title, anchor="w", font=theme.font(15, "bold"),
                     ).pack(fill=tk.X, pady=(0, 8))
        panes.add(column, stretch="always", minsize=260)
        return column

    def refresh(self) -> None:
        """Cheap enough to run synchronously - it is two GROUP BYs."""
        with self._app.db() as conn:
            kept = track_store.artist_taste(conn)
            skipped = track_store.artists_to_skip(conn)
            counts = track_store.verdict_counts(conn)

        self._kept.delete(*self._kept.get_children())
        self._skip.delete(*self._skip.get_children())
        for row in kept:
            self._kept.insert("", tk.END, values=(
                row["liked"], row["heard"], row["artist"]))
        for row in skipped:
            self._skip.insert("", tk.END, values=(row["heard"], row["artist"]))

        liked = counts.get("liked", 0)
        decided = liked + counts.get("skipped", 0)
        rate = f"{100 * liked / decided:.0f}%" if decided else "n/a"
        wasted = sum(row["heard"] for row in skipped)
        self._kept_tile.set(f"{len(kept):,}")
        self._rate_tile.set(rate, f"keep rate  ·  {liked:,} of {decided:,} decided")
        self._skip_tile.set(
            f"{len(skipped):,}", f"artists to stop queueing  ·  {wasted:,} sets")
