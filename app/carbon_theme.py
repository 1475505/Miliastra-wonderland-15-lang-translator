"""Carbon-inspired layout with a light-blue palette and clear checkbox states."""
import math
import sys
from tkinter import PhotoImage, font as tkfont, ttk
from m3_theme import ScrollableFrame


def _checkbox_images(root, colors, scale):
    """Draw a real tick, with focus around the box instead of dotted text."""
    size = max(22, round(22 * scale))
    border_width = max(1, round(scale))
    inset = max(3, round(3 * scale))
    lo, hi = inset, size - inset - 1

    def stroke(image, points, color, width):
        # Round stroke caps keep the tick legible at fractional Windows scaling.
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            steps = max(1, math.ceil(max(abs(x1-x0), abs(y1-y0)) * 2))
            radius = width / 2
            for step in range(steps + 1):
                x, y = x0 + (x1-x0)*step/steps, y0 + (y1-y0)*step/steps
                for iy in range(max(0, math.floor(y-radius)), min(size, math.ceil(y+radius)+1)):
                    for ix in range(max(0, math.floor(x-radius)), min(size, math.ceil(x+radius)+1)):
                        if (ix-x)**2 + (iy-y)**2 <= radius**2:
                            image.put(color, (ix, iy))

    def draw(selected=False, hover=False, focus=False, disabled=False):
        image = PhotoImage(master=root, width=size+round(6*scale), height=size)
        image.put(colors["surface"], to=(0, 0, image.width(), size))
        fill = colors["primary_hover"] if hover and selected else colors["primary"] if selected else colors["surface"]
        edge = colors["focus"] if selected or hover else colors["outline_variant"]
        tick = colors["on_primary"]
        if disabled:
            fill, edge, tick = ("#e1eaf1", "#b4c4d1", "#8196a7") if selected else ("#f4f7fa", "#c3d0db", "#8196a7")
        if focus and not disabled:
            # A separate blue outer ring remains visible on selected controls.
            image.put(colors["focus"], to=(0, 0, size, size))
            image.put(colors["surface"], to=(border_width, border_width, size-border_width, size-border_width))
        image.put(edge, to=(lo, lo, hi+1, hi+1))
        image.put(fill, to=(lo+border_width, lo+border_width, hi+1-border_width, hi+1-border_width))
        if selected:
            span = hi - lo
            points = [(lo+span*.22, lo+span*.50), (lo+span*.43, lo+span*.71), (lo+span*.79, lo+span*.29)]
            stroke(image, points, tick, max(2, 1.8*scale))
        return image

    return {
        "normal": draw(), "hover": draw(hover=True), "selected": draw(selected=True),
        "selected_hover": draw(selected=True, hover=True), "focus": draw(focus=True),
        "focus_selected": draw(selected=True, focus=True), "disabled": draw(disabled=True),
        "disabled_selected": draw(selected=True, disabled=True),
    }


def apply_carbon_theme(root):
    c = {
        "background": "#f3f8fc", "surface": "#ffffff", "text": "#16324a",
        "text_secondary": "#4c6478", "primary": "#b8ddf7", "primary_hover": "#9ecded",
        "on_primary": "#163f60", "link": "#245f8f", "focus": "#2878b0",
        "shell": "#e1f0fb", "selected": "#e6f3fd", "field": "#f4f9fd",
        "error": "#c62838", "outline_variant": "#7f99ad", "border": "#d6e5f0",
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
    for name, bg in (("TFrame", c["background"]), ("Card.TFrame", c["surface"]),
                     ("Shell.TFrame", c["shell"]), ("Line.TFrame", c["border"]),
                     ("Accent.TFrame", "#6caee0")):
        s.configure(name, background=bg, borderwidth=0, relief="flat")
    labels = {
        "TLabel": (c["background"], c["text"], 10, ""),
        "Caption.TLabel": (c["background"], c["text_secondary"], 9, ""),
        "Title.TLabel": (c["background"], c["text"], 23, ""),
        "Headline.TLabel": (c["background"], c["text"], 14, ""),
        "Eyebrow.TLabel": (c["background"], c["text_secondary"], 9, ""),
        "Card.TLabel": (c["surface"], c["text"], 10, ""),
        "CardCaption.TLabel": (c["surface"], c["text_secondary"], 9, ""),
        "Section.TLabel": (c["surface"], c["text"], 12, "bold"),
        "Metric.TLabel": (c["surface"], c["text"], 20, ""),
        "Status.TLabel": (c["surface"], c["text"], 16, ""),
        "Success.Status.TLabel": (c["surface"], "#198038", 16, ""),
        "Paused.Status.TLabel": (c["surface"], "#8a3800", 16, ""),
        "Error.Status.TLabel": (c["surface"], c["error"], 16, ""),
        "Error.CardCaption.TLabel": (c["surface"], c["error"], 9, ""),
        "Shell.TLabel": (c["shell"], c["text"], 11, "bold"),
        "ShellCaption.TLabel": (c["shell"], c["text_secondary"], 9, ""),
        "NavCaption.TLabel": (c["surface"], c["text_secondary"], 9, ""),
    }
    for name, (bg, fg, size, weight) in labels.items():
        s.configure(name, background=bg, foreground=fg,
                    font=(family, size, weight) if weight else (family, size))
    variants = {
        "M3.TButton": (c["primary"], c["on_primary"], c["primary_hover"], (16, 12)),
        "Tonal.TButton": ("#e3eef7", c["on_primary"], "#d2e5f3", (16, 12)),
        "Compact.TButton": ("#edf6fc", c["link"], "#dceefb", (12, 9)),
        "Text.TButton": (c["surface"], c["link"], c["selected"], (8, 8)),
        "Shell.TButton": (c["shell"], c["link"], "#cfe5f5", (16, 10)),
        "Nav.TButton": (c["surface"], c["text_secondary"], "#f0f7fc", (12, 14)),
        "Active.Nav.TButton": (c["selected"], c["link"], "#d6ebfa", (12, 14)),
    }
    for name, (bg, fg, hover, padding) in variants.items():
        s.configure(name, background=bg, foreground=fg, borderwidth=1, relief="flat",
                    bordercolor=bg, lightcolor=bg, darkcolor=bg,
                    focusthickness=1, focuscolor=c["focus"], anchor="w",
                    padding=tuple(px(n) for n in padding), font=(family, 10), width=0)
        s.map(name, background=[("disabled", "#e0e9f0"), ("pressed", hover), ("active", hover)],
              foreground=[("disabled", "#7c91a2")],
              bordercolor=[("focus", c["focus"]), ("active", hover)])
    for name in ("Nav.TButton", "Active.Nav.TButton"):
        s.map(name, background=[("disabled", c["surface"]), ("active", "#edf6fc")],
              foreground=[("disabled", "#8298aa")])
    s.configure("Compact.TMenubutton", padding=(px(12), px(9)), background="#edf6fc",
                foreground=c["link"], borderwidth=0, relief="flat", width=0)
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        s.configure(name, fieldbackground=c["field"], foreground=c["text"],
                    insertcolor=c["text"], bordercolor=c["outline_variant"], lightcolor=c["field"],
                    darkcolor=c["outline_variant"], borderwidth=1, relief="flat",
                    padding=(px(10), px(9)), arrowsize=px(14))
        s.map(name, fieldbackground=[("disabled", "#e6eef5"), ("readonly", c["field"])],
              foreground=[("disabled", "#7c91a2")],
              bordercolor=[("focus", c["focus"])], lightcolor=[("focus", c["focus"])],
              darkcolor=[("focus", c["focus"])])
    if not hasattr(root, "_carbon_checkbox_images"):
        root._carbon_checkbox_images = _checkbox_images(root, c, scale)
    images = root._carbon_checkbox_images
    if "Sky.Check.indicator" not in s.element_names():
        s.element_create("Sky.Check.indicator", "image", images["normal"],
                         ("disabled", "selected", images["disabled_selected"]),
                         ("disabled", images["disabled"]),
                         ("focus", "selected", images["focus_selected"]),
                         ("focus", images["focus"]),
                         ("selected", "active", images["selected_hover"]),
                         ("selected", images["selected"]), ("active", images["hover"]))
    # No Checkbutton.focus text element: the icon itself supplies keyboard focus.
    s.layout("G.TCheckbutton", [
        ("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Sky.Check.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.label", {"side": "left", "sticky": "w"}),
        ]}),
    ])
    s.configure("G.TCheckbutton", background=c["surface"], foreground=c["text"],
                padding=(0, px(4)), indicatormargin=(0, 0, px(8), 0))
    s.map("G.TCheckbutton", background=[("active", c["surface"])],
          foreground=[("disabled", "#7c91a2")])
    s.configure("Horizontal.TProgressbar", background="#4b96cc", troughcolor="#e1eef7",
                borderwidth=0, lightcolor="#4b96cc", darkcolor="#4b96cc", thickness=px(4))
    s.configure("Treeview", background=c["surface"], fieldbackground=c["surface"],
                foreground=c["text"], borderwidth=0, rowheight=px(36), font=(family, 9))
    s.map("Treeview", background=[("selected", "#d9ecfa")], foreground=[("selected", c["text"])])
    s.configure("Treeview.Heading", background="#e4f0f8", foreground=c["text"],
                font=(family, 9, "bold"), padding=(px(12), px(9)), borderwidth=0, relief="flat")
    s.map("Treeview.Heading", background=[("active", "#d3e7f5")])
    for direction in ("Vertical", "Horizontal"):
        s.configure(direction + ".TScrollbar", background="#bccfdf", troughcolor=c["background"],
                    borderwidth=0, arrowsize=px(12), relief="flat")
    s.configure("TSeparator", background=c["border"])
    root.option_add("*Text.selectBackground", "#d9ecfa")
    root.option_add("*Text.selectForeground", c["text"])
    return c
