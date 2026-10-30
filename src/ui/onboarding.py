"""
First launch: connecting to SoundCloud, and what the app is for.

Someone opening this for the first time has no client id, has never heard of
the Discovery loop, and should not need the README to get going. The connect
window walks them through registering a SoundCloud app and signing in; the
guide explains the loop, and opens by itself while the library is empty.

Public: SignIn, ask_to_connect, show_guide

Imports nothing from src/ except ui. app.py hands in what touches api/, so
this module never reads config.json or talks to SoundCloud itself.
"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from dataclasses import dataclass
from typing import Callable

import customtkinter as ctk

from . import theme
from .dialogs import Dialog
from .task import TaskRunner
from .widgets import Button

_WRAP = 520


@dataclass
class SignIn:
    """What the connect window needs from api/auth, handed in by app.py."""

    save: Callable[[str, str], None]    # (client_id, secret) -> config.json
    connect: Callable[[], object]       # browser sign-in; raises on failure
    redirect_uri: str
    apps_page: str
    needs_app: bool                     # no client id and secret yet
    signed_in_before: bool = False
    client_id: str = ""


def _text(parent, text: str, colour: str = theme.TEXT_DIM, size: int = 13,
          pady=(0, 8)) -> ctk.CTkLabel:
    label = ctk.CTkLabel(parent, text=text, anchor="w", justify=tk.LEFT,
                         wraplength=_WRAP, text_color=colour,
                         font=theme.font(size))
    label.pack(fill=tk.X, pady=pady)
    return label


def _caption(parent, text: str) -> None:
    ctk.CTkLabel(parent, text=text, anchor="w", text_color=theme.TEXT_FAINT,
                 height=18, font=theme.font(11, "bold")).pack(fill=tk.X,
                                                              pady=(10, 4))


class _ConnectDialog(Dialog):
    """Register an app if needed, then sign in. Result True once connected."""

    def __init__(self, parent: tk.Misc, sign_in: SignIn):
        first = not sign_in.signed_in_before
        super().__init__(parent, "Welcome to SoundCloud Discovery" if first
                         else "Sign in to SoundCloud again")
        self._sign_in = sign_in
        self._runner = TaskRunner(parent.winfo_toplevel())
        _text(self.body, (
            "It finds long DJ sets in SoundCloud playlists and queues the ones "
            "you have not heard into a playlist of your own. To read and write "
            "your playlists it needs your permission - set up once, in about "
            "two minutes.") if first else (
            "SoundCloud has signed this app out. Sign in again to carry on."),
            pady=(0, 4))
        self._app_step = ctk.CTkFrame(self.body, fg_color="transparent")
        self._build_app_step(self._app_step)
        self._sign_in_step = ctk.CTkFrame(self.body, fg_color="transparent")
        self._sign_in_step.pack(fill=tk.X)
        _caption(self._sign_in_step, "STEP 2  ·  CONNECT YOUR ACCOUNT"
                 if sign_in.needs_app else "CONNECT YOUR ACCOUNT")
        _text(self._sign_in_step, "Press Connect. Your browser opens "
              "SoundCloud: sign in and allow access, and this window carries "
              "on by itself.")
        if sign_in.needs_app:
            self._show_app_step()
        else:
            Button(self._sign_in_step, "Use a different SoundCloud app…",
                   self._show_app_step, kind="ghost", height=30,
                   width=230).pack(anchor="w")
        self._status = _text(self.body, "", pady=(10, 0))
        _, self._connect_button = self.buttons(
            ("Quit", self.destroy, "ghost"), ("Connect", self._connect, "primary"))

    def _build_app_step(self, frame) -> None:
        _caption(frame, "STEP 1  ·  REGISTER AN APP ON SOUNDCLOUD")
        _text(frame, "1.  Open SoundCloud's app page and register a new app. "
              "Any name and website will do - it is only for you.")
        Button(frame, "Open SoundCloud's app page",
               lambda: webbrowser.open(self._sign_in.apps_page),
               kind="secondary", width=230).pack(anchor="w", pady=(0, 10))
        _text(frame, "2.  When it asks for a redirect URI, paste exactly this:")
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill=tk.X, pady=(0, 10))
        uri = ctk.CTkEntry(row, height=34, font=theme.font(13))
        uri.insert(0, self._sign_in.redirect_uri)
        uri.configure(state="readonly")
        uri.pack(side=tk.LEFT, fill=tk.X, expand=True)
        Button(row, "Copy", self._copy_uri, kind="secondary", width=80,
               height=34).pack(side=tk.LEFT, padx=(8, 0))
        _text(frame, "3.  Copy the app's client ID and client secret here:")
        self._client_id = self._field(frame, "CLIENT ID")
        self._client_id.insert(0, self._sign_in.client_id)
        self._secret = self._field(frame, "CLIENT SECRET", show="•")

    def _field(self, parent, caption: str, show: str = "") -> ctk.CTkEntry:
        ctk.CTkLabel(parent, text=caption, anchor="w", height=18,
                     text_color=theme.TEXT_FAINT,
                     font=theme.font(11, "bold")).pack(fill=tk.X)
        entry = ctk.CTkEntry(parent, height=36, font=theme.font(13), show=show)
        entry.pack(fill=tk.X, pady=(2, 8))
        return entry

    def _show_app_step(self) -> None:
        self._app_step.pack(fill=tk.X, before=self._sign_in_step)

    def _copy_uri(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._sign_in.redirect_uri)
        self._say("Redirect URI copied.")

    def _say(self, message: str, error: bool = False) -> None:
        self._status.configure(
            text=message, text_color=theme.ORANGE_SOFT if error else theme.TEXT_DIM)

    def _connect(self) -> None:
        if self._app_step.winfo_manager():
            client_id = self._client_id.get().strip()
            secret = self._secret.get().strip()
            if not client_id or not secret:
                self._say("Paste both the client ID and the client secret "
                          "first.", error=True)
                return
            try:
                self._sign_in.save(client_id, secret)
            except OSError as exc:
                self._say(f"Could not save config.json: {exc}", error=True)
                return
        self._connect_button.configure(state=tk.DISABLED)
        self._say("Waiting for you to sign in and allow access in your "
                  "browser…")
        self._runner.start()
        self._runner.run(lambda _report: self._sign_in.connect(),
                         on_done=lambda _creds: self._finish(True),
                         on_error=self._failed, name="sign_in")

    def _failed(self, error: Exception) -> None:
        self._connect_button.configure(state=tk.NORMAL)
        self._say(str(error), error=True)

    def destroy(self) -> None:
        self._runner.stop()
        super().destroy()


def ask_to_connect(parent: tk.Misc, sign_in: SignIn) -> bool:
    """True once the user is signed in; False if they quit instead."""
    return bool(_ConnectDialog(parent, sign_in).run())


# The loop, in the order it is done. Numbered because the order is the point.
_STEPS = (
    ("Add sets",
     "On Add Sets, paste the link to any SoundCloud playlist. The app keeps "
     "the sets of 20 minutes or more, drops anything you have met before, "
     "works out who really played each one and removes duplicate copies. "
     "Add to Discovery puts the rest in a private playlist called "
     "“discovery” in your SoundCloud account."),
    ("Listen in SoundCloud",
     "Play that playlist anywhere - the SoundCloud app, the website. Heart "
     "the sets you love. Delete each set from the playlist once you have "
     "heard enough of it, whether you liked it or not."),
    ("Record what you did",
     "Back here, open Clean Up and press Check Discovery. Sets you deleted "
     "are recorded as heard, sets you hearted as kept. The app never asks you "
     "to rate anything - it reads what you did."),
    ("Find more",
     "Find Sets searches SoundCloud for sets by the artists you keep and the "
     "people around them. Taste shows who you keep, and who you keep "
     "skipping."),
)


class _GuideDialog(Dialog):
    """The four steps. Result "import" if the user asked to import likes."""

    def __init__(self, parent: tk.Misc):
        super().__init__(parent, "How SoundCloud Discovery works")
        for number, (title, text) in enumerate(_STEPS, 1):
            self._step(number, title, text)

        tip = ctk.CTkFrame(self.body, fg_color=theme.RAISED, corner_radius=10)
        tip.pack(fill=tk.X, pady=(10, 0))
        ctk.CTkLabel(
            tip, anchor="w", justify=tk.LEFT, wraplength=_WRAP - 200,
            text_color=theme.TEXT_DIM, font=theme.font(13),
            text="Already hearted long sets on SoundCloud? Import them, so "
                 "Find Sets has artists to start from.").pack(
                     side=tk.LEFT, fill=tk.X, expand=True, padx=14, pady=12)
        Button(tip, "Import my liked sets", lambda: self._finish("import"),
               kind="secondary", width=170).pack(side=tk.RIGHT, padx=12)
        _text(self.body, "Open this again any time from How it works, at the "
              "bottom of the sidebar.", colour=theme.TEXT_FAINT, size=12,
              pady=(12, 0))
        self.buttons(("Got it", self.destroy, "primary"))

    def _step(self, number: int, title: str, text: str) -> None:
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill=tk.X, pady=(0, 12))
        ctk.CTkLabel(row, text=str(number), width=28, height=28,
                     corner_radius=14, fg_color=theme.ORANGE_TINT,
                     text_color=theme.ORANGE_SOFT,
                     font=theme.font(13, "bold")).pack(side=tk.LEFT, anchor="n")
        words = ctk.CTkFrame(row, fg_color="transparent")
        words.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 0))
        ctk.CTkLabel(words, text=title, anchor="w", height=22,
                     font=theme.font(14, "bold")).pack(fill=tk.X)
        ctk.CTkLabel(words, text=text, anchor="w", justify=tk.LEFT,
                     wraplength=_WRAP - 40, text_color=theme.TEXT_DIM,
                     font=theme.font(13)).pack(fill=tk.X)


def show_guide(parent: tk.Misc) -> str | None:
    """Show how the app works. Returns "import" if the user asked to."""
    return _GuideDialog(parent).run()
