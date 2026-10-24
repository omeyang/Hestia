"""运行：python3.12 -m unittest discover -s tests -v。测试数据全部为虚构。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.sax.saxutils import escape
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from douyin_favorites_check import (
    analyze, canonical_url, compare, embedded_title_issues, load_records, title_issues,
)
from douyin_favorites_audit import audit, read_table


def record(number, **extra):
    return {"url": f"https://www.douyin.com/video/{number}", "title": "测试标题", **extra}


def make_xlsx(path, rows, formula=False, shared=False):
    """生成最小 XLSX 读取夹具，测试真实 OOXML 分支而非伪造解析器返回值。"""
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="测试" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="/xl/worksheets/sheet1.xml"/></Relationships>')
        strings = []
        content = []
        for number, row in enumerate(rows, 1):
            cells = []
            for index, value in enumerate(row):
                address = f"{chr(65 + index)}{number}"
                if value is None:
                    continue
                if formula and number == 2 and index == 1:
                    cells.append(f'<c r="{address}"><f>1+1</f><v>2</v></c>')
                elif shared:
                    strings.append(value)
                    cells.append(f'<c r="{address}" t="s"><v>{len(strings)-1}</v></c>')
                else:
                    cells.append(f'<c r="{address}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
            content.append(f'<row r="{number}">{"".join(cells)}</row>')
        archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + ''.join(content) + '</sheetData></worksheet>')
        if shared:
            archive.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' + ''.join(f'<si><t>{escape(str(value))}</t></si>' for value in strings) + '</sst>')


class JsonTests(unittest.TestCase):
    def test_url_normalization_and_large_id(self):
        self.assertEqual(canonical_url("https://douyin.com/video/1234567890123456789/?a=1#x"),
                         "https://www.douyin.com/video/1234567890123456789")
        for value in (None, "https://evil.com/video/1", "https://www.douyin.com.evil.com/video/1",
                      "https://www.douyin.com/user/self", "http://douyin.com/video/1"):
            self.assertIsNone(canonical_url(value))

    def test_duplicate_query_variants_and_bad_rows(self):
        result = analyze([record(1), record(1, url="https://douyin.com/video/1?x=1"), {}, None])
        self.assertEqual(result["unique_urls"], 1)
        self.assertEqual(list(result["duplicate_urls"].values()), [2])
        self.assertEqual(len(result["errors"]), 3)

    def test_availability_is_three_states(self):
        result = analyze([record(1), record(2, invalid=False), record(3, invalid=True),
                          record(4, invalid="false")], expected_total=5)
        self.assertEqual(result["availability"], {"unknown": 2, "no_unavailable_hint": 1,
                                                  "unavailable_hint": 1})
        self.assertEqual(len(result["errors"]), 2)

    def test_comparison_preserves_unknown_vs_false(self):
        diff = compare([record(1)], [record(1, invalid=False), record(2)])
        self.assertEqual(diff["differences"][0]["field"], "invalid")
        self.assertEqual(diff["only_second"], [record(2)["url"]])

    def test_short_broken_title_and_natural_numeric_prefix(self):
        self.assertTrue(title_issues("年装修顺序"))
        self.assertFalse(title_issues("39分钟封窗帧帧干货"))
        self.assertTrue(title_issues("这是一个足够长的重复测试标题这是一个足够长的重复测试标题"))

    def test_embedded_title_is_reported_without_flagging_equal_titles(self):
        embedded = "乙标题用于串联检测的完整文本"
        records = [record(1, title="可能串联的前缀" + embedded),
                   record(2, title=embedded), record(3, title=embedded)]
        findings = embedded_title_issues(records)
        self.assertEqual([(item["row"], item["contains_row"]) for item in findings], [(1, 2), (1, 3)])
        self.assertEqual(analyze(records)["embedded_title_review"], findings)

    def test_loader_formats_and_invalid_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.json"
            for data in ([record(1)], {"schema": "hestia.douyin.favorites.v1", "records": [record(1)]}):
                path.write_text(json.dumps(data), encoding="utf-8-sig")
                self.assertEqual(load_records(path)[0], [record(1)])
            path.write_text('{"records": []}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_records(path)

    def test_cli_rejects_duplicates_before_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text(json.dumps([record(1), record(1)]))
            result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] /
                                    "scripts/douyin_favorites_check.py"), str(path), str(path), "--json"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("comparison", json.loads(result.stdout))


class AuditTests(unittest.TestCase):
    def test_reconciliation_preserves_missing_records_and_input_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw.json"
            raw.write_text(json.dumps([record(1), record(2), record(3)]))
            a, b = root / "a.xlsx", root / "b.xlsx"
            headers = ["序号", "标题", "视频链接", "博主", "博主主页链接"]
            make_xlsx(a, [headers, [1, "修改的标题", record(1)["url"], None, None]])
            make_xlsx(b, [["序号", "归类", "标题", "视频链接", "博主"],
                          [1, "其他", "测试标题", record(2)["url"], "测试作者"]], shared=True)
            before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (raw, a, b)}
            result = audit(raw, [a, b])
            self.assertEqual(result["errors"], [])
            self.assertEqual(len(result["title_changes"]), 1)
            self.assertEqual(result["omitted_from_tables"][0]["url"], record(3)["url"])
            self.assertEqual(result["raw"]["availability"]["unknown"], 3)
            self.assertIsNone(result["tables"][1]["missing_homepages"])
            self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (raw, a, b)})

    def test_cross_table_duplicate_and_addition_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw.json"
            raw.write_text(json.dumps([record(1)]))
            a, b = root / "a.xlsx", root / "b.xlsx"
            rows = [["序号", "标题", "视频链接", "博主"], [1, "测试标题", record(1)["url"], "作者"],
                    [2, "测试标题", record(2)["url"], "作者"]]
            make_xlsx(a, rows)
            make_xlsx(b, rows[:2])
            self.assertEqual(len(audit(raw, [a, b])["errors"]), 2)

    def test_formula_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.xlsx"
            make_xlsx(path, [["序号", "标题", "视频链接", "博主"], [1, "标题", record(1)["url"], "作者"]], formula=True)
            with self.assertRaisesRegex(ValueError, "含公式"):
                read_table(path)


if __name__ == "__main__":
    unittest.main()
