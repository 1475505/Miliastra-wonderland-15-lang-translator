#!/usr/bin/env python3
"""Google Material Design 3 (M3) theme for tkinter/ttk.

Provides a complete M3-inspired palette, card surfaces, tonal buttons,
filled/outlined/text button variants, Google-style checkboxes, and consistent
spacing. Built on the ``clam`` theme so every widget colour is controllable.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable container for tall option panels.

    Uses a Canvas + inner ttk.Frame. Mouse-wheel scrolling is enabled while the
    pointer is over the widget. The inner frame is widened to the canvas width
    so children can use ``sticky="ew"`` normally.
    """

    def __init__(self, parent, colors: dict[str, str] | None = None, **kw) -> None:
        super().__init__(parent, **kw)
        bg = (colors or {}).get("background", "#F7F8F9")
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                background=bg)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.bind("<Enter>", lambda _e: self.bind_all("<MouseWheel>", self._on_wheel))
        self.bind("<Leave>", lambda _e: self.unbind_all("<MouseWheel>"))

    @property
    def content(self) -> ttk.Frame:
        return self.inner

    def _on_inner_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfig(self._window, width=event.width)

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix(a: str, b: str, t: float) -> str:
    ar, ag, ab = _rgb(a)
    br, bg, bb = _rgb(b)
    return "#{:02x}{:02x}{:02x}".format(
        round(ar + (br - ar) * t),
        round(ag + (bg - ag) * t),
        round(ab + (bb - ab) * t),
    )


def _with_alpha(base: str, alpha: float) -> str:
    """Blend base colour with white using alpha."""
    return _mix("#FFFFFF", base, alpha)


def _checkbox_images(root, c: dict[str, str]):
    """Build rounded checkbox indicator images with a real checkmark.

    Returns ``(unchecked, checked)`` PhotoImages sized 18x18. The unchecked
    state is a white box with a hairline border; the checked state is a filled
    primary box with a white tick. Rounded corners are cut to the card surface
    colour so the indicator blends on white surfaces.
    """
    from tkinter import PhotoImage

    size = 18
    radius = 3

    def _line(pts: set, x0: int, y0: int, x1: int, y1: int, w: int) -> None:
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            for ox in range(-w, w + 1):
                for oy in range(-w, w + 1):
                    if ox * ox + oy * oy <= w * w + 1:
                        pts.add((x0 + ox, y0 + oy))
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    check: set[tuple[int, int]] = set()
    _line(check, 4, 9, 7, 12, 1)
    _line(check, 7, 12, 13, 5, 1)

    def _is_corner(x: int, y: int) -> bool:
        cx = min(x, size - 1 - x)
        cy = min(y, size - 1 - y)
        if cx >= radius or cy >= radius:
            return False
        ddx = radius - cx - 0.5
        ddy = radius - cy - 0.5
        return ddx * ddx + ddy * ddy > radius * radius

    def _build(filled: bool) -> PhotoImage:
        img = PhotoImage(width=size, height=size, master=root)
        rows: list[str] = []
        for y in range(size):
            row: list[str] = []
            for x in range(size):
                if _is_corner(x, y):
                    row.append(c["surface"])
                    continue
                on_edge = x == 0 or x == size - 1 or y == 0 or y == size - 1
                if filled:
                    color = c["primary"] if (x, y) not in check else "#FFFFFF"
                else:
                    color = c["border"] if on_edge else c["surface"]
                row.append(color)
            rows.append(" ".join(row))
        img.put(rows)
        return img

    return _build(False), _build(True)


def apply_m3_theme(root) -> dict[str, str]:
    # Google Material 3 baseline light palette with a refined indigo/blue accent.
    # Tonal surfaces follow the M3 surface-container ramp for subtle elevation.
    c = {
        "primary": "#0B57D0",
        "on_primary": "#FFFFFF",
        "primary_hover": "#3367D6",
        "primary_pressed": "#0842A0",
        "primary_container": "#D3E3FD",
        "on_primary_container": "#041E49",
        "secondary": "#444746",
        "on_secondary": "#FFFFFF",
        "secondary_container": "#E3F2FD",
        "on_secondary_container": "#1F1F1F",
        "tertiary": "#7D5260",
        "on_tertiary": "#FFFFFF",
        "tertiary_container": "#FFD8E4",
        "on_tertiary_container": "#633B48",
        "surface": "#FFFFFF",
        "surface_variant": "#F8F9FA",
        "surface_container_low": "#F7F8F9",
        "surface_container": "#F1F3F4",
        "surface_container_high": "#EBEDEF",
        "background": "#F7F8F9",
        "border": "#DADCE0",
        "outline": "#747775",
        "outline_variant": "#C4C7C5",
        "border_focus": "#0B57D0",
        "text": "#1F1F1F",
        "text_secondary": "#444746",
        "disabled_bg": "#F1F3F4",
        "disabled_fg": "#BDC1C6",
        "error": "#BA1A1A",
        "on_error": "#FFFFFF",
        "error_container": "#FCE8E6",
        "on_error_container": "#B3261E",
        # Warning-tonal surface used by the license/notice banner.
        "warning_container": "#FFF3CD",
        "on_warning_container": "#4A3A00",
        "warning_border": "#F0DCA0",
        # Success-tonal surface for completion hints.
        "success_container": "#E6F4EA",
        "on_success_container": "#1E4620",
    }

    from tkinter import ttk

    try:
        root.configure(background=c["background"])
    except Exception:
        pass

    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except Exception:
        pass

    # --- fonts ---
    fam = "Microsoft YaHei UI" if sys.platform == "win32" else "Segoe UI"
    mono = "Cascadia Mono" if sys.platform == "win32" else "Consolas"
    for n in ("TkDefaultFont", "TkTextFont"):
        tkfont.nametofont(n).configure(family=fam, size=10)
    tkfont.nametofont("TkFixedFont").configure(family=mono, size=10)

    # --- surfaces / backgrounds ---
    s.configure("TFrame", background=c["background"])
    s.configure("TLabel", background=c["background"], foreground=c["text"])
    s.configure("Card.TFrame", background=c["surface"], relief="flat")
    s.configure("Accent.TFrame", background=c["primary"], relief="flat")
    s.configure("Caption.TLabel", background=c["background"],
                foreground=c["text_secondary"], font=(fam, 9))
    s.configure("Subtitle.TLabel", background=c["background"],
                foreground=c["text_secondary"], font=(fam, 11))
    s.configure("Title.TLabel", background=c["background"],
                foreground=c["text"], font=(fam, 21, "bold"))
    s.configure("Headline.TLabel", background=c["background"],
                foreground=c["text"], font=(fam, 15, "bold"))
    s.configure("M3Error.TLabel", background=c["error_container"],
                foreground=c["on_error_container"], font=(fam, 15, "bold"))

    # --- M3 accessory surfaces ---
    # Small tonal "badge" (primary container) for version/variant chips.
    s.configure("Badge.TFrame", background=c["primary_container"], relief="flat")
    s.configure("Badge.TLabel", background=c["primary_container"],
                foreground=c["on_primary_container"], font=(fam, 9, "bold"))

    # card-style labelframe (elevated surface with hairline outline)
    s.configure("M3.TLabelframe", background=c["surface"],
                borderwidth=1, relief="solid",
                bordercolor=c["outline_variant"], padding=16)
    s.configure("M3.TLabelframe.Label", background=c["surface"],
                foreground=c["primary"],
                font=(fam, 10, "bold"))

    # --- buttons ---
    btn_pad = (20, 9)
    btn_font = (fam, 10, "bold")
    btn_font_reg = (fam, 10)

    # Filled primary button
    s.configure("M3.TButton", background=c["primary"],
                foreground=c["on_primary"], borderwidth=0, relief="flat",
                focusthickness=0, padding=btn_pad, font=btn_font)
    s.map("M3.TButton",
          background=[("disabled", c["disabled_bg"]),
                      ("pressed", c["primary_pressed"]),
                      ("active", c["primary_hover"])],
          foreground=[("disabled", c["disabled_fg"])])

    # Tonal button (secondary container)
    s.configure("Tonal.TButton", background=c["secondary_container"],
                foreground=c["on_secondary_container"], borderwidth=0,
                relief="flat", focusthickness=0, padding=btn_pad,
                font=btn_font)
    s.map("Tonal.TButton",
          background=[("disabled", c["disabled_bg"]),
                      ("pressed", _mix(c["secondary_container"], "#000", 0.12)),
                      ("active", _mix(c["secondary_container"], "#000", 0.06))],
          foreground=[("disabled", c["disabled_fg"])])

    # Elevated / surface button
    s.configure("Surface.TButton", background=c["surface"],
                foreground=c["text"], borderwidth=1, relief="solid",
                bordercolor=c["outline_variant"], focusthickness=0, padding=btn_pad,
                font=btn_font_reg)
    s.map("Surface.TButton",
          background=[("disabled", c["disabled_bg"]),
                      ("pressed", c["surface_container_high"]),
                      ("active", c["surface_container"])],
          foreground=[("disabled", c["disabled_fg"])])

    # Outlined button
    s.configure("Outline.TButton", background=c["surface"],
                foreground=c["primary"], borderwidth=1, relief="solid",
                bordercolor=c["primary"], focusthickness=0, padding=btn_pad,
                font=btn_font)
    s.map("Outline.TButton",
          background=[("disabled", c["surface"]),
                      ("pressed", c["primary_container"]),
                      ("active", c["primary_container"])],
          foreground=[("disabled", c["disabled_fg"])])

    # Text button (no border, primary text)
    s.configure("Text.TButton", background=c["background"],
                foreground=c["primary"], borderwidth=0, relief="flat",
                focusthickness=0, padding=(8, 6), font=btn_font)
    s.map("Text.TButton",
          background=[("disabled", c["background"]),
                      ("pressed", c["primary_container"]),
                      ("active", _with_alpha(c["primary"], 0.08))],
          foreground=[("disabled", c["disabled_fg"])])

    # Default ttk.Button
    s.configure("TButton", background=c["surface"], foreground=c["text"],
                borderwidth=1, relief="solid", bordercolor=c["outline_variant"],
                focusthickness=0, padding=btn_pad, font=btn_font_reg)
    s.map("TButton",
          background=[("disabled", c["disabled_bg"]),
                      ("pressed", c["border"]),
                      ("active", c["background"])],
          foreground=[("disabled", c["disabled_fg"])])

    # --- checkbuttons ---
    s.configure("TCheckbutton", background=c["surface"],
                foreground=c["text"], indicatorcolor=c["border"],
                focusthickness=0, padding=(2, 3))
    s.map("TCheckbutton",
          indicatorcolor=[("selected", c["primary"]),
                          ("!selected", c["border"])],
          foreground=[("disabled", c["disabled_fg"])])

    # Google-style checkbutton with a real checkmark drawn via images.
    img_empty, img_check = _checkbox_images(root, c)
    # Keep references so the PhotoImages are not garbage-collected.
    root._g_check_images = (img_empty, img_check)
    s.element_create("G.Check.indicator", "image", img_empty,
                     ("selected", img_check))
    s.layout("G.TCheckbutton", [
        ("Checkbutton.padding", {"children": [
            ("G.Check.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"children": [
                ("Checkbutton.label", {"side": "left", "sticky": ""})
            ], "expand": "1", "sticky": "nswe"})
        ], "sticky": "nswe"})
    ])
    s.configure("G.TCheckbutton", background=c["surface"],
                foreground=c["text"], borderwidth=0, relief="flat",
                focusthickness=0, padding=(4, 3))
    s.map("G.TCheckbutton",
          background=[("active", c["surface"])],
          foreground=[("disabled", c["disabled_fg"])])

    # --- radiobuttons ---
    s.configure("TRadiobutton", background=c["surface"],
                foreground=c["text"], indicatorcolor=c["border"],
                focusthickness=0, padding=(2, 3))
    s.map("TRadiobutton",
          indicatorcolor=[("selected", c["primary"]),
                          ("!selected", c["border"])],
          foreground=[("disabled", c["disabled_fg"])])

    # --- entries ---
    s.configure("TEntry", fieldbackground=c["surface"],
                foreground=c["text"], borderwidth=1, relief="solid",
                bordercolor=c["outline_variant"], padding=(10, 7),
                insertcolor=c["primary"])
    s.map("TEntry",
          bordercolor=[("focus", c["border_focus"])],
          lightcolor=[("focus", c["border_focus"])],
          darkcolor=[("focus", c["border_focus"])])

    s.configure("TSpinbox", fieldbackground=c["surface"],
                foreground=c["text"], borderwidth=1, relief="solid",
                bordercolor=c["outline_variant"], arrowcolor=c["primary"],
                padding=(10, 7))
    s.map("TSpinbox", bordercolor=[("focus", c["border_focus"])])

    # --- progressbar (thin M3 linear indicator) ---
    s.configure("Horizontal.TProgressbar", background=c["primary"],
                troughcolor=c["surface_container"], borderwidth=0, thickness=6)

    # --- scrollbar ---
    s.configure("Vertical.TScrollbar", background=c["outline_variant"],
                troughcolor=c["background"], borderwidth=0,
                arrowcolor=c["text_secondary"])
    s.map("Vertical.TScrollbar",
          background=[("active", c["text_secondary"])])
    s.configure("Horizontal.TScrollbar", background=c["outline_variant"],
                troughcolor=c["background"], borderwidth=0,
                arrowcolor=c["text_secondary"])

    # --- treeview ---
    s.configure("Treeview", background=c["surface"],
                foreground=c["text"], fieldbackground=c["surface"],
                borderwidth=0, rowheight=28)
    s.configure("Treeview.Heading", background=c["surface_container_low"],
                foreground=c["text_secondary"], borderwidth=0, relief="flat",
                font=(fam, 9, "bold"))
    s.map("Treeview",
          background=[("selected", c["primary_container"])],
          foreground=[("selected", c["on_primary_container"])])

    # --- combobox ---
    s.configure("TCombobox", fieldbackground=c["surface"],
                foreground=c["text"], borderwidth=1, relief="solid",
                bordercolor=c["outline_variant"], arrowcolor=c["primary"],
                padding=(10, 7))
    s.map("TCombobox", bordercolor=[("focus", c["border_focus"])])

    # --- separators ---
    s.configure("TSeparator", background=c["outline_variant"])

    # --- labelframe default ---
    s.configure("TLabelframe", background=c["surface"],
                borderwidth=1, relief="solid", bordercolor=c["outline_variant"],
                padding=12)
    s.configure("TLabelframe.Label", background=c["surface"],
                foreground=c["primary"], font=(fam, 10, "bold"))

    return c
