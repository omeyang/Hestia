#!/usr/bin/env python3.12
"""只读核查抖音收藏 JSON；仅 Python 3.12 标准库，无网络请求。

python3.12 scripts/douyin_favorites_check.py raw.json [second.json] [--json]
支持历史数组与控制台采集器的 records 封装。退出码 1 表示结构/对账错误，
0 仅表示这些检查通过，不能证明视频有效、作者归属或标题正确。
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


def canonical_url(value, kind="video"):
    """只接受抖音正式 HTTPS 路径；去参数、片段与尾斜杠，视频 ID 始终为字符串。"""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.strip())
        pattern = r"/video/[0-9]+/?" if kind == "video" else r"/user/[A-Za-z0-9_-]+/?"
        if (parsed.scheme != "https" or parsed.netloc not in ("www.douyin.com", "douyin.com")
                or not re.fullmatch(pattern, parsed.path)):
            return None
        return "https://www.douyin.com" + parsed.path.rstrip("/")
    except ValueError:
        return None


def load_records(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        if data.get("schema") != "hestia.douyin.favorites.v1":
            raise ValueError("未知采集封装 schema")
        records = data.get("records")
        unresolved = data.get("unresolved_cards", [])
        if not isinstance(unresolved, list):
            raise ValueError("unresolved_cards 必须是数组")
    else:
        records, unresolved = data, []
    if not isinstance(records, list):
        raise ValueError("输入须为 JSON 数组，或含 records 数组的采集封装")
    return records, unresolved


def title_issues(title):
    """只标记可疑现象，不推测丢失的数字，也不产生自动修复文本。"""
    issues = []
    if title[:1] and title[0] in "%年月日":
        issues.append("疑似残缺开头")
    for length in range(min(80, len(title) // 2), 11, -1):
        for start in range(len(title) - 2 * length + 1):
            part = title[start:start + length]
            if title.find(part, start + length) != -1:
                return issues + ["重复片段（可能是自然重复）"]
    return issues


def embedded_title_issues(records):
    """找出一条记录完整包含另一条记录标题的情况；相同标题不算。"""
    titles = [(number, record.get("title"), record.get("url"))
              for number, record in enumerate(records, 1) if isinstance(record, dict)]
    findings = []
    for number, title, url in titles:
        if not isinstance(title, str) or len(title) < 12:
            continue
        for other_number, other, _ in titles:
            if (number != other_number and isinstance(other, str) and other != title
                    and len(other) >= 12 and other in title):
                findings.append({"row": number, "url": canonical_url(url),
                                 "contains_row": other_number})
    return findings


def analyze(records, unresolved=(), expected_total=None):
    errors, titles, conflicts = [], [], []
    urls = []
    statuses = Counter()
    missing = Counter()
    for number, record in enumerate(records, 1):
        if not isinstance(record, dict):
            errors.append({"row": number, "error": "记录不是对象"})
            continue
        url = canonical_url(record.get("url"))
        if url:
            urls.append(url)
        else:
            errors.append({"row": number, "error": "无效或缺失视频 URL"})
        for field in ("title", "author", "author_url"):
            value = record.get(field)
            if value is not None and not isinstance(value, str):
                errors.append({"row": number, "error": f"{field} 必须为字符串或 null"})
            if not value:
                missing[field] += 1
        if record.get("author_url") and not canonical_url(record["author_url"], "user"):
            errors.append({"row": number, "error": "无效作者主页 URL"})
        flag = record.get("invalid")
        if flag is True:
            statuses["unavailable_hint"] += 1
        elif flag is False:
            statuses["no_unavailable_hint"] += 1
        else:
            statuses["unknown"] += 1
            if flag is not None:
                errors.append({"row": number, "error": "invalid 必须为布尔值或 null"})
        title = record.get("title")
        if isinstance(title, str) and (issues := title_issues(title)):
            titles.append({"row": number, "url": url, "title": title, "issues": issues})
        if record.get("field_variants"):
            conflicts.append({"row": number, "url": url, "variants": record["field_variants"]})
    duplicates = {url: count for url, count in Counter(urls).items() if count > 1}
    if duplicates:
        errors.append({"error": "规范化后视频 URL 重复", "urls": duplicates})
    if expected_total is not None and len(set(urls)) != expected_total:
        errors.append({"error": "唯一视频数与页面总数不符", "expected": expected_total,
                       "actual": len(set(urls))})
    return {
        "records": len(records), "unique_urls": len(set(urls)),
        "availability": {key: statuses[key] for key in
                         ("unavailable_hint", "no_unavailable_hint", "unknown")},
        "missing_fields": {key: missing[key] for key in ("title", "author", "author_url")},
        "duplicate_urls": duplicates, "errors": errors,
        "unresolved_cards": list(unresolved), "field_conflicts": conflicts,
        "title_review": titles, "embedded_title_review": embedded_title_issues(records),
    }


def compare(a, b):
    """调用方先检查重复与无效 URL，避免字典覆盖掩盖输入错误。"""
    left = {canonical_url(r["url"]): r for r in a}
    right = {canonical_url(r["url"]): r for r in b}
    differences = []
    for url in sorted(left.keys() & right.keys()):
        for field in ("title", "author", "author_url", "invalid"):
            av, bv = left[url].get(field), right[url].get(field)
            if av != bv:
                differences.append({"url": url, "field": field, "first": av, "second": bv})
    return {"only_first": sorted(left.keys() - right.keys()),
            "only_second": sorted(right.keys() - left.keys()), "differences": differences}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first")
    parser.add_argument("second", nargs="?")
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 报告（含个人清单，仅本地保存）")
    args = parser.parse_args()
    try:
        a, unresolved = load_records(args.first)
        first = analyze(a, unresolved, args.expected_total)
        report = {"first": first}
        if args.second:
            b, unresolved_b = load_records(args.second)
            second = analyze(b, unresolved_b, args.expected_total)
            report["second"] = second
            if not first["errors"] and not second["errors"]:
                report["comparison"] = compare(a, b)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            for key in ("first", "second"):
                if key not in report:
                    continue
                result = report[key]
                print(f"{key}: 记录 {result['records']}，唯一 URL {result['unique_urls']}，"
                      f"结构/对账错误 {len(result['errors'])}")
                print("可用性观察（不代表播放核验）:", result["availability"])
                print("空字段:", result["missing_fields"])
                print(f"待复核标题 {len(result['title_review'])}，"
                      f"串联标题 {len(result['embedded_title_review'])}，"
                      f"字段冲突 {len(result['field_conflicts'])}，"
                      f"无链接失效提示样本 {len(result['unresolved_cards'])}")
                for error in result["errors"][:5]:
                    print("错误:", error)
            if "comparison" in report:
                comp = report["comparison"]
                print(f"仅第一遍 {len(comp['only_first'])}，仅第二遍 {len(comp['only_second'])}，"
                      f"字段差异 {len(comp['differences'])}")
            print("完整明细用 --json 查看；本工具不改写数据，不推断失效或自动修复。")
        return int(any(report[key]["errors"] for key in ("first", "second") if key in report))
    except (OSError, ValueError) as exc:
        print(f"输入错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
