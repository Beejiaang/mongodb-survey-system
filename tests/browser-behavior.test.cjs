// 在最小 DOM/Clipboard 替身中执行真实脚本；不将此称为真实浏览器验收。
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const {mount} = require('../static/result-charts.js');

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.attrs = {}; this.style = {};
    this.hidden = false; this.textContent = ''; this.listeners = {};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener(event, handler) { this.listeners[event] = handler; }
  tags() { return [this.tag, ...this.children.flatMap(child => child.tags())]; }
}

function chartFixture({kind = 'single_choice', key = 'survey:q1', storage = new Map(), raw, failDraw = false} = {}) {
  const parts = Object.fromEntries(['.chart-kind', '.chart-controls', '.result-chart', '[data-chart-table]', '.chart-note', '.chart-data'].map(s => [s, new Element()]));
  parts['.chart-kind'].value = kind === 'number' ? 'columns' : 'bars';
  for (const s of ['.chart-controls', '.result-chart', '.chart-note']) parts[s].hidden = true;
  parts['.chart-data'].textContent = raw ?? JSON.stringify({kind, series: [{label: 'A', count: 2}, {label: 'B', count: 1}]});
  const widget = {dataset: {chartKey: key}, querySelector: s => parts[s]};
  const document = {
    querySelectorAll: () => [widget],
    createElement: tag => new Element(tag),
    createElementNS: (_, tag) => { if (failDraw) throw new Error('drawing unavailable'); return new Element(tag); },
    defaultView: {localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)}},
  };
  mount(document);
  return {parts, select(mode) { parts['.chart-kind'].value = mode; parts['.chart-kind'].listeners.change(); }};
}

test('实际切换事件绘制全部支持图形，表格模式恢复原表格', () => {
  for (const kind of ['single_choice', 'multiple_choice', 'status', 'number']) {
    const f = chartFixture({kind});
    const modes = kind === 'number' ? {columns: 'rect', bars: 'rect', line: 'polyline'} : {bars: 'rect', columns: 'rect', pie: 'path', donut: 'circle'};
    for (const [mode, shape] of Object.entries(modes)) {
      f.select(mode);
      assert.equal(f.parts['[data-chart-table]'].hidden, true);
      assert.equal(f.parts['.result-chart'].hidden, false);
      assert.ok(f.parts['.result-chart'].tags().includes(shape), `${kind}/${mode}`);
    }
    f.select('table');
    assert.equal(f.parts['[data-chart-table]'].hidden, false);
    assert.equal(f.parts['.result-chart'].hidden, true);
    assert.equal(f.parts['.chart-note'].hidden, true);
  }
});

test('图表选择重新挂载后恢复，其他题不受影响，非法缓存忽略', () => {
  const storage = new Map();
  chartFixture({storage}).select('donut');
  assert.equal(chartFixture({storage}).parts['.chart-kind'].value, 'donut');
  assert.equal(chartFixture({storage, key: 'survey:q2'}).parts['.chart-kind'].value, 'bars');
  storage.set('survey-chart:v1:survey:q1', 'pie');
  assert.equal(chartFixture({storage, kind: 'number'}).parts['.chart-kind'].value, 'columns');
  assert.deepEqual([...storage.keys()], ['survey-chart:v1:survey:q1', 'survey-chart:v1:survey:q2']);
});

test('浏览器拒绝本地存储时仍能切换图表和表格', () => {
  const storage = {get() { throw new Error('denied'); }, set() { throw new Error('denied'); }};
  const f = chartFixture({storage});
  f.select('pie');
  assert.ok(f.parts['.result-chart'].tags().includes('path'));
  f.select('table');
  assert.equal(f.parts['[data-chart-table]'].hidden, false);
});

test('损坏JSON或非法计数保留可读表格并隐藏无效控件', () => {
  for (const raw of ['{broken', JSON.stringify({series: [{label: 'A', count: -1}]})]) {
    const {parts} = chartFixture({raw});
    assert.equal(parts['[data-chart-table]'].hidden, false);
    assert.equal(parts['.chart-controls'].hidden, true);
    assert.equal(parts['.result-chart'].hidden, true);
  }
});

test('绘图异常恢复表格且给出提示，仍可切换表格模式', () => {
  const f = chartFixture({failDraw: true});
  assert.equal(f.parts['[data-chart-table]'].hidden, false);
  assert.equal(f.parts['.result-chart'].hidden, true);
  assert.match(f.parts['.chart-note'].textContent, /无法绘图.*原始数据表格/);
  f.select('table');
  assert.equal(f.parts['[data-chart-table]'].hidden, false);
});

function shareFixture(navigator, hasButton = true) {
  const button = new Element('button');
  const input = {value: 'http://127.0.0.1:5000/survey/0123456789abcdef01234567', focused: false, selected: false,
    focus() { this.focused = true; }, select() { this.selected = true; }};
  const message = new Element();
  const elements = {'copy-share': hasButton ? button : null, 'share-url': input, 'copy-status': message};
  vm.runInNewContext(readFileSync(require.resolve('../static/share.js'), 'utf8'), {navigator, document: {getElementById: id => elements[id]}});
  return {button, input, message};
}

test('分享按钮将完整链接交给剪贴板并显示成功提示', async () => {
  const copied = [];
  const f = shareFixture({clipboard: {writeText: async text => copied.push(text)}});
  await f.button.listeners.click();
  assert.deepEqual(copied, [f.input.value]);
  assert.match(f.message.textContent, /链接已复制.*需登录/);
  assert.equal(f.input.selected, false);
});

test('剪贴板缺失或拒绝权限时选中完整链接供手动复制', async () => {
  for (const navigator of [{}, {clipboard: {writeText: async () => { throw new Error('denied'); }}}]) {
    const f = shareFixture(navigator);
    await f.button.listeners.click();
    assert.equal(f.input.focused, true);
    assert.equal(f.input.selected, true);
    assert.match(f.message.textContent, /手动复制/);
  }
});

test('没有分享按钮的页面加载脚本不报错', () => {
  const f = shareFixture({}, false);
  assert.deepEqual(f.button.listeners, {});
});
