import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from custom_glossary import read_glossary, load_column_mapping
from llm_stage2 import Stage2Config, Stage2Task, run_stage2, make_prompt, call_llm
from translate_from_terms import read_input, write_csv
from sync_readme_prompts import prompt_documentation


class GlossaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def table(self, text):
        path = self.root / "terms.csv"
        path.write_text(text, encoding="utf-8-sig")
        return path

    def test_friendly_and_code_headers_in_csv_and_tsv(self):
        for separator in (",", "\t", ";"):
            for headers in (["简体中文", "英语", "日语"], ["chs", "en", "jp"]):
                with self.subTest(separator=separator, headers=headers):
                    path = self.table(separator.join(headers) + "\n星灯工坊" + separator + "Starlamp" + separator + "\n")
                    codes, rows = read_glossary(path)
                    self.assertEqual(codes, ["CHS", "EN", "JP"])
                    self.assertEqual(rows, [["星灯工坊", "Starlamp", ""]])

    def test_custom_mapping_ignores_notes(self):
        path = self.table("原文\t译文\t英语\n星灯工坊\tStarlamp\t内部备注\n")
        self.assertEqual(read_glossary(path, {"原文": "简体中文", "译文": "EN"}),
                         (["CHS", "EN"], [["星灯工坊", "Starlamp"]]))

    def test_quoted_multiline_values_and_separators(self):
        path = self.table('简体中文,英语\n"星灯,工坊","Starlamp,\nWorkshop"\n')
        self.assertEqual(read_glossary(path)[1], [["星灯,工坊", "Starlamp,\nWorkshop"]])

    def test_duplicate_rows_merge_complementary_languages(self):
        path = self.table("CHS,EN,JP\n星灯工坊,Starlamp,\n星灯工坊,,星灯り工房\n")
        self.assertEqual(read_glossary(path)[1], [["星灯工坊", "Starlamp", "星灯り工房"]])

    def test_reject_invalid_tables_and_conflicts(self):
        bad = [
            "", "CHS,EN\n", "CHS,EN\n星灯工坊,\n", "EN,JP\nA,B\n",
            "CHS\n星灯工坊\n", "CHS,CHS,EN\nA,A,B\n", "CHS,,EN\nA,B,C\n",
            "CHS,EN\n星灯工坊,A,extra\n", "CHS,EN\n,A\n",
            "CHS,EN\n星灯工坊,A\n星灯工坊,B\n", 'CHS,EN\n星灯工坊,"unclosed\n',
        ]
        for content in bad:
            with self.subTest(content=content), self.assertRaises(ValueError):
                read_glossary(self.table(content))

    def test_reject_wrong_mapping(self):
        path = self.table("原文,译文,备注\n星灯工坊,Starlamp,note\n")
        for mapping in ({"missing": "CHS"}, {"原文": "CHS", "译文": "XX"},
                        {"原文": "CHS", "译文": "EN", "备注": "EN"}):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                read_glossary(path, mapping)

    def test_public_examples_and_mapping(self):
        standard = read_glossary(ROOT / "examples/glossary.csv")
        custom = read_glossary(ROOT / "examples/glossary-custom.tsv",
                              load_column_mapping(ROOT / "examples/columns.json"))
        self.assertEqual(standard, custom)

    def test_encoding_error_is_actionable(self):
        path = self.root / "terms.csv"
        path.write_bytes("简体中文,英语\n星灯工坊,Starlamp\n".encode("gb18030"))
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            read_glossary(path)


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input.csv"
        self.terms = self.root / "terms.tsv"
        self.terms.write_text("原文\t译文\n星灯工坊\tStarlamp\n", encoding="utf-8-sig")
        write_csv(self.input, ["简体中文", "是否需要翻译", "英语", "日语"], [
            {"简体中文": "星灯工坊", "是否需要翻译": "TRUE", "英语": "", "日语": "已有译文"},
            {"简体中文": "星灯工坊", "是否需要翻译": "FALSE", "英语": "", "日语": ""},
            {"简体中文": "星灯工坊", "是否需要翻译": "TRUE", "英语": "", "日语": "已有译文"},
        ])
        self.config = Stage2Config(
            input_path=self.input, term_path=self.terms,
            output_path=self.root / "out.csv", report_path=self.root / "report.csv",
            term_columns={"原文": "CHS", "译文": "EN"}, threads=1,
        )
        self.requests = []

    def fake_llm(self, task, config):
        payload = json.loads(make_prompt(task, config)[1]["content"].split("任务数据：", 1)[1])
        self.requests.append(payload)
        references = payload["term_references"]
        english = references[0]["translations"].get("英语", "AI") if references else "AI"
        return {"decision": "ai_translation",
                "translations": {col: english for col in task.missing_columns},
                "notes": "", "needs_manual_review": False}

    def run_translation(self):
        with patch("llm_stage2.call_llm", side_effect=self.fake_llm):
            return run_stage2(self.config)

    def test_custom_mapping_reaches_prompt_and_preserves_false_rows(self):
        stats = self.run_translation()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["term_references"][0]["translations"], {"英语": "Starlamp"})
        rows = read_input(self.config.output_path)[1]
        self.assertEqual([row["英语"] for row in rows], ["Starlamp", "", "Starlamp"])
        self.assertEqual(rows[0]["日语"], "已有译文")
        self.assertEqual(stats["stage2_filled_cells"], 2)

    def test_no_glossary_never_reads_local_data(self):
        self.config.term_path = None
        self.config.use_terms = False
        with patch("llm_stage2.read_term_rows", side_effect=AssertionError("unexpected glossary read")):
            self.run_translation()
        self.assertEqual(self.requests[0]["term_references"], [])

    def test_invalid_explicit_path_fails(self):
        self.config.term_path = self.root / "missing.csv"
        with self.assertRaisesRegex(ValueError, "找不到"):
            self.run_translation()
        self.assertEqual(self.requests, [])

    def test_force_all_and_overwrite(self):
        self.config.include_false = True
        self.config.overwrite = True
        self.run_translation()
        for row in read_input(self.config.output_path)[1]:
            self.assertEqual(row["英语"], "Starlamp")
            self.assertEqual(row["日语"], "Starlamp")

    def test_same_config_resumes_but_glossary_changes_restart(self):
        self.run_translation()
        self.run_translation()
        self.assertEqual(len(self.requests), 1)
        self.terms.write_text("原文\t译文\n星灯工坊\tNew Name\n", encoding="utf-8")
        self.run_translation()
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(read_input(self.config.output_path)[1][0]["英语"], "New Name")

    def test_prompt_changes_restart(self):
        self.run_translation()
        self.config.extra_prompt = "使用简短译文"
        self.run_translation()
        self.assertEqual(len(self.requests), 2)

    def test_mapping_changes_restart(self):
        self.terms.write_text("原文\t译文\t替代\n星灯工坊\tStarlamp\tAlternative\n", encoding="utf-8")
        self.run_translation()
        self.config.term_columns = {"原文": "CHS", "替代": "EN"}
        self.run_translation()
        self.assertEqual(read_input(self.config.output_path)[1][0]["英语"], "Alternative")

    def test_deleted_output_does_not_skip_from_checkpoint(self):
        self.run_translation()
        self.config.output_path.unlink()
        self.run_translation()
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(read_input(self.config.output_path)[1][0]["英语"], "Starlamp")

    def test_failed_request_can_retry(self):
        with patch("llm_stage2.call_llm", return_value={"translations": {}, "needs_manual_review": True}):
            run_stage2(self.config)
        self.run_translation()
        self.assertEqual(len(self.requests), 1)

    def test_contains_term_is_used(self):
        write_csv(self.input, ["简体中文", "是否需要翻译", "英语"], [
            {"简体中文": "前往星灯工坊", "是否需要翻译": "TRUE", "英语": ""},
        ])
        self.run_translation()
        self.assertTrue(any(ref["chs"] == "星灯工坊" for ref in self.requests[0]["term_references"]))

    def test_request_contains_custom_prompt_and_references(self):
        task = Stage2Task("星灯工坊", [2], ["英语", "日语"], [], [
            {"chs": "星灯工坊", "translations": {"英语": "Starlamp"}}
        ])
        self.config.extra_prompt = "保持简短"
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self):
                return json.dumps({"choices": [{"message": {"content": '{"translations":{"英语":"Starlamp"}}'}}]}).encode()
        with patch("llm_stage2.urllib.request.urlopen", return_value=Response()) as send:
            result = call_llm(task, self.config)
        body = json.loads(send.call_args.args[0].data)
        self.assertIn("保持简短", body["messages"][0]["content"])
        self.assertIn("Starlamp", body["messages"][1]["content"])
        self.assertTrue(result["needs_manual_review"])
        self.assertIn("日语", result["notes"])

    def test_documented_prompts_match_runtime(self):
        self.assertIn(prompt_documentation(), (ROOT / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
