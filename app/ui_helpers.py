"""Validation and display helpers shared by the desktop UI and its tests."""

from pathlib import Path
from urllib.parse import urlparse

from llm_stage2 import build_tasks
from translate_from_terms import SOURCE_COLUMN, NEED_TRANSLATE_COLUMN, read_input, target_columns


def inspect_input(path: Path, include_false=False, overwrite=False):
    if not path.is_file():
        raise ValueError("请选择一份有效的输入 CSV 文件。")
    try:
        headers, rows = read_input(path)
    except UnicodeDecodeError as exc:
        raise ValueError("输入文件需要 UTF-8 编码，请另存为 UTF-8 CSV 后重试。") from exc
    missing = [name for name in (SOURCE_COLUMN, NEED_TRANSLATE_COLUMN) if name not in headers]
    if missing:
        raise ValueError("缺少必要列：" + "、".join(missing) + "。请选择多语言文本管理导出的 CSV。")
    if len(headers) != len(set(headers)):
        raise ValueError("输入 CSV 包含重复列名，请修正后重试。")
    targets = target_columns(headers)
    if not targets:
        raise ValueError("未找到目标语言列，例如「英语」「日语」。")
    tasks = build_tasks(rows, targets, include_false, overwrite)
    return headers, rows[:30], {
        "rows": len(rows), "targets": len(targets), "groups": len(tasks),
        "eligible_rows": sum(len(task.rows) for task in tasks.values()),
    }


def bounded_integer(value, label, minimum, maximum):
    try:
        number = int(str(value).strip())
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label}请输入 {minimum}–{maximum} 之间的整数。") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{label}请输入 {minimum}–{maximum} 之间的整数。")
    return number


def validate_endpoint(value: str) -> str:
    value = value.strip()
    parts = urlparse(value)
    if parts.scheme not in ("https", "http") or not parts.hostname or any(c.isspace() for c in value):
        raise ValueError("服务地址需要是完整的 http:// 或 https:// 接口地址。")
    return value


def duration_label(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"
