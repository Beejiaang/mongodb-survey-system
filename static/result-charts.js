/* 本地图表渲染：只使用汇总数据，不加载外部资源。 */
(() => {
  'use strict';
  const colors = ['#23554b', '#bd7944', '#507fb1', '#8973a5', '#76944e', '#bb6266', '#438c91', '#9c873c'];
  const allowed = kind => kind === 'number' ? ['columns', 'bars', 'line', 'table'] : ['single_choice', 'multiple_choice'].includes(kind) ? ['bars', 'columns', 'pie', 'donut', 'table'] : [];
  function seriesOf(data) {
    if (!Array.isArray(data.series)) throw new Error('图表数据格式有误');
    return data.series.map(item => {
      if (typeof item.label !== 'string' || !Number.isSafeInteger(item.count) || item.count < 0) throw new Error('图表计数无效');
      return {label: item.label, count: item.count};
    });
  }
  function metrics(series) {
    return {total: series.reduce((n, item) => n + item.count, 0), max: Math.max(0, ...series.map(item => item.count))};
  }
  function noteFor(kind, mode) {
    if (kind === 'number') return '横轴按数值区间排列，显示全量有效数字回答的频数；区间可能不等宽，柱宽不代表数值跨度。折线连接区间频数，不表示时间趋势。';
    if (kind === 'multiple_choice' && ['pie', 'donut'].includes(mode)) return '圆形图展示各选项占总选中次数的构成比例，分母是所有选项选中次数之和，不是回答人数；回答人数占比请切换数据表格。';
    if (kind === 'multiple_choice') return '每项显示选中该选项的回答份数；一份多选回答可同时计入多个选项。回答人数占比请查看数据表格。';
    return '每项显示选择该选项的回答份数；圆形图以本题有效回答份数为分母。';
  }
  function arcPath(cx, cy, radius, start, end) {
    const p = angle => [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)];
    const a = p(start), b = p(end);
    return `M ${cx} ${cy} L ${a[0]} ${a[1]} A ${radius} ${radius} 0 ${end - start > Math.PI ? 1 : 0} 1 ${b[0]} ${b[1]} Z`;
  }
  function draw(document, container, data, mode) {
    const series = seriesOf(data), {total, max} = metrics(series);
    container.replaceChildren();
    if (!total) {
      const empty = document.createElement('p');
      empty.className = 'chart-empty'; empty.textContent = '暂无可绘制的数据。';
      container.append(empty); return;
    }
    const ns = 'http://www.w3.org/2000/svg';
    const node = (tag, attrs = {}, text) => {
      const el = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
      if (text !== undefined) el.textContent = text;
      return el;
    };
    const svg = node('svg', {role: 'img', 'aria-label': `${mode === 'bars' ? '条形' : mode === 'columns' ? '柱状' : mode === 'line' ? '频数折线' : mode === 'pie' ? '饼' : '环形'}图：${series.map(i => `${i.label} ${i.count}份`).join('；')}`});
    const color = i => colors[i % colors.length];
    const legend = document.createElement('ul'); legend.className = 'chart-legend';
    const round = ['pie', 'donut'].includes(mode);
    series.forEach((item, i) => {
      const li = document.createElement('li');
      const dot = document.createElement('span'); dot.className = 'chart-color'; dot.style.backgroundColor = color(i); dot.setAttribute('aria-hidden', 'true');
      const label = document.createElement('span');
      label.textContent = `${item.label}：${item.count}份${round ? `（${(item.count * 100 / total).toFixed(1)}%）` : ''}`;
      li.append(dot, label); legend.append(li);
    });
    const plot = document.createElement('div'); plot.className = 'chart-plot';
    if (round) {
      svg.setAttribute('viewBox', '0 0 480 320');
      svg.style.maxWidth = '560px'; svg.style.margin = 'auto';
      let angle = -Math.PI / 2;
      series.forEach((item, i) => {
        if (!item.count) return;
        const next = angle + item.count / total * 2 * Math.PI;
        const slice = item.count === total ? node('circle', {cx: 240, cy: 155, r: 125, fill: color(i)}) : node('path', {d: arcPath(240, 155, 125, angle, next), fill: color(i), stroke: '#fffefa', 'stroke-width': 2});
        slice.append(node('title', {}, `${item.label}：${item.count}份`)); svg.append(slice); angle = next;
      });
      if (mode === 'donut') {
        svg.append(node('circle', {cx: 240, cy: 155, r: 75, fill: '#fffefa'}));
        svg.append(node('text', {x: 240, y: 155, 'text-anchor': 'middle', 'font-size': 28, fill: '#23554b'}, total));
        svg.append(node('text', {x: 240, y: 181, 'text-anchor': 'middle', 'font-size': 13}, data.kind === 'multiple_choice' ? '总选中次数' : '合计份数'));
      }
    } else if (mode === 'bars') {
      const height = Math.max(160, series.length * 46 + 30);
      svg.setAttribute('viewBox', `0 0 760 ${height}`); svg.style.minWidth = '560px';
      series.forEach((item, i) => {
        const y = i * 46 + 15, width = item.count / max * 380;
        const short = Array.from(item.label).length > 15 ? Array.from(item.label).slice(0, 15).join('') + '…' : item.label;
        svg.append(node('text', {x: 0, y: y + 20, 'font-size': 14}, short));
        const bar = node('rect', {x: 245, y, width, height: 28, rx: 4, fill: color(i)});
        bar.append(node('title', {}, `${item.label}：${item.count}份`)); svg.append(bar);
        svg.append(node('text', {x: 635, y: y + 20, 'font-size': 14}, `${item.count}份`));
      });
    } else {
      const width = Math.max(640, series.length * 88 + 100), height = 340, baseline = 260;
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.style.minWidth = `${width}px`;
      for (let tick = 0; tick <= 4; tick++) {
        const y = baseline - tick * 55;
        svg.append(node('line', {x1: 55, y1: y, x2: width - 20, y2: y, stroke: '#dde4db'}));
        svg.append(node('text', {x: 45, y: y + 4, 'text-anchor': 'end', 'font-size': 12}, Number((max * tick / 4).toFixed(2))));
      }
      const points = series.map((item, i) => [75 + (i + 0.5) * (width - 100) / series.length, baseline - item.count / max * 220]);
      if (mode === 'line') svg.append(node('polyline', {points: points.map(p => p.join(',')).join(' '), fill: 'none', stroke: '#23554b', 'stroke-width': 3}));
      series.forEach((item, i) => {
        const [x, y] = points[i];
        const shape = mode === 'line' ? node('circle', {cx: x, cy: y, r: 5, fill: color(i)}) : node('rect', {x: x - 22, y, width: 44, height: baseline - y, rx: 3, fill: color(i)});
        shape.append(node('title', {}, `${item.label}：${item.count}份`)); svg.append(shape);
        svg.append(node('text', {x, y: y - 10, 'text-anchor': 'middle', 'font-size': 13}, item.count));
        // 轴标签用序号，完整标签在图例中，长文本不会互相覆盖。
        svg.append(node('text', {x, y: baseline + 25, 'text-anchor': 'middle', 'font-size': 13}, i + 1));
      });
      svg.append(node('text', {x: 55, y: 325, 'font-size': 12}, '横轴序号对应下方图例；纵轴：份数'));
    }
    plot.append(svg); container.append(plot, legend);
  }
  function mount(document) {
    document.querySelectorAll('[data-result-visual]').forEach(widget => {
      const select = widget.querySelector('.chart-kind'), controls = widget.querySelector('.chart-controls');
      const chart = widget.querySelector('.result-chart'), table = widget.querySelector('[data-chart-table]'), note = widget.querySelector('.chart-note');
      let data;
      const key = `survey-chart:v1:${widget.dataset.chartKey}`;
      try {
        data = JSON.parse(widget.querySelector('.chart-data').textContent);
        seriesOf(data);
        if (!allowed(data.kind).length) return;
        try { const saved = document.defaultView.localStorage.getItem(key); if (allowed(data.kind).includes(saved)) select.value = saved; } catch (_) { /* 隐私模式仍可切换。 */ }
      } catch (_) { return; } // 数据异常保留原表格。
      const update = () => {
        const mode = select.value;
        if (!allowed(data.kind).includes(mode)) return;
        try {
          if (mode !== 'table') draw(document, chart, data, mode);
          table.hidden = mode !== 'table'; chart.hidden = mode === 'table';
          note.textContent = noteFor(data.kind, mode); note.hidden = mode === 'table';
          try { document.defaultView.localStorage.setItem(key, mode); } catch (_) { /* 无持久化权限时继续使用。 */ }
        } catch (_) {
          table.hidden = false; chart.hidden = true; note.hidden = false; note.textContent = '暂时无法绘图，已显示原始数据表格。';
        }
      };
      controls.hidden = false; select.addEventListener('change', update); update();
    });
  }
  if (typeof module === 'object' && module.exports) module.exports = {allowed, seriesOf, metrics, noteFor, arcPath, mount};
  else mount(document);
})();
