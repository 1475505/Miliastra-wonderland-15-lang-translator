#!/usr/bin/env python3
"""Second-stage LLM translation using local term-table references."""

from __future__ import annotations

import argparse
import csv
import heapq
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable

from translate_from_terms import (
    INPUT_TO_TERM_LANG,
    NEED_TRANSLATE_COLUMN,
    PROTECTED_RE,
    SOURCE_COLUMN,
    is_translatable,
    read_input,
    read_term_rows,
    score_text,
    target_columns,
    write_csv,
)

DEFAULT_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"

TERM_TO_INPUT_LANG = {value: key for key, value in INPUT_TO_TERM_LANG.items()}
TERM_LANG_ORDER = ["CHT", "EN", "KR", "JP", "ES", "FR", "RU", "TH", "VI", "DE", "ID", "PT", "TR", "IT"]
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")


@dataclass
class Stage2Config:
    input_path: Path
    term_path: Path
    output_path: Path
    report_path: Path
    endpoint: str = DEFAULT_ENDPOINT
    api_key: str = ""
    model: str = DEFAULT_MODEL
    threads: int = 3
    use_terms: bool = True
    include_false: bool = False
    overwrite: bool = False
    max_retries: int = 2
    progress_callback: Callable[[str, int | None, int | None], None] | None = None
    run_log_path: Path | None = None
    stop_event: threading.Event | None = None
    flush_interval: int = 5
    extra_prompt: str = ""
    custom_term_path: Path | None = None
    stage1_matched_terms: dict[str, dict[str, str]] | None = None
    pivot_language: str = ""
    only_columns: list[str] | None = None
    extra_prompt: str = ""
    # Number of nearest term references to inject per source in the fuzzy
    # fallback pass. Sources that already have references (exact/contains
    # matches) get the top K; sources with no references get max(3, K) as a
    # wider fallback. Default 1 keeps prompts tight for short UI text; raise it
    # for content-heavy long text where a few nearest terms may not capture the
    # relevant vocabulary.
    nearest_top_k: int = 1


@dataclass
class Stage2Task:
    source: str
    rows: list[int]
    missing_columns: list[str]
    protected_tokens: list[str]
    references: list[dict[str, object]]
    pivot_original: str = ""
    pivot_language: str = ""


def is_copy_as_is_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if not CHINESE_RE.search(stripped):
        return True
    without_tokens = PROTECTED_RE.sub("", stripped)
    without_symbols = "".join(
        char for char in without_tokens if not unicodedata.category(char).startswith(("P", "S", "N", "Z"))
    )
    return not CHINESE_RE.search(without_symbols)


def protected_tokens(text: str) -> list[str]:
    tokens = PROTECTED_RE.findall(text)
    if "\\n" in text:
        tokens.append("\\n")
    return tokens


def build_tasks(
    rows: list[dict[str, str]],
    targets: list[str],
    include_false: bool,
    overwrite: bool,
    only_columns: list[str] | None = None,
) -> dict[str, Stage2Task]:
    tasks: dict[str, Stage2Task] = {}
    effective_targets = [c for c in targets if only_columns is None or c in only_columns]
    for row_index, row in enumerate(rows, start=2):
        if not is_translatable(row, include_false):
            continue
        source = row.get(SOURCE_COLUMN, "").strip()
        if not source:
            continue
        missing = [
            col
            for col in effective_targets
            if overwrite or not (row.get(col) or "").strip()
        ]
        if not missing:
            continue
        task = tasks.setdefault(
            source,
            Stage2Task(
                source=source,
                rows=[],
                missing_columns=[],
                protected_tokens=protected_tokens(source),
                references=[],
            ),
        )
        task.rows.append(row_index)
        for column in missing:
            if column not in task.missing_columns:
                task.missing_columns.append(column)
    return tasks


def term_reference_from_row(headers: list[str], row: list[str], match_type: str, score: float) -> dict[str, object]:
    values = {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}
    return {
        "match_type": match_type,
        "score": round(score, 2),
        "chs": values.get("CHS", ""),
        "translations": {
            input_lang: values.get(term_lang, "")
            for term_lang, input_lang in TERM_TO_INPUT_LANG.items()
            if values.get(term_lang, "")
        },
    }


def add_stage1_used_references(
    tasks: dict[str, Stage2Task],
    stage1_matched_terms: dict[str, dict[str, str]] | None,
    progress_callback: Callable[[str, int | None, int | None], None] | None = None,
) -> None:
    if not stage1_matched_terms:
        return
    terms = [(chs, trans) for chs, trans in stage1_matched_terms.items() if chs]
    if not terms:
        return
    if progress_callback:
        progress_callback(
            f"术语参考：开始注入第一阶段实际用到的术语（{len(terms)} 条）。", None, None
        )
    # Index terms by first character so each source only scans candidates whose
    # first char actually occurs in the source (keeps this O(sum of candidate
    # hits) instead of O(terms * tasks) substring scans).
    by_first_char: dict[str, list[tuple[str, dict[str, str]]]] = {}
    for chs, trans in terms:
        if not chs:
            continue
        by_first_char.setdefault(chs[0], []).append((chs, trans))

    max_per_source = 6
    seen = {source: {ref.get("chs") for ref in task.references if ref.get("chs")} for source, task in tasks.items()}
    counts = {source: len(task.references) for source, task in tasks.items()}
    injected = 0
    for source, task in tasks.items():
        if counts[source] >= max_per_source:
            continue
        source_chars = set(source)
        for first_char, candidate_terms in by_first_char.items():
            if counts[source] >= max_per_source:
                break
            if first_char not in source_chars:
                continue
            for chs, trans in candidate_terms:
                if counts[source] >= max_per_source:
                    break
                if chs == source or len(chs) > len(source) or chs in seen[source]:
                    continue
                if chs in source:
                    ref = {
                        "match_type": "stage1_used",
                        "score": 98.0,
                        "chs": chs,
                        "translations": {k: v for k, v in trans.items() if v},
                    }
                    task.references.append(ref)
                    seen[source].add(chs)
                    counts[source] += 1
                    injected += 1
    if progress_callback:
        progress_callback(f"术语参考：第一阶段术语注入完成，共注入 {injected} 条参考。", None, None)


def _count_term_rows(term_path) -> int | None:
    """Quick line count of the term file(s) for progress estimation."""
    try:
        paths = list(term_path) if isinstance(term_path, (list, tuple)) else [term_path]
        total = 0
        for p in paths:
            with Path(p).open("r", encoding="utf-8-sig") as f:
                total += sum(1 for _ in f)
        return total
    except Exception:
        return None


def collect_term_references(
    term_path,
    tasks: dict[str, Stage2Task],
    progress_callback: Callable[[str, int | None, int | None], None] | None = None,
    stage1_matched_terms: dict[str, dict[str, str]] | None = None,
    nearest_top_k: int = 1,
) -> None:
    if not tasks:
        return
    if nearest_top_k < 1:
        nearest_top_k = 1

    # Stage-1 actually-used terms: for every source being translated, check each
    # term CHS that stage 1 successfully matched somewhere; if that CHS appears
    # as a substring of the current source, inject it as a reference so the LLM
    # sees the official translation of the embedded term.
    add_stage1_used_references(tasks, stage1_matched_terms, progress_callback)

    max_contains_refs = 6
    active_sources = set(tasks)
    candidate_counts: dict[str, int] = {source: len(task.references) for source, task in tasks.items()}
    # Seed seen-chs from references already injected (e.g. stage-1 used terms) so
    # the contains/nearest passes don't re-add the same term CHS.
    seen_chs: dict[str, set[str]] = {
        source: {str(ref.get("chs", "")) for ref in task.references if ref.get("chs")}
        for source, task in tasks.items()
    }
    exact_count = 0

    # Read the term table once and reuse the parsed rows across all passes to
    # avoid re-reading and re-parsing the same file three (plus one for the
    # row-count estimate) times.
    term_rows = list(read_term_rows(term_path))

    # Pass 1: exact match short-circuits a source. Also collect source-in-term
    # candidates, which are usually useful for shortened UI text.
    if progress_callback:
        progress_callback("术语参考：开始精确匹配 / 待处理中文被术语表包含扫描。", None, None)
    for headers, row in term_rows:
        if "CHS" not in headers:
            raise ValueError("Term table must contain a CHS column")
        chs_index = headers.index("CHS")
        chs = row[chs_index] if chs_index < len(row) else ""
        if not chs:
            continue

        if chs in active_sources:
            tasks[chs].references = [term_reference_from_row(headers, row, "exact", 100.0)]
            active_sources.remove(chs)
            exact_count += 1
            continue

        for source in list(active_sources):
            if candidate_counts[source] >= max_contains_refs:
                continue
            if len(source) <= len(chs) and source in chs:
                append_term_reference(
                    tasks[source],
                    seen_chs[source],
                    term_reference_from_row(headers, row, "source_contained_in_term", 96.0),
                )
                candidate_counts[source] += 1

    if not active_sources:
        if progress_callback:
            progress_callback(f"术语参考：精确命中 {exact_count} 组，所有文本已获得参考。", None, None)
        return

    if progress_callback:
        candidate_source_count = sum(1 for source in active_sources if tasks[source].references)
        progress_callback(
            f"术语参考：第一轮完成。精确命中 {exact_count} 组，包含候选 {candidate_source_count} 组。",
            None,
            None,
        )

    # Pass 2: term-in-source candidates. If a source gets any candidate from
    # either contains direction, it will not enter fuzzy matching.
    needs_contained_scan = {
        source for source in active_sources if candidate_counts[source] < max_contains_refs
    }
    if needs_contained_scan:
        if progress_callback:
            progress_callback(f"术语参考：开始术语表中文被待处理中文包含扫描（{len(needs_contained_scan)} 组）。", None, None)
        for headers, row in term_rows:
            chs_index = headers.index("CHS")
            chs = row[chs_index] if chs_index < len(row) else ""
            if not chs:
                continue

            for source in list(needs_contained_scan):
                if candidate_counts[source] >= max_contains_refs:
                    needs_contained_scan.remove(source)
                    continue
                if len(chs) <= len(source) and chs in source:
                    append_term_reference(
                        tasks[source],
                        seen_chs[source],
                        term_reference_from_row(headers, row, "term_contained_in_source", 94.0),
                    )
                    candidate_counts[source] += 1

    fuzzy_sources = set(active_sources)
    if not fuzzy_sources:
        if progress_callback:
            progress_callback("术语参考：所有文本均已精确命中，跳过最近匹配。", None, None)
        return

    # Pass 3: fuzzy nearest references. Every source gets up to K (=
    # ``nearest_top_k``) nearest term matches. Sources that collected no
    # references in the exact/contains passes fall back to max(3, K) instead,
    # so the LLM always has at least 3 references to consult.
    nearest_k = nearest_top_k
    if progress_callback:
        progress_callback(
            f"术语参考：开始最近匹配兜底（{len(fuzzy_sources)} 组，有参考补 Top {nearest_k}，无参考补 Top {max(3, nearest_k)}）。",
            None,
            None,
        )
    # Build character sets for fast pre-filter — a term that shares no characters
    # with the source text cannot be a meaningful fuzzy match.
    source_char_sets = {source: set(source) for source in fuzzy_sources}
    # Union of all characters across every source: lets us skip a term entirely
    # (avoiding the full inner loop) when it shares no character with any source.
    all_source_chars: set[str] = set()
    for chars in source_char_sets.values():
        all_source_chars |= chars
    # Character-level inverted index: char -> sources that contain it. For a
    # given term we only visit sources sharing at least one character, which is
    # exactly the set that passed the old ``src_chars & term_chars`` filter, so
    # the result is identical while skipping sources with no overlap at all.
    char_to_sources: dict[str, list[str]] = {}
    for source, chars in source_char_sets.items():
        for ch in chars:
            char_to_sources.setdefault(ch, []).append(source)
    # Per-source min-heap of (score, -seq, chs, headers, row). The heap is
    # capped at ``max(3, K)`` so we always keep enough candidates to satisfy
    # the empty-set fallback (max(3, K)) below; sources with prior references
    # only take the top K from the same heap.
    heap_cap = max(3, nearest_k)
    top_heaps: dict[str, list[tuple[float, int, str, list[str], list[str]]]] = {
        source: [] for source in fuzzy_sources
    }
    # Total rows already known from the cached parse — no extra file scan.
    total_terms = len(term_rows)
    scanned = 0
    REPORT_EVERY = 10000
    for headers, row in term_rows:
        scanned += 1
        if progress_callback and scanned % REPORT_EVERY == 0:
            progress_callback(
                f"术语参考：最近匹配扫描中（{scanned} / {total_terms or '?'} 行）",
                scanned,
                total_terms,
            )
        chs_index = headers.index("CHS")
        chs = row[chs_index] if chs_index < len(row) else ""
        if not chs:
            continue
        term_chars = set(chs)
        if not (term_chars & all_source_chars):
            continue

        # Collect candidate sources (those sharing >=1 char) without iterating
        # the full source set. A small visited set dedupes postings.
        candidates: set[str] = set()
        for ch in term_chars:
            for source in char_to_sources.get(ch, ()):
                candidates.add(source)

        for source in candidates:
            if length_can_be_useful(source, chs):
                fuzzy_score = score_text(source, chs)
                if fuzzy_score > 0:
                    heap = top_heaps[source]
                    item = (fuzzy_score, -scanned, chs, headers, row)
                    if len(heap) < heap_cap:
                        heapq.heappush(heap, item)
                    else:
                        heapq.heappushpop(heap, item)

    if progress_callback:
        progress_callback(
            f"术语参考：最近匹配扫描完成（{scanned} 行），正在收集结果。", None, None
        )
    for source, heap in top_heaps.items():
        if not heap:
            continue
        # Reproduce the previous output order: score descending, ties broken by
        # earliest-scanned first.
        ordered = sorted(heap, key=lambda it: (-it[0], -it[1]))
        # Sources that gathered references in earlier (exact/contains) passes
        # take the top K nearest matches; sources with no prior references get
        # a wider fallback of max(3, K) so the LLM always has >=3 references.
        limit = max(3, nearest_k) if not tasks[source].references else nearest_k
        for score, _neg_seq, chs, headers, row in ordered[:limit]:
            ref = term_reference_from_row(headers, row, "nearest", score)
            append_term_reference(tasks[source], seen_chs[source], ref)
    if progress_callback:
        progress_callback("术语参考：最近匹配兜底完成。", None, None)


def append_term_reference(
    task: Stage2Task,
    seen: set[str],
    reference: dict[str, object],
) -> None:
    chs = str(reference.get("chs", ""))
    if chs and chs not in seen:
        task.references.append(reference)
        seen.add(chs)


def length_can_be_useful(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if abs(len(a) - len(b)) > max(12, len(a) * 2):
        return False
    return SequenceMatcher(None, a, b, autojunk=False).real_quick_ratio() >= 0.45


def make_prompt(task: Stage2Task, config: Stage2Config) -> list[dict[str, str]]:
    target_languages = [
        {"column": column, "term_code": INPUT_TO_TERM_LANG[column]}
        for column in task.missing_columns
        if column in INPUT_TO_TERM_LANG
    ]
    pivot_label = task.pivot_language or ""
    payload = {
        "source": task.source,
        "source_language": pivot_label if task.pivot_original else "chinese",
        "original_chinese": task.pivot_original,
        "missing_languages": target_languages,
        "protected_tokens": task.protected_tokens,
        "term_references": task.references[:9],
    }
    if task.pivot_original:
        system = (
            f"你是游戏本地化译者。当前采用「基准语言」模式：先已将中文译成{pivot_label}，"
            f"现在以{pivot_label}为源文翻译其余语言，original_chinese 是原始中文，"
            "仅供理解上下文与术语对齐。"
            "输出严格 JSON。遵守规则：\n"
            "1. 同一中文必须同译；\n"
            "2. <...>、{...}、\\n、数字变量和格式标签必须原样保留且顺序不变；\n"
            "3. 术语表参考中的官方译名应优先采用；\n"
            "4. 非文本、代码、标签、占位符、数字、纯符号应原样复制到各语言。"
        )
    else:
        system = (
            "你是游戏本地化译者。请基于给定中文、缺失语言和术语表参考，输出严格 JSON。"
            "遵守规则：\n"
            "1. 同一中文必须同译；\n"
            "2. <...>、{...}、\\n、数字变量和格式标签必须原样保留且顺序不变。"
            "  例如："
            "  中文「<color=red>攻击力</color>」→ 英语必须包含「<color=red>」和「</color>」且内容完全不变；"
            "  中文「{player_name}的等级」→ 各语言必须保留「{player_name}」；"
            "  中文「等级\\n{0}」→ 必须保留换行符「\\n」和占位符「{0}」；\n"
            "3. 术语表完全对应时可直接采用，部分匹配只能截取或改写，最近匹配只作参考；\n"
            "4. 非中文、代码、标签、占位符、数字、纯符号应原样复制到各语言。"
        )
    extra = (config.extra_prompt or "").strip()
    if extra:
        system = system + "\n\n附加要求（请严格遵守）：\n" + extra
    user = (
        "请逐项分析并翻译。只返回 JSON，不要 Markdown。格式："
        '{"decision":"term_exact|term_partial|term_reference|ai_translation|copy_as_is|manual_review",'
        '"translations":{"英语":"..."},"notes":"简短说明","needs_manual_review":false}\n\n'
        f"任务数据：{json.dumps(payload, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_llm(task: Stage2Task, config: Stage2Config) -> dict[str, object]:
    # In pivot round-2 the source is the English translation (no Chinese), so
    # the "copy as-is when no Chinese" shortcut must not apply — it would copy
    # English verbatim into every target language instead of translating.
    if not task.pivot_original and is_copy_as_is_text(task.source):
        return {
            "decision": "copy_as_is",
            "translations": {column: task.source for column in task.missing_columns},
            "notes": "源文不是可翻译中文或仅包含占位/符号，按规则原样复制。",
            "needs_manual_review": False,
        }

    body = {
        "model": config.model,
        "messages": make_prompt(task, config),
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    last_error: Exception | None = None
    for attempt in range(config.max_retries + 1):
        try:
            request = urllib.request.Request(config.endpoint, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=120) as response:
                response_data = json.loads(response.read().decode("utf-8"))
            content = response_data["choices"][0]["message"]["content"]
            parsed = parse_json_object(content)
            return normalize_llm_result(parsed, task)
        except Exception as exc:  # noqa: BLE001 - surface final error in report
            last_error = exc
            if attempt < config.max_retries:
                time.sleep(1.5 * (attempt + 1))

    return {
        "decision": "manual_review",
        "translations": {},
            "notes": f"LLM 请求失败：{last_error}",
            "needs_manual_review": True,
        }


def parse_json_object(text: str) -> dict[str, object]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def normalize_llm_result(result: dict[str, object], task: Stage2Task) -> dict[str, object]:
    translations = result.get("translations")
    if not isinstance(translations, dict):
        translations = {}
    normalized = {
        column: str(translations.get(column, "")).strip()
        for column in task.missing_columns
        if str(translations.get(column, "")).strip()
    }
    notes = str(result.get("notes", "")).strip()
    needs_review = bool(result.get("needs_manual_review", False))

    for token in task.protected_tokens:
        if token == "\\n":
            continue
        for column, value in normalized.items():
            if token not in value:
                needs_review = True
                notes = append_note(notes, f"{column} 缺少保护片段 {token}")

    return {
        "decision": str(result.get("decision", "ai_translation")),
        "translations": normalized,
        "notes": notes,
        "needs_manual_review": needs_review,
    }


def append_note(notes: str, extra: str) -> str:
    return f"{notes}; {extra}" if notes else extra


def _checkpoint_path(output_path: Path) -> Path:
    return output_path.with_name(f".{output_path.stem}_checkpoint.json")


def load_checkpoint(output_path: Path) -> set[str]:
    cp = _checkpoint_path(output_path)
    if not cp.is_file():
        return set()
    try:
        with cp.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("done"), list):
            return set(data["done"])
    except Exception:
        pass
    return set()


def save_checkpoint(output_path: Path, done: set[str]) -> None:
    cp = _checkpoint_path(output_path)
    cp.parent.mkdir(parents=True, exist_ok=True)
    with cp.open("w", encoding="utf-8") as f:
        json.dump({"done": sorted(done)}, f, ensure_ascii=False, indent=2)


def _read_existing_output(path: Path) -> tuple[list[str], list[dict[str, str]]] | None:
    if not path.is_file():
        return None
    try:
        return read_input(path)
    except Exception:
        return None


def write_stage2_report(path: Path, tasks: dict[str, Stage2Task], results: dict[str, dict[str, object]]) -> None:
    fieldnames = [
        "简体中文",
        "rows",
        "missing_languages",
        "decision",
        "filled_languages",
        "term_reference_chs",
        "notes",
        "needs_manual_review",
    ]
    rows = []
    for source, task in tasks.items():
        result = results.get(source, {})
        translations = result.get("translations", {})
        if not isinstance(translations, dict):
            translations = {}
        rows.append(
            {
                "简体中文": task.pivot_original or source,
                "rows": " | ".join(map(str, task.rows)),
                "missing_languages": " | ".join(task.missing_columns),
                "decision": result.get("decision", ""),
                "filled_languages": " | ".join(translations.keys()),
                "term_reference_chs": " | ".join(str(ref.get("chs", "")) for ref in task.references[:5]),
                "notes": result.get("notes", ""),
                "needs_manual_review": str(bool(result.get("needs_manual_review", False))),
            }
        )
    write_csv(path, fieldnames, rows)


def apply_results_for_source(
    rows: list[dict[str, str]],
    task: Stage2Task,
    result: dict[str, object],
    overwrite: bool,
    match_by_original: bool = False,
) -> int:
    filled = 0
    translations = result.get("translations", {})
    if not isinstance(translations, dict):
        return 0
    match_value = (task.pivot_original if match_by_original and task.pivot_original else task.source).strip()
    for row in rows:
        if row.get(SOURCE_COLUMN, "").strip() != match_value:
            continue
        for column in task.missing_columns:
            value = str(translations.get(column, "")).strip()
            if not value:
                continue
            if (row.get(column) or "").strip() and not overwrite:
                continue
            row[column] = value
            filled += 1
    return filled


class Stage2Paused(Exception):
    """Raised when Stage2 is paused by user."""
    pass


def _run_llm_round(
    config: Stage2Config,
    tasks: dict[str, Stage2Task],
    rows: list[dict[str, str]],
    fieldnames: list[str],
    run_log_path: Path,
    run_id: str,
    checkpoint_done: set[str],
    stage_label: str,
    match_by_original: bool = False,
) -> tuple[dict[str, dict[str, object]], int]:
    """Run one LLM round over `tasks`. Returns (results, filled_cells)."""
    results: dict[str, dict[str, object]] = {}
    filled = 0
    if not tasks:
        return results, filled

    lock = threading.Lock()
    completed = 0
    total_tasks = len(tasks)
    pending = [(task.source, task) for task in tasks.values() if task.source not in checkpoint_done]
    progress_callback = config.progress_callback
    if progress_callback:
        progress_callback(
            f"{stage_label}：本轮 {total_tasks} 组，待执行 {len(pending)} 组。",
            0,
            total_tasks,
        )

    def _flush() -> None:
        write_csv(config.output_path, fieldnames, rows)
        save_checkpoint(config.output_path, checkpoint_done)

    with ThreadPoolExecutor(max_workers=max(1, config.threads)) as executor:
        future_map = {
            executor.submit(call_llm, task, config): (src, task)
            for src, task in pending
        }
        for future in as_completed(future_map):
            if config.stop_event and config.stop_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                _flush()
                if progress_callback:
                    progress_callback(f"{stage_label}：已暂停，进度已保存。", None, None)
                raise Stage2Paused()

            source, task = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "decision": "manual_review",
                    "translations": {},
                    "notes": f"LLM 请求异常：{exc}",
                    "needs_manual_review": True,
                }
            with lock:
                results[source] = result
                checkpoint_done.add(source)
                completed += 1
                filled += apply_results_for_source(
                    rows, task, result, config.overwrite, match_by_original
                )
                interval = max(1, config.flush_interval)
                if completed % interval == 0 or completed == total_tasks:
                    _flush()
                log_stage2_event(
                    run_log_path,
                    {
                        "event": "task_done",
                        "stage": stage_label,
                        "run_id": run_id,
                        "completed": completed,
                        "total": total_tasks,
                        "source": source,
                        "rows": task.rows,
                        "missing_columns": task.missing_columns,
                        "references": task.references[:9],
                        "result": result,
                    },
                )
                if progress_callback:
                    decision = result.get("decision", "unknown")
                    review_mark = "，需复核" if result.get("needs_manual_review") else ""
                    if result.get("needs_manual_review"):
                        note = str(result.get("notes", "")).strip()
                        if note:
                            progress_callback(f"{stage_label}：需复核原因：{note}", None, None)
                    progress_callback(
                        f"{stage_label}：{completed}/{total_tasks} 完成：{source[:40]} [{decision}{review_mark}]",
                        completed,
                        total_tasks,
                    )
    return results, filled


def _effective_term_sources(config: Stage2Config) -> list[Path]:
    sources: list[Path] = []
    for candidate in (config.term_path, config.custom_term_path):
        if candidate and Path(candidate).is_file():
            sources.append(Path(candidate))
    return sources


def run_stage2(config: Stage2Config) -> dict[str, int | str]:
    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    run_log_path = config.run_log_path or config.report_path.with_name(
        f"{config.report_path.stem}_{run_id}.jsonl"
    )
    log_stage2_event(
        run_log_path,
        {
            "event": "start",
            "run_id": run_id,
            "input": str(config.input_path),
            "output": str(config.output_path),
            "report": str(config.report_path),
            "endpoint": config.endpoint,
            "model": config.model,
            "threads": config.threads,
            "use_terms": config.use_terms,
            "pivot_language": config.pivot_language,
            "custom_terms": str(config.custom_term_path) if config.custom_term_path else "",
            "extra_prompt": config.extra_prompt,
        },
    )
    if config.progress_callback:
        config.progress_callback(f"第二阶段：运行日志 {run_log_path}", None, None)

    fieldnames, rows = read_input(config.input_path)
    targets = target_columns(fieldnames)

    checkpoint_done = load_checkpoint(config.output_path)
    if checkpoint_done and config.progress_callback:
        config.progress_callback(f"第二阶段：检测到 checkpoint，已跳过 {len(checkpoint_done)} 组。", None, None)

    # Try to resume from existing output (intermediate result)
    existing = _read_existing_output(config.output_path)
    if existing is not None:
        existing_fieldnames, existing_rows = existing
        if existing_fieldnames == fieldnames:
            for i, row in enumerate(existing_rows):
                if i < len(rows):
                    rows[i] = row
            if config.progress_callback:
                config.progress_callback("第二阶段：已从中间结果 CSV 恢复进度。", None, None)

    if config.pivot_language:
        results, filled, tasks_report = _run_stage2_pivot(
            config, rows, fieldnames, targets, run_log_path, run_id, checkpoint_done
        )
    else:
        tasks = build_tasks(rows, targets, config.include_false, config.overwrite,
                            only_columns=config.only_columns)
        if config.progress_callback:
            config.progress_callback(f"第二阶段：发现 {len(tasks)} 组唯一中文需要处理。", 0, len(tasks))
        if config.use_terms:
            if config.progress_callback:
                config.progress_callback("第二阶段：正在查询术语表参考。", 0, len(tasks))
            collect_term_references(
                _effective_term_sources(config),
                tasks,
                config.progress_callback,
                config.stage1_matched_terms,
                config.nearest_top_k,
            )
            if config.progress_callback:
                config.progress_callback("第二阶段：术语表参考查询完成。", 0, len(tasks))
        elif config.progress_callback:
            config.progress_callback("第二阶段：已跳过术语表参考查询。", 0, len(tasks))

        results, filled = _run_llm_round(
            config, tasks, rows, fieldnames, run_log_path, run_id, checkpoint_done, "第二阶段"
        )
        tasks_report = tasks

    write_csv(config.output_path, fieldnames, rows)
    save_checkpoint(config.output_path, checkpoint_done)
    write_stage2_report(config.report_path, tasks_report, results)
    log_stage2_event(
        run_log_path,
        {
            "event": "finish",
            "run_id": run_id,
            "filled": filled,
            "manual_review": sum(1 for result in results.values() if result.get("needs_manual_review")),
            "output": str(config.output_path),
            "report": str(config.report_path),
        },
    )

    return {
        "input_rows": len(rows),
        "stage2_unique_source_texts": len(tasks_report),
        "stage2_filled_cells": filled,
        "manual_review": sum(1 for result in results.values() if result.get("needs_manual_review")),
        "output": str(config.output_path),
        "stage2_report": str(config.report_path),
        "stage2_run_log": str(run_log_path),
    }


def _run_stage2_pivot(
    config: Stage2Config,
    rows: list[dict[str, str]],
    fieldnames: list[str],
    targets: list[str],
    run_log_path: Path,
    run_id: str,
    checkpoint_done: set[str],
) -> tuple[dict[str, dict[str, object]], int, dict[str, Stage2Task]]:
    """Pivot mode: Chinese -> pivot_language first, then pivot_language -> others."""
    pivot_language = config.pivot_language
    if pivot_language not in targets:
        raise ValueError(f"基准语言模式要求输入表包含「{pivot_language}」列。")
    progress_callback = config.progress_callback
    only_columns = config.only_columns

    # Round 1: Chinese -> pivot_language.
    round1_tasks = build_tasks(
        rows, targets, config.include_false, config.overwrite, only_columns=[pivot_language]
    )
    if progress_callback:
        progress_callback(
            f"第二阶段·基准语言({pivot_language})：第一轮 中→{pivot_language}，共 {len(round1_tasks)} 组需要补{pivot_language}。",
            0, len(round1_tasks),
        )
    if config.use_terms:
        collect_term_references(
            _effective_term_sources(config),
            round1_tasks,
            progress_callback,
            config.stage1_matched_terms,
            config.nearest_top_k,
        )
    results1, filled1 = _run_llm_round(
        config, round1_tasks, rows, fieldnames, run_log_path, run_id,
        checkpoint_done, f"第二阶段·基准语言(中→{pivot_language})",
    )

    # Round 2: pivot_language -> other languages, with original Chinese as context.
    # Restrict to user-selected target columns (if any), always excluding the pivot language.
    if only_columns:
        round2_targets = [c for c in only_columns if c != pivot_language and c in targets]
    else:
        round2_targets = [c for c in targets if c != pivot_language]
    round2_tasks: dict[str, Stage2Task] = {}
    for row_index, row in enumerate(rows, start=2):
        if not is_translatable(row, config.include_false):
            continue
        zh = row.get(SOURCE_COLUMN, "").strip()
        if not zh:
            continue
        pivot_text = (row.get(pivot_language) or "").strip()
        if not pivot_text:
            continue
        missing = [
            col
            for col in round2_targets
            if config.overwrite or not (row.get(col) or "").strip()
        ]
        if not missing:
            continue
        task = round2_tasks.get(zh)
        if task is None:
            refs: list[dict[str, object]] = []
            if zh in round1_tasks:
                refs = list(round1_tasks[zh].references)
            task = Stage2Task(
                source=pivot_text,
                rows=[],
                missing_columns=[],
                protected_tokens=protected_tokens(pivot_text),
                references=refs,
                pivot_original=zh,
                pivot_language=pivot_language,
            )
            round2_tasks[zh] = task
        task.rows.append(row_index)
        for col in missing:
            if col not in task.missing_columns:
                task.missing_columns.append(col)

    if progress_callback:
        progress_callback(
            f"第二阶段·基准语言({pivot_language})：第二轮 {pivot_language}→其他语言，共 {len(round2_tasks)} 组。",
            0, len(round2_tasks),
        )
    results2, filled2 = _run_llm_round(
        config, round2_tasks, rows, fieldnames, run_log_path, run_id,
        checkpoint_done, f"第二阶段·基准语言({pivot_language}→其他)", match_by_original=True,
    )

    combined_results = {**results1, **results2}
    # Key report tasks by task.source so write_stage2_report's results.get(source)
    # matches: round-1 sources are Chinese, round-2 sources are English (no collision).
    report_tasks: dict[str, Stage2Task] = {}
    for task in round1_tasks.values():
        report_tasks[task.source] = task
    for task in round2_tasks.values():
        report_tasks[task.source] = task
    return combined_results, filled1 + filled2, report_tasks


def log_stage2_event(path: Path, event: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), **event}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run second-stage LLM translation.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--terms", default="", help="Term table path(s), comma-separated for multiple sources. Leave empty to use --custom-terms only.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--threads", type=int, default=3)
    parser.add_argument("--skip-terms", action="store_true")
    parser.add_argument("--include-false", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--custom-terms", default="", help="Optional custom term table CSV/TSV path.")
    parser.add_argument("--pivot-language", default="", help="Pivot via a base language (e.g. 英语). Empty = direct Chinese mode.")
    parser.add_argument("--extra-prompt", default="", help="Additional instructions appended to the system prompt.")
    parser.add_argument(
        "--nearest-top-k",
        type=int,
        default=1,
        help="Number of nearest term references to inject per source in the fuzzy fallback pass. "
        "Default 1; raise for content-heavy long text.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stats = run_stage2(
        Stage2Config(
            input_path=Path(args.input),
            term_path=Path(args.terms),
            output_path=Path(args.output),
            report_path=Path(args.report),
            endpoint=args.endpoint,
            api_key=args.api_key,
            model=args.model,
            threads=args.threads,
            use_terms=not args.skip_terms,
            include_false=args.include_false,
            overwrite=args.overwrite,
            custom_term_path=Path(args.custom_terms) if args.custom_terms else None,
            pivot_language=args.pivot_language,
            extra_prompt=args.extra_prompt,
            nearest_top_k=args.nearest_top_k,
        )
    )
    for key, value in stats.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
