"""User glossary mapping with inline validation and translated-column preview."""

from pathlib import Path
from tkinter import StringVar, Toplevel, ttk

import carbon_theme
from custom_glossary import read_glossary, read_glossary_headers
from translate_from_terms import FRIENDLY_TO_TERM


class GlossaryDialog:
    def __init__(self, parent, path: Path, mapping=None):
        self.headers = read_glossary_headers(path)
        self.path, self.result, self._preview_after = path, None, None
        self.window = Toplevel(parent)
        self.window.title("导入术语表 · 确认语言列")
        self.window.transient(parent)
        scale = max(1.0, float(parent.tk.call("tk", "scaling")) / (96 / 72))
        sw, sh = self.window.winfo_screenwidth(), self.window.winfo_screenheight()
        width, height = min(int(780 * scale), sw - 60), min(int(760 * scale), sh - 100)
        self.window.geometry(f"{width}x{height}+{max(0,(sw-width)//2)}+{max(0,(sh-height)//3)}")
        self.window.minsize(min(int(640*scale), width), min(int(500*scale), height))
        self.window.grab_set()
        outer = ttk.Frame(self.window, padding=18)
        outer.pack(fill="both", expand=True)
        footer = ttk.Frame(outer)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        self.status = StringVar()
        self.status_label = ttk.Label(footer, textvariable=self.status, style="Caption.TLabel", wraplength=650)
        self.status_label.pack(fill="x", pady=(0, 10))
        footer.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(100, e.width)), add="+")
        buttons = ttk.Frame(footer)
        buttons.pack(fill="x")
        self.confirm_button = ttk.Button(buttons, text="确认导入", style="M3.TButton", command=lambda: self.confirm(path), state="disabled")
        self.confirm_button.pack(side="right")
        ttk.Button(buttons, text="取消", style="Text.TButton", command=self.close).pack(side="right", padx=10)
        ttk.Button(buttons, text="自动识别列名", style="Text.TButton", command=self.auto_map).pack(side="left")
        scroll = carbon_theme.ScrollableFrame(outer, colors={"background": "#f3f8fc"})
        scroll.pack(fill="both", expand=True)
        body = scroll.content
        heading = ttk.Label(body, text=path.name, style="Headline.TLabel", wraplength=650)
        heading.pack(fill="x", pady=(0, 8))
        body.bind("<Configure>", lambda e: heading.configure(wraplength=max(100, e.width-16)), add="+")
        ttk.Label(body, text="先对应简体中文源文列，再选择需要的目标语言；其他语言可留空。",
                  style="Caption.TLabel", wraplength=600).pack(anchor="w", pady=(0, 16))
        fields = ttk.Frame(body, style="Card.TFrame", padding=12)
        fields.pack(fill="x")
        fields.columnconfigure(1, weight=1)
        fields.columnconfigure(3, weight=1)
        self.variables = {}
        for index, (language, code) in enumerate(FRIENDLY_TO_TERM.items()):
            value = ""
            for header in self.headers:
                mapped = mapping.get(header, "") if mapping is not None else header
                if FRIENDLY_TO_TERM.get(mapped, mapped.upper()) == code:
                    value = header
                    break
            variable = StringVar(master=self.window, value=value)
            self.variables[language] = variable
            column, row = (index // 8) * 2, index % 8
            label = language + (" *" if code == "CHS" else "")
            ttk.Label(fields, text=label, style="Card.TLabel").grid(row=row, column=column, sticky="w", padx=(0, 6), pady=5)
            ttk.Combobox(fields, textvariable=variable, values=[""] + self.headers, state="readonly", width=16).grid(
                row=row, column=column+1, sticky="ew", padx=(0, 12), pady=5)
        ttk.Label(body, text="导入预览", style="Headline.TLabel").pack(anchor="w", pady=(18, 6))
        ttk.Label(body, text="显示前 5 条有效术语；未映射的列不会导入。", style="Caption.TLabel").pack(anchor="w", pady=(0, 8))
        preview = ttk.Frame(body)
        preview.pack(fill="x")
        preview.columnconfigure(0, weight=1)
        self.preview_tree = ttk.Treeview(preview, show="headings", height=5, columns=("empty",))
        self.preview_tree.heading("empty", text="选择语言列后查看预览")
        self.preview_tree.column("empty", width=300)
        self.preview_tree.grid(row=0, column=0, sticky="ew")
        bar = ttk.Scrollbar(preview, orient="horizontal", command=self.preview_tree.xview)
        bar.grid(row=1, column=0, sticky="ew")
        self.preview_tree.configure(xscrollcommand=bar.set)
        for variable in self.variables.values():
            variable.trace_add("write", self._schedule_preview)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _e: self.close())
        self._update_preview()

    def _mapping(self):
        selected = [var.get() for var in self.variables.values() if var.get()]
        if len(selected) != len(set(selected)):
            raise ValueError("同一列不能映射到多个语言，请调整重复选择。")
        return {var.get(): FRIENDLY_TO_TERM[language] for language, var in self.variables.items() if var.get()}

    def _schedule_preview(self, *_):
        if self._preview_after:
            self.window.after_cancel(self._preview_after)
        self._preview_after = self.window.after(150, self._update_preview)

    def _update_preview(self):
        self._preview_after = None
        self.preview_tree.delete(*self.preview_tree.get_children())
        try:
            mapping = self._mapping()
            codes, rows = read_glossary(self.path, mapping)
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            self.status_label.configure(foreground="#da1e28")
            self.confirm_button.state(["disabled"])
            return
        names = {value: key for key, value in FRIENDLY_TO_TERM.items()}
        self.preview_tree.configure(columns=codes)
        for code in codes:
            self.preview_tree.heading(code, text=names[code])
            self.preview_tree.column(code, width=170, minwidth=80, stretch=False)
        for row in rows[:5]:
            self.preview_tree.insert("", "end", values=[value[:150].replace("\n", " ↵ ") for value in row])
        self.status.set(f"可以导入 · {len(rows)} 条术语 · {len(codes)-1} 种目标语言")
        self.status_label.configure(foreground="#198038")
        self.confirm_button.state(["!disabled"])

    def auto_map(self):
        for language, code in FRIENDLY_TO_TERM.items():
            self.variables[language].set(next((h for h in self.headers if FRIENDLY_TO_TERM.get(h, h.upper()) == code), ""))

    def confirm(self, path: Path):
        try:
            mapping = self._mapping()
            _, rows = read_glossary(path, mapping)
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            self.status_label.configure(foreground="#da1e28")
            self.confirm_button.state(["disabled"])
            return
        self.result = mapping, len(rows)
        self.close()

    def close(self):
        if self._preview_after:
            self.window.after_cancel(self._preview_after)
        self.window.destroy()
