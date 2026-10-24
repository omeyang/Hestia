#!/usr/bin/env python3.12
"""抖音收藏提取结果对账与伪影检测。

输入：控制台提取脚本产出的 JSON 文件（记录含 url/title/author/author_url/invalid）。
输出：对账统计与标题伪影检测报告。本脚本只报告、不修改任何数据；
所有"建议修复"都需人工确认后另行处理，避免自动改写引入新错误。

用法：
  对账与伪影检测：python3.12 scripts/douyin_favorites_check.py douyin_favorites_raw.json
  双次提取比对：python3.12 scripts/douyin_favorites_check.py raw_第一遍.json raw_第二遍.json

背景：同一视频两次提取可能产生不同伪影（截断型/重拷贝型，2026-10-24
已用 472 条历史数据实证），因此推荐滚动结束后间隔片刻再提取一次并比对。
"""
import json
import sys

# 数字后紧跟这些字（量词/单位）时视为正文的一部分；否则视为粘连的点赞数等噪声
KEEP_UNITS = "秒分钟小时天日年月元个条万倍平次%平方集期"
# 判定为重复伪影的最短重复子串长度
MIN_REPEAT = 12


def longest_repeat(t):
    """返回 (子串, 首次位置, 二次位置)；无则返回 None。取最长的重复片段。"""
    max_l = min(80, len(t) // 2)
    for L in range(max_l, MIN_REPEAT - 1, -1):
        for i in range(len(t) - 2 * L + 1):
            s = t[i:i + L]
            j = t.find(s, i + L)
            if j != -1:
                return s, i, j
    return None


def leading_digits(t, pos, cap=4):
    """取 pos 前紧邻的连续数字（至多 cap 位），用于识别被剥离的 2026/80/15/9 等。"""
    k = 0
    while k < cap and pos - k - 1 >= 0 and t[pos - k - 1].isdigit():
        k += 1
    return t[pos - k:pos] if k else ""


def detect(title):
    """返回 [(置信度, 类型, 建议修复文本或空)] 列表。"""
    issues = []
    rep = longest_repeat(title)
    broken_start = title and title[0] in "%年月日"
    if rep:
        s, i, j = rep
        suggestion = ""
        digits = leading_digits(title, j)
        if digits and j + len(digits) <= len(title):
            nxt = title[j + len(digits):j + len(digits) + 1] or " "
            if nxt in KEEP_UNITS:
                # 数字是正文一部分（如 2026年 / 666秒 / 80%）
                suggestion = digits + title[j:]
            else:
                # 数字是粘连噪声（如点赞数 4251【）
                suggestion = title[j:]
        elif title.endswith(s):
            suggestion = title[j:]
        else:
            suggestion = title[j:]
        level = "高" if broken_start else "中"
        issues.append((level, "重复片段", suggestion))
    elif broken_start:
        issues.append(("低", "残缺开头", ""))
    return issues


def compare(path_a, path_b):
    """双次提取交叉比对：找出仅出现于一侧的卡片（漏卡）与字段不一致（时序伪影）。"""
    with open(path_a, encoding="utf-8") as f:
        a = {v.get("url"): v for v in json.load(f)}
    with open(path_b, encoding="utf-8") as f:
        b = {v.get("url"): v for v in json.load(f)}
    only_a = [u for u in a if u not in b]
    only_b = [u for u in b if u not in a]
    print("=== 双次提取比对 ===")
    print(f"第一遍: {len(a)} 条   第二遍: {len(b)} 条")
    print(f"仅第一遍有（第二遍漏卡）: {len(only_a)}")
    for u in only_a[:5]:
        print("  ", u)
    print(f"仅第二遍有（第一遍漏卡）: {len(only_b)}")
    for u in only_b[:5]:
        print("  ", u)
    fields = ("title", "author", "author_url", "invalid")
    diff = [(u, k) for u in a if u in b for k in fields
            if (a[u].get(k) or "") != (b[u].get(k) or "")]
    print(f"字段不一致: {len(diff)} 条")
    for u, k in diff[:10]:
        print(f"  [{k}] {u}")
        print(f"    第一遍: {str(a[u].get(k))[:60]}")
        print(f"    第二遍: {str(b[u].get(k))[:60]}")
    print("字段不一致处优先以更完整的一方为准，并按伪影检测规则复核。")


def main():
    if len(sys.argv) == 3:
        compare(sys.argv[1], sys.argv[2])
        return
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)

    # ---- 对账 ----
    urls = [v.get("url", "") for v in data]
    dup = sorted({u for u in urls if urls.count(u) > 1})
    invalid = [v for v in data if v.get("invalid")]
    no_author = [v for v in data if not v.get("author")]
    no_author_url = [v for v in data if not v.get("author_url")]
    print("=== 对账 ===")
    print(f"总记录: {len(data)}   URL唯一数: {len(set(urls))}   重复URL: {len(dup)}")
    if dup:
        print("  重复URL示例:", dup[:3])
    print(f"失效标记: {len(invalid)}   对账公式: 总记录 = 有效 + 失效")
    print(f"author为空: {len(no_author)}   author_url为空: {len(no_author_url)}")
    print("  （author 大面积为空说明卡片内无 a[href*='/user/'] 锚点，需改用探针确认博主元素选择器）")

    # ---- 伪影检测 ----
    print("\n=== 标题伪影检测（报告型，不修改数据；建议均需人工确认） ===")
    n_flag = 0
    for idx, v in enumerate(data, 1):
        t = v.get("title") or ""
        if len(t) < MIN_REPEAT:
            continue
        for level, kind, sug in detect(t):
            n_flag += 1
            print(f"\n[{level}|{kind}] #{idx}")
            print(f"  原文: {t[:80]}{'…' if len(t) > 80 else ''}")
            if sug:
                print(f"  建议: {sug[:80]}{'…' if len(sug) > 80 else ''}")
            print(f"  链接: {v.get('url', '')}")
    print(f"\n共标记 {n_flag} 条（含可能的误报，如正文自然重复的说明书式长标题）。")


if __name__ == "__main__":
    main()
