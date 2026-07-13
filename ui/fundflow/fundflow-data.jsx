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

/* 本地记忆:切回页面从浏览器 localStorage 秒恢复,连后端请求都不发。
   只有点「更新」按钮才拉最新并覆盖记忆。key 按档位分,concept/industry 各存一份。 */
function glLoadFundflowMemory(kind) {
  try {
    const raw = localStorage.getItem("fundflow_mem_" + (kind || "concept"));
    return raw ? JSON.parse(raw) : null;
  } catch (e) { return null; }
}

function glSaveFundflowMemory(kind, data) {
  try { localStorage.setItem("fundflow_mem_" + (kind || "concept"), JSON.stringify(data)); }
  catch (e) { /* localStorage 满/隐私模式:静默,记忆只是加速,失败不影响功能 */ }
}

Object.assign(window, {
  glFetchFundflowLive, glFetchFundflowHistory,
  glLoadFundflowMemory, glSaveFundflowMemory,
});
