#!/usr/bin/env python3
"""Fill multilingual columns in input.csv from a 15-language term table."""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

try:
    from rapidfuzz import fuzz as rapidfuzz_fuzz
except Exception:  # pragma: no cover - optional speed-up
    rapidfuzz_fuzz = None


SOURCE_COLUMN = "简体中文"
NEED_TRANSLATE_COLUMN = "是否需要翻译"

INPUT_TO_TERM_LANG = {
    "繁体中文": "CHT",
    "英语": "EN",
    "韩语": "KR",
    "日语": "JP",
    "西班牙语": "ES",
    "法语": "FR",
    "俄语": "RU",
    "泰语": "TH",
    "越南语": "VI",
    "德语": "DE",
    "印尼语": "ID",
    "葡萄牙语": "PT",
    "土耳其语": "TR",
    "意大利语": "IT",
}

TERM_TO_INPUT_LANG = {value: key for key, value in INPUT_TO_TERM_LANG.items()}

FRIENDLY_TO_TERM = {
    SOURCE_COLUMN: "CHS",
    **INPUT_TO_TERM_LANG,
}

# Friendly column order used when writing/importing/exporting custom term files.
FRIENDLY_TERM_COLUMNS = [SOURCE_COLUMN] + list(INPUT_TO_TERM_LANG.keys())

PROTECTED_RE = re.compile(r"<[^<>]*>|\{[^{}]*\}")


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return base / relative_path


@dataclass
class Match:
    match_type: str
    score: float
    term_source: str
    translations: dict[str, str]


@dataclass(frozen=True)
class FuzzySource:
    text: str
    chars: frozenset[str]
    bigrams: frozenset[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate input.csv columns from the custom term table."
    )
    parser.add_argument(
        "--input", default=str(resource_path("data/samples/input.csv")), help="Input CSV path."
    )
    parser.add_argument(
        "--terms",
        default="",
        help="Term table path(s). Comma-separated for multiple sources. Leave empty to use --custom-terms only.",
    )
    parser.add_argument(
        "--output", default="outputs/output_15lang.csv", help="Output CSV path."
    )
    parser.add_argument(
        "--remaining",
        default="outputs/ai_remaining.csv",
        help="CSV listing rows/languages still needing AI translation.",
    )
    parser.add_argument(
        "--report",
        default="outputs/translation_report.csv",
        help="CSV report of exact/punctuation_stripped/fuzzy/blank matches.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=92.0,
        help="Fuzzy matching threshold, inclusive. Default: 92.",
    )
    parser.add_argument(
        "--include-false",
        action="store_true",
        help="Also fill rows whose 是否需要翻译 column is not TRUE.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing non-empty target language cells.",
    )
    parser.add_argument(
        "--no-fuzzy",
        action="store_true",
        help="Only use exact and punctuation-stripped CHS matches from the term table.",
    )
    parser.add_argument(
        "--custom-terms",
        default="",
        help="Optional custom term table (friendly CSV or code TSV) merged with the built-in table.",
    )
    return parser.parse_args()


def read_input(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"{path} has no header row")
        # Filter out any None fieldnames (from empty header columns like trailing commas)
        fieldnames = [name for name in reader.fieldnames if name is not None]
        rows: list[dict[str, str]] = []
        for row in reader:
            # Keep only fields that are in our cleaned fieldnames
            cleaned_row = {k: v for k, v in row.items() if k is not None and k in fieldnames}
            rows.append(cleaned_row)
        return fieldnames, rows


def is_translatable(row: dict[str, str], include_false: bool) -> bool:
    if include_false:
        return True
    return row.get(NEED_TRANSLATE_COLUMN, "").strip().upper() == "TRUE"


def target_columns(fieldnames: Iterable[str]) -> list[str]:
    return [name for name in fieldnames if name in INPUT_TO_TERM_LANG]


def collect_sources(
    rows: list[dict[str, str]], include_false: bool, overwrite: bool, targets: list[str]
) -> set[str]:
    sources: set[str] = set()
    for row in rows:
        source = row.get(SOURCE_COLUMN, "").strip()
        if not source or not is_translatable(row, include_false):
            continue
        if overwrite or any(not row.get(col, "").strip() for col in targets):
            sources.add(source)
    return sources


def row_translations(term_headers: list[str], row: list[str]) -> dict[str, str]:
    return {header: row[i] if i < len(row) else "" for i, header in enumerate(term_headers)}


def read_term_rows(path):
    """Yield (headers_in_term_codes, values) for a term source.

    Accepts a single Path, or a list/tuple of Paths. Each file is auto-detected:
    the built-in table is TSV using term codes (CHS/CHT/EN...); custom term
    files use friendly language names (简体中文/繁体中文/英语...) as column
    headers and are converted to term codes on the fly so callers stay uniform.
    """
    if isinstance(path, (list, tuple)):
        for sub in path:
            yield from _read_single_term_file(Path(sub))
    else:
        yield from _read_single_term_file(Path(path))


def _read_single_term_file(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        first_line = f.readline()
        f.seek(0)
        if "\t" in first_line and "CHS" in first_line:
            reader = csv.reader(f, dialect="excel-tab")
            headers = next(reader, None)
            if headers is None:
                return
            for row in reader:
                if not row:
                    continue
                yield headers, row
        else:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            code_headers = [FRIENDLY_TO_TERM.get(h.strip(), h.strip()) for h in headers]
            for row in reader:
                values = [row.get(h, "") or "" for h in headers]
                if not any(v.strip() for v in values):
                    continue
                yield code_headers, values


def exact_matches(term_path: Path, sources: set[str]) -> dict[str, Match]:
    matches: dict[str, Match] = {}
    if not sources:
        return matches

    remaining = set(sources)
    for headers, row in read_term_rows(term_path):
        if "CHS" not in headers:
            raise ValueError("Term table must contain a CHS column")
        chs = row[headers.index("CHS")] if headers.index("CHS") < len(row) else ""
        if chs in remaining:
            matches[chs] = Match("exact", 100.0, chs, row_translations(headers, row))
            remaining.remove(chs)
            if not remaining:
                break
    return matches


def remove_punctuation(text: str) -> str:
    return "".join(char for char in text if not unicodedata.category(char).startswith("P"))


def punctuation_stripped_matches(term_path: Path, sources: set[str]) -> dict[str, Match]:
    """Match when CHS and source are equal after removing punctuation."""
    matches: dict[str, Match] = {}
    if not sources:
        return matches

    remaining_by_stripped: dict[str, set[str]] = defaultdict(set)
    for source in sources:
        stripped = remove_punctuation(source)
        if stripped:
            remaining_by_stripped[stripped].add(source)

    for headers, row in read_term_rows(term_path):
        if "CHS" not in headers:
            raise ValueError("Term table must contain a CHS column")
        chs = row[headers.index("CHS")] if headers.index("CHS") < len(row) else ""
        if not chs:
            continue

        stripped_chs = remove_punctuation(chs)
        if not stripped_chs:
            continue

        for source in list(remaining_by_stripped.get(stripped_chs, ())):
            if source == chs:
                continue
            matches[source] = Match(
                "punctuation_stripped", 99.0, chs, row_translations(headers, row)
            )
            remaining_by_stripped[stripped_chs].remove(source)
            if not remaining_by_stripped[stripped_chs]:
                del remaining_by_stripped[stripped_chs]
            if not remaining_by_stripped:
                return matches
    return matches


def length_can_reach_score(a_len: int, b_len: int, threshold: float) -> bool:
    if not a_len or not b_len:
        return False
    # SequenceMatcher's ratio cannot exceed 2 * min_len / (len_a + len_b).
    return (200.0 * min(a_len, b_len) / (a_len + b_len)) >= threshold


def score_text(a: str, b: str) -> float:
    if rapidfuzz_fuzz is not None:
        return float(rapidfuzz_fuzz.ratio(a, b))

    matcher = SequenceMatcher(None, a, b, autojunk=False)
    if matcher.real_quick_ratio() * 100 < 88:
        return 0.0
    if matcher.quick_ratio() * 100 < 88:
        return 0.0
    return matcher.ratio() * 100


def fuzzy_matches(
    term_path: Path, sources: set[str], threshold: float
) -> dict[str, Match]:
    best: dict[str, Match] = {}
    if not sources:
        return best

    unresolved_by_len: dict[int, list[FuzzySource]] = defaultdict(list)
    for source in sources:
        unresolved_by_len[len(source)].append(
            FuzzySource(source, frozenset(source), bigrams(source))
        )
    lengths = sorted(unresolved_by_len)

    for headers, row in read_term_rows(term_path):
        chs_index = headers.index("CHS")
        term_source = row[chs_index] if chs_index < len(row) else ""
        term_len = len(term_source)
        if not term_source:
            continue

        term_chars = frozenset(term_source)
        term_bigrams = bigrams(term_source)
        for source_len in lengths:
            if not length_can_reach_score(source_len, term_len, threshold):
                continue
            for source_info in unresolved_by_len[source_len]:
                source = source_info.text
                current = best.get(source)
                if current and current.score >= 99.99:
                    continue
                if overlap_ratio(source_info.chars, term_chars) < 0.72:
                    continue
                if source_len >= 4 and overlap_ratio(source_info.bigrams, term_bigrams) < 0.58:
                    continue
                score = score_text(source, term_source)
                if score >= threshold and (current is None or score > current.score):
                    best[source] = Match(
                        "fuzzy", score, term_source, row_translations(headers, row)
                    )
    return best


def protected_tokens(text: str) -> list[str]:
    return PROTECTED_RE.findall(text)


def bigrams(text: str) -> frozenset[str]:
    if len(text) < 2:
        return frozenset({text}) if text else frozenset()
    return frozenset(text[i : i + 2] for i in range(len(text) - 1))


def overlap_ratio(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


def fill_rows(
    rows: list[dict[str, str]],
    matches: dict[str, Match],
    targets: list[str],
    include_false: bool,
    overwrite: bool,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    remaining: list[dict[str, str]] = []

    for index, row in enumerate(rows, start=2):
        source = row.get(SOURCE_COLUMN, "").strip()
        if not source or not is_translatable(row, include_false):
            continue

        match = matches.get(source)
        missing_languages: list[str] = []
        for target in targets:
            if row.get(target, "").strip() and not overwrite:
                continue
            translated = ""
            if match:
                translated = match.translations.get(INPUT_TO_TERM_LANG[target], "")
            if translated:
                row[target] = translated
            else:
                missing_languages.append(target)

        if missing_languages:
            remaining.append(
                {
                    "row": str(index),
                    "来源": row.get("来源", ""),
                    SOURCE_COLUMN: source,
                    "protected_tokens": " | ".join(protected_tokens(source)),
                    "missing_languages": " | ".join(missing_languages),
                    "match_type": match.match_type if match else "blank",
                    "match_score": f"{match.score:.2f}" if match else "",
                    "matched_term_chs": match.term_source if match else "",
                }
            )

    return rows, remaining


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_custom_terms(path: Path) -> list[dict[str, str]]:
    """Read a custom term file into rows keyed by friendly column names."""
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        first_line = f.readline()
        f.seek(0)
        if "\t" in first_line and "CHS" in first_line:
            reader = csv.reader(f, dialect="excel-tab")
            headers = next(reader, None)
            if headers is None:
                return rows
            term_to_friendly = TERM_TO_INPUT_LANG
            for row in reader:
                if not row or not any(cell.strip() for cell in row):
                    continue
                values = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
                friendly = {SOURCE_COLUMN: values.get("CHS", "")}
                for term_code, friendly_name in term_to_friendly.items():
                    friendly[friendly_name] = values.get(term_code, "")
                rows.append(friendly)
        else:
            reader = csv.DictReader(f)
            for row in reader:
                item = {col: (row.get(col, "") or "") for col in FRIENDLY_TERM_COLUMNS}
                if any(v.strip() for v in item.values()):
                    rows.append(item)
    return rows


def write_custom_terms(path: Path, rows: list[dict[str, str]]) -> None:
    """Write custom terms using friendly column names (convenient for Excel)."""
    write_csv(path, FRIENDLY_TERM_COLUMNS, rows)


def write_remaining(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "row",
        "来源",
        SOURCE_COLUMN,
        "protected_tokens",
        "missing_languages",
        "match_type",
        "match_score",
        "matched_term_chs",
    ]
    write_csv(path, fieldnames, rows)


def write_report(path: Path, sources: set[str], matches: dict[str, Match]) -> None:
    fieldnames = [SOURCE_COLUMN, "match_type", "score", "matched_term_chs"]
    rows = []
    for source in sorted(sources):
        match = matches.get(source)
        rows.append(
            {
                SOURCE_COLUMN: source,
                "match_type": match.match_type if match else "blank",
                "score": f"{match.score:.2f}" if match else "",
                "matched_term_chs": match.term_source if match else "",
            }
        )
    write_csv(path, fieldnames, rows)


def _merge_existing_output(
    rows: list[dict[str, str]],
    output_path: Path,
    targets: list[str],
) -> list[dict[str, str]]:
    if not output_path.is_file():
        return rows
    try:
        _, existing_rows = read_input(output_path)
    except Exception:
        return rows
    if len(existing_rows) != len(rows):
        return rows
    key_col = SOURCE_COLUMN
    for i, row in enumerate(rows):
        if i >= len(existing_rows):
            break
        existing = existing_rows[i]
        if row.get(key_col, "").strip() != existing.get(key_col, "").strip():
            continue
        for col in targets:
            if existing.get(col, "").strip():
                row[col] = existing[col]
    return rows


def _force_translate(rows: list[dict[str, str]]) -> None:
    for row in rows:
        row[NEED_TRANSLATE_COLUMN] = "TRUE"


def run_translation(
    input_path: Path,
    term_path: Path,
    output_path: Path,
    remaining_path: Path,
    report_path: Path,
    threshold: float = 92.0,
    include_false: bool = False,
    overwrite: bool = False,
    no_fuzzy: bool = False,
    stop_event=None,
    force_translate_all: bool = False,
    custom_term_path: Path | None = None,
    only_columns: list[str] | None = None,
) -> dict[str, int | str | dict[str, dict[str, str]]]:
    fieldnames, rows = read_input(input_path)
    if force_translate_all:
        _force_translate(rows)
    # When forcing translation, behave as if include_false is True
    effective_include_false = include_false or force_translate_all
    targets = target_columns(fieldnames)
    if only_columns:
        targets = [c for c in targets if c in only_columns]

    # Resume from existing output if available (preserves manually edited translations)
    rows = _merge_existing_output(rows, output_path, targets)

    if stop_event and stop_event.is_set():
        write_csv(output_path, fieldnames, rows)
        return {
            "input_rows": len(rows),
            "unique_source_texts": 0,
            "matched": 0,
            "remaining_rows_needing_ai": 0,
            "output": str(output_path),
            "remaining": str(remaining_path),
            "report": str(report_path),
            "matched_terms": {},
        }

    sources = collect_sources(rows, effective_include_false, overwrite, targets)

    term_sources: list[Path] = []
    for candidate in (term_path, custom_term_path):
        if candidate and Path(candidate).is_file():
            term_sources.append(Path(candidate))

    matches = exact_matches(term_sources, sources)
    unresolved = sources - set(matches)
    if unresolved:
        matches.update(punctuation_stripped_matches(term_sources, unresolved))
    unresolved = sources - set(matches)
    if unresolved and not no_fuzzy:
        matches.update(fuzzy_matches(term_sources, unresolved, threshold))

    filled_rows, remaining = fill_rows(
        rows, matches, targets, effective_include_false, overwrite
    )
    write_csv(output_path, fieldnames, filled_rows)
    write_remaining(remaining_path, remaining)
    write_report(report_path, sources, matches)

    # Collect the term rows actually used in stage 1 (chs -> translations by
    # term code). Stage 2 can reuse these to inject references for any source
    # whose text contains one of these term CHS strings.
    matched_terms: dict[str, dict[str, str]] = {}
    for match in matches.values():
        if match.term_source and match.term_source not in matched_terms:
            matched_terms[match.term_source] = {
                code: value for code, value in match.translations.items() if value
            }

    return {
        "input_rows": len(rows),
        "unique_source_texts": len(sources),
        "matched": len(matches),
        "remaining_rows_needing_ai": len(remaining),
        "output": str(output_path),
        "remaining": str(remaining_path),
        "report": str(report_path),
        "matched_terms": matched_terms,
    }


def main() -> None:
    args = parse_args()
    stats = run_translation(
        input_path=Path(args.input),
        term_path=Path(args.terms),
        output_path=Path(args.output),
        remaining_path=Path(args.remaining),
        report_path=Path(args.report),
        threshold=args.threshold,
        include_false=args.include_false,
        overwrite=args.overwrite,
        no_fuzzy=args.no_fuzzy,
        custom_term_path=Path(args.custom_terms) if args.custom_terms else None,
    )

    print(f"input rows: {stats['input_rows']}")
    print(f"unique source texts: {stats['unique_source_texts']}")
    print(f"matched: {stats['matched']}")
    print(f"remaining rows needing AI: {stats['remaining_rows_needing_ai']}")
    print(f"output: {stats['output']}")
    print(f"remaining: {stats['remaining']}")
    print(f"report: {stats['report']}")


if __name__ == "__main__":
    main()
