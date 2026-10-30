"""
The left rail: the app's mark, one button per screen, the library at a glance,
and the way back to the guide.

Choosing a screen is all it does - it knows nothing about what the screens
contain. The numbers at the bottom are handed in by App, which already reads
them for the window title.

Public: Sidebar

Imports nothing from src/ except ui.theme.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from . import theme


class _NavItem(ctk.CTkFrame):
    """One row: an accent bar, a glyph and a name. Orange when selected."""

    def __init__(self, parent, glyph: str, text: str, command):
        super().__init__(parent, fg_color="transparent", corner_radius=10)
        self._command = command
        self._selected = False
        self._bar = ctk.CTkFrame(self, width=4, height=20, corner_radius=2,
                                 fg_color="transparent")
        self._icon = ctk.CTkLabel(self, text=glyph, width=24,
                                  font=theme.icon_font(16))
        self._label = ctk.CTkLabel(self, text=text, anchor="w",
                                   font=theme.font(14, "bold"))
        self._bar.pack(side=tk.LEFT, padx=(6, 10))
        self._icon.pack(side=tk.LEFT)
        self._label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 12),
                         pady=8)

        for widget in (self, self._bar, self._icon, self._label):
            widget.bind("<Button-1>", lambda _e: self._command())
            widget.bind("<Enter>", self._sync_hover)
            # Leave also fires when the pointer moves onto a child, so check
            # where it actually is rather than trusting the event.
            widget.bind("<Leave>", lambda _e: self.after(10, self._sync_hover))
        self.configure(cursor="hand2")
        self._paint(hover=False)

    def select(self, selected: bool) -> None:
        self._selected = selected
        self._paint(hover=False)

    def _sync_hover(self, _event=None) -> None:
        x, y = self.winfo_pointerxy()
        under = self.winfo_containing(x, y)
        mine = str(self)
        self._paint(hover=under is not None and (
            str(under) == mine or str(under).startswith(mine + ".")))

    def _paint(self, hover: bool) -> None:
        if self._selected:
            fill, accent, text, icon = (theme.RAISED, theme.ORANGE,
                                        theme.TEXT, theme.ORANGE_SOFT)
        else:
            fill = theme.SURFACE if hover else "transparent"
            accent, icon = "transparent", theme.TEXT_FAINT
            text = theme.TEXT if hover else theme.TEXT_DIM
        self.configure(fg_color=fill)
        self._bar.configure(fg_color=accent)
        self._icon.configure(text_color=icon)
        self._label.configure(text_color=text)


class Sidebar(ctk.CTkFrame):
    """
    `screens` is a list of (glyph, name). `on_select(index)` is called when
    one is clicked; the caller shows it and calls select(index) back.
    `on_help`, if given, adds "How it works" above the library numbers.
    """

    def __init__(self, parent, screens: list, on_select, on_help=None):
        super().__init__(parent, fg_color=theme.SIDEBAR, corner_radius=0,
                         width=232)
        self.pack_propagate(False)
        self._build_brand()

        ctk.CTkLabel(self, text="SCREENS", anchor="w", text_color=theme.TEXT_FAINT,
                     font=theme.font(11, "bold")).pack(fill=tk.X, padx=24,
                                                       pady=(26, 6))
        self._items = []
        for index, (glyph, name) in enumerate(screens):
            item = _NavItem(self, glyph, name, lambda i=index: on_select(i))
            item.pack(fill=tk.X, padx=12, pady=2)
            self._items.append(item)

        self._stats = {}
        self._build_stats()
        if on_help is not None:          # an action, so never selected
            _NavItem(self, "\ue897", "How it works", on_help).pack(
                side=tk.BOTTOM, fill=tk.X, padx=12)

    def select(self, index: int) -> None:
        for i, item in enumerate(self._items):
            item.select(i == index)

    def show_counts(self, counts: dict) -> None:
        """`counts` is {verdict: n}, straight from the track_verdicts view."""
        self._stats["sets"].configure(text=f"{sum(counts.values()):,}")
        self._stats["kept"].configure(text=f"{counts.get('liked', 0):,}")
        self._stats["waiting"].configure(text=f"{counts.get('pending', 0):,}")

    # -- layout ------------------------------------------------------------

    def _build_brand(self) -> None:
        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.pack(fill=tk.X, padx=20, pady=(26, 0))
        scale = ctk.ScalingTracker.get_widget_scaling(brand)
        tk.Label(brand, image=theme.icon_image(round(40 * scale)),
                 bg=theme.SIDEBAR, bd=0).pack(side=tk.LEFT)
        words = ctk.CTkFrame(brand, fg_color="transparent")
        words.pack(side=tk.LEFT, padx=(12, 0))
        ctk.CTkLabel(words, text="Discovery", anchor="w", height=24,
                     font=theme.font(19, "bold", display=True)).pack(fill=tk.X)
        ctk.CTkLabel(words, text="for SoundCloud", anchor="w", height=16,
                     text_color=theme.TEXT_FAINT,
                     font=theme.font(12)).pack(fill=tk.X)

    def _build_stats(self) -> None:
        """Three numbers at the foot of the rail, refreshed after every run."""
        box = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=14,
                           border_width=1, border_color=theme.BORDER)
        box.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=20)
        ctk.CTkLabel(box, text="LIBRARY", anchor="w", text_color=theme.TEXT_FAINT,
                     font=theme.font(11, "bold")).pack(fill=tk.X, padx=16,
                                                       pady=(12, 2))
        rows = (("sets", "sets", theme.TEXT),
                ("kept", "kept", theme.ORANGE_SOFT),
                ("waiting", "not heard yet", theme.TEXT_DIM))
        for i, (key, caption, colour) in enumerate(rows):
            row = ctk.CTkFrame(box, fg_color="transparent", height=30)
            row.pack(fill=tk.X, padx=16,
                     pady=(0, 14 if i == len(rows) - 1 else 0))
            self._stats[key] = ctk.CTkLabel(
                row, text="–", text_color=colour, anchor="w", width=64,
                font=theme.font(18, "bold", display=True))
            self._stats[key].pack(side=tk.LEFT)
            ctk.CTkLabel(row, text=caption, anchor="w", text_color=theme.TEXT_DIM,
                         font=theme.font(13)).pack(side=tk.LEFT, padx=(6, 0))
