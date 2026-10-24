// 运行：node --test tests/douyin_favorites_collect.test.cjs；全部为虚构 DOM。
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../scripts/douyin_favorites_collect.js'), 'utf8');

function card(text, videos = [], authors = [], alt = '') {
  return { innerText: text, outerHTML: '<li>虚构卡片</li>', querySelectorAll(selector) {
    const isVideo = selector.includes('/video/');
    const values = isVideo ? videos : authors;
    return values.map(value => {
      const anchor = typeof value === 'string' ? { href: value, innerText: '' } : value;
      if (isVideo) anchor.querySelector = () => alt ? { alt } : null;
      return anchor;
    });
  } };
}
const video = id => `https://www.douyin.com/video/${id}`;
function setup(cards) {
  let observed = 0, disconnected = 0, intervals = 0, cleared = 0;
  const document = { body: {}, querySelectorAll: () => cards };
  const context = vm.createContext({ window: {}, document, location: {
    href: 'https://www.douyin.com/user/self?showTab=favorite',
    origin: 'https://www.douyin.com', pathname: '/user/self',
  }, URL, console: { log() {} },
  MutationObserver: class { observe() { observed++; } disconnect() { disconnected++; } },
  setInterval() { intervals++; return 1; }, clearInterval() { cleared++; },
  setTimeout() { return 2; }, clearTimeout() {},
  });
  vm.runInContext(source, context);
  return { context, document, api: context.window.__hestiaFavorites,
    state: () => ({ observed, disconnected, intervals, cleared }),
    data: () => JSON.parse(context.window.__hestiaFavorites.export()),
  };
}

test('captures first screen, retains recycled cards, normalizes URLs without losing ID precision', () => {
  const s = setup([card('首屏', [video('1234567890123456789') + '?x=1#fragment'])]);
  s.document.querySelectorAll = () => [card('第二屏', [video('2')])];
  s.api.scan();
  assert.equal(s.data().records.length, 2);
  assert.equal(s.data().records[0].url, video('1234567890123456789'));
});

test('prefers cover alt for title and author instead of visible like count', () => {
  const s = setup([card('3234\n可见标题', [video('5')], [], '作者甲：完整标题：含冒号')]);
  const r = s.data().records[0];
  assert.equal(r.title, '完整标题：含冒号');
  assert.equal(r.title_source, 'cover-alt');
  assert.equal(r.author, '作者甲');
  assert.equal(r.author_source, 'cover-alt');
  assert.equal(r.author_url, '');
});

test('preserves conflicting titles, authors and availability instead of overwriting', () => {
  const s = setup([card('原标题', [video('1')], [{ href: 'https://www.douyin.com/user/abc?x=1', innerText: '@作者' }])]);
  s.document.querySelectorAll = () => [card('视频不见了', [video('1')])];
  const r = s.data().records[0];
  assert.equal(r.title, '原标题');
  assert.equal(r.author, '作者');
  assert.deepEqual(r.field_variants.invalid, [false, true]);
  assert.deepEqual(r.field_variants.title, ['原标题', '视频不见了']);
  assert.equal(r.author_url, 'https://www.douyin.com/user/abc');
});

test('retains unavailable linked cards and anonymous unavailable samples', () => {
  const s = setup([card('视频不见了', [video('1')]), card('视频已删除')]);
  assert.equal(s.data().records[0].invalid, true);
  assert.equal(s.data().unresolved_cards.length, 1);
});

test('ignores ambiguous containers, foreign URLs and does not guess among multiple authors', () => {
  const s = setup([card('父列表', [video('1'), video('2')]),
    card('外站', ['https://evil.com/video/3']),
    card('多作者', [video('4')], ['https://www.douyin.com/user/aaa', 'https://www.douyin.com/user/bbb'])]);
  assert.equal(s.data().records.length, 1);
  assert.equal(s.data().records[0].author_url, '');
  assert.equal(s.api.probe().ambiguous_video_li_count, 1);
});

test('reinjection does not duplicate observers; stop disconnects and export still works', () => {
  const s = setup([card('标题', [video('1')])]);
  vm.runInContext(source, s.context);
  assert.equal(s.state().observed, 1);
  assert.equal(s.state().intervals, 1);
  s.api.stop();
  assert.equal(s.state().disconnected, 1);
  assert.equal(s.state().cleared, 1);
  assert.equal(s.api.probe().stopped, true);
  assert.equal(s.data().records.length, 1);
});

test('the original user snippet raises ReferenceError when a record reaches filter', () => {
  const context = vm.createContext({ document: { querySelectorAll: () => [{ innerText: '标题',
    querySelector: selector => selector.includes('/video/') ? { href: video('1') } : null }] }, copy() {} });
  assert.throws(() => vm.runInContext(`copy(JSON.stringify(Array.from(document.querySelectorAll('li')).map(li=>{const a=li.querySelector('a[href*="/video/"]');if(!a)return null;const u=a.href.split('?')[0];const t=(li.innerText||'').split('\\n')[0].trim();const au=li.querySelector('a[href*="/user/"]');return {title:t,url:u,author:au?.innerText?.trim().replace('@','')||'',author_url:au?.href?.split('?')[0]||''}}).filter(v=>v&&!li.innerText.includes('视频不见了'))))`, context), /li is not defined/);
});
