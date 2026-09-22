#!/usr/bin/env python3
"""LLM translator with optional, user-supplied CSV/TSV glossaries."""

from __future__ import annotations

import os
import queue
import json
import sys
import threading
import time
import traceback
from pathlib import Path
from tkinter import BooleanVar, TclError, Menu, PhotoImage, StringVar, Text, Tk, filedialog, messagebox, ttk

if sys.platform == "win32":
    import ctypes

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
for _p in (str(TOOLS), str(APP_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import m3_theme  # noqa: E402
from ui_helpers import inspect_input, bounded_integer, validate_endpoint, duration_label
from custom_glossary import read_glossary  # noqa: E402
from glossary_dialog import GlossaryDialog  # noqa: E402

from llm_stage2 import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    Stage2Config,
    Stage2Paused,
    run_stage2,
)  # noqa: E402
from validate_output import validate_and_fix  # noqa: E402
from translate_from_terms import (
    FRIENDLY_TERM_COLUMNS,
    resource_path,
    write_csv,
)  # noqa: E402


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return ROOT


CONFIG_PATH = app_dir() / "settings.json"
APP_VERSION = "v1.05"
MAX_RECENT_FILES = 5


def enable_high_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


class DirectTranslatorApp:
    def __init__(self) -> None:
        enable_high_dpi_awareness()
        saved = self._load_settings()
        self.root = Tk()
        self.root.title(f"千星奇域 · 多语言翻译 {APP_VERSION}")
        if sys.platform == "win32":
            try:
                self.root.tk.call("tk", "scaling", ctypes.windll.user32.GetDpiForWindow(self.root.winfo_id()) / 72)
            except Exception:
                pass
        self.scale = max(1.0, float(self.root.tk.call("tk", "scaling")) / (96 / 72))
        self.m3_colors = m3_theme.apply_m3_theme(self.root)
        self._saved_geometry = str(saved.get("window_geometry", ""))
        try:
            self.root.iconbitmap(str(resource_path("app/icon.ico")))
        except TclError:
            pass
        recent = saved.get("recent_files", [])
        self._recent_files = [p for p in recent if isinstance(p, str) and Path(p).is_file()][:5] if isinstance(recent, list) else []
        self.input_path = StringVar()
        self.output_dir = StringVar(value=str(saved.get("output_dir", app_dir() / "outputs")))
        self.glossary_path = StringVar(value=str(saved.get("glossary_path", "")))
        mapping = saved.get("glossary_columns")
        self.glossary_columns = mapping if isinstance(mapping, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()) else None
        self.overwrite = BooleanVar(value=False)
        self.force_translate_all = BooleanVar(value=False)
        self.run_stage3 = BooleanVar(value=bool(saved.get("run_stage3", False)))
        self.llm_endpoint = StringVar(value=str(saved.get("llm_endpoint", DEFAULT_ENDPOINT)))
        self.llm_model = StringVar(value=str(saved.get("llm_model", DEFAULT_MODEL)))
        self.llm_api_key = StringVar(value=str(saved.get("llm_api_key", os.getenv("DEEPSEEK_API_KEY", ""))))
        self.llm_threads = StringVar(value=str(saved.get("llm_threads", 3)))
        self.flush_interval = StringVar(value=str(saved.get("flush_interval", 5)))
        self.extra_prompt = StringVar(value=str(saved.get("extra_prompt", "")))
        self.input_summary = StringVar(value="选择 CSV 后，查看行数、目标语言和待处理文本。")
        self.glossary_summary = StringVar(value="可选 · 不导入时直接翻译")
        self.form_feedback = StringVar()
        self.status_title = StringVar(value="准备开始")
        self.status_detail = StringVar(value="选择输入文件，确认模型设置后开始翻译。")
        self.count_text = StringVar(value="—")
        self.elapsed_text = StringVar(value="00:00")
        self.progress_text = StringVar(value="尚未开始")
        self.result_summary = StringVar()
        self._messages = queue.Queue()
        self._running, self._close_requested = False, False
        self._state = "ready"
        self._stop_event = self._active_config = self._started_at = None
        self._preview_generation = 0
        self._preview_after = self._last_preview = None
        self._output_path = self._report_path = None
        self._locked_widgets = []
        self._progress_limit = 100
        self._build()
        self._fit_window()
        self._refresh_glossary_summary()
        for variable in (self.input_path, self.overwrite, self.force_translate_all):
            variable.trace_add("write", self._schedule_preview)
        self.root.bind("<Control-o>", lambda _e: self.choose_input())
        self.root.bind("<Control-Return>", lambda _e: self.run())
        self.root.bind("<Control-period>", lambda _e: self.stop())
        self.root.bind("<F1>", lambda _e: self.show_help())
        self.root.after(80, self._poll_messages)
        self.root.after(1000, self._tick)
        self.root.after(150, lambda: self._body_pane.sashpos(0, int(self._body_pane.winfo_width() * 0.57)))
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._append_log("准备就绪。先选择输入文件；术语表可选。")

    def _load_settings(self):
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        self._sync_extra_prompt()
        names = ("llm_endpoint", "llm_model", "llm_api_key", "llm_threads", "flush_interval",
                 "extra_prompt", "glossary_path", "output_dir", "run_stage3")
        data = {name: getattr(self, name).get() for name in names}
        data.update(glossary_columns=self.glossary_columns, recent_files=self._recent_files,
                    window_geometry=self.root.geometry())
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _fit_window(self):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        max_w, max_h = max(600, sw - 40), max(440, sh - 90)
        width, height = int(1180 * self.scale), int(820 * self.scale)
        try:
            geometry = self._saved_geometry.split("+")[0].split("-")[0].split("x")
            if len(geometry) == 2:
                width, height = map(int, geometry)
        except ValueError:
            pass
        min_w, min_h = min(int(920 * self.scale), max_w), min(int(620 * self.scale), max_h)
        width, height = min(max_w, max(min_w, width)), min(max_h, max(min_h, height))
        self.root.geometry(f"{width}x{height}+{max(0,(sw-width)//2)}+{max(0,(sh-height)//3)}")
        self.root.minsize(min_w, min_h)

    def _wrap(self, label, parent, margin=36):
        parent.bind("<Configure>", lambda e: label.configure(wraplength=max(100, e.width-margin)), add="+")

    def _card(self, parent, title, subtitle=None):
        card = ttk.Frame(parent, style="Card.TFrame", padding=16)
        card.pack(fill="x", pady=(0, 12))
        ttk.Label(card, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 10))
        if subtitle:
            label = ttk.Label(card, text=subtitle, style="CardCaption.TLabel", wraplength=380)
            label.pack(fill="x", pady=(0, 10))
            self._wrap(label, card)
        return card

    def _build(self):
        c = self.m3_colors
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 16))
        ttk.Button(header, text="使用说明  F1", style="Text.TButton", command=self.show_help).pack(side="right")
        try:
            self._logo_img = PhotoImage(file=str(resource_path("app/app_logo.png"))).subsample(3, 3)
            ttk.Label(header, image=self._logo_img).pack(side="left", padx=(0, 12))
        except TclError:
            pass
        titles = ttk.Frame(header)
        titles.pack(side="left", fill="x", expand=True)
        ttk.Label(titles, text="千星奇域 · 多语言翻译", style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="简体中文 → 14 种语言    /    自定义术语 · 统一译名", style="Caption.TLabel").pack(anchor="w", pady=(3, 0))
        footer = ttk.Frame(outer)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        actions = ttk.Frame(footer)
        actions.pack(fill="x")
        self.run_button = ttk.Button(actions, text="开始翻译", style="M3.TButton", command=self.run)
        self.run_button.pack(side="right", padx=(8, 0))
        self.stop_button = ttk.Button(actions, text="暂停", style="Tonal.TButton", command=self.stop, state="disabled")
        self.stop_button.pack(side="right")
        ttk.Label(actions, text="Ctrl+O  选择文件\nCtrl+Enter  开始 / 继续", style="Caption.TLabel").pack(side="left")
        ttk.Label(footer, text=f"{APP_VERSION}  ·  仅供学习交流，禁止售卖  ·  QQ 群 1007538100",
                  style="Caption.TLabel").pack(anchor="w", pady=(10, 0))
        self._body_pane = ttk.PanedWindow(outer, orient="horizontal")
        self._body_pane.pack(fill="both", expand=True)
        left, right = ttk.Frame(self._body_pane), ttk.Frame(self._body_pane)
        self._body_pane.add(left, weight=3)
        self._body_pane.add(right, weight=2)
        self.workspace_tabs = ttk.Notebook(left)
        self.workspace_tabs.pack(fill="both", expand=True, padx=(0, 12))
        self.task_panel = m3_theme.ScrollableFrame(self.workspace_tabs, colors=c)
        self.settings_panel = m3_theme.ScrollableFrame(self.workspace_tabs, colors=c)
        self.workspace_tabs.add(self.task_panel, text="翻译任务")
        self.workspace_tabs.add(self.settings_panel, text="模型设置")
        self._build_task_form(self.task_panel.content)
        self._build_settings(self.settings_panel.content)
        self.feedback_label = ttk.Label(left, textvariable=self.form_feedback, foreground=c["error"],
                                        style="Caption.TLabel", wraplength=400)
        self.feedback_label.pack(fill="x", pady=(6, 0))
        self._wrap(self.feedback_label, left)
        status = self._card(right, "本次任务")
        self.status_label = ttk.Label(status, textvariable=self.status_title, style="Status.TLabel")
        self.status_label.pack(anchor="w")
        detail = ttk.Label(status, textvariable=self.status_detail, style="CardCaption.TLabel", wraplength=320)
        detail.pack(fill="x", pady=(7, 14))
        self._wrap(detail, status)
        metrics = ttk.Frame(status, style="Card.TFrame")
        metrics.pack(fill="x")
        for column, (caption, variable) in enumerate((("文本组", self.count_text), ("已用时间", self.elapsed_text))):
            metrics.columnconfigure(column, weight=1)
            ttk.Label(metrics, text=caption, style="CardCaption.TLabel").grid(row=0, column=column, sticky="w")
            ttk.Label(metrics, textvariable=variable, style="Metric.TLabel").grid(row=1, column=column, sticky="w", pady=(3, 10))
        self.progress = ttk.Progressbar(status, maximum=100, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(status, textvariable=self.progress_text, style="CardCaption.TLabel").pack(anchor="w", pady=(7, 0))
        self.result_card = self._card(right, "输出文件")
        result_label = ttk.Label(self.result_card, textvariable=self.result_summary, style="CardCaption.TLabel", wraplength=320)
        result_label.pack(fill="x", pady=(0, 10))
        self._wrap(result_label, self.result_card)
        row = ttk.Frame(self.result_card, style="Card.TFrame")
        row.pack(fill="x")
        self.result_button = ttk.Button(row, text="打开结果", style="Compact.TButton", command=lambda: self._open_path(self._output_path))
        self.result_button.pack(side="left", padx=(0, 6))
        self.report_button = ttk.Button(row, text="查看报告", style="Compact.TButton", command=lambda: self._open_path(self._report_path))
        self.report_button.pack(side="left", padx=(0, 6))
        ttk.Button(row, text="所在文件夹", style="Compact.TButton", command=self.open_output_dir).pack(side="left")
        self.result_card.pack_forget()
        self.detail_tabs = ttk.Notebook(right)
        self.detail_tabs.pack(fill="both", expand=True)
        self.preview_tab = ttk.Frame(self.detail_tabs, padding=10, style="Card.TFrame")
        self.log_tab = ttk.Frame(self.detail_tabs, padding=10, style="Card.TFrame")
        self.detail_tabs.add(self.preview_tab, text="输入预览")
        self.detail_tabs.add(self.log_tab, text="运行日志")
        ttk.Label(self.preview_tab, text="预览前 30 行 · 原始文件保持不变", style="CardCaption.TLabel").pack(anchor="w", pady=(0, 8))
        tree_frame = ttk.Frame(self.preview_tab, style="Card.TFrame")
        tree_frame.pack(fill="both", expand=True)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.input_tree = ttk.Treeview(tree_frame, columns=("empty",), show="headings", height=5)
        self.input_tree.heading("empty", text="选择文件后，在这里预览")
        self.input_tree.column("empty", width=260, stretch=True)
        self.input_tree.grid(row=0, column=0, sticky="nsew")
        ybar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.input_tree.yview)
        ybar.grid(row=0, column=1, sticky="ns")
        xbar = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.input_tree.xview)
        xbar.grid(row=1, column=0, sticky="ew")
        self.input_tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        row = ttk.Frame(self.log_tab, style="Card.TFrame")
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="详细过程与错误信息", style="CardCaption.TLabel").pack(side="left")
        ttk.Button(row, text="复制", style="Compact.TButton", command=self._copy_log).pack(side="right")
        ttk.Button(row, text="清空", style="Compact.TButton", command=self._clear_log).pack(side="right", padx=6)
        log_body = ttk.Frame(self.log_tab, style="Card.TFrame")
        log_body.pack(fill="both", expand=True)
        self.log_text = Text(log_body, height=5, width=20, wrap="word", state="disabled", relief="flat",
                             background=c["surface"], foreground=c["text_secondary"], font=("Microsoft YaHei UI", 9), padx=4, pady=4)
        bar = ttk.Scrollbar(log_body, orient="vertical", command=self.log_text.yview)
        bar.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(yscrollcommand=bar.set)

    def _build_task_form(self, parent):
        card = self._card(parent, "01  选择翻译文件")
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x")
        self.input_entry = ttk.Entry(row, textvariable=self.input_path, width=18)
        self.input_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(row, text="选择 CSV", style="Compact.TButton", command=self.choose_input).pack(side="left")
        self.recent_menu_btn = ttk.Menubutton(row, text="最近", style="Compact.TButton")
        self.recent_menu_btn.pack(side="left", padx=(6, 0))
        self.recent_menu = Menu(self.recent_menu_btn, tearoff=0)
        self.recent_menu_btn.configure(menu=self.recent_menu)
        self._update_recent_menu()
        self.input_summary_label = ttk.Label(card, textvariable=self.input_summary, style="CardCaption.TLabel", wraplength=400)
        self.input_summary_label.pack(fill="x", pady=(9, 14))
        self._wrap(self.input_summary_label, card)
        ttk.Label(card, text="输出目录", style="CardCaption.TLabel").pack(anchor="w", pady=(0, 5))
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x")
        self.output_entry = ttk.Entry(row, textvariable=self.output_dir, width=18)
        self.output_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(row, text="更改", style="Compact.TButton", command=self.choose_output_dir).pack(side="left")
        card = self._card(parent, "02  自定义术语表", "使用你的固定译名，保持不同文本中的术语一致。")
        label = ttk.Label(card, textvariable=self.glossary_summary, style="Card.TLabel", wraplength=400)
        label.pack(fill="x", pady=(0, 10))
        self._wrap(label, card)
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x")
        self.glossary_import_button = ttk.Button(row, text="导入术语表", style="Compact.TButton", command=self.choose_glossary)
        self.glossary_import_button.pack(side="left", padx=(0, 6))
        self.glossary_mapping_button = ttk.Button(row, text="修改列映射", style="Compact.TButton", command=self.edit_glossary_mapping, state="disabled")
        self.glossary_mapping_button.pack(side="left", padx=(0, 6))
        self.glossary_clear_button = ttk.Button(row, text="移除", style="Compact.TButton", command=self.clear_glossary, state="disabled")
        self.glossary_clear_button.pack(side="left")
        ttk.Button(card, text="导出空白模板", style="Text.TButton", command=self.export_glossary_template).pack(anchor="w", pady=(7, 0))
        card = self._card(parent, "03  翻译偏好", "默认只填写空白译文，并跳过标记为不翻译的行。")
        for label, variable in (("覆盖已有译文", self.overwrite), ("忽略不翻译标记，处理所有行", self.force_translate_all),
                                ("完成后检查标签与译文一致性", self.run_stage3)):
            ttk.Checkbutton(card, text=label, variable=variable, style="G.TCheckbutton").pack(anchor="w", pady=3)
        ttk.Label(card, text="额外要求（可选）", style="CardCaption.TLabel").pack(anchor="w", pady=(12, 6))
        c = self.m3_colors
        self.extra_prompt_text = Text(card, height=3, width=20, wrap="word", relief="flat", borderwidth=1,
                                      highlightthickness=1, highlightbackground=c["outline_variant"],
                                      highlightcolor=c["primary"], font=("Microsoft YaHei UI", 10), padx=8, pady=8)
        self.extra_prompt_text.pack(fill="x")
        self.extra_prompt_text.insert("1.0", self.extra_prompt.get())
        ttk.Label(card, text="例如：保持口语化；统一女性代词；不要扩写。", style="CardCaption.TLabel").pack(anchor="w", pady=(6, 0))

    def _build_settings(self, parent):
        card = self._card(parent, "连接翻译模型", "设置会保存在本机；支持兼容 Chat Completions 的接口。")
        for title, variable, name in (("服务地址（Endpoint）", self.llm_endpoint, "endpoint_entry"),
                                      ("模型名称（Model）", self.llm_model, "model_entry")):
            ttk.Label(card, text=title, style="CardCaption.TLabel").pack(anchor="w", pady=(0, 5))
            entry = ttk.Entry(card, textvariable=variable, width=20)
            entry.pack(fill="x", pady=(0, 14))
            setattr(self, name, entry)
        ttk.Label(card, text="API Key", style="CardCaption.TLabel").pack(anchor="w", pady=(0, 5))
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x")
        self.api_key_entry = ttk.Entry(row, textvariable=self.llm_api_key, show="*", width=16)
        self.api_key_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.api_visibility_button = ttk.Button(row, text="显示", style="Compact.TButton", command=self._toggle_api_key)
        self.api_visibility_button.pack(side="left")
        label = ttk.Label(card, text="源文及匹配术语会发送到上述接口。API Key 保存在本机 settings.json 中。",
                          style="CardCaption.TLabel", wraplength=400)
        label.pack(fill="x", pady=(12, 0))
        self._wrap(label, card)
        card = self._card(parent, "运行参数")
        self.advanced_button = ttk.Button(card, text="展开高级设置 ▾", style="Text.TButton", command=self._toggle_advanced)
        self.advanced_button.pack(anchor="w")
        self.advanced_frame = ttk.Frame(card, style="Card.TFrame")
        for index, (title, variable, limit) in enumerate((("并发请求数", self.llm_threads, 16), ("自动保存间隔", self.flush_interval, 50))):
            ttk.Label(self.advanced_frame, text=title, style="Card.TLabel").grid(row=index, column=0, sticky="w", pady=8, padx=(0, 12))
            spinbox = ttk.Spinbox(self.advanced_frame, textvariable=variable, from_=1, to=limit, width=7)
            spinbox.grid(row=index, column=1, sticky="w")
            setattr(self, "threads_spinbox" if index == 0 else "flush_spinbox", spinbox)
        ttk.Label(self.advanced_frame, text="并发建议 1–5；保存间隔单位为文本组。",
                  style="CardCaption.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=8)
        self._advanced_open = False



    def _toggle_advanced(self):
        self._advanced_open = not self._advanced_open
        if self._advanced_open:
            self.advanced_frame.pack(fill="x", pady=(8, 0))
        else:
            self.advanced_frame.pack_forget()
        self.advanced_button.configure(text="收起高级设置 ▴" if self._advanced_open else "展开高级设置 ▾")

    def _toggle_api_key(self):
        visible = bool(self.api_key_entry.cget("show"))
        self.api_key_entry.configure(show="" if visible else "*")
        self.api_visibility_button.configure(text="隐藏" if visible else "显示")

    def _sync_extra_prompt(self):
        self.extra_prompt.set(self.extra_prompt_text.get("1.0", "end-1c").strip())

    def _update_recent_menu(self):
        self.recent_menu.delete(0, "end")
        for path in self._recent_files:
            self.recent_menu.add_command(label=path, command=lambda p=path: self._select_input(p))
        if not self._recent_files:
            self.recent_menu.add_command(label="暂无最近文件", state="disabled")

    def _select_input(self, path):
        if self._running:
            return
        self.input_path.set(str(path))
        self._recent_files = ([str(path)] + [p for p in self._recent_files if p != str(path)])[:MAX_RECENT_FILES]
        self._update_recent_menu()
        self.workspace_tabs.select(self.task_panel)
        self.detail_tabs.select(self.preview_tab)

    def choose_input(self):
        if not self._running:
            path = filedialog.askopenfilename(title="选择多语言文本 CSV", filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")])
            if path:
                self._select_input(path)

    def _schedule_preview(self, *_):
        self._preview_generation += 1
        if self._preview_after:
            self.root.after_cancel(self._preview_after)
        self._preview_after = self.root.after(300, self._start_preview)

    def _start_preview(self):
        self._preview_after = None
        generation = self._preview_generation
        path = self.input_path.get().strip()
        options = (self.force_translate_all.get(), self.overwrite.get())
        self._last_preview = None
        if not path:
            self.input_summary.set("选择 CSV 后，查看行数、目标语言和待处理文本。")
            self.input_tree.delete(*self.input_tree.get_children())
            return
        self.input_summary.set("正在读取文件…")
        def inspect():
            try:
                result = inspect_input(Path(path).expanduser(), *options)
                self._messages.put(("preview", (generation, result, None)))
            except Exception as exc:
                self._messages.put(("preview", (generation, None, str(exc))))
        threading.Thread(target=inspect, daemon=True).start()

    def _display_preview(self, result):
        headers, rows, summary = result
        self._last_preview = result
        self.input_summary.set(f"{summary['rows']} 行 · {summary['targets']} 种目标语言 · {summary['groups']} 组待处理文本")
        self.input_summary_label.configure(style="CardCaption.TLabel")
        self.input_tree.delete(*self.input_tree.get_children())
        columns = [f"c{i}" for i in range(len(headers))]
        self.input_tree.configure(columns=columns)
        for column, header in zip(columns, headers):
            self.input_tree.heading(column, text=header)
            self.input_tree.column(column, width=int((200 if header == "简体中文" else 130) * self.scale), stretch=False)
        for row in rows:
            self.input_tree.insert("", "end", values=[str(row.get(h, ""))[:250].replace("\n", " ↵ ") for h in headers])
        if not self._running and self._state == "ready":
            self.count_text.set(str(summary["groups"]))

    def _refresh_glossary_summary(self, count=None):
        selected = bool(self.glossary_path.get())
        if selected:
            languages = len(self.glossary_columns or {}) - 1
            detail = f"{count} 条术语" if count is not None else "运行前自动核验"
            self.glossary_summary.set(f"{Path(self.glossary_path.get()).name}\n{detail}" + (f" · {languages} 种目标语言" if languages > 0 else ""))
        else:
            self.glossary_summary.set("可选 · 不导入时直接翻译")
        for widget in (self.glossary_mapping_button, self.glossary_clear_button):
            widget.state(["!disabled"] if selected and not self._running else ["disabled"])

    def choose_glossary(self):
        if self._running:
            return
        path = filedialog.askopenfilename(title="导入自己的术语表", filetypes=[("CSV / TSV 术语表", "*.csv *.tsv"), ("所有文件", "*.*")])
        if path:
            self._map_glossary(Path(path), self.glossary_columns if path == self.glossary_path.get() else None)

    def edit_glossary_mapping(self):
        if not self._running and self.glossary_path.get():
            self._map_glossary(Path(self.glossary_path.get()), self.glossary_columns)

    def _map_glossary(self, path, mapping):
        try:
            dialog = GlossaryDialog(self.root, path, mapping)
            self.root.wait_window(dialog.window)
            if dialog.result is not None:
                self.glossary_columns, count = dialog.result
                self.glossary_path.set(str(path))
                self._refresh_glossary_summary(count)
                self.form_feedback.set("")
                self._append_log(f"已导入 {path.name}：{count} 条术语。")
        except (OSError, ValueError) as exc:
            self._show_form_error(str(exc), self.glossary_import_button)

    def clear_glossary(self):
        if not self._running:
            self.glossary_path.set("")
            self.glossary_columns = None
            self._refresh_glossary_summary()

    def export_glossary_template(self):
        path = filedialog.asksaveasfilename(title="保存空白术语表模板", defaultextension=".csv",
                                          initialfile="custom_terms.csv", filetypes=[("CSV 文件", "*.csv")])
        if path:
            try:
                write_csv(Path(path), FRIENDLY_TERM_COLUMNS, [])
                self._append_log(f"模板已保存：{path}")
            except OSError as exc:
                self._show_form_error(str(exc))

    def choose_output_dir(self):
        if not self._running:
            path = filedialog.askdirectory(title="选择结果保存目录")
            if path:
                self.output_dir.set(path)

    def _open_path(self, path):
        try:
            if path is None or not Path(path).exists():
                raise ValueError("文件尚未生成，或已被移动。请查看输出目录。")
            os.startfile(path)
        except (OSError, ValueError) as exc:
            self._show_form_error(str(exc))

    def open_output_dir(self):
        self._open_path(self._output_path.parent if self._output_path else Path(self.output_dir.get()).expanduser())

    def _show_form_error(self, text, widget=None, settings=False):
        self.form_feedback.set(text)
        self.feedback_label.configure(foreground=self.m3_colors["error"])
        self.workspace_tabs.select(self.settings_panel if settings else self.task_panel)
        if widget:
            panel = self.settings_panel if settings else self.task_panel
            self.root.update_idletasks()
            position = widget.winfo_rooty() - panel.content.winfo_rooty()
            panel.canvas.yview_moveto(max(0, position - 45) / max(1, panel.content.winfo_height()))
            widget.focus_set()

    def _lock_configuration(self, locked):
        if locked:
            self._locked_widgets = []
            def lock(parent):
                for widget in parent.winfo_children():
                    if isinstance(widget, (ttk.Entry, ttk.Button, ttk.Checkbutton, ttk.Menubutton, ttk.Combobox, ttk.Spinbox)):
                        self._locked_widgets.append((widget, widget.state()))
                        widget.state(["disabled"])
                    elif isinstance(widget, Text):
                        self._locked_widgets.append((widget, widget.cget("state")))
                        widget.configure(state="disabled")
                    lock(widget)
            lock(self.task_panel)
            lock(self.settings_panel)
            self.api_key_entry.configure(show="*")
            self.api_visibility_button.configure(text="显示")
        else:
            for widget, state in self._locked_widgets:
                if isinstance(widget, Text):
                    widget.configure(state=state)
                else:
                    widget.state(["!disabled", "!readonly"])
                    widget.state(state)
            self._locked_widgets = []
            self._refresh_glossary_summary()

    def run(self):
        if self._running:
            return
        self.form_feedback.set("")
        try:
            input_path = Path(self.input_path.get().strip()).expanduser()
            preview = inspect_input(input_path, self.force_translate_all.get(), self.overwrite.get())
            self._display_preview(preview)
        except (OSError, ValueError) as exc:
            self._show_form_error(str(exc), self.input_entry)
            return
        if preview[2]["groups"] == 0:
            self.status_title.set("没有待处理文本")
            self.status_detail.set("目标单元格已有译文，或行被标记为不翻译。可调整翻译偏好后重试。")
            self.progress_text.set("未发起翻译请求")
            return
        if not self.llm_api_key.get().strip():
            self._show_form_error("请填写 API Key 后开始翻译。", self.api_key_entry, settings=True)
            return
        invalid_field = self.endpoint_entry
        try:
            endpoint = validate_endpoint(self.llm_endpoint.get())
            invalid_field = self.model_entry
            if not self.llm_model.get().strip():
                raise ValueError("请填写服务商提供的模型名称。")
            invalid_field = self.threads_spinbox
            threads = bounded_integer(self.llm_threads.get(), "并发请求数", 1, 16)
            invalid_field = self.flush_spinbox
            interval = bounded_integer(self.flush_interval.get(), "自动保存间隔", 1, 50)
        except ValueError as exc:
            if "整数" in str(exc) and not self._advanced_open:
                self._toggle_advanced()
            self._show_form_error(str(exc), invalid_field, settings=True)
            return
        self._sync_extra_prompt()
        try:
            if not self.output_dir.get().strip():
                raise ValueError("请选择输出目录。")
            output_dir = Path(self.output_dir.get().strip()).expanduser()
            output_dir.mkdir(parents=True, exist_ok=True)
            term_path = Path(self.glossary_path.get()).expanduser() if self.glossary_path.get() else None
            if term_path:
                _, terms = read_glossary(term_path, self.glossary_columns)
                self._refresh_glossary_summary(len(terms))
        except (OSError, ValueError) as exc:
            self._show_form_error(str(exc))
            return
        self._stop_event = threading.Event()
        config = Stage2Config(
            input_path=input_path, term_path=term_path,
            output_path=output_dir / f"{input_path.stem}_direct.csv",
            report_path=output_dir / f"{input_path.stem}_direct_report.csv",
            endpoint=endpoint, api_key=self.llm_api_key.get().strip(), model=self.llm_model.get().strip(),
            threads=threads, flush_interval=interval, use_terms=term_path is not None,
            term_columns=dict(self.glossary_columns) if self.glossary_columns is not None else None,
            include_false=self.force_translate_all.get(), overwrite=self.overwrite.get(),
            extra_prompt=self.extra_prompt.get(), progress_callback=self._queue_stage2_progress, stop_event=self._stop_event,
        )
        try:
            self._save_settings()
        except OSError as exc:
            self._append_log(f"设置未能保存，本次仍可继续：{exc}")
        self._preview_generation += 1
        if self._preview_after:
            self.root.after_cancel(self._preview_after)
            self._preview_after = None
        self._run_summary_groups = preview[2]["groups"]
        self._active_config = config
        self._running, self._state = True, "running"
        self._progress_limit = 90 if self.run_stage3.get() else 100
        self._started_at = time.monotonic()
        self.elapsed_text.set("00:00")
        self._output_path = self._report_path = None
        self.result_card.pack_forget()
        self._lock_configuration(True)
        self.status_label.configure(style="Status.TLabel")
        self.status_title.set("正在准备")
        self.status_detail.set("正在读取术语与已保存的进度…")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)
        self.count_text.set(f"0 / {preview[2]['groups']}")
        self.progress_text.set("准备中")
        self.run_button.configure(text="翻译中…", state="disabled")
        self.stop_button.configure(text="暂停", state="normal")
        self._append_log("开始本次任务。")
        self.workspace_tabs.select(self.task_panel)
        threading.Thread(target=self._run_worker, args=(config, self.run_stage3.get()), daemon=True).start()

    def stop(self):
        if not self._running or self._stop_event is None or self._state == "pausing":
            return
        self._state = "pausing"
        self._stop_event.set()
        self.stop_button.configure(text="正在保存…", state="disabled")
        self.status_title.set("正在暂停")
        self.status_detail.set("不再发起新请求；等待正在处理的译文返回并保存后，即可继续。")
        self._append_log("已请求暂停，将保存正在完成的译文。")

    def _run_worker(self, config, stage3_enabled):
        try:
            stats = {"direct": run_stage2(config)}
            if config.stop_event and config.stop_event.is_set():
                raise Stage2Paused()
            if stage3_enabled:
                self._messages.put(("validating", None))
                stats["stage3"] = validate_and_fix(
                    input_path=config.output_path,
                    output_path=config.output_path.with_name(f"{config.input_path.stem}_validated.csv"),
                    report_path=config.output_path.with_name(f"{config.input_path.stem}_validation_report.csv"),
                )
            self._messages.put(("done", stats))
        except Stage2Paused:
            self._messages.put(("paused", None))
        except Exception as exc:
            self._messages.put(("error", "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))))

    def _queue_stage2_progress(self, message, completed, total):
        self._messages.put(("stage_progress", (message, completed, total)))

    def _redact(self, message):
        for secret in (self.llm_api_key.get().strip(), getattr(self._active_config, "api_key", "")):
            if secret:
                message = message.replace(secret, "[API Key]")
        return message

    def _append_log(self, message):
        message = self._redact(message)
        at_bottom = self.log_text.yview()[1] >= 0.98
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 5000:
            self.log_text.delete("1.0", f"{lines-4000}.0")
        if at_bottom:
            self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log_text.get("1.0", "end-1c"))
        self.form_feedback.set("日志已复制。")
        self.feedback_label.configure(foreground=self.m3_colors["on_success_container"])

    def _tick(self):
        if self._running and self._started_at is not None:
            self.elapsed_text.set(duration_label(time.monotonic() - self._started_at))
        self.root.after(1000, self._tick)

    def _show_results(self):
        has_output = self._output_path is not None and self._output_path.is_file()
        has_report = self._report_path is not None and self._report_path.is_file()
        if has_output or has_report:
            self.result_card.pack(before=self.detail_tabs, fill="x", pady=(0, 12))
        self.result_button.state(["!disabled"] if has_output else ["disabled"])
        self.report_button.state(["!disabled"] if has_report else ["disabled"])

    def _handle_message(self, kind, payload):
        if kind == "preview":
            generation, result, error = payload
            if generation != self._preview_generation:
                return
            if error:
                self.input_summary.set(error)
                self.input_summary_label.configure(style="Error.CardCaption.TLabel")
                self.input_tree.delete(*self.input_tree.get_children())
            else:
                self._display_preview(result)
            return
        if kind == "stage_progress":
            message, completed, total = payload
            self._append_log(message)
            if completed is not None and total:
                if str(self.progress["mode"]) == "indeterminate":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                self.progress.configure(value=max(float(self.progress["value"]), completed / total * self._progress_limit))
                self.count_text.set(f"{completed} / {total}")
                self.progress_text.set(f"已处理 {completed} / {total} 组")
            if self._state != "pausing":
                self.status_title.set("正在翻译" if completed else "正在准备")
                self.status_detail.set(message[:150])
            return
        if kind == "validating":
            self.status_title.set("正在检查译文")
            self.status_detail.set("正在检查格式标签和重复源文的一致性。")
            self.progress.configure(value=95)
            return
        if kind not in ("done", "paused", "error"):
            return
        self._running = False
        self.progress.stop()
        self.progress.configure(mode="determinate")
        if self._started_at is not None:
            self.elapsed_text.set(duration_label(time.monotonic() - self._started_at))
        self.stop_button.configure(text="暂停", state="disabled")
        self._lock_configuration(False)
        self._state = kind
        if self._active_config:
            self._output_path = self._active_config.output_path
            self._report_path = self._active_config.report_path
        if kind == "done":
            direct, stage3 = payload["direct"], payload.get("stage3", {})
            review = direct["manual_review"]
            inconsistent = len(stage3.get("consistency_errors", []))
            self.status_title.set("已完成，建议复核" if review or inconsistent else "翻译完成")
            self.status_label.configure(style="Paused.Status.TLabel" if review or inconsistent else "Success.Status.TLabel")
            self.status_detail.set(f"填写 {direct['stage2_filled_cells']} 个单元格 · {review} 组待复核" +
                                   (f" · {inconsistent} 处译文不一致" if inconsistent else ""))
            if stage3.get("protected_changes"):
                self._output_path = Path(stage3["output_path"])
            self.progress.configure(value=100)
            self.progress_text.set("结果已保存")
            self.count_text.set(f"{self._run_summary_groups} / {self._run_summary_groups}")
            self.run_button.configure(text="再次处理", state="normal")
            self.result_summary.set(self._output_path.name)
            self._append_log(f"{self.status_detail.get()}\n结果：{self._output_path}\n报告：{self._report_path}")
            if stage3.get("report_path"):
                self._append_log(f"后处理报告：{stage3['report_path']}")
        elif kind == "paused":
            self.status_title.set("已暂停 · 进度已保存")
            self.status_label.configure(style="Paused.Status.TLabel")
            self.status_detail.set("点击「继续翻译」从已保存的位置继续；更换输入或配置会重新开始。")
            self.progress_text.set("已安全暂停")
            self.run_button.configure(text="继续翻译", state="normal")
            self.result_summary.set("当前已保存的部分结果")
            self._append_log("已暂停，当前进度已保存。")
        else:
            self.status_title.set("本次处理未完成")
            self.status_label.configure(style="Error.Status.TLabel")
            self.status_detail.set(self._redact(str(payload).strip().splitlines()[-1][:200]))
            self.progress_text.set("请查看日志并调整设置")
            self.run_button.configure(text="重试", state="normal")
            self.result_summary.set("输出目录中的文件（本次未完成）")
            self._append_log(str(payload))
            self.detail_tabs.select(self.log_tab)
        self._show_results()
        if self._close_requested:
            self._finish_close()

    def _poll_messages(self):
        started = time.monotonic()
        for _ in range(250):
            try:
                kind, payload = self._messages.get_nowait()
            except queue.Empty:
                break
            self._handle_message(kind, payload)
            if self._close_requested and not self._running:
                return
            if time.monotonic() - started > 0.025:
                break
        self.root.after(15 if not self._messages.empty() else 80, self._poll_messages)

    def _on_close(self):
        if self._running:
            if not self._close_requested and messagebox.askokcancel(
                "暂停并退出", "任务仍在运行。是否停止派发新请求，等待当前译文保存后退出？", parent=self.root
            ):
                self._close_requested = True
                self.stop()
            return
        self._finish_close()

    def _finish_close(self):
        try:
            self._save_settings()
        except (OSError, TclError):
            pass
        self.root.destroy()

    def show_help(self):
        messagebox.showinfo("使用说明",
            "1. 选择多语言文本管理导出的 UTF-8 CSV，在右侧检查预览。\n"
            "2. 可选导入自己的 CSV / TSV 术语表，在映射窗口选择语言列。\n"
            "3. 在「模型设置」填写服务地址、模型名称和 API Key。\n"
            "4. 点击「开始翻译」；完成后可直接打开结果和报告。\n\n"
            "默认只填空白译文，并跳过不翻译的行。暂停会等待当前请求返回并保存。\n"
            "术语、输入或配置变化后会重新开始处理。项目不附带原神术语表。\n\n"
            "快捷键：Ctrl+O 选择文件；Ctrl+Enter 开始 / 继续；Ctrl+. 暂停。\n"
            "完整提示词与格式说明见 README.md。", parent=self.root)

    def mainloop(self):
        self.root.mainloop()


if __name__ == "__main__":
    DirectTranslatorApp().mainloop()
