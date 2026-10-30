"""
Shared test fixtures.

Exists for one reason: Tk does not reliably tolerate several roots being
created and destroyed in one process. Two test modules each making their own
root is enough to produce "This probably means that tk wasn't installed
properly" from whichever one runs second - a failure that looks like a broken
environment and is really just the second root.

Any test needing a widget takes `tk_root` and builds on that one.
"""

import tkinter as tk

import pytest


@pytest.fixture(scope="session")
def tk_root():
    """The one and only Tk root, hidden, shared by every UI test."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:                      # no display available
        pytest.skip(f"Tk is unavailable here: {exc}")
    root.withdraw()
    yield root
    root.destroy()
