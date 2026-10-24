# 抖音收藏提取方案与脚本

状态：收藏页控制台提取脚本（增量累积版）、探针脚本与对账脚本已成文。对账脚本已用 472 条历史真实数据回测，提取脚本核心逻辑已通过模拟 DOM 单元测试（见[验证记录](#验证记录)）。选择器在真实收藏页的命中情况未经浏览器实测，**首次运行必须先跑探针**。个人收藏清单与原始数据保存在本地，不进入本仓库。技术核对日期：2026-10-24。

本文是[统一来源管道](09-source-pipeline-and-github.md)在抖音收藏场景下的采集执行方案：提取产物是"搜索/发现"阶段的候选元数据，不自动进入 `sources/registry.json`。

## 为什么在收藏页一次性提取

| 方法 | 实测结果 | 依据 |
| --- | --- | --- |
| 收藏页控制台一次性提取（本方案） | 一条脚本随滚动增量收集，约 1～2 分钟拿全量 | 首选方案 |
| 逐个打开视频页提取 | 391 个视频约半小时量级；博主主页链接 388/388 全部提取失败（DOM 锚点法），博主名靠 meta description 才成功 | 2026-10-18 selenium 实测 |
| 无浏览器直接请求 | 返回无数据的反爬壳页（JS 混淆，无正文） | 2026-10-24 curl 实测 |

## 使用流程

登录 [www.douyin.com](https://www.douyin.com) → 进入"我的收藏" → 滚动一屏确认卡片渲染 → 依次执行：

### 第 1 步：探针（首次必跑）

```js
(() => {
  const lis = [...document.querySelectorAll('li')];
  const cards = lis.filter(li => li.querySelector('a[href*="/video/"]'));
  const withUser = cards.filter(li => li.querySelector('a[href*="/user/"]'));
  const invalid = cards.filter(li => (li.innerText || '').includes('视频不见了'));
  const sample = cards.slice(0, 3).map(li => ({
    全部行: (li.innerText || '').split('\n'),
    博主锚点: !!li.querySelector('a[href*="/user/"]')
  }));
  console.log(JSON.stringify({
    li总数: lis.length,
    视频卡片: cards.length,
    其中含博主锚点: withUser.length,
    失效卡片: invalid.length,
    样本: sample
  }, null, 2));
})();
```

| 探针结果 | 判读 |
| --- | --- |
| 视频卡片为 0 | 列表未渲染或未使用 `li`；等待/滚动后重试，仍为 0 则按"样本"里实际的 DOM 结构调整选择器，并回写本文档 |
| 含博主锚点 ≈ 视频卡片 | `author`、`author_url` 可一次提取完整 |
| 含博主锚点为 0 | 卡片内无 `a[href*="/user/"]`；从样本"全部行"中定位博主行（通常带 `@` 或独立成行），确定替代选择器后回写本文档 |
| 失效卡片 > 0 | "视频不见了"过滤逻辑将被触发，记录为失效而非丢弃，保证对账可闭合 |

### 第 2 步：提取（间隔片刻跑两遍）

```js
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  window.__favAcc = window.__favAcc || new Map();
  const acc = window.__favAcc;
  const clean = s => (s || '').trim();

  window.__favCollect = () => {
    document.querySelectorAll('li').forEach(li => {
      const a = li.querySelector('a[href*="/video/"]');
      if (!a) return;
      const url = a.href.split('?')[0];
      const text = li.innerText || '';
      const rec = acc.get(url) || { url };
      if (text.includes('视频不见了')) { rec.invalid = true; }
      else {
        rec.title = clean(text.split('\n')[0]);
        const au = li.querySelector('a[href*="/user/"]');
        rec.author = au ? clean(au.innerText).replace(/^@/, '') : (rec.author || '');
        rec.author_url = au ? au.href.split('?')[0] : (rec.author_url || '');
        rec.invalid = false;
      }
      acc.set(url, rec);
    });
    return acc.size;
  };

  window.__favStop = () => {
    if (window.__favTimer) { clearInterval(window.__favTimer); window.__favTimer = null; }
    const arr = [...acc.values()];
    console.log(`共 ${arr.length} 条，其中失效 ${arr.filter(x => x.invalid).length} 条`);
    console.log('样本:', JSON.stringify(arr.slice(0, 2), null, 2));
    copy(JSON.stringify(arr));
    console.log('已复制到剪贴板 → 保存为 douyin_favorites_raw_1.json');
  };

  window.__favCollect();          // 先收集首屏，防止首轮滚动触发重渲染时首屏卡片被虚拟列表回收
  let last = -1, stable = 0;
  for (let round = 0; round < 300; round++) {
    window.scrollTo(0, document.body.scrollHeight);
    await sleep(1200);
    const n = window.__favCollect();
    if (n === last) { if (++stable >= 3) break; } else { stable = 0; }
    last = n;
  }
  window.__favTimer = setInterval(() => console.log('累积:', window.__favCollect()), 2000);
  console.log('自动滚动结束。若数量已达收藏总数，运行 __favStop()；' +
    '若滚动容器不是 window（数量停增长但页面未到底），继续手动滚动，定时器会持续收集，完成后运行 __favStop()。');
})();
```

要点：

- **增量累积**：收藏页为懒加载，且可能虚拟列表回收已滚动过的卡片；按 URL 去重的 Map 在滚动过程中反复合并，两次渲染的同一卡片只留一条。
- **失效标记不丢弃**：失效视频记 `invalid: true`，保证总数对账闭合。
- **后渲染覆盖**：同一 URL 后一次渲染的标题会覆盖前一次。若后一次是截断形态，会覆盖完整形态——这是跑两遍并交叉比对的直接原因。
- 第二遍提取前，刷新页面重新执行同一段脚本，`__favStop()` 时保存为 `raw_2.json`。

### 第 3 步：对账与交叉比对

```bash
# 双遍比对：漏卡与字段不一致（时序伪影）
python3.12 scripts/douyin_favorites_check.py douyin_favorites_raw_1.json douyin_favorites_raw_2.json
# 对账与伪影检测
python3.12 scripts/douyin_favorites_check.py douyin_favorites_raw_1.json
```

对账公式：**收藏总数 = 提取记录数 = 有效 + 失效**；URL 必须唯一。2026-10 历史基线：472 = 465 有效 + 7 失效。

### 第 4 步：人工确认清洗

检测脚本的"建议修复"只供参考，**所有清洗必须人工确认后执行**，不得自动改写（对账脚本只报告、不修改数据）。

## 已知数据伪影（历史实证）

标题来自卡片可见文本时，至少存在以下形态（均取自 2026-10 历史数据真实案例）：

| 类型 | 真实案例 | 推断成因 | 处置 | 检出方式 |
| --- | --- | --- | --- | --- |
| 截断型开头 | `年厨房装修哪些地方需要升级？…`（实为 `2026年厨房装修…`） | 长标题跑马灯只拷贝被读取 | 以另一遍提取或完整形态补齐 | 低置信规则（`%年月日` 开头）＋双遍比对 |
| 重拷贝型 | `卫生间门单包口双包口哪个好 卫生间门单包口双包口哪个好#卫生间门…` | 跑马灯双份 DOM 文本被拼进同一行 | 保留后一次出现 | 中置信规则（重复子串 ≥12 字） |
| 计数粘连 | `4251【装修攻略】施工前务必…`（4251 为点赞数） | 计数与标题文本节点相邻 | 去前缀数字 | 中置信规则（前缀数字＋重复） |
| 时序不稳定 | 同一 URL 一次提取为 `2026年厨房…`，另一次为 `年厨房…` | DOM 渲染时机不同 | 以更完整一方为准，再按本表复核 | 双遍比对（专为此设计） |
| 同款爆款同题 | `不会真的有人跟着装修博主学装修吧？`（15 条同题） | 多博主模仿同款梗，链接互不相同 | **不是伪影，不得误删** | 人工判断：比对链接是否不同 |
| 自然语言重复 | `3小时带你快速看完室内精装修工程的所有施工工序 本视频将…` | 说明式长标题的自然重复 | 人工排除（误报） | 检测器会标记，置信区分 |

数字开头不一定是伪影：`39分钟封窗帧帧干货`、`50元改造鸡肋悬空鞋柜`、`122秒记录第一次装修` 均为正常标题。判断依据是数字后是否为量词或与正文连续，而非"以数字开头"本身。

## 验证记录

2026-10-24 验证：

| 结论 | 方式 |
| --- | --- |
| 初版片段 `.filter(v => v && !li.innerText…)` 中 `li` 越界引用，脚本必然中断 | Node 实证 `ReferenceError`；修正为失效判断移入 `map` 内 |
| 提取脚本核心逻辑（累积去重、失效标记、无锚点兜底、URL 去参、收敛终止、首屏先收集）可用 | 模拟 DOM 单元测试 11 项断言通过；"循环前先收集首屏"缺陷由该测试发现并修正 |
| 伪影检测有效但非完备 | 472 条历史数据回测：14 条真实伪影检出 13；1 条截断型（`秒讲透…`，缺 `666`）因原文无内部证据漏检，需双遍比对捕捉；6 条误报均以"低/中"置信呈现，供人工复核 |
| 同一视频两次提取产生不同伪影形态 | 历史两次提取数据按 URL 对照实证（详见上表"时序不稳定"） |

未验证、待首次真实运行确认：`a[href*="/user/"]` 在收藏卡片的命中情况（历史一次提取 author 全空，无法区分"未查询"与"查不到"）；"视频不见了"失效卡是否保留视频锚点；滚动容器是否为 `window`。探针结果应回写本文档。

## 与来源管道的衔接

提取产物即"搜索/发现"阶段交付的候选元数据：标题、链接、博主、主页链接、失效标记。后续步骤：用户从候选中指定范围 → 粗验、初验 → 通过后写入 `sources/registry.json`。账号信息、收藏清单与原始 JSON 属个人资料，保存在本地，不进入公开 Git 历史。
