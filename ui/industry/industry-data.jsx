/* 观澜 · AI投研 — 数据层:真后端优先,file:// 直开时显示断供占位(不合成假数据)。
   2026-07-03 多框架:全部请求带 fw(框架id,缺省 ai_chain)。 */
const API = window.GUANLAN_BACKEND || "";

async function glFetchFrameworks() {
  if (!API) return [];
  try { return await (await fetch(`${API}/industry/frameworks`)).json(); }
  catch (e) { return []; }
}
async function glFetchBoard(refresh, fw) {
  if (!API) return { ok: false, reason: "file:// 直开无后端 — 请经 9999 访问" };
  try {
    const r = await fetch(`${API}/industry/board?fw=${encodeURIComponent(fw || "ai_chain")}${refresh ? "&refresh=1" : ""}`);
    return await r.json();
  } catch (e) {
    return { ok: false, reason: `后端不可达: ${e}` };
  }
}
async function glFetchSegment(sid, fw) {
  try { return await (await fetch(`${API}/industry/segment/${sid}?fw=${encodeURIComponent(fw || "ai_chain")}`)).json(); }
  catch (e) { return { ok: false, reason: String(e) }; }
}
async function glStartIngest(fw) {
  try {
    return await (await fetch(`${API}/industry/ingest`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fw: fw || "ai_chain" }),
    })).json();
  } catch (e) { return { ok: false, reason: String(e) }; }
}
async function glIngestState(fw) {
  try { return await (await fetch(`${API}/industry/ingest_state?fw=${encodeURIComponent(fw || "ai_chain")}`)).json(); }
  catch (e) { return { ok: false, reason: String(e) }; }
}
Object.assign(window, { glFetchFrameworks, glFetchBoard, glFetchSegment, glStartIngest, glIngestState });
