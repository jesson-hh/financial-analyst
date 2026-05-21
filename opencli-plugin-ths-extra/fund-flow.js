/**
 * 同花顺资金流排行 — 个股 / 概念 / 行业 / 大单 4 种.
 *
 * ``target`` switches which leaderboard to pull:
 *   - ``gegu`` (默认): 个股资金流 /funds/ggzjl/ (10 cols)
 *   - ``gainian``: 概念资金流 /funds/gnzjl/ (per-concept 主力净额)
 *   - ``hangye``: 行业资金流 /funds/hyzjl/ (per-industry 主力净额)
 *   - ``ddzz``: 大单追踪 /funds/ddzz/ (single large-order events)
 *
 * For ``gainian`` / ``hangye`` the leaderboard row identifies a board
 * (not a stock), so ``code`` holds the board code (e.g. ``881101``)
 * and the per-row schema differs slightly — we still return ``code/
 * name/change_pct/main_net/...`` columns; downstream code uses the
 * board code transparently.
 */
import { cli, Strategy } from '@jackwener/opencli/registry';

const TARGET_URLS = {
  gegu: 'http://data.10jqka.com.cn/funds/ggzjl/',
  gainian: 'http://data.10jqka.com.cn/funds/gnzjl/',
  hangye: 'http://data.10jqka.com.cn/funds/hyzjl/',
  ddzz: 'http://data.10jqka.com.cn/funds/ddzz/',
};

cli({
  site: 'ths-extra',
  name: 'fund-flow',
  access: 'read',
  description: '同花顺资金流: 个股(gegu)/概念(gainian)/行业(hangye)/大单(ddzz)',
  domain: 'data.10jqka.com.cn',
  strategy: Strategy.COOKIE,
  navigateBefore: true,
  args: [
    { name: 'target', type: 'str', default: 'gegu',
      help: 'gegu=个股 / gainian=概念 / hangye=行业 / ddzz=大单追踪' },
    { name: 'page_no', type: 'int', default: 1, help: '页码 (每页约 20 行)' },
    { name: 'limit', type: 'int', default: 30, help: '本次返回最多行数 (跨页)' },
    { name: 'sort_by', type: 'str', default: 'zdf',
      help: 'zdf=涨跌幅 / zljlrjzb=主力净占比, 排序字段' },
    { name: 'debug', type: 'str', default: '', help: 'Set to 1 to dump column layout' },
  ],
  columns: ['code', 'name', 'price', 'change_pct',
            'turnover_pct', 'inflow', 'outflow', 'main_net', 'total_amount'],
  func: async (page, kwargs) => {
    const target = String(kwargs.target || 'gegu');
    const base = TARGET_URLS[target] || TARGET_URLS.gegu;
    // /funds/ggzjl/ supports field/order/page/ajax/free path-style;
    // gnzjl / hyzjl / ddzz may not — fall back to the base URL with no
    // path params for non-gegu targets, then sort-by becomes a no-op.
    const url = target === 'gegu'
      ? `${base}field/${kwargs.sort_by}/order/desc/page/${kwargs.page_no}/ajax/1/free/1/`
      : `${base}field/${kwargs.sort_by}/order/desc/page/${kwargs.page_no}/ajax/1/`;
    await page.goto(url);
    await page.wait({ timeout: 15000 });
    await new Promise(r => setTimeout(r, 1500));

    const debug = String(kwargs.debug || '') === '1';
    const out = await page.evaluate(`
      (() => {
        const clean = el => (el?.textContent || '').replace(/\\s+/g, ' ').trim();
        const candidates = [
          'table.m-table',
          'div.m-table-box table',
          'div.m-pager-table',
          'table',
        ];
        let table = null;
        let bestRows = 0;
        for (const sel of candidates) {
          for (const t of document.querySelectorAll(sel)) {
            const n = t.querySelectorAll('tbody tr').length;
            if (n > bestRows) { bestRows = n; table = t; }
          }
          if (table && bestRows > 0) break;
        }
        if (!table) return { headers: [], rows: [] };
        const headerRow = table.querySelector('thead tr') || table.querySelector('tr');
        const headers = headerRow
          ? Array.from(headerRow.querySelectorAll('th, td')).map(clean)
          : [];
        const rows = Array.from(table.querySelectorAll('tbody tr')).map(tr => {
          const cells = Array.from(tr.querySelectorAll('td')).map(clean);
          // Capture first anchor href so the Python side can build a code link
          const firstAnchor = tr.querySelector('a[href]');
          return {
            cells,
            href: firstAnchor?.href || '',
          };
        }).filter(r => r.cells.length > 0);
        return { headers, rows };
      })()
    `);

    const headers = (out && out.headers) || [];
    const rawRows = (out && out.rows) || [];

    if (debug) {
      return [{
        __debug: {
          target,
          url,
          headers,
          rowCount: rawRows.length,
          firstCells: rawRows[0]?.cells || [],
        },
      }];
    }

    // Header-driven column resolution: find the index of each known
    // logical column in the header row, falling back to position-based
    // guesses if the page doesn't render a thead.
    const idxOf = (...names) => {
      for (const n of names) {
        const i = headers.findIndex(h => h.includes(n));
        if (i >= 0) return i;
      }
      return -1;
    };
    const HAS_HEADERS = headers.length > 0;
    // The four leaderboards have non-overlapping schemas:
    //   gegu:    序号 | 代码 | 简称 | 现价 | 涨跌幅 | 换手率 | 流入 | 流出 | 净额 | 成交额
    //   gainian: 序号 | 行业(=concept name) | 行业指数 | 涨跌幅 | 流入 | 流出 | 净额 | 公司家数 | 领涨股 | (领涨涨跌幅) | (当前价)
    //   hangye:  similar to gainian (industry instead of concept)
    //   ddzz:    different — large-order events, has 成交时间/方向/价格/数量
    // Aliases below capture each.
    const codeIdx = HAS_HEADERS ? idxOf('代码', '板块代码') : 1;
    const nameIdx = HAS_HEADERS
      ? idxOf('简称', '名称', '行业', '概念', '板块')
      : 2;
    const priceIdx = HAS_HEADERS
      ? idxOf('最新价', '现价', '指数', '当前价', '成交价格', '价格')
      : 3;
    const chgIdx = HAS_HEADERS ? idxOf('涨跌幅', '涨幅') : 4;
    const turnoverIdx = HAS_HEADERS ? idxOf('换手率') : 5;
    const inflowIdx = HAS_HEADERS ? idxOf('流入', '资金流入') : 6;
    const outflowIdx = HAS_HEADERS ? idxOf('流出', '资金流出') : 7;
    const mainNetIdx = HAS_HEADERS
      ? idxOf('净额', '主力净流入', '净流入')
      : 8;
    const totalAmtIdx = HAS_HEADERS ? idxOf('成交额', '成交金额') : 9;
    // gainian/hangye specific extras
    const numStocksIdx = HAS_HEADERS ? idxOf('公司家数', '股票数') : -1;
    const leaderIdx = HAS_HEADERS ? idxOf('领涨股', '领涨') : -1;
    // ddzz specific
    const tradeTimeIdx = HAS_HEADERS ? idxOf('成交时间', '时间') : -1;
    const directionIdx = HAS_HEADERS ? idxOf('大单性质', '方向', '买卖', '性质') : -1;
    // ddzz volume column
    const volumeIdx = HAS_HEADERS ? idxOf('成交量', '数量') : -1;

    const pickCell = (cells, idx) => (idx >= 0 && idx < cells.length) ? cells[idx] : '';

    const items = rawRows.map(({ cells, href }) => ({
      target,
      code: pickCell(cells, codeIdx),
      name: pickCell(cells, nameIdx),
      price: pickCell(cells, priceIdx),
      change_pct: pickCell(cells, chgIdx),
      turnover_pct: pickCell(cells, turnoverIdx),
      inflow: pickCell(cells, inflowIdx),
      outflow: pickCell(cells, outflowIdx),
      main_net: pickCell(cells, mainNetIdx),
      total_amount: pickCell(cells, totalAmtIdx),
      num_stocks: pickCell(cells, numStocksIdx),
      leader: pickCell(cells, leaderIdx),
      trade_time: pickCell(cells, tradeTimeIdx),
      direction: pickCell(cells, directionIdx),
      volume: pickCell(cells, volumeIdx),
      url: href,
      raw_cells: cells.join('|'),
    })).filter(r => {
      // Drop placeholder rows where neither code nor a meaningful
      // name exists. '--' is the 10jqka empty-cell sentinel.
      const validName = r.name && r.name !== '--' && r.name !== '';
      const validCode = r.code && r.code !== '--';
      return validName || validCode;
    });

    // For concept/industry leaderboards the board code is encoded in
    // the row anchor href (e.g. .../code/309152/), not in the cells.
    // Extract it so downstream consumers have a stable identifier.
    for (const r of items) {
      if (!r.code && r.url) {
        const tag = '/code/';
        const i = r.url.indexOf(tag);
        if (i >= 0) r.code = r.url.slice(i + tag.length).split('/')[0];
      }
    }
    return items.slice(0, kwargs.limit);
  },
});
