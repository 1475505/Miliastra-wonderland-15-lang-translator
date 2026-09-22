"""Read user-selected CSV/TSV glossaries; no bundled data or default paths."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from translate_from_terms import FRIENDLY_TO_TERM


DELIMITERS = {"comma": ",", "tab": "\t", "semicolon": ";"}


def _read_table(path: Path, delimiter: str = "auto") -> tuple[list[str], list[list[str]]]:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("术语表编码无法识别，请另存为 UTF-8 CSV / TSV。") from exc
    if not content.strip():
        raise ValueError("术语表为空，请至少填写表头和一条术语。")
    if delimiter == "auto":
        # Parse the first record with each candidate: quoted separators must not
        # count as columns, and multiline quoted headers must remain one record.
        separator = max(
            DELIMITERS.values(),
            key=lambda sep: len(next(csv.reader(io.StringIO(content), delimiter=sep), [])),
        )
    elif delimiter in DELIMITERS:
        separator = DELIMITERS[delimiter]
    else:
        raise ValueError(f"不支持的分隔符：{delimiter}")
    try:
        reader = csv.reader(io.StringIO(content, newline=""), delimiter=separator, strict=True)
        headers = [value.strip() for value in next(reader)]
        if any(not value for value in headers) or len(set(headers)) != len(headers):
            raise ValueError("术语表列名不能为空或重复。")
        rows = []
        for row in reader:
            if not any(value.strip() for value in row):
                continue
            if len(row) > len(headers):
                raise ValueError(f"术语表第 {reader.line_num} 行的列数超过表头，请检查分隔符和引号。")
            rows.append(row + [""] * (len(headers) - len(row)))
        return headers, rows
    except csv.Error as exc:
        raise ValueError(f"术语表 CSV / TSV 格式错误：{exc}") from exc


def read_glossary_headers(path: Path, delimiter: str = "auto") -> list[str]:
    return _read_table(path, delimiter)[0]


def load_column_mapping(path: Path) -> dict[str, str]:
    mapping = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(mapping, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in mapping.items()
    ):
        raise ValueError('列映射必须是 JSON 对象，例如 {"原文": "简体中文", "译文": "英语"}。')
    return mapping


def read_glossary(
    path: Path, column_mapping: dict[str, str] | None = None, delimiter: str = "auto"
) -> tuple[list[str], list[list[str]]]:
    """Normalize a glossary, merging complementary rows and rejecting conflicts.

    Without a mapping, accept Chinese language names or CHS/CHT/EN/... codes.
    With a mapping, only explicitly selected columns are used.
    """
    headers, rows = _read_table(path, delimiter)
    if column_mapping is not None:
        unknown = set(column_mapping) - set(headers)
        if unknown:
            raise ValueError(f"列映射中的列不存在：{'、'.join(sorted(unknown))}")
    selected: list[tuple[int, str]] = []
    valid_codes = set(FRIENDLY_TO_TERM.values())
    for index, header in enumerate(headers):
        name = column_mapping.get(header, "") if column_mapping is not None else header
        code = FRIENDLY_TO_TERM.get(name, name.upper())
        if code not in valid_codes:
            if column_mapping is not None and name:
                raise ValueError(f"未知语言：{name}")
            continue
        if code in [existing for _, existing in selected]:
            raise ValueError(f"多个列映射到了同一语言：{code}")
        selected.append((index, code))
    codes = [code for _, code in selected]
    if "CHS" not in codes:
        raise ValueError("请将源文列映射到「简体中文」，或使用简体中文 / CHS 列名。")
    if len(codes) < 2:
        raise ValueError("术语表至少需要简体中文和一种目标语言。")
    merged: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(rows, start=2):
        values = {code: row[index].strip() for index, code in selected}
        if not any(values.values()):
            continue
        source = values["CHS"]
        if not source:
            raise ValueError(f"术语表记录 {row_number} 缺少简体中文源文。")
        if not any(value for code, value in values.items() if code != "CHS"):
            continue
        entry = merged.setdefault(source, {})
        for code, value in values.items():
            if not value:
                continue
            if entry.get(code) and entry[code] != value:
                raise ValueError(f"术语「{source}」的 {code} 译文冲突，请合并或修正重复记录。")
            entry[code] = value
    if not merged:
        raise ValueError("术语表没有有效译文，请至少填写一条源文及对应译文。")
    return codes, [[entry.get(code, "") for code in codes] for entry in merged.values()]
