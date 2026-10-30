"""
Small shared pieces, so every screen is built from the same parts.

The old app built its own treeview and scrollbar in every tab, which is how
they drifted apart. Anything two screens need lives here instead.

Public: Button, card, page_header, PillRow, StatTile, Table, make_tree,
        fmt_duration, StatusBar

Imports nothing from src/ except ui.theme.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from . import theme

# (fg, hover, text, border) per kind of button. Primary is the one thing to
# press on a screen - an orange wash, not a solid block, which glared.
# Secondary is plain grey; ghost recedes.
_BUTTONS = {
    "primary": (theme.ORANGE_TINT, theme.ORANGE_TINT_HOVER, theme.ORANGE_SOFT,
                theme.ORANGE_EDGE),
    "secondary": (theme.RAISED, theme.BORDER, theme.TEXT, theme.BORDER),
    "ghost": ("transparent", theme.RAISED, theme.TEXT_DIM, theme.BORDER),
}
_DISABLED = ("transparent", theme.SURFACE, theme.TEXT_FAINT, theme.BORDER)

# (background, text) per pill tone.
_TONES = {
    "accent": (theme.ORANGE_TINT, theme.ORANGE_SOFT),
    "neutral": (theme.RAISED, theme.TEXT_DIM),
}


def fmt_duration(ms: int) -> str:
    """72m 30s. Sets are long, so hours would just add a zero to everything."""
    return f"{ms // 60_000}m {(ms % 60_000) // 1000:02d}s"


class Button(ctk.CTkButton):
    """
    A CTkButton that looks disabled when it is.

    CTk only greys the text of a disabled button and leaves it bright orange,
    which reads as pressable. This swaps the whole palette with the state.
    """

    def __init__(self, parent, text: str, command, kind: str = "primary",
                 state: str = tk.NORMAL, **kwargs):
        self._kind = kind
        kwargs.setdefault("height", 38)
        kwargs.setdefault("font", theme.font(13, "bold"))
        super().__init__(parent, text=text, command=command, border_width=1,
                         state=state, **self._look(state), **kwargs)

    def configure(self, require_redraw=False, **kwargs):
        if "state" in kwargs:
            kwargs = {**self._look(kwargs["state"]), **kwargs}
        super().configure(require_redraw, **kwargs)

    def _look(self, state: str) -> dict:
        fg, hover, text, border = (
            _DISABLED if state == tk.DISABLED else _BUTTONS[self._kind])
        return dict(fg_color=fg, hover_color=hover, text_color=text,
                    border_color=border)


def card(parent, **kwargs) -> ctk.CTkFrame:
    """A rounded, bordered surface to group things on."""
    options = dict(fg_color=theme.SURFACE, corner_radius=14, border_width=1,
                   border_color=theme.BORDER)
    return ctk.CTkFrame(parent, **{**options, **kwargs})


def page_header(parent, title: str, subtitle: str) -> ctk.CTkFrame:
    """The big title and one-line explanation at the top of each screen."""
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    ctk.CTkLabel(frame, text=title, anchor="w", height=36,
                 font=theme.font(28, "bold", display=True)).pack(fill=tk.X)
    ctk.CTkLabel(frame, text=subtitle, anchor="w", text_color=theme.TEXT_DIM,
                 font=theme.font(14)).pack(fill=tk.X)
    return frame


class PillRow(ctk.CTkFrame):
    """A row of rounded counters - '12 to add', '3 duplicates' - rebuilt on show()."""

    def __init__(self, parent):
        # A fixed height, or an empty row falls back to CTkFrame's 200px.
        super().__init__(parent, fg_color="transparent", height=26)

    def show(self, pills: list) -> None:
        """`pills` is a list of (text, tone); tone is accent or neutral."""
        for child in self.winfo_children():
            child.destroy()
        for text, tone in pills:
            background, colour = _TONES[tone]
            ctk.CTkLabel(self, text=text, fg_color=background, text_color=colour,
                         corner_radius=13, height=26, font=theme.font(12, "bold"),
                         ).pack(side=tk.LEFT, padx=(0, 8), ipadx=10)


class StatTile(ctk.CTkFrame):
    """One big number with a caption underneath, on a card."""

    def __init__(self, parent, caption: str, colour: str = theme.TEXT):
        super().__init__(parent, fg_color=theme.SURFACE, corner_radius=14,
                         border_width=1, border_color=theme.BORDER)
        self._value = ctk.CTkLabel(self, text="–", text_color=colour, anchor="w",
                                   font=theme.font(30, "bold", display=True))
        self._value.pack(fill=tk.X, padx=18, pady=(14, 0))
        self._caption = ctk.CTkLabel(self, text=caption, anchor="w",
                                     text_color=theme.TEXT_DIM, font=theme.font(13))
        self._caption.pack(fill=tk.X, padx=18, pady=(0, 14))

    def set(self, value: str, caption: str | None = None) -> None:
        self._value.configure(text=value)
        if caption is not None:
            self._caption.configure(text=caption)


class Table(ttk.Treeview):
    """
    A Treeview that says something when it is empty, instead of showing an
    empty dark box. The hint hides on the first insert and returns when the
    last row is deleted, so callers never manage it.
    """

    def __init__(self, master, hint: str = "", **kwargs):
        super().__init__(master, **kwargs)
        self._hint = ctk.CTkLabel(master, text=hint, fg_color=theme.SURFACE,
                                  text_color=theme.TEXT_FAINT, font=theme.font(13))
        self._sync_hint()

    def insert(self, *args, **kwargs):
        self._hint.place_forget()
        return super().insert(*args, **kwargs)

    def delete(self, *items):
        super().delete(*items)
        self._sync_hint()

    def _sync_hint(self) -> None:
        if self.get_children() or not self._hint.cget("text"):
            self._hint.place_forget()
        else:
            self._hint.place(in_=self, relx=0.5, rely=0.55, anchor=tk.CENTER)


def make_tree(parent, columns: list, hint: str = "",
              selectmode: str = "browse") -> Table:
    """
    A scrollable, read-only table on its own card, packed to fill `parent`.

    `columns` is a list of (key, heading, width, anchor). The tree column
    itself is hidden - every column here is a real one. The title column
    takes any spare width, or the artist column if there is no title.
    `selectmode="extended"` allows several rows at once (shift/ctrl-click).
    """
    frame = card(parent)
    frame.pack(fill=tk.BOTH, expand=True)

    keys = [c[0] for c in columns]
    stretchy = "title" if "title" in keys else "artist"
    tree = Table(frame, hint=hint, columns=keys, show="headings",
                 selectmode=selectmode, style=theme.style_tree(frame))
    for key, heading, width, anchor in columns:
        tree.heading(key, text=heading.upper(), anchor=anchor)
        tree.column(key, width=width, anchor=anchor, stretch=(key == stretchy))

    scroll = ctk.CTkScrollbar(frame, command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 4), pady=8)
    # Inset so the square table sits inside the card's rounded corners.
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
    return tree


class StatusBar(ctk.CTkFrame):
    """A status line with a dot, and a progress bar that shows while busy."""

    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        self._dot = ctk.CTkLabel(self, text="●", width=14, font=theme.font(11),
                                 text_color=theme.TEXT_FAINT)
        self._dot.pack(side=tk.LEFT)
        self._text = ctk.CTkLabel(self, text="Ready.", anchor="w",
                                  text_color=theme.TEXT_DIM, font=theme.font(13))
        self._text.pack(side=tk.LEFT, padx=(6, 0))
        self._bar = ctk.CTkProgressBar(self, mode="indeterminate", width=140,
                                       height=6)

    def set(self, message: str) -> None:
        self._text.configure(text=message)

    def busy(self, message: str) -> None:
        self._text.configure(text=message)
        self._dot.configure(text_color=theme.ORANGE_SOFT)
        self._bar.pack(side=tk.LEFT, padx=(14, 0))
        self._bar.start()

    def idle(self, message: str = "Ready.") -> None:
        self._bar.stop()
        self._bar.pack_forget()
        self._dot.configure(text_color=theme.TEXT_FAINT)
        self._text.configure(text=message)
