"""A Carbon-inspired light theme for the desktop translator.

Uses the Carbon gray/blue palette, square controls and visible keyboard focus.
This is a Tk implementation; it does not imply IBM affiliation.
"""
import sys
from tkinter import font as tkfont, ttk
from m3_theme import ScrollableFrame


def apply_carbon_theme(root):
    c = {
        "background": "#f4f4f4", "surface": "#ffffff", "text": "#161616",
        "text_secondary": "#525252", "primary": "#0f62fe", "error": "#da1e28",
        "outline_variant": "#8d8d8d", "border": "#e0e0e0",
        "on_success_container": "#198038",
    }
    root.configure(background=c["background"])
    s = ttk.Style(root)
    s.theme_use("clam")
    family = "Microsoft YaHei UI" if sys.platform == "win32" else "DejaVu Sans"
    scale = max(1, float(root.tk.call("tk", "scaling")) / (96 / 72))
    px = lambda n: round(n * scale)
    for name in ("TkDefaultFont", "TkTextFont"):
        tkfont.nametofont(name).configure(family=family, size=10)
    tkfont.nametofont("TkFixedFont").configure(family="Consolas", size=10)
    s.configure(".", font=(family, 10), background=c["background"], foreground=c["text"])
    for name, bg in (("TFrame", c["background"]), ("Card.TFrame", "#ffffff"),
                     ("Shell.TFrame", "#161616"), ("Line.TFrame", "#e0e0e0"),
                     ("Accent.TFrame", "#0f62fe")):
        s.configure(name, background=bg, borderwidth=0, relief="flat")
    labels = {
        "TLabel": ("#f4f4f4", "#161616", 10, ""),
        "Caption.TLabel": ("#f4f4f4", "#525252", 9, ""),
        "Title.TLabel": ("#f4f4f4", "#161616", 23, ""),
        "Headline.TLabel": ("#f4f4f4", "#161616", 14, ""),
        "Eyebrow.TLabel": ("#f4f4f4", "#525252", 9, ""),
        "Card.TLabel": ("#ffffff", "#161616", 10, ""),
        "CardCaption.TLabel": ("#ffffff", "#525252", 9, ""),
        "Section.TLabel": ("#ffffff", "#161616", 12, "bold"),
        "Metric.TLabel": ("#ffffff", "#161616", 20, ""),
        "Status.TLabel": ("#ffffff", "#161616", 16, ""),
        "Success.Status.TLabel": ("#ffffff", "#198038", 16, ""),
        "Paused.Status.TLabel": ("#ffffff", "#8a3800", 16, ""),
        "Error.Status.TLabel": ("#ffffff", "#da1e28", 16, ""),
        "Error.CardCaption.TLabel": ("#ffffff", "#da1e28", 9, ""),
        "Shell.TLabel": ("#161616", "#f4f4f4", 11, "bold"),
        "ShellCaption.TLabel": ("#161616", "#c6c6c6", 9, ""),
        "NavCaption.TLabel": ("#ffffff", "#6f6f6f", 9, ""),
    }
    for name, (bg, fg, size, weight) in labels.items():
        s.configure(name, background=bg, foreground=fg,
                    font=(family, size, weight) if weight else (family, size))
    # Every action uses the same square footprint; focus is separate from hover.
    variants = {
        "M3.TButton": ("#0f62fe", "#ffffff", "#0353e9", (16, 12)),
        "Tonal.TButton": ("#393939", "#ffffff", "#474747", (16, 12)),
        "Compact.TButton": ("#f4f4f4", "#0f62fe", "#e0e0e0", (12, 9)),
        "Text.TButton": ("#ffffff", "#0f62fe", "#e8e8e8", (8, 8)),
        "Shell.TButton": ("#161616", "#ffffff", "#353535", (16, 10)),
        "Nav.TButton": ("#ffffff", "#525252", "#e8e8e8", (12, 14)),
        "Active.Nav.TButton": ("#e8e8e8", "#0f62fe", "#e0e0e0", (12, 14)),
    }
    for name, (bg, fg, hover, padding) in variants.items():
        s.configure(name, background=bg, foreground=fg, borderwidth=1, relief="flat",
                    bordercolor=bg, lightcolor=bg, darkcolor=bg,
                    focusthickness=1, focuscolor=fg, anchor="w",
                    padding=tuple(px(n) for n in padding), font=(family, 10), width=0)
        s.map(name, background=[("disabled", "#c6c6c6"), ("pressed", hover), ("active", hover)],
              foreground=[("disabled", "#6f6f6f")],
              bordercolor=[("focus", "#0f62fe"), ("active", hover)])
    for name in ("Nav.TButton", "Active.Nav.TButton"):
        s.map(name, background=[("disabled", "#ffffff"), ("active", "#e8e8e8")],
              foreground=[("disabled", "#8d8d8d")])
    s.configure("Compact.TMenubutton", padding=(px(12), px(9)), background="#f4f4f4",
                foreground="#0f62fe", borderwidth=0, relief="flat", width=0)
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        s.configure(name, fieldbackground="#f4f4f4", foreground="#161616",
                    insertcolor="#161616", bordercolor="#8d8d8d", lightcolor="#f4f4f4",
                    darkcolor="#8d8d8d", borderwidth=1, relief="flat",
                    padding=(px(10), px(9)), arrowsize=px(14))
        s.map(name, fieldbackground=[("disabled", "#e0e0e0"), ("readonly", "#f4f4f4")],
              foreground=[("disabled", "#6f6f6f")],
              bordercolor=[("focus", "#0f62fe")], lightcolor=[("focus", "#0f62fe")],
              darkcolor=[("focus", "#0f62fe")])
    s.configure("G.TCheckbutton", background="#ffffff", foreground="#161616",
                padding=(0, px(4)), indicatorsize=px(16), indicatormargin=(0, 0, px(8), 0))
    s.map("G.TCheckbutton", background=[("active", "#ffffff")],
          foreground=[("disabled", "#6f6f6f")],
          indicatorbackground=[("selected", "#161616"), ("!selected", "#ffffff")],
          indicatorforeground=[("selected", "#ffffff")])
    s.configure("Horizontal.TProgressbar", background="#0f62fe", troughcolor="#e0e0e0",
                borderwidth=0, lightcolor="#0f62fe", darkcolor="#0f62fe", thickness=px(4))
    s.configure("Treeview", background="#ffffff", fieldbackground="#ffffff",
                foreground="#161616", borderwidth=0, rowheight=px(36), font=(family, 9))
    s.map("Treeview", background=[("selected", "#d0e2ff")], foreground=[("selected", "#161616")])
    s.configure("Treeview.Heading", background="#e0e0e0", foreground="#161616",
                font=(family, 9, "bold"), padding=(px(12), px(9)),
                borderwidth=0, relief="flat")
    s.map("Treeview.Heading", background=[("active", "#d1d1d1")])
    for direction in ("Vertical", "Horizontal"):
        s.configure(direction + ".TScrollbar", background="#c6c6c6", troughcolor="#f4f4f4",
                    borderwidth=0, arrowsize=px(12), relief="flat")
    s.configure("TSeparator", background="#e0e0e0")
    root.option_add("*Text.selectBackground", "#d0e2ff")
    root.option_add("*Text.selectForeground", "#161616")
    return c
