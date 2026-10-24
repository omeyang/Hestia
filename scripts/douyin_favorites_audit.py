#!/usr/bin/env python3.12
"""只读核对原始 JSON 与分类 XLSX；仅 Python 3.12 标准库，不播放视频。

python3.12 scripts/douyin_favorites_audit.py raw.json renovation.xlsx other.xlsx [--json]
XLSX 支持边界：每个文件一张工作表，第 1 行为中文表头，值为常量。
不支持公式、合并数据单元格或多工作表，遇到这些结构直接报错，避免静默漏读。
输出含个人收藏与改动明细，仅保存至本地。退出码 0 不表示内容已核验。
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import posixpath
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile, BadZipFile

from douyin_favorites_check import analyze, canonical_url, load_records

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def fingerprint(path):
    return {"file": Path(path).name, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def read_table(path):
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = workbook.findall(f"{S}sheets/{S}sheet")
        if len(sheets) != 1:
            raise ValueError(f"{path}: 仅支持一张工作表，实际 {len(sheets)} 张")
        sheet = sheets[0]
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel = next(r for r in relations if r.get("Id") == sheet.get(f"{R}id"))
        target = rel.get("Target", "")
        target = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        root = ET.fromstring(archive.read(target))
        if root.find(f"{S}mergeCells") is not None:
            raise ValueError(f"{path}: 含合并单元格，不支持自动读取")
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(t.text or "" for t in item.iter(f"{S}t")) for item in shared]
        hyperlinks = {}
        relpath = posixpath.dirname(target) + "/_rels/" + posixpath.basename(target) + ".rels"
        if relpath in archive.namelist():
            rels = ET.fromstring(archive.read(relpath))
            targets = {r.get("Id"): r.get("Target") for r in rels
                       if r.get("Type", "").endswith("/hyperlink") and r.get("TargetMode") == "External"}
            hyperlinks = {h.get("ref"): targets.get(h.get(f"{R}id"))
                          for h in root.findall(f"{S}hyperlinks/{S}hyperlink")}
        rows = []
        for row in root.findall(f"{S}sheetData/{S}row"):
            values = {}
            for cell in row.findall(f"{S}c"):
                if cell.find(f"{S}f") is not None:
                    raise ValueError(f"{path}: {cell.get('r')} 含公式，不读取可能过期的缓存")
                address = cell.get("r", "")
                column = address.rstrip("0123456789")
                value = cell.findtext(f"{S}v")
                if cell.get("t") == "s":
                    value = strings[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iter(f"{S}t"))
                values[column] = {"value": value, "hyperlink": hyperlinks.get(address)}
            if any(item["value"] not in (None, "") or item["hyperlink"] for item in values.values()):
                rows.append((int(row.get("r")), values))
        if not rows or rows[0][0] != 1:
            raise ValueError(f"{path}: 第 1 行必须是表头")
        headers = {col: item["value"] for col, item in rows[0][1].items() if item["value"]}
        if len(set(headers.values())) != len(headers):
            raise ValueError(f"{path}: 表头重复")
        if not {"序号", "标题", "视频链接", "博主"}.issubset(headers.values()):
            raise ValueError(f"{path}: 缺少必要中文表头")
        data = []
        for number, values in rows[1:]:
            record = {"excel_row": number}
            for column, header in headers.items():
                item = values.get(column, {})
                record[header] = ((item.get("hyperlink") or item.get("value"))
                                  if header in ("视频链接", "博主主页链接") else item.get("value"))
            data.append(record)
        return sheet.get("name"), list(headers.values()), data


def audit(raw_path, table_paths):
    before = [fingerprint(path) for path in [raw_path, *table_paths]]
    raw, unresolved = load_records(raw_path)
    raw_check = analyze(raw, unresolved)
    if raw_check["errors"]:
        raise ValueError("原始 JSON 存在结构/重复错误，请先运行 douyin_favorites_check.py")
    raw_map = {canonical_url(r["url"]): r for r in raw}
    occurrences = defaultdict(list)
    titles = defaultdict(list)
    errors, table_results, title_changes, author_changes, other_review = [], [], [], [], []
    for path in table_paths:
        sheet, headers, rows = read_table(path)
        urls, missing_authors, missing_homepages = [], [], []
        category_counts = Counter()
        for index, row in enumerate(rows, 1):
            ref = {"file": Path(path).name, "sheet": sheet, "excel_row": row["excel_row"],
                   "serial": row["序号"]}
            if str(row["序号"]) != str(index):
                errors.append({**ref, "error": "序号不连续"})
            url = canonical_url(row["视频链接"])
            if not url:
                errors.append({**ref, "error": "视频 URL 无效或缺失"})
                continue
            ref["url"] = url
            urls.append(url)
            occurrences[url].append(ref)
            if row["标题"]:
                titles[row["标题"]].append(ref)
            else:
                errors.append({**ref, "error": "标题为空"})
            if not row["博主"]:
                missing_authors.append(ref)
            if "博主主页链接" in headers:
                if not row["博主主页链接"]:
                    missing_homepages.append(ref)
                elif not canonical_url(row["博主主页链接"], "user"):
                    errors.append({**ref, "error": "博主主页 URL 无效"})
            if "归类" in headers:
                category_counts[row["归类"]] += 1
                if row["归类"] == "其他":
                    other_review.append({**ref, "title": row["标题"]})
            if url in raw_map:
                old = raw_map[url]
                if old.get("title") != row["标题"]:
                    title_changes.append({**ref, "raw": old.get("title"), "table": row["标题"],
                                          "review_status": "待原页面或独立采集核查"})
                if old.get("author") and old["author"] != row["博主"]:
                    author_changes.append({**ref, "raw": old["author"], "table": row["博主"]})
        table_results.append({"file": Path(path).name, "sheet": sheet, "rows": len(rows),
                              "unique_urls": len(set(urls)), "missing_authors": missing_authors,
                              "missing_homepages": missing_homepages if "博主主页链接" in headers else None,
                              "category_counts": dict(category_counts)})
    duplicates = {url: refs for url, refs in occurrences.items() if len(refs) > 1}
    added = sorted(occurrences.keys() - raw_map.keys())
    if duplicates:
        errors.append({"error": "分类表内部重复或跨表交叉", "urls": duplicates})
    if added:
        errors.append({"error": "分类表存在原始 JSON 未收录的 URL", "urls": added})
    omitted = [{**record, "review_status": "未出现在分类表，不能据此认定失效"}
               for url, record in raw_map.items() if url not in occurrences]
    after = [fingerprint(path) for path in [raw_path, *table_paths]]
    if before != after:
        raise ValueError("核查期间输入文件发生变化，请停止其他编辑后重跑")
    return {"inputs": before, "raw": raw_check, "tables": table_results,
            "classified_unique_urls": len(occurrences), "omitted_from_tables": omitted,
            "title_changes": title_changes, "author_changes": author_changes,
            "same_title_groups": [{"title": title, "records": refs} for title, refs in titles.items()
                                  if len({r["url"] for r in refs}) > 1],
            "other_category_review": other_review, "errors": errors,
            "limits": ["只做文件级核对，未核验视频可播放性、真实作者、标题原文和分类合理性",
                       "同题不同 URL 保留，未归表记录不能自动视为失效"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw")
    parser.add_argument("renovation")
    parser.add_argument("other")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = audit(args.raw, [args.renovation, args.other])
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"原始记录 {result['raw']['records']}，归表唯一 URL {result['classified_unique_urls']}，"
                  f"未归表 {len(result['omitted_from_tables'])}，结构错误 {len(result['errors'])}")
            for table in result["tables"]:
                homes = table["missing_homepages"]
                print(f"{table['file']}: {table['rows']} 条，缺作者 {len(table['missing_authors'])}，"
                      f"缺主页 {len(homes) if homes is not None else '未设此列'}")
            print(f"标题与原始数据不同 {len(result['title_changes'])} 条；"
                  f'“其他”分类 {len(result["other_category_review"])} 条')
            print("输入哈希前后相同；未改写原件。--json 输出完整本地核查明细。")
        return int(bool(result["errors"]))
    except (OSError, ValueError, BadZipFile, ET.ParseError, KeyError, StopIteration, IndexError) as exc:
        print(f"输入错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
