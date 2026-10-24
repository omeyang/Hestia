/* 在抖音「我的收藏」浏览器控制台粘贴整个文件。无依赖、无请求、不改动收藏。
 * 慢速手动滚动；查看 __hestiaFavorites.probe()。
 * 结束后：copy(__hestiaFavorites.export())；停止观察：__hestiaFavorites.stop()。
 * 刷新页面后重跑为独立第二遍。2026-10-24 已验证当前收藏页；结构变化时重新探针核对。
 */
(() => {
  if (window.__hestiaFavorites) {
    window.__hestiaFavorites.scan();
    console.log('已存在采集会话；独立第二遍请刷新页面。');
    return;
  }
  const records = new Map();
  const unresolved = new Map();
  const startedAt = new Date().toISOString();
  const clean = value => (value || '').trim();
  const isUnavailable = text => /视频不见了|视频已删除/.test(text);
  const normalize = (value, kind) => {
    try {
      const url = new URL(value, location.href);
      const pattern = kind === 'video' ? /^\/video\/[0-9]+\/?$/ : /^\/user\/[A-Za-z0-9_-]+\/?$/;
      if (url.protocol !== 'https:' || !['www.douyin.com', 'douyin.com'].includes(url.host)
          || !pattern.test(url.pathname)) return '';
      return 'https://www.douyin.com' + url.pathname.replace(/\/$/, '');
    } catch { return ''; }
  };
  const links = (li, kind) => [...li.querySelectorAll(`a[href*="/${kind}/"]`)]
    .map(anchor => ({ anchor, url: normalize(anchor.href, kind) })).filter(item => item.url);
  const cardMetadata = (li, anchor) => {
    const alt = clean(anchor.querySelector('img[alt]')?.alt);
    const match = alt.match(/^([^：:]+)[：:](.*)$/);
    const altTitle = clean(match ? match[2] : alt);
    if (altTitle) {
      return { title: altTitle, author: clean(match?.[1]), title_source: 'cover-alt' };
    }
    return { title: clean(li.innerText).split('\n')[0], author: '', title_source: 'li.innerText:firstLine' };
  };
  let observer, timer, pending;
  let stopped = false;
  const scan = () => {
    for (const li of document.querySelectorAll('li')) {
      const text = clean(li.innerText);
      const videos = links(li, 'video');
      const urls = new Set(videos.map(item => item.url));
      if (urls.size !== 1) {
        if (isUnavailable(text) && urls.size === 0) {
          unresolved.set(text, { text, reason: '失效提示无视频链接，不能确定视频 ID 或数量' });
        }
        continue;
      }
      const url = videos[0].url;
      const metadata = cardMetadata(li, videos[0].anchor);
      const authors = links(li, 'user');
      const authorUrls = new Set(authors.map(item => item.url));
      const author = authorUrls.size === 1 ? authors[0] : null;
      const observation = {
        title: metadata.title,
        title_source: metadata.title_source,
        author: author ? clean(author.anchor.innerText).replace(/^@/, '') : metadata.author,
        author_source: author ? 'user-anchor' : (metadata.author ? 'cover-alt' : ''),
        author_url: author ? author.url : '',
        invalid: isUnavailable(text),
      };
      const now = new Date().toISOString();
      if (!records.has(url)) {
        records.set(url, { url, ...observation,
          first_seen_at: now, last_seen_at: now, field_variants: {} });
      } else {
        const record = records.get(url);
        for (const [key, value] of Object.entries(observation)) {
          if (value === '') continue;
          if (record[key] === '') record[key] = value;
          else if (record[key] !== value) {
            const variants = record.field_variants[key] ||= [record[key]];
            if (!variants.includes(value)) variants.push(value);
          }
        }
        record.last_seen_at = now;
      }
    }
    return { records: records.size, unresolved_samples: unresolved.size };
  };
  const probe = () => {
    const lis = [...document.querySelectorAll('li')];
    const cards = lis.filter(li => links(li, 'video').length);
    return {
      li_count: lis.length, video_li_count: cards.length,
      user_link_li_count: cards.filter(li => links(li, 'user').length).length,
      ambiguous_video_li_count: cards.filter(li => new Set(links(li, 'video').map(x => x.url)).size > 1).length,
      unavailable_li_count: lis.filter(li => isUnavailable(li.innerText || '')).length,
      samples: cards.slice(0, 3).map(li => {
        const video = links(li, 'video')[0];
        return { text: li.innerText, metadata: cardMetadata(li, video.anchor),
          html: li.outerHTML.slice(0, 1500) };
      }),
      ...scan(), stopped,
    };
  };
  const exportData = () => {
    scan();
    return JSON.stringify({ schema: 'hestia.douyin.favorites.v1', started_at: startedAt,
      exported_at: new Date().toISOString(), page_url: location.origin + location.pathname,
      records: [...records.values()], unresolved_cards: [...unresolved.values()] }, null, 2);
  };
  const stop = () => {
    scan();
    observer?.disconnect();
    clearInterval(timer);
    clearTimeout(pending);
    stopped = true;
    return scan();
  };
  window.__hestiaFavorites = { scan, probe, export: exportData, stop };
  scan();
  observer = new MutationObserver(() => {
    clearTimeout(pending);
    pending = setTimeout(scan, 100);
  });
  observer.observe(document.body, { childList: true, subtree: true, characterData: true,
    attributes: true, attributeFilter: ['href'] });
  timer = setInterval(scan, 1500);
  console.log('采集已启动。先检查 __hestiaFavorites.probe()，再慢速滚动收藏列表。完成后 copy(__hestiaFavorites.export())。');
})();
