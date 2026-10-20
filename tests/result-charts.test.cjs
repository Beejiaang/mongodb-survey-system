const {test} = require('node:test');
const assert = require('node:assert/strict');
const {allowed, seriesOf, metrics, noteFor, arcPath} = require('../static/result-charts.js');

test('单选和多选提供四种图表及表格，数字仅提供分布图', () => {
  for (const kind of ['single_choice', 'multiple_choice', 'status']) {
    assert.deepEqual(allowed(kind), ['bars', 'columns', 'pie', 'donut', 'table']);
  }
  assert.deepEqual(allowed('number'), ['columns', 'bars', 'line', 'table']);
});
test('空值和全零数据不会导致除零，统计单位保留计数', () => {
  assert.deepEqual(metrics([]), {total: 0, max: 0});
  assert.deepEqual(metrics([{count: 0}, {count: 0}]), {total: 0, max: 0});
  assert.deepEqual(metrics([{count: 1}, {count: 2}, {count: 0}]), {total: 3, max: 2});
});
test('禁止负数、非数字、不安全整数和错误标签进入绘图', () => {
  for (const count of [-1, NaN, Infinity, '2', true, 0.5, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => seriesOf({series: [{label: 'A', count}]}));
  }
  assert.throws(() => seriesOf({series: null}));
  assert.throws(() => seriesOf({series: [{label: {}, count: 1}]}));
});
test('长标签、中文和64位整数标签保持字符串且不修改输入', () => {
  const input = {series: [{label: '9223372036854775807', count: 2}, {label: '中'.repeat(500), count: 0}]};
  const copy = JSON.stringify(input);
  assert.deepEqual(seriesOf(input), input.series);
  assert.equal(JSON.stringify(input), copy);
  assert.notEqual(seriesOf(input), input.series);
});
test('多选饼图明确分母为总选中次数，不冒充人数占比', () => {
  for (const mode of ['pie', 'donut']) assert.match(noteFor('multiple_choice', mode), /总选中次数.*不是回答人数/);
  assert.match(noteFor('single_choice', 'pie'), /有效回答份数/);
  assert.match(noteFor('status', 'pie'), /正式提交份数/);
  assert.match(noteFor('number', 'line'), /不表示时间趋势/);
});
test('圆弧坐标在极小及大小扇区中保持有限', () => {
  for (const angle of [1e-10, Math.PI, Math.PI * 1.75]) {
    const path = arcPath(240, 155, 125, -Math.PI / 2, -Math.PI / 2 + angle);
    assert.match(path, /^M /);
    assert.doesNotMatch(path, /NaN|Infinity|undefined/);
  }
});
