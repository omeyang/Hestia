# 抖音收藏提取与核查

状态：已实现收藏页控制台采集器、JSON 核查及 JSON/XLSX 对账工具。2026-10-24 完成文件级回测与自动测试；**尚未在当前登录的抖音收藏页验证选择器及完整性**。不能把模拟 DOM 测试写成真实采集成功，也不能保证一次滚动拿全量。

收藏元数据属于[统一来源管道](09-source-pipeline-and-github.md)的候选输入。个人 JSON、Excel、探针输出、核查明细只放本地，不能提交公开仓库。历史核查与清理见[本地成果核查及归档](16-douyin-favorites-audit-and-cleanup.md)。

## 已确认的方法与边界

- 用户登录网页后，可以从已经渲染的收藏卡片读取可见元数据。一次 `querySelectorAll` 只反映当前 DOM，不能证明读取了全部收藏。
- 懒加载与虚拟列表需要边滚动边累积，按规范视频 URL 去重。不要只在滚动到底后抓一次。
- `li.innerText` 首行只是标题候选，可能包含计数、残缺开头或重复文本。保留观察差异，不凭规则补年份、数字或删正文。
- 卡片内作者链接可以作为作者候选；仍须抽样核对视频归属。不能取整页第一个 `/user/` 链接，也不能取整个 `_ROUTER_DATA` 第一个 `sec_uid` / `nickname`，两者可能来自不同对象或推荐视频。
- 失效提示是页面观察。没有提示不代表已播放核验；没有链接的失效卡不能恢复视频 ID，也不能简单计入唯一视频总数。
- 历史说明中的 Selenium 耗时、HTTP 反爬实测、388 次主页提取失败等，现存文件没有足够运行日志可独立复核，本方案不把这些叙述作为已验证结论。

## 原始片段为什么失败

用户提供的 `.map(li => …).filter(v => v && !li.innerText.includes('视频不见了'))` 中，`li` 只存在于 `map` 回调作用域；只要有记录进入 `filter` 后半段，就会触发 `ReferenceError: li is not defined`。可复现测试保存在 `tests/douyin_favorites_collect.test.cjs`。

下面是便于理解的**当前屏幕快照**修正版，失效判断放在 `map` 内，保留标记而不删除记录：

```js
copy(JSON.stringify(Array.from(document.querySelectorAll('li')).map(li => {
  const a = li.querySelector('a[href*="/video/"]');
  if (!a) return null;
  const u = new URL(a.href);
  if (!['www.douyin.com', 'douyin.com'].includes(u.hostname) ||
      u.protocol !== 'https:' || !/^\/video\/[0-9]+\/?$/.test(u.pathname)) return null;
  const text = li.innerText || '';
  const au = li.querySelector('a[href*="/user/"]');
  return {
    title: text.split('\n')[0].trim(),
    url: 'https://www.douyin.com' + u.pathname.replace(/\/$/, ''),
    author: (au?.innerText || '').trim().replace(/^@/, ''),
    author_url: au ? au.href.split(/[?#]/)[0] : '',
    invalid: /视频不见了|视频已删除/.test(text)
  };
}).filter(Boolean), null, 2));
```

此快照不累积、不去重，也不处理父子 `li` 嵌套与多作者歧义。正式收集使用下列文件，避免维护多份“最终版”。

## 正式采集步骤

1. 登录 [抖音网页版](https://www.douyin.com)，进入“我的收藏”，确认只展示本次目标列表。回到列表顶部，等首屏卡片渲染。
2. 把 [douyin_favorites_collect.js](../scripts/douyin_favorites_collect.js) **完整文件内容**粘贴到开发者工具控制台执行。代码不发网络请求、不操作收藏，只观察当前页 DOM。
3. 执行 `__hestiaFavorites.probe()`，检查样本中的文本、HTML 与链接确实属于目标视频卡片。探针输出只在本地保存。

| 探针字段 | 检查与处置 |
| --- | --- |
| `video_li_count` | 为 0 时先等待渲染；仍为 0 则停止并根据当前 DOM 调整选择器。无匹配 `li` 时，探针样本也为空，需要在 Elements 中检查真实卡片结构。 |
| `user_link_li_count` | 为 0 表示当前卡片未命中作者锚点；作者字段保持空，不据此编造主页。 |
| `ambiguous_video_li_count` | 含多条不同视频链接的 `li` 不作单卡采集；确认是否只是父容器，或真实卡片无法被当前选择器覆盖。 |
| `unavailable_li_count` | 当前 `li` 中的提示观察数，可能因嵌套重复，不能当失效视频数。 |
| `records` / `unresolved_samples` | 已累积的唯一链接数 / 无链接提示文本样本数；后者按文本去重，不代表卡片数量。 |

4. **慢速手动滚动实际收藏列表**，每屏停留等待渲染。采集器先读首屏，再通过 DOM 变化与定时扫描累积；不猜测滚动容器，不以连续几次无增长作为完成证明。滚太快、页面结构改变或非视频收藏仍可能漏采。
5. 到底后执行 `__hestiaFavorites.scan()`，和页面显示的收藏类型、计数核对。执行以下命令复制结果，再把剪贴板保存为本地 `raw_1.json`：

   ```js
   copy(__hestiaFavorites.export())
   __hestiaFavorites.stop()
   ```

6. 刷新页面，从顶部独立采集第二遍，保存 `raw_2.json`。在同一页重复粘贴代码会沿用现有会话，不能当第二遍。停止后的会话保留导出能力，重新采集也应刷新。

采集器用 URL 的字符串路径保留长视频 ID，统一域名、去掉查询参数、片段及尾斜杠。相同视频后续出现非空差异时，首次值保留，差异写入 `field_variants`；缺失字段可由后续观察补齐。无链接的失效提示写入 `unresolved_cards`，不制造 URL。`title_source` 明确记录当前只用了卡片文本首行。

## 对账与差异复核

在仓库根目录运行（Python 3.12 标准库，无第三方依赖）：

```bash
# 单遍核查；只有页面本次明确显示总数时才填写 --expected-total。
python3.12 scripts/douyin_favorites_check.py local/douyin/raw_1.json --expected-total 472

# 两遍均先校验结构、URL 与重复，合格后比较，避免字典覆盖重复记录。
python3.12 scripts/douyin_favorites_check.py local/douyin/raw_1.json local/douyin/raw_2.json

# 全量差异保存在被 Git 忽略的本地目录；父目录须已存在。
python3.12 scripts/douyin_favorites_check.py local/douyin/raw_1.json local/douyin/raw_2.json \
  --json > local/douyin/compare.json
```

`472` 只是历史数据的示例数，不是当前账号固定总数。采集器输出含 `schema`、`records`、`unresolved_cards` 的封装；核查器同时兼容旧版 JSON 数组。

报告区分三类观察：`invalid: true` 为出现失效提示，`false` 为未见提示，缺失或 `null` 为未知。缺少标记不能当作有效。字段冲突、无链接提示、标题可疑项均须复核；结构错误或计数不符退出码为 1，退出码 0 只表示这些检查通过。两遍相同也不能证明未共同漏采。

只有页面总数与目标列表口径一致、每张卡均能识别，且无未解决提示时，才能尝试闭合“页面总数 = 唯一视频数”。历史原始 472 条与归表 465 条只证明有 7 条未归表，不能证明它们失效。

人工核查时按 URL 或视频 ID 对齐，依次处理漏卡、字段冲突和标题差异；有原页面证据后记录原值、新值、依据位置与日期。作者主页必须与同一视频的作者对象关联，不能靠昵称搜索同名账号填充。同题不同 URL 保留；不同 URL 的内容是否重复，需要看视频后判断。

## 验证与尚缺证据

```bash
python3.12 -m unittest discover -s tests -v
node --test tests/douyin_favorites_collect.test.cjs
```

2026-10-24：Python 10 项测试、Node 6 项测试通过，测试数据全部虚构。覆盖 URL 规范化与长 ID、重复/坏数据、可用性三态、双遍差异、XLSX 对账、公式拒读、输入字节不变，以及模拟 DOM 的首屏、回收、冲突、无链接提示、歧义、停止与重复注入。另用现存 472 条 JSON 与两份 XLSX 完成真实文件回测，结果见[核查记录](16-douyin-favorites-audit-and-cleanup.md)。

仍需真实浏览器验证：当前收藏页的 `li` / 视频与作者锚点结构、首行是否标题、不同渲染阶段字段变化、无链接失效卡的形态、实际滚动完整性。未取得这些证据前，本工具是待页面适配确认的采集器，不能标成“已完整采集”。

核查后的装修候选，按用户指定范围做粗验与原文初验，通过后再登记到 `sources/registry.json`。本轮没有读取视频正文或形成施工结论。
