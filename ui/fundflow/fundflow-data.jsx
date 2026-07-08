/* 观澜 · 资金流向 — 数据层:真后端优先,file:// 直开显示断供占位(不合成假数据)。 */
const API = window.GUANLAN_BACKEND || "";

async function glFetchFundflowLive(kind, refresh) {
  if (!API) return { ok: false, reason: "file:// 直开无后端 — 请经 9999 访问" };
  try {
    const q = `kind=${encodeURIComponent(kind || "concept")}${refresh ? "&refresh=1" : ""}`;
    return await (await fetch(`${API}/fundflow/live?${q}`)).json();
  } catch (e) {
    return { ok: false, reason: `后端不可达: ${e.message}` };
  }
}

async function glFetchFundflowHistory(kind, date) {
  if (!API) return { ticks: [], boards: [], market_series: { main_net: [] } };
  try {
    const q = `kind=${encodeURIComponent(kind || "concept")}${date ? `&date=${encodeURIComponent(date)}` : ""}`;
    return await (await fetch(`${API}/fundflow/history?${q}`)).json();
  } catch (e) {
    return { ticks: [], boards: [], market_series: { main_net: [] } };
  }
}

Object.assign(window, { glFetchFundflowLive, glFetchFundflowHistory });
