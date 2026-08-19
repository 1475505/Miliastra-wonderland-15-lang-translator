#!/usr/bin/env python3
"""Post-process validation for translated CSV output.

1. Fix protected tokens: if <...> or {...} content differs from the Chinese source,
   restore it to the Chinese version.
2. Check translation consistency: the same Chinese source must map to the same
   translation in every target language across all rows.
"""

from __future__ import annotations

import csv
from pathlib import Path
from collections import defaultdict

from translate_from_terms import (
    SOURCE_COLUMN,
    NEED_TRANSLATE_COLUMN,
    PROTECTED_RE,
    read_input,
    write_csv,
    resource_path,
)


def _protected_tokens(text: str) -> list[str]:
    return PROTECTED_RE.findall(text)


def _fix_protected_tokens(rows: list[dict[str, str]], fieldnames: list[str], targets: list[str]) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    """Restore protected tokens in targets to match the Chinese source."""
    changes: list[dict[str, object]] = []
    for row in rows:
        source = row.get(SOURCE_COLUMN, "")
        source_tokens = _protected_tokens(source)
        if not source_tokens:
            continue
        for col in targets:
            value = row.get(col, "")
            if not value:
                continue
            target_tokens = _protected_tokens(value)
            if target_tokens != source_tokens:
                # Simple positional replacement: replace each target token with source token
                fixed = value
                for tgt_tok, src_tok in zip(target_tokens, source_tokens):
                    fixed = fixed.replace(tgt_tok, src_tok, 1)
                if fixed != value:
                    changes.append({
                        "row": row.get("来源", ""),
                        "source": source,
                        "column": col,
                        "before": value,
                        "after": fixed,
                    })
                    row[col] = fixed
    return rows, changes


def _check_consistency(rows: list[dict[str, str]], targets: list[str]) -> list[dict[str, object]]:
    """Report rows where the same Chinese has different translations."""
    errors: list[dict[str, object]] = []
    # source -> column -> set of translations
    source_to_translations: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    # source -> column -> list of (row_identifier, translation)
    source_to_rows: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(lambda: defaultdict(list))

    for row in rows:
        source = row.get(SOURCE_COLUMN, "").strip()
        if not source:
            continue
        row_id = row.get("来源", "")
        for col in targets:
            val = row.get(col, "").strip()
            if not val:
                continue
            source_to_translations[source][col].add(val)
            source_to_rows[source][col].append((row_id, val))

    for source, col_map in source_to_translations.items():
        for col, translations in col_map.items():
            if len(translations) <= 1:
                continue
            rows_info = source_to_rows[source][col]
            errors.append({
                "source": source,
                "column": col,
                "translations": sorted(translations),
                "rows": rows_info,
            })
    return errors


def validate_and_fix(
    input_path: Path,
    output_path: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, object]:
    """Run validation and optional fixing on a translated CSV.

    Returns a dict with:
        - fixed_rows: list[dict]
        - protected_changes: list[dict]
        - consistency_errors: list[dict]
        - output_path: str | None
        - report_path: str | None
    """
    fieldnames, rows = read_input(input_path)
    targets = [col for col in fieldnames if col not in (SOURCE_COLUMN, NEED_TRANSLATE_COLUMN, "来源")]

    fixed_rows, protected_changes = _fix_protected_tokens(rows, fieldnames, targets)
    consistency_errors = _check_consistency(fixed_rows, targets)

    if output_path is not None and protected_changes:
        write_csv(output_path, fieldnames, fixed_rows)

    if report_path is not None:
        _write_report(report_path, protected_changes, consistency_errors)

    return {
        "fixed_rows": fixed_rows,
        "protected_changes": protected_changes,
        "consistency_errors": consistency_errors,
        "output_path": str(output_path) if output_path else None,
        "report_path": str(report_path) if report_path else None,
    }


def _write_report(path: Path, changes: list[dict[str, object]], errors: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["类型", "详情"])
        if changes:
            writer.writerow(["", ""])
            writer.writerow(["修改", f"共 {len(changes)} 处 protected token 被修正"])
            for ch in changes:
                writer.writerow([
                    "修正",
                    f"行标识={ch.get('row', '')}, 中文={ch.get('source', '')}, 列={ch.get('column', '')}, "
                    f"修改前={ch.get('before', '')}, 修改后={ch.get('after', '')}"
                ])
        if errors:
            writer.writerow(["", ""])
            writer.writerow(["错误", f"共 {len(errors)} 处翻译不一致"])
            for err in errors:
                translations = err.get("translations", [])
                trans_str = " | ".join(str(t) for t in translations) if isinstance(translations, list) else str(translations)
                writer.writerow([
                    "不一致",
                    f"中文={err.get('source', '')}, 列={err.get('column', '')}, 不同翻译={trans_str}"
                ])
                rows_info = err.get("rows", [])
                if isinstance(rows_info, list):
                    for item in rows_info:
                        if isinstance(item, (list, tuple)) and len(item) == 2:
                            writer.writerow(["", f"  行={item[0]}, 翻译={item[1]}"])
        if not changes and not errors:
            writer.writerow(["通过", "未发现 protected token 错误或翻译不一致问题。"])


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Validate translated CSV output.")
    parser.add_argument("--input", required=True, help="Path to translated CSV")
    parser.add_argument("--output", help="Path to write fixed CSV (optional)")
    parser.add_argument("--report", required=True, help="Path to write report CSV")
    args = parser.parse_args()

    result = validate_and_fix(
        input_path=Path(args.input),
        output_path=Path(args.output) if args.output else None,
        report_path=Path(args.report),
    )
    changes = result.get("protected_changes", [])
    errors = result.get("consistency_errors", [])
    print(f"Protected token changes: {len(changes)}")
    print(f"Consistency errors: {len(errors)}")
    print(f"Report: {result['report_path']}")


if __name__ == "__main__":
    main()
