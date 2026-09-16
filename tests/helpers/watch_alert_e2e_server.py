# -*- coding: utf-8 -*-
"""Isolated real-LLM watch-alert E2E server.

This helper is intentionally impossible to start without an explicit disposable root and
token.  It mounts the production Console router, production alert router and production UI,
but replaces only the market clock/quote source with a controllable intraday feed.  No test
state is written to the ordinary ``var/console`` or ``var/watch_alerts`` stores.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENGINE = REPO / "engine"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from guanlan_v2.console.api import build_console_router, _default_agent_factory  # noqa: E402
from guanlan_v2.console.store import ConsoleStore  # noqa: E402
from guanlan_v2.console import tools as console_tools  # noqa: E402
from guanlan_v2.watch_alerts.api import build_watch_alerts_router  # noqa: E402
from guanlan_v2.watch_alerts.core import AlertStore, WatchAlertEngine  # noqa: E402

TZ = timezone(timedelta(hours=8))


def _required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


ROOT = Path(_required_env("WATCH_ALERT_E2E_ROOT")).resolve()
SAFE_PARENT = (REPO / "var" / "e2e").resolve()
try:
    ROOT.relative_to(SAFE_PARENT)
except ValueError as exc:
    raise RuntimeError("WATCH_ALERT_E2E_ROOT must stay below repo/var/e2e") from exc
if not ROOT.name.startswith("guanlan-watch-alert-e2e-"):
    raise RuntimeError("WATCH_ALERT_E2E_ROOT leaf lacks the safety prefix")
ROOT.mkdir(parents=True, exist_ok=True)
TOKEN = _required_env("WATCH_ALERT_E2E_TOKEN")
PORT = int(_required_env("WATCH_ALERT_E2E_PORT"))

# Fail-closed isolation for every lazy import and every same-process HTTP tool callback.
os.environ["GUANLAN_PORT"] = str(PORT)
os.environ["FA_CONFIG_DIR"] = str(REPO / "config")
os.environ["FINANCIAL_ANALYST_HOME"] = str(ROOT / "fa_home")
os.environ["CONSOLE_REVIEW_MODE"] = "off"
os.environ["GUANLAN_WATCH_ALERT_ROOT"] = str(ROOT / "watch_alerts")
os.environ["GUANLAN_WORKFLOW_ROOT"] = str(ROOT / "workflow")
os.environ["GUANLAN_WISDOM_ROOT"] = str(ROOT / "wisdom")
os.environ["FA_WISDOM_ROOT"] = str(ROOT / "wisdom")
os.environ["GUANLAN_ORCH_STORE_ROOT"] = str(ROOT / "orchestration")

# ConsoleStore injection isolates sessions/workspaces; these older module globals need an
# explicit redirect as well.  The allowlist prevents an unexpected model choice from touching
# unrelated application tools during this focused E2E.
console_tools._MEMORY_PATH = ROOT / "console" / "memory.md"
console_tools._ARCHIVE_PATH = ROOT / "console" / "memory.archive.md"
console_tools._REPORTS_STORE = ROOT / "reports"
console_tools.CONSOLE_ALLOWED = {
    "ww_plan_update", "ww_watch_alert_research", "ww_watch_alerts",
    "ww_watch_alert_manage",
}


def _load_local_secrets() -> None:
    from guanlan_v2.runtime_secrets import load_credential_file

    # This manual real-LLM E2E explicitly opts into the local credential
    # fallback, but it still cannot import feature/write/runtime gates.
    load_credential_file(REPO / "var" / "secrets.env")


_load_local_secrets()

_sim_lock = threading.RLock()
_sim = {
    "now": datetime(2026, 8, 21, 10, 0, 0, tzinfo=TZ),
    "quote_asof": None,
    "change_pct": -1.0,
    "price": 10.0,
    "quote_fetch_count": 0,
}


def _quotes(codes):
    with _sim_lock:
        _sim["quote_fetch_count"] += 1
        now = _sim["now"]
        quote_asof = _sim["quote_asof"] or now
        change_pct = float(_sim["change_pct"])
        price = float(_sim["price"])
    return {
        str(code): {
            "code": str(code), "name": "浦发银行·E2E", "price": price,
            "prevClose": 10.2, "high": 10.3, "low": 9.7,
            "changePercent": change_pct,
            "asof": quote_asof.strftime("%Y%m%d%H%M%S"),
        }
        for code in codes
    }


class SimEngine(WatchAlertEngine):
    def status(self, now=None):
        with _sim_lock:
            simulated_now = _sim["now"]
        return super().status(simulated_now)

    def tick(self, now=None):
        with _sim_lock:
            simulated_now = _sim["now"]
        return super().tick(simulated_now)


alert_store = AlertStore(ROOT / "watch_alerts")
engine = SimEngine(alert_store, quote_fn=_quotes,
                   universe_fn=lambda n: [f"SH{600000 + i:06d}" for i in range(n)])
console_store = ConsoleStore(ROOT / "console")


def _real_alert_agent(sid: str):
    agent = _default_agent_factory(sid)
    # The model, provider, tool registry and confirmation path remain production-real; only
    # the unrelated external data-catalog preflight is disabled for a hermetic alert test.
    agent._guanlan_runtime_catalog_preflight = False
    return agent


@asynccontextmanager
async def lifespan(_app):
    async def loop():
        while True:
            await asyncio.to_thread(engine.tick)
            await asyncio.sleep(0.5)

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)
app.include_router(build_console_router(store=console_store, agent_factory=_real_alert_agent))
app.include_router(build_watch_alerts_router(engine=engine))


class MarketIn(BaseModel):
    at: str
    change_pct: float
    price: float = 10.0
    quote_asof: str | None = None


def _auth(value: str) -> None:
    if value != TOKEN:
        raise HTTPException(status_code=403, detail="invalid e2e token")


@app.post("/e2e/market")
def set_market(body: MarketIn, x_e2e_token: str = Header(default="")):
    _auth(x_e2e_token)
    parsed = datetime.fromisoformat(body.at)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    parsed = parsed.astimezone(TZ)
    if parsed.date().isoformat() != "2026-08-21" or not (9 <= parsed.hour <= 15):
        raise HTTPException(status_code=422, detail="simulation must stay in the pinned session")
    quote_asof = None
    if body.quote_asof:
        quote_asof = datetime.fromisoformat(body.quote_asof)
        if quote_asof.tzinfo is None:
            quote_asof = quote_asof.replace(tzinfo=TZ)
        quote_asof = quote_asof.astimezone(TZ)
    with _sim_lock:
        _sim.update(now=parsed, quote_asof=quote_asof,
                    change_pct=float(body.change_pct), price=float(body.price))
    return {"ok": True, "at": parsed.isoformat(timespec="seconds"),
            "change_pct": body.change_pct, "price": body.price,
            "quote_asof": quote_asof.isoformat(timespec="seconds") if quote_asof else None}


@app.get("/e2e/state")
def e2e_state(x_e2e_token: str = Header(default="")):
    _auth(x_e2e_token)
    with _sim_lock:
        snapshot = dict(_sim)
    snapshot["now"] = snapshot["now"].isoformat(timespec="seconds")
    if snapshot.get("quote_asof"):
        snapshot["quote_asof"] = snapshot["quote_asof"].isoformat(timespec="seconds")
    return {"ok": True, "simulation": snapshot,
            "rules": alert_store.list_rules(), "events": alert_store.list_events(limit=20)}


app.mount("/ui", StaticFiles(directory=str(REPO / "ui"), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
