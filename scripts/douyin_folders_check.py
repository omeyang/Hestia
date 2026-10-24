#!/usr/bin/env python3.12
"""只读校验抖音收藏夹结构；仅 Python 3.12 标准库，无网络请求。

python3.12 scripts/douyin_folders_check.py folders.json [--min-size 10]
输入使用 hestia.douyin.folders.v1 封装，folders[*].items 是实际回读条目。
退出码 1 表示空目录、小目录、重复或结构错误；0 只表示这些检查通过。
本工具不自动移动、取消收藏或删除目录。
"""
import argparse
import json
from pathlib import Path
import sys

SCHEMA = "hestia.douyin.folders.v1"


def load_folders(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"输入须为 {SCHEMA} 封装")
    folders = data.get("folders")
    if not isinstance(folders, list):
        raise ValueError("folders 必须是数组")
    return folders


def item_id(item):
    """视频 ID 必须是十进制字符串，避免长 ID 经数值转换损失精度。"""
    value = item.get("id") if isinstance(item, dict) else item
    return value if isinstance(value, str) and value.isdigit() else None


def analyze_folders(folders, min_size=10, expected_total=None):
    if not isinstance(min_size, int) or isinstance(min_size, bool) or min_size < 1:
        raise ValueError("min_size 必须是大于等于 1 的整数")
    if (expected_total is not None and
            (not isinstance(expected_total, int) or isinstance(expected_total, bool) or expected_total < 0)):
        raise ValueError("expected_total 必须是非负整数或 null")
    if not isinstance(folders, list):
        raise ValueError("folders 必须是数组")

    errors, warnings, empty, small, mismatches = [], [], [], [], []
    counts, names, folder_ids, occurrences = {}, {}, {}, {}
    for index, folder in enumerate(folders, 1):
        if not isinstance(folder, dict):
            errors.append({"row": index, "error": "收藏夹不是对象"})
            continue
        name = folder.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append({"row": index, "error": "收藏夹名称缺失或不是非空字符串"})
            continue
        name = name.strip()
        if name in names:
            errors.append({"folder": name, "error": "收藏夹名称重复"})
        names.setdefault(name, index)
        folder_id = folder.get("id")
        if folder_id is not None:
            if not isinstance(folder_id, str) or not folder_id.isdigit():
                errors.append({"folder": name, "error": "收藏夹 ID 必须是十进制字符串"})
            elif folder_id in folder_ids:
                errors.append({"folder": name, "error": "收藏夹 ID 重复",
                               "first_folder": folder_ids[folder_id]})
            else:
                folder_ids[folder_id] = name
        items = folder.get("items")
        if not isinstance(items, list):
            errors.append({"folder": name, "error": "items 必须是实际回读条目数组"})
            continue
        count = len(items)
        counts[name] = counts.get(name, 0) + count
        if count == 0:
            empty.append(name)
            errors.append({"folder": name, "error": "空收藏夹应删除"})
        elif count < min_size:
            small.append({"folder": name, "count": count})
            errors.append({"folder": name, "count": count,
                           "error": f"收藏夹少于 {min_size} 条，应合并"})
        reported = folder.get("count")
        if reported is not None and reported != count:
            mismatches.append({"folder": name, "reported": reported, "visible": count})
            warnings.append({"folder": name, "warning": "接口显示数量与实际回读条数不一致"})
        for item in items:
            video_id = item_id(item)
            if video_id is None:
                errors.append({"folder": name, "error": "视频条目缺少十进制字符串 ID"})
                continue
            occurrences.setdefault(video_id, []).append(name)

    duplicates = {video_id: folder_names for video_id, folder_names in occurrences.items()
                  if len(folder_names) > 1}
    for video_id, folder_names in sorted(duplicates.items()):
        errors.append({"video_id": video_id, "folders": folder_names,
                       "error": "同一视频在收藏夹中重复"})
    unique = len(occurrences)
    if expected_total is not None and unique != expected_total:
        errors.append({"error": "唯一视频数与预期总数不符",
                       "expected": expected_total, "actual": unique})
    return {
        "folders": len(folders), "video_records": sum(counts.values()), "unique_videos": unique,
        "min_size": min_size, "folder_counts": counts, "empty_folders": empty,
        "small_folders": small, "duplicate_videos": duplicates,
        "count_mismatches": mismatches, "warnings": warnings, "errors": errors,
        "limits": ["只校验实际回读结构，不证明视频有效、分类合理或收藏页面完整",
                   "接口显示数量只作提醒；空目录以 items 为空且无后续分页的复核结果为准"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--min-size", type=int, default=10)
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--json", action="store_true", help="输出完整 JSON；仅保存到本地目录")
    args = parser.parse_args()
    try:
        folders = load_folders(args.input)
        result = analyze_folders(folders, args.min_size, args.expected_total)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"收藏夹 {result['folders']}，视频记录 {result['video_records']}，"
                  f"唯一视频 {result['unique_videos']}")
            print(f"空目录 {len(result['empty_folders'])}，不足 {args.min_size} 条目录 "
                  f"{len(result['small_folders'])}，跨目录重复 {len(result['duplicate_videos'])}")
            print(f"计数提醒 {len(result['count_mismatches'])}，结构/对账错误 {len(result['errors'])}")
            for error in result["errors"][:5]:
                print("错误:", error)
            print("本工具只读校验，不自动移动、取消收藏或删除目录。")
        return int(bool(result["errors"]))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"输入错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
