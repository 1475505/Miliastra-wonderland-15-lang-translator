"""Update README prompt blocks, or verify them with --check."""

import argparse
from pathlib import Path

from prompts import SYSTEM_PROMPT, PIVOT_SYSTEM_PROMPT, EXTRA_PROMPT_PREFIX, USER_PROMPT_PREFIX

START = "<!-- translation-prompts:start -->"
END = "<!-- translation-prompts:end -->"


def prompt_documentation() -> str:
    return (
        START
        + "\n\n### 默认 system 提示词\n\n```text\n" + SYSTEM_PROMPT + "\n```\n"
        + "\n### 基准语言 system 提示词\n\n"
        + "仅命令行启用 `--pivot-language` 后使用；`{pivot_language}` 替换为所选语言。\n\n"
        + "```text\n" + PIVOT_SYSTEM_PROMPT + "\n```\n"
        + "\n### 附加要求\n\n"
        + "填写额外提示词时，在 system 提示词后追加以下前缀和用户输入（前缀前有两个换行）：\n\n"
        + "```text\n" + EXTRA_PROMPT_PREFIX.strip() + "\n{用户填写的额外提示词}\n```\n"
        + "\n### user 提示词\n\n"
        + "以下前缀后拼接本次任务的 JSON 数据：\n\n"
        + "```text\n" + USER_PROMPT_PREFIX + "{任务 JSON}\n```\n\n"
        + END
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / "README.md"
    text = path.read_text(encoding="utf-8")
    start, end = text.index(START), text.index(END) + len(END)
    updated = text[:start] + prompt_documentation() + text[end:]
    if args.check:
        if text != updated:
            raise SystemExit("README 提示词已过期，请运行 python tools/sync_readme_prompts.py")
        print("README 提示词与运行代码一致。")
    else:
        path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
