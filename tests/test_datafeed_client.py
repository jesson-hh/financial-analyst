# -*- coding: utf-8 -*-
"""datafeed.live_client 单测(全离线,桩 subprocess)+ 与 stocks 注册表的 source_id 对账守护。"""
import json
import re
import subprocess as _sp
import types
from pathlib import Path

import pytest

import guanlan_v2.datafeed.live_client as lc


def _proc(stdout="", returncode=0, stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _envelope(source_id="cninfo_irm", status="ok", items=None, error=""):
    return json.dumps({"source_id": source_id, "provider": "cninfo", "category": "interaction",
                       "query": {}, "fetched_ts": "2026-07-07T01:00:00", "items": items or [],
                       "status": status, "error": error, "write_enabled": False},
                      ensure_ascii=False)


@pytest.fixture(autouse=True)
def _fast_and_isolated(monkeypatch, tmp_path):
    """免真实节流等待 + probe 指到临时桩文件 + catalog 缓存隔离。"""
    monkeypatch.setattr(lc, "_MIN_INTERVAL_S", 0.0)
    probe = tmp_path / "scripts" / "probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(lc, "_STOCKS_PROBE", probe)
    monkeypatch.setattr(lc, "_CATALOG_CACHE", {"ts": 0.0, "rows": None})
    yield


def test_resolve_source_alias_and_unknown():
    assert lc.resolve_source("em_zt_pool") == "em_limit_up_pool"     # 旧短名→canonical
    assert lc.resolve_source("eastmoney_fund_flow") == "eastmoney_fund_flow"
    assert lc.resolve_source("northbound") == "ths_hsgt_realtime"
    assert lc.resolve_source("catalog") == "catalog"
    assert lc.resolve_source("nope") == ""


def test_probe_caller_errors(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr("subprocess.run", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    assert lc.probe("nope")["ok"] is False
    assert lc.probe("stock_news")["ok"] is False                     # 缺必填 code
    assert "code" in lc.probe("stock_news")["note"]
    assert lc.probe("eps_forecast")["ok"] is False                   # ths_eps_forecast 缺 code 亦拒(需股票代码)
    assert "code" in lc.probe("ths_eps_forecast")["note"]
    assert lc.probe("em_zt_pool", date="下周")["ok"] is False         # date 归一后非 8 位
    assert lc.probe("cninfo_irm", code="000630", limit="abc")["ok"] is False
    assert called["n"] == 0                                          # caller 错误全部不起子进程


def test_probe_happy_normalizes_and_clips(monkeypatch):
    items = [{"title": "问", "text": "答" * 900, "publish_ts": "2026-07-07T00:30:00",
              "raw": {"question": "Q1", "answer": "答" * 900}}]
    seen = {}
    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["kw"] = kw
        return _proc(stdout=_envelope(items=items))
    monkeypatch.setattr("subprocess.run", fake_run)
    out = lc.probe("irm", code="SZ000630", limit=5)                  # alias 进,canonical 出
    assert out["ok"] is True and out["status"] == "ok" and out["n"] == 1
    assert out["source"] == "cninfo_irm" and out["code"] == "000630"
    assert "--source=cninfo_irm" in seen["cmd"] and "--code=000630" in seen["cmd"]
    assert seen["kw"].get("cwd") and seen["kw"].get("timeout") == 90
    it = out["items"][0]
    assert it["text"].endswith("…") and len(it["text"]) == 401       # 顶层截 400
    assert it["raw"]["answer"].endswith("…")                          # raw 内层同截,不剥(保真)


def test_probe_date_pool_default_and_iso(monkeypatch):
    seen = {}
    monkeypatch.setattr("subprocess.run",
                        lambda cmd, **kw: (seen.__setitem__("cmd", cmd),
                                           _proc(stdout=_envelope(source_id="em_limit_up_pool")))[1])
    lc.probe("em_zt_pool")
    assert any(a.startswith("--date=2") and len(a) == 15 for a in seen["cmd"])   # --date=YYYYMMDD
    out2 = lc.probe("em_zt_pool", date="2026-07-06")
    assert out2["date"] == "20260706" and "--date=20260706" in seen["cmd"]


def test_probe_planned_and_error_passthrough(monkeypatch):
    # iwencai_search 是剩余唯一 planned 源(需 API key);ths_eps_forecast 已转 available
    monkeypatch.setattr("subprocess.run",
                        lambda cmd, **kw: _proc(stdout=_envelope(source_id="iwencai_search",
                                                                 status="planned")))
    out = lc.probe("iwencai")
    assert out["ok"] is True and out["status"] == "planned" and out["items"] == []
    assert "planned" in out["note"]
    monkeypatch.setattr("subprocess.run",
                        lambda cmd, **kw: _proc(stdout=_envelope(status="error", error="TypeError: boom")))
    out2 = lc.probe("cninfo_irm", code="000630")
    assert out2["ok"] is True and out2["status"] == "error" and "boom" in out2["note"]


def test_probe_mechanical_failures_degrade(monkeypatch, tmp_path):
    monkeypatch.setattr(lc, "_STOCKS_PROBE", tmp_path / "absent.py")
    out = lc.probe("em_hot_rank")
    assert out["ok"] is True and out["items"] == [] and "不可用" in out["note"]
    probe = tmp_path / "s" / "p.py"
    probe.parent.mkdir()
    probe.write_text("#", encoding="utf-8")
    monkeypatch.setattr(lc, "_STOCKS_PROBE", probe)
    def boom(cmd, **kw):
        raise _sp.TimeoutExpired(cmd, 90)
    monkeypatch.setattr("subprocess.run", boom)
    assert "超时" in lc.probe("em_hot_rank")["note"]
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: _proc(returncode=2, stderr="Trace x"))
    assert "Trace x" in lc.probe("em_hot_rank")["note"]
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: _proc(stdout="not json"))
    assert "JSON" in lc.probe("em_hot_rank")["note"]


def test_catalog_cache_and_static_fallback(monkeypatch):
    rows = [{"source_id": "eastmoney_stock_news", "alias": "stock_news", "status": "available"},
            {"source_id": "brand_new_source", "alias": "shiny", "status": "available"}]
    calls = {"n": 0}
    def fake_run(cmd, **kw):
        calls["n"] += 1
        return _proc(stdout=json.dumps({"source": "catalog", "rows": rows}))
    monkeypatch.setattr("subprocess.run", fake_run)
    c1 = lc.catalog()
    c2 = lc.catalog()
    assert c1["origin"] == "probe" and c2["origin"] == "cache" and calls["n"] == 1
    assert lc.resolve_source("shiny") == "brand_new_source"          # 动态目录参与别名解析
    # 失败 → 静态兜底(诚实 note)
    monkeypatch.setattr(lc, "_CATALOG_CACHE", {"ts": 0.0, "rows": None})
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: _proc(returncode=1, stderr="x"))
    c3 = lc.catalog()
    assert c3["origin"] == "static" and "兜底" in c3["note"]
    assert len(c3["sources"]) == len(lc._STATIC_SOURCES)


def test_probe_catalog_source(monkeypatch):
    rows = [{"source_id": f"s{i}", "alias": f"a{i}", "status": "available"} for i in range(31)]
    monkeypatch.setattr("subprocess.run",
                        lambda cmd, **kw: _proc(stdout=json.dumps({"source": "catalog", "rows": rows})))
    out = lc.probe("catalog", limit=5)
    assert out["ok"] is True and out["n"] == 31                      # catalog 不受 limit 截


def test_native_rows_flatten_raw_first():
    items = [{"title": "金科股份", "publish_ts": "2026-07-06T09:30:00",
              "visible_ts": "2026-07-06T09:30:00", "visible_ts_reason": "exact",
              "raw": {"code": "000656", "name": "金科股份", "zt_stat": "3天3板", "limit_days": 3}},
             {"title": "无raw条", "text": "t", "raw": {}}]
    rows = lc.native_rows(items)
    assert rows[0]["zt_stat"] == "3天3板" and rows[0]["code"] == "000656"   # raw 原生键平铺保真
    assert rows[0]["publish_ts"] == "2026-07-06T09:30:00"                   # 统一时间字段补入
    assert "raw" not in rows[0]
    assert rows[1]["title"] == "无raw条"                                     # raw 空退回顶层字段


def test_throttle_spaces_launches(monkeypatch):
    monkeypatch.setattr(lc, "_MIN_INTERVAL_S", 1.0)
    slept = []
    monkeypatch.setattr(lc.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(lc, "_LAST_LAUNCH", [lc.time.time()])        # 刚起跑过一发
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: _proc(stdout=_envelope()))
    lc.probe("cninfo_irm", code="000630")
    assert slept and 0 < slept[0] <= 1.0                              # 第二发被间隔


def test_probe_non_dict_json_degrades(monkeypatch):
    """合法 JSON 但非对象(数组/标量)→ 诚实作废,不 .get 崩(评审 Minor:旧壳 isinstance 守卫回归)。"""
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: _proc(stdout="[1,2,3]"))
    out = lc.probe("cninfo_irm", code="000630")
    assert out["ok"] is True and out["items"] == [] and "非对象" in out["note"]
    monkeypatch.setattr("subprocess.run",
                        lambda cmd, **kw: _proc(stdout=json.dumps({"source": "catalog", "rows": [1, 2]})))
    monkeypatch.setattr(lc, "_CATALOG_CACHE", {"ts": 0.0, "rows": None})
    # catalog 里 rows 非 dict 元素不崩(能起 probe、结果结构异常也回静态兜底或空)
    c = lc.catalog()
    assert c["ok"] is True


def test_tencent_quote_preserves_prefix_and_multicode(monkeypatch):
    """tencent 前缀/多码原样透传(评审:6位提取会毁 SH000001 前缀、砍逗号多码)。"""
    seen = {}
    monkeypatch.setattr("subprocess.run",
                        lambda cmd, **kw: (seen.__setitem__("cmd", cmd),
                                           _proc(stdout=_envelope(source_id="tencent_realtime_quote")))[1])
    lc.probe("realtime_quote", code="SH000001")
    assert "--code=SH000001" in seen["cmd"]                           # 前缀不被 6 位提取毁掉
    lc.probe("tencent_realtime_quote", code="600519,000630")
    assert "--code=600519,000630" in seen["cmd"]                      # 逗号多码不被砍成首只


def test_sector_fund_flow_alias_and_passthrough():
    """板块资金流源:别名解析 + code 档位透传(concept/industry 不被 6位提取毁掉)。"""
    assert lc.resolve_source("sector_fund_flow") == "eastmoney_sector_fund_flow"
    assert lc.resolve_source("eastmoney_sector_fund_flow") == "eastmoney_sector_fund_flow"
    # code 档位透传:concept 不被 \d{6} 提取清空
    norm = lc._normalize_args("eastmoney_sector_fund_flow", "concept", "")
    assert norm["err"] == "" and norm["code"] == "concept"


def test_static_sources_reconcile_with_stocks_registry():
    """守护 _STATIC_SOURCES 与 stocks LIVE_SOURCE_REGISTRY 的 canonical source_id 集合对账;
    stocks 缺席则 skip。别名为加法式 resolve 键,不要求与 stocks alias 字段逐一相等。"""
    reg = Path(r"G:\stocks\src\data\live_sources.py")
    if not reg.exists():
        pytest.skip("stocks 仓不在此机")
    text = reg.read_text(encoding="utf-8")
    ids = set(re.findall(r'"source_id":\s*"([a-z_0-9]+)"', text))
    ids.discard("a_stock_live_sources")                              # catalog meta 源,非注册表条目
    static_ids = set(lc._STATIC_SOURCES)
    assert static_ids == ids, f"漂移:观澜多 {sorted(static_ids - ids)};stocks 多 {sorted(ids - static_ids)}"
    assert len(ids) == 53   # 2026-07-16:+overseas_index_quote(海外六大指数,数据面收口)


def test_new_sources_resolve_and_arg_classification():
    """2026-07-09 补的 16 源:静态即可解析(无需 catalog 探针)+ arg 口径分类正确。
    要害:期权合约 id/问财 query 禁 \\d{6} 提取;打板情绪 date 缺省补当日;tdx 类缺 code 报错。"""
    # 1) 16 新源全部静态可解析(catalog 未探针也认)
    for sid in ("tdx_realtime_quote", "tdx_orderbook", "tdx_transaction", "tdx_kline",
                "sina_option_tquote", "sina_option_greeks", "sina_financial_report",
                "eastmoney_stock_info", "stock_live_brief", "full_valuation",
                "limit_up_sentiment", "iwencai_query", "baidu_kline_ma"):
        assert lc.resolve_source(sid) == sid, f"{sid} 未静态解析"
    # 别名也认
    assert lc.resolve_source("orderbook") == "tdx_orderbook"
    assert lc.resolve_source("tdx_quote") == "tdx_realtime_quote"
    # 2) 期权合约 id(8 位/CON_OP_ 前缀)passthrough 保原样,绝不被 \d{6} 截成 6 位
    n = lc._normalize_args("sina_option_tquote", "CON_OP_10004949", "")
    assert n["err"] == "" and n["code"] == "CON_OP_10004949"
    n2 = lc._normalize_args("sina_option_greeks", "10004949", "")
    assert n2["err"] == "" and n2["code"] == "10004949"
    # 3) 问财 query=自然语言,passthrough 保原样(修既有 iwencai_search 隐患一并覆盖)
    for src in ("iwencai_query", "iwencai_search"):
        nq = lc._normalize_args(src, "医药板块高增长龙头", "")
        assert nq["err"] == "" and nq["code"] == "医药板块高增长龙头", f"{src} query 被清空"
    # 4) 打板情绪 date 缺省补当日 8 位;非法 date 报错
    nd = lc._normalize_args("limit_up_sentiment", "", "")
    assert nd["err"] == "" and len(nd["date"]) == 8 and nd["date"].isdigit()
    # 5) tdx 类缺 code 诚实报错;带 6 位 code 归一放行
    ne = lc._normalize_args("tdx_realtime_quote", "", "")
    assert ne["err"] and "code" in ne["err"]
    ng = lc._normalize_args("tdx_orderbook", "SZ000630", "")
    assert ng["err"] == "" and ng["code"] == "000630"
    # 6) 新浪期权代码档有默认标的,不强制 code(缺 code 不报错)
    nc = lc._normalize_args("sina_option_codes", "", "")
    assert nc["err"] == ""


_FAKE_WORKER = r'''
import json, sys, time
def reply(obj):
    sys.stdout.write("\x1e" + json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()
reply({"id": "__ready__", "payload": {"ok": True}})
print("stray non-protocol line")   # 杂散打印必须被宿主过滤
sys.stdout.flush()
for line in sys.stdin:
    req = json.loads(line)
    src = req.get("source")
    if src == "eastmoney_stock_news":   # sleepy:用于超时测试
        time.sleep(30)
    reply({"id": req["id"],
           "payload": {"source_id": src, "status": "ok",
                       "items": [{"raw": {"src": src, "code": req.get("code")}}],
                       "error": "", "fetched_ts": "t"}})
'''


def _enable_fake_worker(monkeypatch, tmp_path, script: str = _FAKE_WORKER):
    p = tmp_path / "fake_worker.py"
    p.write_text(script, encoding="utf-8")
    monkeypatch.setenv("GUANLAN_PROBE_WORKER", "1")
    monkeypatch.setattr(lc, "_STOCKS_WORKER", p)
    monkeypatch.setattr(lc, "_MIN_INTERVAL_S", 0.0)   # 测试不等 1s 闸
    monkeypatch.setattr(lc, "_WORKER", {"w": None, "seq": 0})
    return p


def test_worker_roundtrip_reuses_process_and_filters_stray_lines(monkeypatch, tmp_path):
    """2026-07-30 T2:probe() 走常驻 worker——同形信封、杂散 stdout 行被 RS 前缀过滤、
    连续两次调用复用同一进程(这就是提速的全部来源:不再每次 spawn+import)。"""
    _enable_fake_worker(monkeypatch, tmp_path)
    out1 = lc.probe("em_hot_rank", limit=5)
    assert out1["ok"] is True and out1["status"] == "ok" and out1["n"] == 1
    assert out1["items"][0]["raw"]["src"] == "eastmoney_hot_rank"   # worker 收正典 id
    pid1 = lc._WORKER["w"]["proc"].pid
    out2 = lc.probe("ths_hot_list", limit=5)
    assert out2["ok"] is True and lc._WORKER["w"]["proc"].pid == pid1, "必须复用常驻进程"
    lc._worker_kill(lc._WORKER["w"])


def test_worker_timeout_kills_and_respawns(monkeypatch, tmp_path):
    """超时=杀 worker 惰性重生,机械失败信封与一次性路径同形(note 超时,绝不半截数据);
    下一条请求自动重生新进程继续服务。"""
    _enable_fake_worker(monkeypatch, tmp_path)
    out = lc.probe("stock_news", code="600519", timeout=2)   # fake 里 sleep 30
    assert out["ok"] is True and out["status"] == "" and "超时" in out["note"]
    assert lc._WORKER["w"] is None, "超时必须杀掉句柄"
    out2 = lc.probe("em_hot_rank", limit=3)                  # 重生后继续服务
    assert out2["ok"] is True and out2["status"] == "ok"
    lc._worker_kill(lc._WORKER["w"])


def test_worker_busy_falls_back_to_oneshot_not_queue(monkeypatch, tmp_path):
    """T2 评审 important 回归:worker 单飞,抢不到锁(前一条慢源在飞)必须立即回落
    一次性子进程(按隔离并行),绝不在锁上排队逃逸调用方 timeout 预算。"""
    _enable_fake_worker(monkeypatch, tmp_path)
    monkeypatch.setattr(lc, "_WORKER_LOCK_WAIT_S", 0.1)
    seen = []
    monkeypatch.setattr(lc, "_run_probe",
                        lambda args, timeout=90: seen.append(1) or
                        {"ok": True, "payload": {"source_id": "eastmoney_hot_rank",
                                                 "status": "ok", "items": [], "error": ""}})
    assert lc._WORKER_LOCK.acquire(timeout=1), "测试自身须先占住锁"
    try:
        out = lc.probe("em_hot_rank")
        assert out["ok"] is True and len(seen) == 1, "忙时必须走一次性回落"
        assert out.get("worker_fallback"), "回落必须带显形键"
    finally:
        lc._WORKER_LOCK.release()


def test_worker_spawn_failure_enters_cooldown(monkeypatch, tmp_path):
    """T2 评审回归:spawn 失败进入冷却窗——窗内后续调用不再付 spawn 成本直接回落;
    状态经 worker_status() 显形(health probe_worker 项消费)。"""
    bad = tmp_path / "bad_worker.py"
    bad.write_text("import sys; sys.exit(3)", encoding="utf-8")   # 起来即死
    monkeypatch.setenv("GUANLAN_PROBE_WORKER", "1")
    monkeypatch.setattr(lc, "_STOCKS_WORKER", bad)
    monkeypatch.setattr(lc, "_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(lc, "_WORKER", {"w": None, "seq": 0})
    monkeypatch.setattr(lc, "_WORKER_STDERR_LOG", tmp_path / "w.log")
    monkeypatch.setattr(lc, "_WORKER_STATE",
                        {"state": "cold", "spawn_fails": 0, "cooldown_until": 0.0,
                         "last_error": "", "last_ok_ts": 0.0})
    monkeypatch.setattr(lc, "_run_probe",
                        lambda args, timeout=90: {"ok": True, "payload": {
                            "source_id": "eastmoney_hot_rank", "status": "ok",
                            "items": [], "error": ""}})
    spawns = []
    real_spawn = lc._worker_spawn

    def _counting_spawn(*a, **k):
        spawns.append(1)
        return real_spawn(*a, **k)

    monkeypatch.setattr(lc, "_worker_spawn", _counting_spawn)
    lc.probe("em_hot_rank")          # 首调:真 spawn 一次并失败
    lc.probe("em_hot_rank")          # 冷却窗内:不再 spawn
    assert len(spawns) == 1, "冷却窗内不得重复付 spawn 成本"
    st = lc.worker_status()
    assert st["state"] == "cooldown" and st["spawn_fails"] == 1
    assert st["cooldown_until"] > 0 and st["last_error"]


def test_worker_unavailable_falls_back_to_oneshot(monkeypatch, tmp_path):
    """worker 脚本缺席 → 诚实回落一次性 CLI 路径(_run_probe),行为与旧世界逐位一致。"""
    monkeypatch.setenv("GUANLAN_PROBE_WORKER", "1")
    monkeypatch.setattr(lc, "_STOCKS_WORKER", tmp_path / "absent_worker.py")
    seen = []
    monkeypatch.setattr(lc, "_run_probe",
                        lambda args, timeout=90: seen.append(args) or
                        {"ok": True, "payload": {"source_id": "em_hot_rank", "status": "ok",
                                                 "items": [], "error": ""}})
    out = lc.probe("em_hot_rank")
    assert out["ok"] is True and out["status"] == "ok" and len(seen) == 1


def test_probe_many_single_subprocess_same_envelope(monkeypatch):
    """2026-07-28 提速契约:probe_many 一批只起一个子进程(--batch),
    每条结果与 probe() 同形信封、序=入参序,status/planned/error 显形照旧。"""
    calls = []

    def fake_batch(specs, timeout):
        calls.append(list(specs))
        payloads = []
        for s in specs:
            payloads.append({"source_id": s["source"], "status": "ok",
                             "items": [{"raw": {"x": s["source"]}}], "error": "",
                             "fetched_ts": "t"})
        return {"ok": True, "payloads": payloads}

    monkeypatch.setattr(lc, "_run_probe_batch", fake_batch)
    outs = lc.probe_many([{"source": "em_zt_pool"}, {"source": "northbound"}])
    assert len(calls) == 1 and len(calls[0]) == 2, "必须一次批量子进程"
    # 别名归一为正典 id(与 probe() 同语义)
    assert [o["source"] for o in outs] == ["em_limit_up_pool", "ths_hsgt_realtime"]
    assert all(o["ok"] and o["status"] == "ok" and o["n"] == 1 for o in outs)


def test_probe_many_falls_back_per_item_only_when_channel_absent(monkeypatch):
    """旧 stocks CLI 无 --batch(退码非 0,argparse 在任何上游请求前退出)→ 逐条回退。
    channel_absent 是回退的唯一通行证。"""
    monkeypatch.setattr(lc, "_run_probe_batch",
                        lambda specs, timeout: {"ok": False, "channel_absent": True,
                                                "note": "旧 CLI 不认 --batch"})
    seen = []
    monkeypatch.setattr(lc, "probe",
                        lambda source, code="", date="", limit=20, timeout=90:
                        seen.append((source, timeout)) or {"ok": True, "source": source, "status": "ok",
                                                           "items": [], "n": 0, "note": ""})
    outs = lc.probe_many([{"source": "em_zt_pool"}, {"source": "northbound", "code": ""}], timeout=30)
    assert [s for s, _ in seen] == ["em_limit_up_pool", "ths_hsgt_realtime"]  # 回退用归一后的正典 id
    assert all(t == 30 for _, t in seen), "回退必须透传调用方 timeout(评审:不透传静默突破预算)"
    assert len(outs) == 2 and all(o["ok"] for o in outs)


def test_probe_many_timeout_never_refires_upstream(monkeypatch):
    """评审 important 回归:批量超时/输出损坏=上游请求已真实发出,绝不逐条回退重打
    (恰在限流时加压);全部条目按机械失败 ok:False 诚实作废(消费方据此保留上轮)。"""
    monkeypatch.setattr(lc, "_run_probe_batch",
                        lambda specs, timeout: {"ok": False, "channel_absent": False,
                                                "note": "batch probe 超时(150s),本批作废不重拉"})

    def _no_refire(*a, **k):
        raise AssertionError("超时后绝不允许逐条重打上游")

    monkeypatch.setattr(lc, "probe", _no_refire)
    outs = lc.probe_many([{"source": "em_zt_pool"}, {"source": "northbound"}])
    assert len(outs) == 2
    assert all(o["ok"] is False and "作废" in o["note"] for o in outs)


def test_probe_many_discards_misaligned_batch(monkeypatch):
    """评审回归:payloads 与 specs 数量/源名失配(两仓漂移时 stocks 侧 skip/重排)→
    整批 ok:False 作废,绝不按下标串位把 A 源信封装进 B 源数据。"""
    def _skewed(specs, timeout):
        # 模拟 stocks 侧漏掉第一条:数量少一且名字错位
        return {"ok": True, "payloads": [{"source_id": s["source"], "status": "ok", "items": []}
                                         for s in specs[1:]]}

    monkeypatch.setattr(lc, "_run_probe_batch", _skewed)
    outs = lc.probe_many([{"source": "em_zt_pool"}, {"source": "northbound"}])
    assert all(o["ok"] is False and "失配" in o["note"] for o in outs)


def test_probe_many_isolates_invalid_items(monkeypatch):
    """非法 source 条目就地落 ok:False 信封,不进批也不拖垮其余条目;位置保持。"""
    def fake_batch(specs, timeout):
        return {"ok": True, "payloads": [{"source_id": s["source"], "status": "ok",
                                          "items": [], "error": ""} for s in specs]}

    monkeypatch.setattr(lc, "_run_probe_batch", fake_batch)
    outs = lc.probe_many([{"source": "em_zt_pool"}, {"source": "no_such_src"}, {"source": "northbound"}])
    assert outs[0]["ok"] is True and outs[2]["ok"] is True
    assert outs[1]["ok"] is False and "非法" in outs[1]["note"]
