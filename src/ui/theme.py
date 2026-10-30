"""
The look: one palette, one type family, and the ttk styling CTk cannot reach.

Neutral greys around Lead (#212121), White Smoke text, and International
Orange as a sparing accent - a wash behind the one thing to press, a bar on
the current screen, never a solid slab. Pure white and near-black were tried
first and glared. Every colour the UI uses is named here.

Public: apply, font, icon_font, style_tree, dress_window, icon_image,
        and the palette constants (BG, SURFACE, TEXT, ORANGE, ...)

Imports nothing from src/. Knows about customtkinter and ttk, nothing else.
"""

from __future__ import annotations

import base64
import struct
import sys
import tkinter as tk
import zlib
from functools import cache
from tkinter import font as tkfont, ttk

import customtkinter as ctk

# -- surfaces, darkest first -------------------------------------------------
SIDEBAR = "#1b1b1b"
BG = "#212121"          # the window - Lead
SURFACE = "#292929"     # cards and tables
RAISED = "#323232"      # entries, table headings, hover
BORDER = "#3b3b3b"
SELECT = "#474747"      # the selected table row

# -- text --------------------------------------------------------------------
TEXT = "#f5f5f5"        # White Smoke; pure white glares on grey
TEXT_DIM = "#b8b8b8"
TEXT_FAINT = "#868686"

# -- the accent: International Orange ----------------------------------------
ORANGE = "#ff5722"              # bars, progress, the mark - small things only
ORANGE_SOFT = "#ff8a65"         # orange text on grey
ORANGE_TINT = "#402921"         # a wash of orange behind accent text
ORANGE_TINT_HOVER = "#522d21"
ORANGE_EDGE = "#9b3f22"         # the border of an accent button

_TEXT_FAMILIES = ("Segoe UI Variable Text", "Segoe UI", "Helvetica")
_DISPLAY_FAMILIES = ("Segoe UI Variable Display", "Segoe UI", "Helvetica")
_ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets", "Segoe UI Symbol")

TREE_STYLE = "Discovery.Treeview"


@cache
def _installed(candidates: tuple) -> str:
    """The first family in `candidates` this machine has. Needs a Tk root."""
    have = set(tkfont.families())
    return next((name for name in candidates if name in have), candidates[-1])


def font(size: int = 13, weight: str = "normal", *, display: bool = False) -> tuple:
    """A CTk font tuple. Sizes are pixels; CTk scales them for the screen."""
    return (_installed(_DISPLAY_FAMILIES if display else _TEXT_FAMILIES),
            size, weight)


def icon_font(size: int = 16) -> tuple:
    """The Windows icon font - pass it glyphs like '\\uE710' (Add)."""
    return (_installed(_ICON_FAMILIES), size)


def apply(root: tk.Misc) -> None:
    """
    Dark mode and the palette, for every CTk widget made after this.

    CTk reads its theme when a widget is built, so this must run before the
    first one. Idempotent.
    """
    ctk.set_appearance_mode("dark")
    theme = ctk.ThemeManager.theme
    theme["CTk"]["fg_color"] = BG
    theme["CTkToplevel"]["fg_color"] = BG
    theme["CTkFrame"].update(fg_color=SURFACE, top_fg_color=RAISED,
                             border_color=BORDER, corner_radius=12)
    theme["CTkButton"].update(
        fg_color=RAISED, hover_color=BORDER, text_color=TEXT,
        text_color_disabled=TEXT_FAINT, corner_radius=8)
    theme["CTkLabel"]["text_color"] = TEXT
    theme["CTkEntry"].update(
        fg_color=RAISED, border_color=BORDER, text_color=TEXT,
        placeholder_text_color=TEXT_FAINT, corner_radius=8, border_width=1)
    theme["CTkProgressBar"].update(fg_color=RAISED, progress_color=ORANGE)
    theme["CTkScrollbar"].update(button_color="#4a4a4a",
                                 button_hover_color="#5c5c5c")
    # Switches and sliders carry the accent only in their "on" part - small
    # enough to read as a highlight across a whole panel of them.
    theme["CTkSwitch"].update(
        fg_color=BORDER, progress_color=ORANGE_EDGE, button_color=TEXT_DIM,
        button_hover_color=TEXT, text_color=TEXT)
    theme["CTkSlider"].update(
        fg_color=BORDER, progress_color=ORANGE_EDGE, button_color=ORANGE_SOFT,
        button_hover_color=TEXT)
    theme["CTkSegmentedButton"].update(
        fg_color=RAISED, selected_color=SELECT, selected_hover_color="#545454",
        unselected_color=RAISED, unselected_hover_color=BORDER,
        text_color=TEXT, border_width=2)
    theme["CTkScrollableFrame"]["label_fg_color"] = RAISED
    theme["CTkCheckBox"].update(
        fg_color=ORANGE_EDGE, hover_color=ORANGE_TINT_HOVER, border_color=TEXT_FAINT,
        checkmark_color=TEXT, text_color=TEXT, corner_radius=4, border_width=2)
    theme["CTkFont"].update(family=_installed(_TEXT_FAMILIES), size=13)
    # CTkEntry leaves text selection to Tk, which would use Windows blue.
    root.option_add("*Entry.selectBackground", ORANGE_EDGE)
    root.option_add("*Entry.selectForeground", TEXT)
    if isinstance(root, ctk.CTk):
        root.configure(fg_color=BG)
    else:
        root.configure(bg=BG)


def style_tree(widget: ctk.CTkBaseClass) -> str:
    """
    Dress ttk.Treeview to match, and return the style name to give it.

    CTk has no table, so the one ttk widget left gets its colours here. Row
    height and fonts are scaled by hand: ttk does not know about CTk's DPI
    scaling, so without this the rows are cramped on a high-DPI screen.
    """
    scale = ctk.ScalingTracker.get_widget_scaling(widget)
    family = _installed(_TEXT_FAMILIES)
    flat = dict(borderwidth=0, relief="flat")

    style = ttk.Style(widget)
    style.theme_use("clam")
    style.configure(
        TREE_STYLE, background=SURFACE, fieldbackground=SURFACE,
        foreground=TEXT, bordercolor=SURFACE, lightcolor=SURFACE,
        darkcolor=SURFACE, rowheight=round(34 * scale),
        font=(family, -round(13 * scale)), **flat)
    style.map(TREE_STYLE, background=[("selected", SELECT)],
              foreground=[("selected", TEXT)])
    # Drop the border element entirely; the card around the table is the frame.
    style.layout(TREE_STYLE, [(f"{TREE_STYLE}.treearea", {"sticky": "nswe"})])
    style.configure(
        f"{TREE_STYLE}.Heading", background=RAISED, foreground=TEXT_FAINT,
        bordercolor=RAISED, lightcolor=RAISED, darkcolor=RAISED,
        padding=(round(10 * scale), round(8 * scale)),
        font=(family, -round(11 * scale), "bold"), **flat)
    style.map(f"{TREE_STYLE}.Heading", background=[("active", RAISED)],
              foreground=[("active", TEXT_DIM)])
    return TREE_STYLE


# ---------------------------------------------------------------------------
# Window chrome
# ---------------------------------------------------------------------------

def dress_window(window: tk.Wm) -> None:
    """
    Give a window the app's icon and, on Windows 11, a title bar that matches.

    CTk swaps its own logo onto every window 200ms after it opens unless
    iconbitmap() was called, so it is called - with "", which sets nothing.
    """
    window.iconbitmap("")
    window.iconphoto(False, icon_image(64), icon_image(32))
    _tint_title_bar(window)


def _tint_title_bar(window: tk.Wm) -> None:
    """Boundary: a Windows-only DWM call. Older Windows just keeps its own."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        for attribute, colour in ((35, SIDEBAR), (36, TEXT), (34, BORDER)):
            red, green, blue = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
            value = ctypes.c_int(red | green << 8 | blue << 16)  # COLORREF
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError, tk.TclError):
        pass


# The mark: an orange tile with a white waveform - five capsule bars.
_BARS = (0.30, 0.52, 0.40, 0.60, 0.34)     # bar heights, as a share of the tile
_BAR_WIDTH, _BAR_GAP, _CORNER = 0.09, 0.055, 0.24


@cache
def icon_image(size: int) -> tk.PhotoImage:
    """The app mark as a `size`-pixel image. Drawn, so there is no asset."""
    return tk.PhotoImage(data=base64.b64encode(_png(size, _draw_mark(size))))


def _draw_mark(size: int, samples: int = 4) -> bytes:
    """RGBA pixels, anti-aliased by sampling each pixel samples x samples."""
    top, bottom = (0xFF, 0x70, 0x43), (0xF4, 0x51, 0x1E)   # around ORANGE
    pixels = bytearray()
    for py in range(size):
        shade = py / max(size - 1, 1)
        base = [round(a + (b - a) * shade) for a, b in zip(top, bottom)]
        for px in range(size):
            tile = bar = 0
            for sy in range(samples):
                for sx in range(samples):
                    x = (px + (sx + 0.5) / samples) / size
                    y = (py + (sy + 0.5) / samples) / size
                    if _in_tile(x, y):
                        tile += 1
                        bar += _in_bar(x, y)
            white = bar / tile if tile else 0
            colour = [round(c + (255 - c) * white) for c in base]
            pixels += bytes(colour) + bytes([round(255 * tile / samples ** 2)])
    return bytes(pixels)


def _in_tile(x: float, y: float) -> bool:
    dx = max(_CORNER - x, 0, x - 1 + _CORNER)
    dy = max(_CORNER - y, 0, y - 1 + _CORNER)
    return dx * dx + dy * dy <= _CORNER * _CORNER


def _in_bar(x: float, y: float) -> bool:
    step = _BAR_WIDTH + _BAR_GAP
    left = 0.5 - (len(_BARS) * step - _BAR_GAP) / 2
    index = int((x - left) // step)
    if not 0 <= index < len(_BARS):
        return False
    radius = _BAR_WIDTH / 2
    half = _BARS[index] / 2 - radius
    cx = left + index * step + radius
    cy = min(max(y, 0.5 - half), 0.5 + half)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


def _png(size: int, rgba: bytes) -> bytes:
    """The smallest valid RGBA PNG: one IHDR, one IDAT, one IEND."""
    row = size * 4
    raw = b"".join(b"\x00" + rgba[y * row:(y + 1) * row] for y in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
