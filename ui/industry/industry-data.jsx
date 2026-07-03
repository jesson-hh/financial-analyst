/* 观澜 · AI投研 — 数据层:真后端优先,file:// 直开时显示断供占位(不合成假数据)。 */
const API = window.GUANLAN_BACKEND || "";

async function glFetchBoard(refresh) {
  if (!API) return { ok: false, reason: "file:// 直开无后端 — 请经 9999 访问" };
  try {
    const r = await fetch(`${API}/industry/board${refresh ? "?refresh=1" : ""}`);
    return await r.json();
  } catch (e) {
    return { ok: false, reason: `后端不可达: ${e}` };
  }
}
async function glFetchSegment(sid) {
  try { return await (await fetch(`${API}/industry/segment/${sid}`)).json(); }
  catch (e) { return { ok: false, reason: String(e) }; }
}
async function glStartIngest() {
  try {
    return await (await fetch(`${API}/industry/ingest`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    })).json();
  } catch (e) { return { ok: false, reason: String(e) }; }
}
async function glIngestState() {
  try { return await (await fetch(`${API}/industry/ingest_state`)).json(); }
  catch (e) { return { ok: false, reason: String(e) }; }
}
Object.assign(window, { glFetchBoard, glFetchSegment, glStartIngest, glIngestState });
