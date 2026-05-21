/**
 * 同花顺新概念 (gn 频道) + 概念板块涨幅榜.
 *
 * Two modes via ``--mode``:
 *   - ``new`` (default): pulls the "newly minted concept" list at
 *     ``q.10jqka.com.cn/gn/`` — date / concept name / press-release
 *     link / num stocks. Useful for catching catalysts as they appear.
 *   - ``rank``: pulls the ranked concept-board leaderboard from
 *     ``q.10jqka.com.cn/gn/index/board/all/field/zdf/order/desc/page/N``
 *     — board name / change_pct / total amount / leader stock.
 */
import { cli, Strategy } from '@jackwener/opencli/registry';

cli({
  site: 'ths-extra',
  name: 'concept-board',
  access: 'read',
  description: '同花顺新概念 (mode=new) 或概念板块涨幅榜 (mode=rank)',
  domain: 'q.10jqka.com.cn',
  strategy: Strategy.COOKIE,
  navigateBefore: true,
  args: [
    { name: 'mode', type: 'str', default: 'new', help: 'new=新概念发布表 / rank=涨幅榜 / explore=dump page anchors' },
    { name: 'limit', type: 'int', default: 30 },
    { name: 'page_no', type: 'int', default: 1 },
    { name: 'debug', type: 'str', default: '', help: 'Set to 1 to dump DOM stats' },
    { name: 'url', type: 'str', default: '', help: 'Override probe URL (for explore mode)' },
  ],
  columns: ['board_code', 'board_name', 'change_pct',
            'num_stocks', 'leader_name', 'leader_change', 'release_date'],
  func: async (page, kwargs) => {
    const mode = String(kwargs.mode || 'new');
    let url;
    if (kwargs.url) {
      url = String(kwargs.url);
    } else if (mode === 'rank') {
      url = `http://q.10jqka.com.cn/gn/index/board/all/field/zdf/order/desc/page/${kwargs.page_no}/ajax/1/`;
    } else if (mode === 'explore') {
      url = 'http://q.10jqka.com.cn/gn/';
    } else {
      url = 'http://q.10jqka.com.cn/gn/';
    }
    await page.goto(url);
    await page.wait({ timeout: 15000 });
    await new Promise(r => setTimeout(r, 2000));

    if (mode === 'explore') {
      const anchors = await page.evaluate(`
        (() => {
          return Array.from(document.querySelectorAll('a')).map(a => ({
            text: (a.textContent || '').trim().slice(0, 40),
            href: a.href,
          })).filter(a => a.text && a.href);
        })()
      `);
      return (anchors || []).slice(0, kwargs.limit);
    }

    const debug = String(kwargs.debug || '') === '1';
    const out = await page.evaluate(`
      (() => {
        const clean = el => (el?.textContent || '').replace(/\\s+/g, ' ').trim();
        const tables = document.querySelectorAll('table');
        let best = null, bestRows = 0;
        for (const t of tables) {
          const trs = t.querySelectorAll('tbody tr');
          if (trs.length > bestRows) { bestRows = trs.length; best = t; }
        }
        if (${debug}) {
          return { __debug: {
            tableCount: tables.length,
            bestRows,
            url: location.href,
            title: document.title,
            tableClasses: Array.from(tables).map(t => t.className).slice(0, 8),
            firstRowHTML: best ? best.querySelector('tbody tr')?.outerHTML?.slice(0, 1200) : null,
          }};
        }
        if (!best) return [];
        const rows = Array.from(best.querySelectorAll('tbody tr'));
        return rows.map(tr => {
          const c = tr.querySelectorAll('td');
          return {
            cells: Array.from(c).map(clean),
            html_classes: tr.className,
            anchors: Array.from(tr.querySelectorAll('a')).map(a => ({ text: clean(a), href: a.href })),
          };
        });
      })()
    `);
    if (out && out.__debug) return [out.__debug];
    if (!Array.isArray(out) || out.length === 0) return [];

    // Mode-specific column mapping. The "new concept" page has columns
    // [date, conceptNameAnchor, newsTitle, change, numStocks]; the
    // rank page is similar but with ranking-style columns.
    const extractBoardCode = (href) => {
      const tag = '/code/';
      const idx = (href || '').indexOf(tag);
      if (idx < 0) return '';
      return (href.slice(idx + tag.length).split('/')[0] || '');
    };
    const isPercent = (s) => typeof s === 'string' && s.endsWith('%');
    const isDate = (s) => typeof s === 'string' && s.length === 10
        && s[4] === '-' && s[7] === '-';

    const results = out.map(row => {
      const cells = row.cells || [];
      const anchors = row.anchors || [];
      const boardLink = anchors.find(a => (a.href || '').indexOf('/gn/detail/code/') >= 0)
                      || anchors[0] || {};
      return {
        board_code: extractBoardCode(boardLink.href),
        board_name: boardLink.text || cells[1] || '',
        change_pct: cells.find(isPercent) || cells[3] || '',
        num_stocks: cells[cells.length - 1] || '',
        release_date: isDate(cells[0]) ? cells[0] : '',
        leader_name: '',
        leader_change: '',
        board_url: boardLink.href || '',
        raw_cells: cells.join(' | '),
      };
    }).filter(r => r.board_name && r.board_name !== '--');
    return results.slice(0, kwargs.limit);
  },
});
