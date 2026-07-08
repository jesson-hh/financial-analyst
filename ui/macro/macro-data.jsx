/* 观澜 · 全球情绪 — 数据层:真后端优先,file:// 直开显示断供占位(不合成假数据)。 */
const API = window.GUANLAN_BACKEND || "";

async function glFetchMacroPulse(refresh) {
  if (!API) return { ok: false, reason: "file:// 直开无后端 — 请经 9999 访问" };
  try {
    const r = await fetch(`${API}/macro/pulse${refresh ? "?refresh=1" : ""}`);
    return await r.json();
  } catch (e) {
    return { ok: false, reason: `后端不可达: ${e}` };
  }
}

async function glFetchMacroHistory(marketId) {
  if (!API) return [];
  try {
    return await (await fetch(`${API}/macro/history?market_id=${encodeURIComponent(marketId)}`)).json();
  } catch (e) { return []; }
}

async function glFetchMarketTape() {
  if (!API) return { ok: false, warming: false, reason: "file:// 直开无后端 — 请经 9999 访问" };
  try {
    return await (await fetch(`${API}/data/market_tape`)).json();
  } catch (e) {
    return { ok: false, warming: false, reason: `后端不可达: ${e}` };
  }
}

Object.assign(window, { glFetchMacroPulse, glFetchMacroHistory, glFetchMarketTape });
