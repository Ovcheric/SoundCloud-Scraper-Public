"""
Small modal dialogs, in the app's own colours.

Native message boxes are white on Windows and flash bright over a dark window,
so questions and errors asked from inside the window come from here. Startup
errors still use tkinter.messagebox: there is no window to match yet, and a
crash report must not depend on the code that may be what crashed.

Public: Dialog, ask_artist, ask_dismissal, ask_yes_no, show_error

Imports nothing from src/ except ui.theme and ui.widgets.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from . import theme
from .widgets import Button

_WRAP = 460


def _caption(parent, text: str) -> None:
    ctk.CTkLabel(parent, text=text, anchor="w", text_color=theme.TEXT_FAINT,
                 height=18, font=theme.font(11, "bold")).pack(fill=tk.X)


class Dialog(ctk.CTkToplevel):
    """A modal window: a heading, a body the subclass fills, a button row."""

    def __init__(self, parent: tk.Misc, heading: str):
        super().__init__(parent)
        self.result = None
        self._parent = parent.winfo_toplevel()
        self.title(heading)
        self.resizable(False, False)
        # Before the main window is shown - first-launch setup - a dialog
        # transient to the hidden root would be hidden with it.
        if self._parent.winfo_viewable():
            self.transient(self._parent)
        theme.dress_window(self)

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill=tk.BOTH, expand=True, padx=26, pady=22)
        ctk.CTkLabel(self.body, text=heading, anchor="w",
                     font=theme.font(20, "bold", display=True)).pack(
                         fill=tk.X, pady=(0, 14))
        self.bind("<Escape>", lambda _e: self.destroy())

    def buttons(self, *specs) -> list:
        """
        `specs` are (text, command, kind), listed left to right. Returns the
        buttons in the same order, for a caller that disables one.
        """
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill=tk.X, pady=(18, 0))
        made = []
        for text, command, kind in reversed(specs):
            made.append(Button(row, text, command, kind=kind, width=112))
            made[-1].pack(side=tk.RIGHT, padx=(10, 0))
        return made[::-1]

    def run(self):
        """Centre over the main window, block until closed, return result."""
        self.update_idletasks()
        if self._parent.winfo_viewable():
            left, top = self._parent.winfo_rootx(), self._parent.winfo_rooty()
            width, height = self._parent.winfo_width(), self._parent.winfo_height()
        else:                       # nothing to centre over yet: the screen
            left, top = 0, 0
            width, height = self.winfo_screenwidth(), self.winfo_screenheight()
        # Requested size, not winfo_width(): until it is first shown a CTk
        # window reports its 200px default, which pushed every dialog off-centre.
        x = left + (width - self.winfo_reqwidth()) // 2
        y = top + (height - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.wait_window(self)
        return self.result

    def _finish(self, result) -> None:
        self.result = result
        self.destroy()


class _ArtistDialog(Dialog):
    """Edit the performer for one track, with the title visible for context."""

    def __init__(self, parent: tk.Misc, track_title: str, current: str,
                 reason: str = "", alias_of: str = ""):
        super().__init__(parent, "Correct the artist")
        self.remember = False

        _caption(self.body, "TRACK")
        ctk.CTkLabel(self.body, text=track_title, anchor="w", justify=tk.LEFT,
                     wraplength=_WRAP, font=theme.font(14, "bold")).pack(
                         fill=tk.X, pady=(2, 14))

        # The caller words this - it explains where the name came from, which
        # is not always a complaint, so the dialog adds no label of its own.
        if reason:
            note = ctk.CTkFrame(self.body, fg_color=theme.RAISED,
                                corner_radius=10)
            note.pack(fill=tk.X, pady=(0, 16))
            ctk.CTkLabel(note, text=reason, anchor="w", justify=tk.LEFT,
                         wraplength=_WRAP - 28, text_color=theme.TEXT_DIM,
                         font=theme.font(13)).pack(fill=tk.X, padx=14, pady=10)

        _caption(self.body, "ARTIST  ·  COMMA-SEPARATED FOR A B2B")
        self._entry = ctk.CTkEntry(self.body, width=_WRAP, height=40,
                                   font=theme.font(14))
        self._entry.insert(0, current)
        self._entry.pack(fill=tk.X, pady=(4, 6))
        self._entry.bind("<Return>", lambda _e: self._ok())

        ctk.CTkLabel(
            self.body, anchor="w", justify=tk.LEFT, wraplength=_WRAP,
            text="Saved as your correction — the resolver can never overwrite it.",
            text_color=theme.TEXT_FAINT, font=theme.font(12)).pack(fill=tk.X)
        self._always = tk.BooleanVar(value=False)
        if alias_of:
            # A duo's shared name (LEVELTRONICS, ALLDAT) recurs in every
            # playlist; one correction should cover all of them.
            ctk.CTkCheckBox(
                self.body, variable=self._always, font=theme.font(13),
                text=f"Always read “{alias_of}” as this - for every playlist",
            ).pack(anchor="w", pady=(12, 0))

        self.buttons(("Cancel", self.destroy, "ghost"),
                     ("Save", self._ok, "primary"))
        # Focus after CTk has finished re-showing the window for its dark
        # title bar, or the entry loses it again immediately.
        self.after(120, self._focus_entry)

    def _focus_entry(self) -> None:
        self._entry.focus_force()
        self._entry.select_range(0, tk.END)

    def _ok(self) -> None:
        self.remember = self._always.get()
        value = self._entry.get().strip()
        self._finish(value or None)


def ask_artist(parent: tk.Misc, track_title: str, current: str,
               reason: str = "", alias_of: str = "", on_always=None) -> str | None:
    """
    Ask for a corrected artist. Returns the new name, or None if the user
    cancelled or did not change anything.

    With `alias_of`, the dialog also offers "always read this name as the
    new one"; if ticked, `on_always(new_name)` is called before returning.
    """
    dialog = _ArtistDialog(parent, track_title, current, reason, alias_of)
    answer = dialog.run()
    if not answer or answer == current:
        return None
    if dialog.remember and on_always:
        on_always(answer)
    return answer


class _DismissDialog(Dialog):
    """One click per reason - or a number key - so removing stays quick."""

    def __init__(self, parent: tk.Misc, count: int, reasons: dict,
                 learns: str, performers: list, not_now: str):
        noun = "set" if count == 1 else f"{count} sets"
        super().__init__(parent, f"Remove {noun}")
        ctk.CTkLabel(
            self.body, anchor="w", justify=tk.LEFT, wraplength=_WRAP,
            text_color=theme.TEXT_DIM, font=theme.font(13),
            text="Why? The reason is remembered, so they are never offered "
                 "again.").pack(fill=tk.X, pady=(0, 12))

        for i, (code, label) in enumerate(reasons.items(), 1):
            Button(self.body, f"{i}    {label}",
                   lambda c=code: self._choose(c), kind="secondary",
                   anchor="w", height=36).pack(fill=tk.X, pady=3)
            self.bind(str(i), lambda e, c=code: self._on_key(e, c))
            if code == learns and performers:
                shown = ", ".join(performers[:3]) + (
                    f" +{len(performers) - 3} more" if len(performers) > 3 else "")
                ctk.CTkLabel(self.body, anchor="w", text_color=theme.TEXT_FAINT,
                             font=theme.font(12), height=18,
                             text=f"      also leaves {shown} out of future "
                                  "searches").pack(fill=tk.X, pady=(0, 4))

        _caption(self.body, "NOTE  ·  OPTIONAL")
        self._note = ctk.CTkEntry(self.body, height=36, font=theme.font(13))
        self._note.pack(fill=tk.X, pady=(4, 0))
        self.buttons(("Not now - may come back", lambda: self._choose(not_now),
                      "ghost"),
                     ("Cancel", self.destroy, "ghost"))
        self.focus_force()

    def _on_key(self, event, code: str) -> None:
        # A digit typed into the note is part of the note, not a choice.
        if not isinstance(event.widget, tk.Entry):
            self._choose(code)

    def _choose(self, code: str) -> None:
        self._finish((code, self._note.get().strip()))


def ask_dismissal(parent: tk.Misc, count: int, reasons: dict, learns: str,
                  performers: list, not_now: str) -> tuple | None:
    """
    Ask why sets are being removed. Returns (reason_code, note), or None if
    the user cancelled. `reasons` is {code: label}; the `learns` reason gets a
    line naming the `performers` it will leave out of later searches.
    """
    return _DismissDialog(parent, count, reasons, learns, performers,
                          not_now).run()


def _message(parent: tk.Misc, heading: str, message: str) -> Dialog:
    dialog = Dialog(parent, heading)
    ctk.CTkLabel(dialog.body, text=message, anchor="w", justify=tk.LEFT,
                 wraplength=_WRAP, text_color=theme.TEXT_DIM,
                 font=theme.font(14)).pack(fill=tk.X)
    return dialog


def ask_yes_no(parent: tk.Misc, heading: str, message: str,
               confirm: str = "Yes") -> bool:
    """A question with one orange answer. Escape or closing means no."""
    dialog = _message(parent, heading, message)
    dialog.buttons(("Cancel", dialog.destroy, "ghost"),
                   (confirm, lambda: dialog._finish(True), "primary"))
    return bool(dialog.run())


def show_error(parent: tk.Misc, heading: str, message: str) -> None:
    dialog = _message(parent, heading, message)
    dialog.buttons(("OK", dialog.destroy, "secondary"))
    dialog.run()
