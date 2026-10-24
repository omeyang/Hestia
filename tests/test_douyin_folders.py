"""收藏夹结构校验测试；数据全部虚构。运行：python3.12 -m unittest discover -s tests -v。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from douyin_folders_check import SCHEMA, analyze_folders, load_folders


def folder(name, size, start=1, **extra):
    return {"name": name, "items": [{"id": str(number)} for number in range(start, start + size)], **extra}


class FolderRuleTests(unittest.TestCase):
    def test_valid_folders_preserve_long_ids_and_counts(self):
        folders = [folder("甲目录", 10, 1234567890123456000),
                   folder("乙目录", 11, 2234567890123456000)]
        result = analyze_folders(folders, expected_total=21)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["folder_counts"], {"甲目录": 10, "乙目录": 11})
        self.assertEqual(result["unique_videos"], 21)
        self.assertIn("1234567890123456000", {item["id"] for item in folders[0]["items"]})

    def test_empty_and_small_folders_are_distinct_errors(self):
        result = analyze_folders([folder("空目录", 0), folder("小目录", 9), folder("合格", 10, 100)])
        self.assertEqual(result["empty_folders"], ["空目录"])
        self.assertEqual(result["small_folders"], [{"folder": "小目录", "count": 9}])
        self.assertEqual(len(result["errors"]), 2)

    def test_custom_minimum_size(self):
        result = analyze_folders([folder("四条", 4), folder("五条", 5, 100)], min_size=5)
        self.assertEqual(result["small_folders"], [{"folder": "四条", "count": 4}])
        self.assertEqual(len(result["errors"]), 1)
        with self.assertRaises(ValueError):
            analyze_folders([], min_size=0)

    def test_cross_folder_duplicates_are_errors(self):
        result = analyze_folders([folder("甲", 10), folder("乙", 10)])
        self.assertEqual(len(result["duplicate_videos"]), 10)
        self.assertEqual(result["video_records"], 20)
        self.assertEqual(result["unique_videos"], 10)
        self.assertEqual(len(result["errors"]), 10)

    def test_structure_errors_and_reported_count_warning(self):
        folders = [folder("同名", 1, id="100", count=99), folder("同名", 1, 100, id="100"),
                   {"name": "坏条目", "items": [{"id": 123}, "不是纯数字"]}]
        result = analyze_folders(folders)
        self.assertEqual(result["count_mismatches"], [{"folder": "同名", "reported": 99, "visible": 1}])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertGreaterEqual(len(result["errors"]), 7)

    def test_loader_cli_expected_total_and_input_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "folders.json"
            path.write_text(json.dumps({"schema": SCHEMA, "folders": [folder("甲", 10)]}), encoding="utf-8-sig")
            self.assertEqual(len(load_folders(path)), 1)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] /
                                    "scripts/douyin_folders_check.py"), str(path),
                                    "--expected-total", "11", "--json"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertEqual(report["errors"][0]["actual"], 10)
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
            path.write_text('{"folders": []}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_folders(path)


if __name__ == "__main__":
    unittest.main()
