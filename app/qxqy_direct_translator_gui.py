#!/usr/bin/env python3
"""Direct LLM translator GUI (no term table lookup)."""

from __future__ import annotations

import os
import queue
import json
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from tkinter import BooleanVar, IntVar, Menu, PhotoImage, StringVar, Text, Tk, Toplevel, filedialog, messagebox, ttk
from tkinter import font as tkfont

if sys.platform == "win32":
    import ctypes

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
for _p in (str(TOOLS), str(APP_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import m3_theme  # noqa: E402

from llm_stage2 import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    Stage2Config,
    Stage2Paused,
    run_stage2,
)  # noqa: E402
from validate_output import validate_and_fix  # noqa: E402
from translate_from_terms import (
    NEED_TRANSLATE_COLUMN,
    SOURCE_COLUMN,
    read_input,
    resource_path,
    write_csv,
)  # noqa: E402


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return ROOT


class Tooltip:
    """Simple hover tooltip for tkinter widgets using M3 surface styling."""

    def __init__(self, widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip_window = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event=None) -> None:
        if self.tip_window is not None:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip_window = tw = Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        try:
            tw.configure(background="#DADCE0")
        except Exception:
            pass
        label = ttk.Label(
            tw,
            text=self.text,
            justify="left",
            background="#FFFFFF",
            foreground="#1F1F1F",
            relief="solid",
            borderwidth=1,
            padding=(10, 8),
            font=("Microsoft YaHei UI", 9),
            wraplength=360,
        )
        label.pack()

    def hide(self, event=None) -> None:
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None


CONFIG_PATH = app_dir() / "settings.json"
DEEPSEEK_BUY_URL = "https://platform.deepseek.com/"
EXTRA_PROMPT_PLACEHOLDER = "（可选）追加到 LLM system prompt 末尾的额外要求，例如：保持口语化、统一女性代词、禁止扩写……"
APP_VERSION = "v1.03"
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
        saved_settings = self._load_settings()
        self._recent_files = self._load_recent_files(saved_settings)
        self.root = Tk()
        self.root.title(f"千星奇域15国直接翻译工具（无术语表） {APP_VERSION}")
        saved_geo = saved_settings.get("window_geometry")
        self.root.geometry(str(saved_geo) if isinstance(saved_geo, str) and "x" in str(saved_geo) else "1400x1020")
        self.root.minsize(1200, 820)
        self.m3_colors = m3_theme.apply_m3_theme(self.root)
        self._set_window_icon()
        if sys.platform == "win32":
            try:
                dpi = ctypes.windll.user32.GetDpiForWindow(self.root.winfo_id())
                self.root.tk.call("tk", "scaling", dpi / 72)
            except Exception:
                pass

        self.input_path = StringVar()
        self.output_dir = StringVar(value=str(Path.cwd() / "outputs"))
        self.run_stage3 = BooleanVar(value=False)
        self.overwrite = BooleanVar(value=False)
        self.force_translate_all = BooleanVar(value=False)
        self.llm_endpoint = StringVar(value=str(saved_settings.get("llm_endpoint", DEFAULT_ENDPOINT)))
        self.llm_model = StringVar(value=str(saved_settings.get("llm_model", DEFAULT_MODEL)))
        self.llm_api_key = StringVar(value=str(saved_settings.get("llm_api_key", os.getenv("DEEPSEEK_API_KEY", ""))))
        self.llm_threads = IntVar(value=int(saved_settings.get("llm_threads", 3)))
        self.flush_interval = IntVar(value=int(saved_settings.get("flush_interval", 5)))
        self.extra_prompt = StringVar(value=str(saved_settings.get("extra_prompt", "")))
        self._messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self._running = False
        self._stop_event: threading.Event | None = None
        self.log_text: Text | None = None
        self.extra_prompt_text: Text | None = None

        self._build()
        self._fit_window()
        self._append_log("请选择需要处理的 CSV。所有翻译由 LLM 直接生成。")
        self.root.after(150, self._poll_messages)
        self.root.after(200, self._set_pane_sizes)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        try:
            self._save_settings()
        except Exception:
            pass
        self.root.destroy()

    def _set_pane_sizes(self) -> None:
        """Lock the left pane to ~70% width and the right log pane narrow."""
        try:
            body = getattr(self, "_body_pane", None)
            if body is None:
                return
            body_width = body.winfo_width()
            left_width = max(900, int(body_width * 0.72))
            body.sashpos(0, left_width)
        except Exception:
            pass

    def _fit_window(self) -> None:
        """Size the window so the full layout is visible, clamped to the screen."""
        try:
            self.root.update_idletasks()
            req_w = self.root.winfo_reqwidth()
            req_h = self.root.winfo_reqheight()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            min_w, min_h = 1200, 820
            max_w = int(sw * 0.98)
            max_h = int(sh * 0.98)
            # Use the explicitly configured geometry, clamped to the screen.
            cur = self.root.geometry()
            parts = cur.split("+")[0].split("x")
            req_w = int(parts[0]) if len(parts) == 2 else 1320
            req_h = int(parts[1]) if len(parts) == 2 else 880
            w = max(min_w, min(req_w, max_w))
            h = max(min_h, min(req_h, max_h))
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 4)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            self.root.minsize(min_w, min_h)
        except Exception:
            pass

    def _set_window_icon(self) -> None:
        """Set the window icon from the packaged or source app/icon.ico."""
        try:
            icon_path = resource_path("app/icon.ico")
            if icon_path.is_file():
                self.root.iconbitmap(str(icon_path))
        except Exception:
            pass

    def _load_settings(self) -> dict[str, object]:
        try:
            if CONFIG_PATH.is_file():
                with CONFIG_PATH.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_settings(self) -> None:
        data = {
            "llm_endpoint": self.llm_endpoint.get().strip(),
            "llm_model": self.llm_model.get().strip(),
            "llm_api_key": self.llm_api_key.get().strip(),
            "llm_threads": int(self.llm_threads.get()),
            "flush_interval": int(self.flush_interval.get()),
            "extra_prompt": self.extra_prompt.get().strip(),
            "recent_files": self._recent_files,
            "window_geometry": self.root.geometry(),
        }
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_recent_files(self, settings: dict[str, object]) -> list[str]:
        recent = settings.get("recent_files")
        if isinstance(recent, list):
            return [str(p) for p in recent if isinstance(p, str) and Path(p).is_file()][:MAX_RECENT_FILES]
        return []

    def _add_recent_file(self, path: str) -> None:
        path = str(path)
        if path in self._recent_files:
            self._recent_files.remove(path)
        self._recent_files.insert(0, path)
        self._recent_files = self._recent_files[:MAX_RECENT_FILES]
        self._update_recent_menu()

    def _update_recent_menu(self) -> None:
        if not hasattr(self, "recent_menu"):
            return
        self.recent_menu.delete(0, "end")
        if not self._recent_files:
            self.recent_menu.add_command(label="（无最近文件）", state="disabled")
            return
        for p in self._recent_files:
            self.recent_menu.add_command(
                label=p,
                command=lambda path=p: self._set_input_from_recent(path),
            )

    def _set_input_from_recent(self, path: str) -> None:
        self.input_path.set(path)
        if not self.output_dir.get():
            self.output_dir.set(str(Path(path).parent / "outputs"))

    def _sync_extra_prompt(self) -> None:
        if hasattr(self, "extra_prompt_text"):
            value = self.extra_prompt_text.get("1.0", "end-1c").strip()
            if value == EXTRA_PROMPT_PLACEHOLDER:
                value = ""
            self.extra_prompt.set(value)

    def _setup_prompt_placeholder(self, widget: Text) -> None:
        """Show grey placeholder text when the extra-prompt box is empty."""
        c = self.m3_colors

        def _is_placeholder() -> bool:
            return widget.get("1.0", "end-1c").strip() == EXTRA_PROMPT_PLACEHOLDER

        def _on_focus_in(_event=None) -> None:
            if _is_placeholder():
                widget.delete("1.0", "end")
                widget.configure(foreground=c["text"])

        def _on_focus_out(_event=None) -> None:
            if not widget.get("1.0", "end-1c").strip():
                widget.insert("1.0", EXTRA_PROMPT_PLACEHOLDER)
                widget.configure(foreground=c["outline"])

        widget.bind("<FocusIn>", _on_focus_in)
        widget.bind("<FocusOut>", _on_focus_out)
        if not widget.get("1.0", "end-1c").strip():
            widget.insert("1.0", EXTRA_PROMPT_PLACEHOLDER)
            widget.configure(foreground=c["outline"])

    def _build(self) -> None:
        c = self.m3_colors
        # Layout note: bottom-docked widgets (action bar, progress, caption) are
        # packed with side="bottom" BEFORE the body pane, so pack always grants
        # them their requested height first. This keeps the controls visible on
        # high-DPI displays where the content requests more space than the window.
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        # --- Header: logo + title/subtitle + tonal badge ---
        header = ttk.Frame(outer)
        header.pack(side="top", fill="x", pady=(0, 12))

        # Accent bar spans the full header width at the bottom of the header.
        accent = ttk.Frame(header, height=3, style="Accent.TFrame")
        accent.pack(side="bottom", fill="x", pady=(12, 0))
        # Tonal badge docks right first so it is never clipped on narrow windows.
        badge = ttk.Frame(header, style="Badge.TFrame", padding=(12, 5))
        badge.pack(side="right", anchor="n", pady=(6, 0))
        ttk.Label(badge, text="无术语表模式", style="Badge.TLabel").pack()

        self._logo_img = self._load_logo()
        if self._logo_img is not None:
            ttk.Label(header, image=self._logo_img).pack(side="left", padx=(0, 14))
        title_box = ttk.Frame(header)
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="千星奇域15国直接翻译工具", style="Title.TLabel").pack(
            anchor="w", pady=(2, 0)
        )
        ttk.Label(
            title_box,
            text="无术语表 · 由 LLM 直接生成 15 国语言翻译",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        # --- Body: left scrollable config + right log (packed at the end) ---
        body = ttk.PanedWindow(outer, orient="horizontal")
        self._body_pane = body

        left = m3_theme.ScrollableFrame(body, colors=c)
        left.content.columnconfigure(0, weight=1)
        right = ttk.Frame(body)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        body.add(left, weight=4)
        body.add(right, weight=1)
        cfg = left.content
        right.rowconfigure(0, weight=1)

        # --- I/O card ---
        io_card = ttk.LabelFrame(cfg, text="文件", style="M3.TLabelframe")
        io_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        io_card.columnconfigure(1, weight=1)

        ttk.Label(io_card, text="输入 CSV").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(io_card, textvariable=self.input_path).grid(
            row=0, column=1, sticky="ew", padx=8
        )
        ttk.Button(io_card, text="选择...", style="Surface.TButton", command=self.choose_input).grid(
            row=0, column=2, sticky="ew", padx=(0, 6)
        )
        self.recent_menu_btn = ttk.Menubutton(
            io_card, text="最近", style="Surface.TButton", direction="below"
        )
        self.recent_menu_btn.grid(row=0, column=3, sticky="ew")
        self.recent_menu = Menu(self.recent_menu_btn, tearoff=0)
        self.recent_menu_btn.configure(menu=self.recent_menu)
        self._update_recent_menu()

        ttk.Label(io_card, text="输出目录").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(io_card, textvariable=self.output_dir).grid(
            row=1, column=1, sticky="ew", padx=8
        )
        ttk.Button(io_card, text="选择...", style="Surface.TButton", command=self.choose_output_dir).grid(
            row=1, column=2, sticky="ew"
        )

        # --- Options card ---
        options = ttk.LabelFrame(cfg, text="翻译选项", style="M3.TLabelframe")
        options.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        cb = ttk.Checkbutton(options, text="覆盖已有", style="G.TCheckbutton", variable=self.overwrite)
        cb.grid(row=0, column=0, sticky="w", padx=(0, 16))
        Tooltip(cb, "即使目标语言列已有内容，也用 LLM 翻译重新填充。")
        cb = ttk.Checkbutton(
            options, text="强制全部翻译", style="G.TCheckbutton", variable=self.force_translate_all
        )
        cb.grid(row=0, column=1, sticky="w", padx=(0, 16))
        Tooltip(cb, "将所有行的「是否需要翻译」列强制改为 TRUE，全部参与翻译。")
        cb = ttk.Checkbutton(options, text="后处理验证", style="G.TCheckbutton", variable=self.run_stage3)
        cb.grid(row=0, column=2, sticky="w")
        Tooltip(cb, "修正 protected token 错误并检查同一中文是否对应不同翻译。")

        # --- LLM options card ---
        llm = ttk.LabelFrame(cfg, text="LLM 选项", style="M3.TLabelframe")
        llm.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        llm.columnconfigure(1, weight=1)
        llm.columnconfigure(3, weight=1)

        ttk.Label(llm, text="Endpoint").grid(row=0, column=0, sticky="w", pady=4)
        ent = ttk.Entry(llm, textvariable=self.llm_endpoint)
        ent.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=4)
        Tooltip(ent, "LLM API 的 OpenAI 兼容接口地址。默认使用 DeepSeek。")

        ttk.Label(llm, text="Model").grid(row=1, column=0, sticky="w", pady=4)
        ent = ttk.Entry(llm, textvariable=self.llm_model)
        ent.grid(row=1, column=1, sticky="ew", padx=(8, 16), pady=4)
        Tooltip(ent, "LLM 模型名称。默认 deepseek-v4-flash。")

        ttk.Label(llm, text="API Key").grid(row=1, column=2, sticky="w", pady=4)
        ent = ttk.Entry(llm, textvariable=self.llm_api_key, show="*")
        ent.grid(row=1, column=3, sticky="ew", padx=(8, 0), pady=4)
        Tooltip(ent, "LLM 服务商提供的 API Key。")

        ttk.Label(llm, text="线程").grid(row=2, column=0, sticky="w", pady=4)
        sp = ttk.Spinbox(llm, from_=1, to=16, width=8, textvariable=self.llm_threads)
        sp.grid(row=2, column=1, sticky="w", padx=(8, 16), pady=4)
        Tooltip(sp, "同时请求 LLM 的并发数量。建议 1~5，过高可能触发 API 限流。")

        ttk.Label(llm, text="写入间隔").grid(row=2, column=2, sticky="w", pady=4)
        sp = ttk.Spinbox(llm, from_=1, to=50, width=8, textvariable=self.flush_interval)
        sp.grid(row=2, column=3, sticky="w", padx=(8, 0), pady=4)
        Tooltip(sp, "每完成多少个 LLM 任务就实时写入一次结果 CSV。")

        # Custom extra prompt
        ttk.Label(llm, text="自定义额外提示词").grid(row=3, column=0, sticky="nw", pady=(10, 4))
        prompt_frame = ttk.Frame(llm)
        prompt_frame.grid(row=3, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(10, 4))
        prompt_frame.columnconfigure(0, weight=1)
        self.extra_prompt_text = Text(
            prompt_frame,
            height=3,
            wrap="word",
            relief="solid",
            borderwidth=1,
            background=c["surface"],
            foreground=c["text"],
            insertbackground=c["primary"],
            highlightbackground=c["outline_variant"],
            highlightcolor=c["border_focus"],
            font=("Microsoft YaHei UI", 10),
            padx=10,
            pady=8,
        )
        self.extra_prompt_text.grid(row=0, column=0, sticky="ew")
        self.extra_prompt_text.insert("1.0", self.extra_prompt.get())
        self._setup_prompt_placeholder(self.extra_prompt_text)

        # --- Action bar (bottom-docked below; packed at the end of _build) ---
        action_bar = ttk.Frame(outer)
        action_bar.columnconfigure(0, weight=1)
        action_bar.columnconfigure(1, weight=1)
        action_bar.columnconfigure(2, weight=1)
        action_bar.columnconfigure(3, weight=1)

        self.run_button = ttk.Button(action_bar, text="开始翻译", style="M3.TButton", command=self.run)
        self.run_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.stop_button = ttk.Button(
            action_bar, text="暂停", style="Tonal.TButton", command=self.stop, state="disabled"
        )
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(action_bar, text="打开输出目录", style="Surface.TButton", command=self.open_output_dir).grid(
            row=0, column=2, sticky="ew", padx=(0, 6)
        )
        ttk.Button(action_bar, text="使用说明", style="Outline.TButton", command=self.show_help).grid(
            row=0, column=3, sticky="ew"
        )

        # --- Log card (right pane, expands) ---
        log_box = ttk.LabelFrame(right, text="日志", style="M3.TLabelframe")
        log_box.grid(row=0, column=0, sticky="nsew")
        log_box.rowconfigure(0, weight=1)
        log_box.columnconfigure(0, weight=1)
        clear_btn = ttk.Button(
            log_box, text="清空", style="Text.TButton", command=self._clear_log
        )
        clear_btn.place(relx=1.0, x=-10, y=6, anchor="ne")
        self.log_text = Text(
            log_box,
            height=8,
            width=48,
            wrap="word",
            relief="flat",
            state="disabled",
            background=c["surface_container_low"],
            foreground=c["text"],
            insertbackground=c["primary"],
            font=("Cascadia Mono", 10),
            padx=10,
            pady=8,
            spacing1=1,
            spacing3=1,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_box, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.progress = ttk.Progressbar(outer, mode="determinate", maximum=100, value=0)

        # Single unobtrusive license / contact line at the very bottom.
        caption = ttk.Label(
            outer,
            text="仅供学习交流使用 · 禁止售卖和对外分享 · 用户QQ群：1007538100",
            style="Caption.TLabel",
        )
        # Bottom dock order (first packed = lowest): caption, progress, action bar.
        # Packing these before the body guarantees they are never clipped when the
        # window is shorter than the requested content height (e.g. 150% DPI).
        caption.pack(side="bottom", anchor="w", pady=(2, 0))
        self.progress.pack(side="bottom", fill="x", pady=(6, 2))
        action_bar.pack(side="bottom", fill="x", pady=(0, 10))

        # Body fills whatever space remains between header and bottom bar.
        body.pack(side="top", fill="both", expand=True)

    def _load_logo(self) -> PhotoImage | None:
        """Load the header logo (96px asset downsampled to 48px)."""
        try:
            logo_path = resource_path("app/app_logo.png")
            if logo_path.is_file():
                return PhotoImage(file=str(logo_path)).subsample(2, 2)
        except Exception:
            pass
        return None

    def _append_log(self, message: str) -> None:
        if self.log_text is None:
            return
        self.log_text.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        if self.log_text is None:
            return
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _queue_log(self, message: str) -> None:
        self._messages.put(("log", message))

    def _queue_progress(self, value: float) -> None:
        self._messages.put(("progress", max(0.0, min(100.0, value))))

    def _queue_stage2_progress(self, message: str, completed: int | None, total: int | None) -> None:
        self._queue_log(message)
        if total and total > 0 and completed is not None:
            ratio = completed / total
            self._queue_progress(ratio * 100.0)

    def choose_input(self) -> None:
        path = filedialog.askopenfilename(
            title="选择输入 CSV",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if path:
            self.input_path.set(path)
            self._add_recent_file(path)
            if not self.output_dir.get():
                self.output_dir.set(str(Path(path).parent / "outputs"))

    def choose_output_dir(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_dir.set(path)

    def open_output_dir(self) -> None:
        output_dir = Path(self.output_dir.get()).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(output_dir)

    def show_help(self) -> None:
        help_text = (
            "使用说明\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "1. 选择输入 CSV 和输出目录。\n"
            "2. 填写 LLM 选项（Endpoint / Model / API Key）。\n"
            "3. 按需勾选翻译选项，点击「开始翻译」。\n"
            "4. 运行中可暂停，再次点击继续从断点续跑。\n"
            "\n"
            "翻译选项\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• 覆盖已有：即使已有译文也重新填充。\n"
            "• 强制全部翻译：所有行都参与翻译。\n"
            "• 后处理验证：修正 token 并检查一致性。\n"
            "\n"
            "处理逻辑\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• 不查询术语表，所有翻译由 LLM 直接生成。\n"
            "• 按唯一中文分组，每组一次返回 15 国语言。\n"
            "• 自动保留 <...>、{...}、\\n 等格式标签。\n"
            "\n"
            "输出\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• 翻译结果：*_direct.csv\n"
            "• 报告：*_direct_report.csv\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        messagebox.showinfo("使用说明", help_text)

    def _show_api_key_missing(self) -> None:
        """Show an M3-styled error dialog when API key is missing, with a link to buy credits."""
        c = self.m3_colors
        dialog = Toplevel(self.root)
        dialog.title("缺少 API Key")
        dialog.geometry("520x300")
        dialog.minsize(460, 260)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        try:
            dialog.configure(background=c["background"])
        except Exception:
            pass

        body = ttk.Frame(dialog, padding=24)
        body.pack(fill="both", expand=True)

        banner = ttk.Frame(body, style="Card.TFrame")
        banner.pack(fill="x")
        try:
            banner.configure(background=c["error_container"])
        except Exception:
            pass
        head = ttk.Label(
            banner,
            text="⚠  缺少 API Key",
            style="M3Error.TLabel",
        )
        head.pack(anchor="w", padx=14, pady=12)
        try:
            head.configure(background=c["error_container"])
        except Exception:
            pass

        ttk.Label(
            body,
            text="本工具需要调用 LLM API 才能翻译。\n请在「LLM 选项」中填写 API Key 后再开始。",
            style="Subtitle.TLabel",
            wraplength=460,
        ).pack(anchor="w", pady=(16, 14))

        link = ttk.Label(
            body,
            text="还没有 Key？前往 platform.deepseek.com 购买（少量充值即可）",
            style="Subtitle.TLabel",
            foreground=c["primary"],
            cursor="hand2",
            wraplength=460,
        )
        link.pack(anchor="w", pady=(0, 18))
        link.bind("<Button-1>", lambda _event: webbrowser.open(DEEPSEEK_BUY_URL))

        btns = ttk.Frame(body)
        btns.pack(fill="x", side="bottom")
        ttk.Button(
            btns,
            text="前往 DeepSeek 购买",
            style="Outline.TButton",
            command=lambda: webbrowser.open(DEEPSEEK_BUY_URL),
        ).pack(side="left")
        ttk.Button(
            btns,
            text="我知道了",
            style="M3.TButton",
            command=dialog.destroy,
        ).pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

    def run(self) -> None:
        if self._running:
            return
        input_path = Path(self.input_path.get()).expanduser()
        if not input_path.is_file():
            messagebox.showerror("缺少输入文件", "请先上传一个有效的 CSV 文件。")
            return
        if not self.llm_api_key.get().strip():
            self._show_api_key_missing()
            return
        self._sync_extra_prompt()
        try:
            self._save_settings()
        except Exception as exc:
            self._append_log(f"保存 LLM 配置失败：{exc}")

        output_dir = Path(self.output_dir.get()).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = input_path.stem
        direct_output_path = output_dir / f"{stem}_direct.csv"
        direct_report_path = output_dir / f"{stem}_direct_report.csv"
        stage3_report_path = output_dir / f"{stem}_validation_report.csv"
        stage3_fixed_path = output_dir / f"{stem}_validated.csv"

        # Placeholder argument only; use_terms=False means it is never read.
        dummy_term_path = Path("")

        self._running = True
        self._stop_event = threading.Event()
        self.run_button.configure(text="翻译中...", state="disabled")
        self.stop_button.configure(text="暂停", state="normal")
        self.progress.configure(value=0)
        self._append_log("开始翻译。不查询术语表，所有翻译直接由 LLM 生成。")

        thread = threading.Thread(
            target=self._run_worker,
            args=(
                input_path,
                dummy_term_path,
                direct_output_path,
                direct_report_path,
                stage3_report_path,
                stage3_fixed_path,
            ),
            daemon=True,
        )
        thread.start()

    def stop(self) -> None:
        if not self._running or self._stop_event is None:
            return
        self._stop_event.set()
        self.stop_button.configure(state="disabled")
        self._append_log("正在请求暂停，请等待当前任务完成...")

    def _run_worker(
        self,
        input_path: Path,
        term_path: Path,
        direct_output_path: Path,
        direct_report_path: Path,
        stage3_report_path: Path,
        stage3_fixed_path: Path,
    ) -> None:
        try:
            stats: dict[str, object] = {}
            stage3_enabled = bool(self.run_stage3.get())

            if bool(self.force_translate_all.get()):
                self._queue_log("正在强制翻译所有行：将「是否需要翻译」列全部改为 TRUE。")
                fieldnames, rows = read_input(input_path)
                for row in rows:
                    row[NEED_TRANSLATE_COLUMN] = "TRUE"
                forced_path = direct_output_path.with_name(f"{input_path.stem}_forced.csv")
                write_csv(forced_path, fieldnames, rows)
                input_path = forced_path
                self._queue_log(f"强制翻译文件已生成：{forced_path}")

            self._queue_log(
                "开始直接翻译。"
                f"线程数 {int(self.llm_threads.get())}，"
                f"模型 {self.llm_model.get().strip() or DEFAULT_MODEL}。"
            )
            stage2_stats = run_stage2(
                Stage2Config(
                    input_path=input_path,
                    term_path=term_path,
                    output_path=direct_output_path,
                    report_path=direct_report_path,
                    endpoint=self.llm_endpoint.get().strip() or DEFAULT_ENDPOINT,
                    api_key=self.llm_api_key.get().strip(),
                    model=self.llm_model.get().strip() or DEFAULT_MODEL,
                    threads=int(self.llm_threads.get()),
                    use_terms=False,
                    include_false=False,
                    overwrite=bool(self.overwrite.get()),
                    progress_callback=self._queue_stage2_progress,
                    stop_event=self._stop_event,
                    flush_interval=int(self.flush_interval.get()),
                    extra_prompt=self.extra_prompt.get().strip(),
                )
            )
            stats["direct"] = stage2_stats
            self._queue_progress(66.6 if stage3_enabled else 100.0)
            self._queue_log(
                "翻译完成。"
                f"填写空白单元格 {stage2_stats['stage2_filled_cells']}，"
                f"需人工复核 {stage2_stats['manual_review']}。"
            )

            if self._stop_event and self._stop_event.is_set():
                raise Stage2Paused()

            if stage3_enabled:
                self._queue_log("第三阶段：开始后处理验证。")
                stage3_result = validate_and_fix(
                    input_path=direct_output_path,
                    output_path=stage3_fixed_path,
                    report_path=stage3_report_path,
                )
                stats["stage3"] = stage3_result
                changes = stage3_result.get("protected_changes", [])
                errors = stage3_result.get("consistency_errors", [])
                self._queue_progress(100.0)
                self._queue_log(
                    "第三阶段：完成。"
                    f"修正 protected token {len(changes)} 处，"
                    f"发现不一致 {len(errors)} 处。"
                )
                if errors:
                    self._queue_log("第三阶段：发现翻译不一致，请查看验证报告。")

            self._messages.put(("done", stats))
        except Stage2Paused:
            self._messages.put(("paused", {}))
        except Exception as exc:
            error_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self._messages.put(("error", error_text))

    def _poll_messages(self) -> None:
        try:
            kind, payload = self._messages.get_nowait()
        except queue.Empty:
            self.root.after(150, self._poll_messages)
            return

        if kind == "log":
            self._append_log(str(payload))
            self.root.after(150, self._poll_messages)
            return
        if kind == "progress":
            self.progress.configure(value=float(payload))
            self.root.after(150, self._poll_messages)
            return

        self._running = False
        self.stop_button.configure(text="暂停", state="disabled")
        if kind == "done":
            self.progress.configure(value=100)
            self.run_button.configure(text="开始翻译", state="normal")
            stats = payload
            lines = ["处理完成。"]
            direct = stats.get("direct") if isinstance(stats, dict) else None
            if isinstance(direct, dict):
                lines.extend(
                    [
                        "直接翻译：",
                        f"  唯一中文：{direct['stage2_unique_source_texts']}",
                        f"  填写空白单元格：{direct['stage2_filled_cells']}",
                        f"  需人工复核：{direct['manual_review']}",
                        f"  最终输出：{direct['output']}",
                        f"  报告：{direct['stage2_report']}",
                        f"  运行日志：{direct['stage2_run_log']}",
                    ]
                )
            stage3 = stats.get("stage3") if isinstance(stats, dict) else None
            if isinstance(stage3, dict):
                changes = stage3.get("protected_changes", [])
                errors = stage3.get("consistency_errors", [])
                lines.extend(
                    [
                        "第三阶段：",
                        f"  修正 protected token：{len(changes)}",
                        f"  不一致错误：{len(errors)}",
                        f"  修正输出：{stage3.get('output_path') or '无'}",
                        f"  验证报告：{stage3.get('report_path') or '无'}",
                    ]
                )
            self._append_log("\n".join(lines))
            messagebox.showinfo("完成", "CSV 已处理完成。")
        elif kind == "paused":
            self.run_button.configure(text="继续翻译", state="normal")
            self._append_log("处理已暂停。进度已保存，可以点击「继续翻译」续跑。")
            messagebox.showinfo("已暂停", "处理已暂停。进度已保存，可点击「继续翻译」续跑。")
        else:
            self.run_button.configure(text="开始翻译", state="normal")
            self._append_log("处理失败，错误详情如下：")
            self._append_log(str(payload))
            messagebox.showerror("处理失败", str(payload).splitlines()[-1] if str(payload).splitlines() else str(payload))
        self.root.after(150, self._poll_messages)

    def mainloop(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    DirectTranslatorApp().mainloop()
