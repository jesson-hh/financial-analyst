/**
 * 同花顺问财 (iwencai) — natural-language stock screener.
 *
 * Navigates to the result page in a real browser so we sidestep the
 * `hexin-v` HMAC-style header the JSON API requires.
 *
 * The result table's columns vary per question (e.g. "PE 最低的 20 只"
 * yields different columns from "机构持仓>50% 且 ROE>15%"), so we
 * return both ``cells`` (raw text array) and ``columns`` (header row)
 * — let the Python side build a dict.
 */
import { cli, Strategy } from '@jackwener/opencli/registry';

cli({
  site: 'ths-extra',
  name: 'iwencai',
  access: 'read',
  description: '同花顺问财: 自然语言选股查询',
  domain: 'iwencai.com',
  strategy: Strategy.COOKIE,
  navigateBefore: true,
  args: [
    { name: 'question', required: true, positional: true, help: '自然语言查询, 如 "PE 最低的 30 只白酒"' },
    { name: 'limit', type: 'int', default: 20, help: '返回行数' },
  ],
  columns: ['columns', 'cells'],
  func: async (page, kwargs) => {
    const q = encodeURIComponent(String(kwargs.question));
    await page.goto(`https://www.iwencai.com/unifiedwap/result?w=${q}&querytype=stock`);
    // Wait for the result table to render. Selector guesswork — adjust
    // once we see real DOM. The iwencai result table is the largest
    // <table> on the page; we wait for any table to appear.
    await page.wait({ timeout: 20000 });
    // Give the async result a few extra seconds to populate rows.
    await new Promise(r => setTimeout(r, 2500));

    const out = await page.evaluate(`
      (() => {
        const clean = el => (el?.textContent || '').replace(/\\s+/g, ' ').trim();
        // Try several candidate selectors — iwencai changes layouts often.
        const candidates = [
          'table.iwc-table',
          'div.condition-result-wrap table',
          'div.table-container table',
          'table.m-table',
          'table',  // last-ditch fallback: pick the largest table on the page
        ];
        let table = null;
        for (const sel of candidates) {
          const found = document.querySelectorAll(sel);
          if (found.length > 0) {
            // pick the one with most rows
            let best = found[0];
            for (const t of found) {
              if (t.querySelectorAll('tr').length > best.querySelectorAll('tr').length) {
                best = t;
              }
            }
            table = best;
            break;
          }
        }
        if (!table) return { columns: [], rows: [] };
        // Header: prefer thead > th; if iwencai uses a divider-based
        // layout with no thead, the first tr in tbody is likely the
        // header (cells with class hint like 'th'/'header'/short text).
        let columns = Array.from(table.querySelectorAll('thead th')).map(clean).filter(Boolean);
        let bodyTrs = Array.from(table.querySelectorAll('tbody tr'));
        if (columns.length === 0 && bodyTrs.length > 1) {
          const firstCells = bodyTrs[0].querySelectorAll('td');
          // If first row cells are all short text without numeric-only
          // values, treat as header.
          const firstTexts = Array.from(firstCells).map(clean);
          const looksLikeHeader = firstTexts.every(t =>
            t.length > 0 && !/^[-+]?[\\d.]+%?$/.test(t)
          );
          if (looksLikeHeader) {
            columns = firstTexts;
            bodyTrs = bodyTrs.slice(1);
          }
        }
        const rows = bodyTrs.map(r =>
          Array.from(r.querySelectorAll('td')).map(clean)
        ).filter(row => row.length > 0);
        return { columns, rows };
      })()
    `);

    const rows = (out && out.rows) || [];
    const cols = (out && out.columns) || [];
    return rows.slice(0, kwargs.limit).map(cells => ({
      columns: cols.join('|'),
      cells: cells.join('|'),
    }));
  },
});
