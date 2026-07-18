# -*- coding: utf-8 -*-
"""datafeed.snapshot_archive 单测(全离线,tmp 注入,绝不碰真 var/)。

小 phase 甲「盘口/资金快照归档」:EOD append-only 归档器 + read_archive 三方契约。
覆盖:archive_eod 三 kind 落盘/幂等/append-only/月轮转(尾读红线)、诚实三态跳过、
maybe_archive_eod 三门钩子、read_archive PIT 读、契约不变/无网络/tmp 隔离守护。
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

import guanlan_v2.datafeed.snapshot_archive as sa
import guanlan_v2.datafeed.market_tape as mt
import guanlan_v2.fundflow.pulse as ff


# ── fixtures / helpers ─────────────────────────────────────────────────────────
def _mt_cache(*, board_date=None, pulled_at="2026-07-17T15:03:00", zt_rows=None):
    """market_tape 缓存 payload(含 sources 行级池 + derived + board_date)。"""
    zt = zt_rows if zt_rows is not None else [
        {"code": "600000", "limit_days": 2, "break_times": 0},
        {"code": "300001", "limit_days": 1, "break_times": 1},
    ]
    return {
        "pulled_at": pulled_at, "ttl_s": 180,
        "sources": {
            "em_limit_up_pool": {"status": "ok", "n": len(zt), "pulled_at": pulled_at,
                                 "note": "", "rows": zt},
            "em_zb_pool": {"status": "ok", "n": 1, "pulled_at": pulled_at,
                           "note": "", "rows": [{"code": "111"}]},
            "northbound": {"status": "ok", "n": 1, "pulled_at": pulled_at,
                           "note": "", "rows": [{"time": "15:00", "hgt_yi": -9.28, "sgt_yi": -31.1}]},
        },
        "derived": {"zt_count": len(zt), "zb_count": 1, "max_streak": 2,
                    "north_net": -40.38, "north_scope": "沪+深股通"},
        "board_date": board_date, "board_backfilled": bool(board_date),
    }


def _ff_cache(*, kind="concept", ok=True, pulled_at="2026-07-17T15:03:00", market_date="2026-07-17"):
    """fundflow_live_<kind> 缓存 payload(boards 排行行级 + 大盘五档分解)。"""
    return {
        "ok": ok, "kind": kind, "pulled_at": pulled_at, "trading": False,
        "market": {"date": market_date, "main_net": -100.5, "super_net": -50.0,
                   "big_net": -50.5, "mid_net": 20.0, "small_net": 80.0, "src_host": "push2"},
        "breadth": {"allA": {"up": None, "down": None},
                    "industry": {"up": 40, "down": 46}, "concept": {"up": 200, "down": 150}},
        "boards": [{"code": "BK0473", "name": "半导体", "main_net": 12.3, "change_pct": 1.1, "rank": 1},
                   {"code": "BK0447", "name": "煤炭", "main_net": -8.7, "change_pct": -0.9, "rank": 2}],
        "src_host": "push2", "notes": ["全A 涨跌家数暂无独立源"],
    }


def _write_live(live_dir: Path, *, mt_payload=None, concept=None, industry=None):
    live_dir.mkdir(parents=True, exist_ok=True)
    if mt_payload is not None:
        (live_dir / "market_tape.json").write_text(
            json.dumps(mt_payload, ensure_ascii=False), encoding="utf-8")
    if concept is not None:
        (live_dir / "fundflow_live_concept.json").write_text(
            json.dumps(concept, ensure_ascii=False), encoding="utf-8")
    if industry is not None:
        (live_dir / "fundflow_live_industry.json").write_text(
            json.dumps(industry, ensure_ascii=False), encoding="utf-8")


def _read_lines(path: Path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.fixture
def dirs(tmp_path):
    live = tmp_path / "var" / "live"
    arch = tmp_path / "var" / "archive"
    return live, arch


# ── Task 1: archive_eod 落盘 + 幂等 + 月轮转 + archived_at + 无网络接缝 ──────────────
def test_archive_eod_appends_one_row_per_kind_with_full_rowlevel_payload(dirs):
    live, arch = dirs
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"),
                concept=_ff_cache(kind="concept"), industry=_ff_cache(kind="industry"))
    rpt = sa.archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)

    assert sorted(rpt["archived"]) == ["fundflow_concept", "fundflow_industry", "market_tape"]
    assert rpt["skipped"] == []

    mt_rows = _read_lines(arch / "market_tape" / "snapshots.jsonl")
    assert len(mt_rows) == 1
    row = mt_rows[0]
    assert row["trade_date"] == "20260717" and row["kind"] == "market_tape"
    # 全量行级:zt 池行级明细必须在 payload(R16:rot 族电池因子需要行级,裁减=因子永久 UNAVAILABLE)
    zt = row["payload"]["sources"]["em_limit_up_pool"]["rows"]
    assert zt[0]["code"] == "600000" and zt[0]["limit_days"] == 2
    assert row["payload"]["derived"]["north_net"] == -40.38

    ff_rows = _read_lines(arch / "fundflow_concept" / "snapshots.jsonl")
    assert len(ff_rows) == 1
    ffp = ff_rows[0]["payload"]
    assert ff_rows[0]["trade_date"] == "20260717"
    assert ffp["boards"][0]["code"] == "BK0473"            # boards 行级
    assert ffp["market"]["main_net"] == -100.5             # 大盘五档分解


def test_archive_eod_idempotent_same_day_bytes_unchanged(dirs):
    live, arch = dirs
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"),
                concept=_ff_cache(kind="concept"), industry=_ff_cache(kind="industry"))
    sa.archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    before = (arch / "market_tape" / "snapshots.jsonl").read_bytes()
    rpt2 = sa.archive_eod(now=datetime(2026, 7, 17, 18, 30, 0), cache_dir=live, archive_dir=arch)
    after = (arch / "market_tape" / "snapshots.jsonl").read_bytes()
    assert before == after                                  # 同 (trade_date, kind) 重跑零改
    assert "market_tape" in [s["kind"] for s in rpt2["skipped"]]
    assert rpt2["archived"] == []


def test_archive_eod_monthly_rotation_moves_prior_month_out_of_main(dirs):
    live, arch = dirs
    mtdir = arch / "market_tape"
    mtdir.mkdir(parents=True, exist_ok=True)
    # 主文件里先塞一条上月(6 月)行 → 归档时应搬到 snapshots-202606.jsonl,主文件只剩本月
    prior = {"trade_date": "20260630", "archived_at": "2026-06-30T18:00:00",
             "kind": "market_tape", "payload": {"sources": {}, "derived": {}}}
    (mtdir / "snapshots.jsonl").write_text(
        json.dumps(prior, ensure_ascii=False) + "\n", encoding="utf-8")

    _write_live(live, mt_payload=_mt_cache(board_date="20260717"))
    sa.archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)

    main_rows = _read_lines(mtdir / "snapshots.jsonl")
    assert [r["trade_date"] for r in main_rows] == ["20260717"]      # 主文件只剩本月
    rotated = _read_lines(mtdir / "snapshots-202606.jsonl")
    assert [r["trade_date"] for r in rotated] == ["20260630"]        # 上月搬入月度归档


def test_archive_eod_archived_at_is_call_time(dirs):
    live, arch = dirs
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"))
    now = datetime(2026, 7, 17, 18, 5, 42)
    sa.archive_eod(now=now, cache_dir=live, archive_dir=arch)
    row = _read_lines(arch / "market_tape" / "snapshots.jsonl")[0]
    assert row["archived_at"] == now.isoformat(timespec="seconds")


def test_archive_eod_never_touches_network_seams(dirs, monkeypatch):
    live, arch = dirs
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"),
                concept=_ff_cache(kind="concept"), industry=_ff_cache(kind="industry"))

    def _boom(*a, **k):
        raise AssertionError("归档路径绝不触网/绝不调 SWR 刷新")
    monkeypatch.setattr(mt, "_refresh", _boom)
    monkeypatch.setattr(mt, "_trigger_refresh", _boom)
    monkeypatch.setattr(ff, "_refresh_live", _boom)
    monkeypatch.setattr(ff, "build_live", _boom)
    monkeypatch.setattr(ff, "_trigger_live_refresh", _boom)

    rpt = sa.archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    assert sorted(rpt["archived"]) == ["fundflow_concept", "fundflow_industry", "market_tape"]


def test_archive_eod_market_tape_trade_date_falls_back_to_pulled_at_when_no_board_date(dirs):
    """正常交易日 board_date=None(_backfill_board_pools 未回溯)→ trade_date 取 pulled_at 之日。"""
    live, arch = dirs
    _write_live(live, mt_payload=_mt_cache(board_date=None, pulled_at="2026-07-17T15:03:00"))
    sa.archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    row = _read_lines(arch / "market_tape" / "snapshots.jsonl")[0]
    assert row["trade_date"] == "20260717"


# ── Task 2: fundflow 侧诚实三态跳过(缺源/ok:false/warming)+ 死 docstring 更正 ────
def test_archive_eod_skips_missing_fundflow_cache_with_specific_reason(dirs):
    """fundflow 缓存文件缺失 → 该 kind 出现在 skipped、reason 具体、归档零新行。"""
    live, arch = dirs
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"))   # 仅 market_tape,fundflow 缺
    rpt = sa.archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    skipped = {s["kind"]: s["reason"] for s in rpt["skipped"]}
    assert "fundflow_concept" in skipped and "缺失" in skipped["fundflow_concept"]
    assert "fundflow_industry" in skipped
    assert not (arch / "fundflow_concept" / "snapshots.jsonl").exists()   # 零新行(文件不建)


def test_archive_eod_skips_fundflow_ok_false(dirs):
    """fundflow 缓存 ok:false(源不可用)→ 诚实跳过,reason 携带 notes,绝不落半假行。"""
    live, arch = dirs
    bad = {"ok": False, "kind": "concept", "pulled_at": "2026-07-17T15:03:00",
           "market": {}, "boards": [], "notes": ["concept 档板块资金流不可用:空"]}
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"),
                concept=bad, industry=_ff_cache(kind="industry"))
    rpt = sa.archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    skipped = {s["kind"]: s["reason"] for s in rpt["skipped"]}
    assert "fundflow_concept" in skipped and "ok:false" in skipped["fundflow_concept"]
    assert "不可用" in skipped["fundflow_concept"]               # notes 具体病因显形
    assert "fundflow_industry" in rpt["archived"]               # 健康档正常归档
    assert not (arch / "fundflow_concept" / "snapshots.jsonl").exists()


def test_archive_eod_skips_warming(dirs):
    """warming 预热占位(缓存缺失时 read 层的占位态)→ 跳过,绝不把空壳当真快照归档。"""
    live, arch = dirs
    warming = {"warming": True, "kind": "concept", "pulled_at": None, "boards": []}
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"), concept=warming)
    rpt = sa.archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    skipped = {s["kind"]: s["reason"] for s in rpt["skipped"]}
    assert "fundflow_concept" in skipped and "warming" in skipped["fundflow_concept"]
    assert not (arch / "fundflow_concept" / "snapshots.jsonl").exists()


def test_archive_eod_skips_market_tape_warming(dirs):
    """market_tape 侧 warming 占位同样诚实跳过。"""
    live, arch = dirs
    _write_live(live, mt_payload={"warming": True, "sources": {}, "derived": {}})
    rpt = sa.archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    skipped = {s["kind"]: s["reason"] for s in rpt["skipped"]}
    assert "market_tape" in skipped and "warming" in skipped["market_tape"]


def test_snap_default_dead_path_not_referenced_by_archiver():
    """fundflow.pulse._SNAP_DEFAULT 是母版抄来的死路径(定义后从未使用)——归档器绝不引用它。
    docstring 更正后 pulse 仍留置 _SNAP_DEFAULT 不动(避免连带),但归档器不得依赖它。"""
    import inspect
    src = inspect.getsource(sa)
    assert "_SNAP_DEFAULT" not in src
    assert "var/fundflow" not in src and "var\\fundflow" not in src   # 不写死母版死目录


# ── Task 3: maybe_archive_eod 三门钩子(env 闸 / 收盘后 15:05 / 当日 dedup)+ 自吞异常 ──
def test_maybe_archive_gate_env_off_returns_false_no_write(dirs, monkeypatch):
    """①env 总闸 GUANLAN_SNAPSHOT_ARCHIVE!=1 → 直接 False,零写。"""
    live, arch = dirs
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"))
    monkeypatch.delenv("GUANLAN_SNAPSHOT_ARCHIVE", raising=False)
    ok = sa.maybe_archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    assert ok is False
    assert not (arch / "market_tape").exists()


def test_maybe_archive_gate_before_close_returns_false_no_write(dirs, monkeypatch):
    """②收盘后门:now < 15:05 → False,零写(盘中不归档,数据未定格)。"""
    live, arch = dirs
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"))
    monkeypatch.setenv("GUANLAN_SNAPSHOT_ARCHIVE", "1")
    ok = sa.maybe_archive_eod(now=datetime(2026, 7, 17, 14, 30, 0), cache_dir=live, archive_dir=arch)
    assert ok is False
    assert not (arch / "market_tape").exists()


def test_maybe_archive_passes_all_gates_writes_and_returns_true(dirs, monkeypatch):
    """闸开 + 收盘后(>=15:05)+ 当日未归档 → 真归档,返回 True。"""
    live, arch = dirs
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"),
                concept=_ff_cache(kind="concept"), industry=_ff_cache(kind="industry"))
    monkeypatch.setenv("GUANLAN_SNAPSHOT_ARCHIVE", "1")
    ok = sa.maybe_archive_eod(now=datetime(2026, 7, 17, 15, 6, 0), cache_dir=live, archive_dir=arch)
    assert ok is True
    assert len(_read_lines(arch / "market_tape" / "snapshots.jsonl")) == 1


def test_maybe_archive_gate_already_archived_today_returns_false(dirs, monkeypatch):
    """③当日 dedup:当日三 kind 都已归档 → 再调返 False(无新增)。"""
    live, arch = dirs
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"),
                concept=_ff_cache(kind="concept"), industry=_ff_cache(kind="industry"))
    monkeypatch.setenv("GUANLAN_SNAPSHOT_ARCHIVE", "1")
    assert sa.maybe_archive_eod(now=datetime(2026, 7, 17, 15, 6, 0), cache_dir=live, archive_dir=arch) is True
    ok2 = sa.maybe_archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    assert ok2 is False                    # 全部幂等跳过 → 无新增,返 False


def test_maybe_archive_swallows_exceptions_returns_false(dirs, monkeypatch):
    """自吞异常绝不拖垮宿主 tick:archive_eod 抛 → maybe_archive_eod 返 False 不冒泡。"""
    live, arch = dirs
    monkeypatch.setenv("GUANLAN_SNAPSHOT_ARCHIVE", "1")

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(sa, "archive_eod", _boom)
    ok = sa.maybe_archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    assert ok is False


def test_market_scheduler_loop_calls_maybe_archive_eod():
    """宿主挂点唯一性:market-status 收盘后调度 _loop 单行调用 maybe_archive_eod(结构性守护)。"""
    import inspect
    import guanlan_v2.market.api as mapi
    src = inspect.getsource(mapi.start_market_status_scheduler)
    assert "maybe_archive_eod" in src


# ── Task 4: read_archive —— 三方契约(升序 / asof PIT / 脏行 / 跨月合并 / 空诚实)──────
def _seed_archive(arch: Path, kind: str, rows):
    """rows = [(trade_date, archived_at, payload_marker)];按 trade_date 月份落主文件/月度归档。
    主文件恒当月(此处以最大月为当月),其余月落 snapshots-YYYYMM.jsonl(照写侧尾读红线)。"""
    kdir = arch / kind
    kdir.mkdir(parents=True, exist_ok=True)
    lines = [{"trade_date": td, "archived_at": aa, "kind": kind, "payload": {"m": mk}}
             for td, aa, mk in rows]
    cur_month = max(l["trade_date"][:6] for l in lines)
    main, rotated = [], {}
    for l in lines:
        m = l["trade_date"][:6]
        (main if m == cur_month else rotated.setdefault(m, [])).append(l)
    (kdir / "snapshots.jsonl").write_text(
        "".join(json.dumps(l, ensure_ascii=False) + "\n" for l in main), encoding="utf-8")
    for m, ls in rotated.items():
        (kdir / f"snapshots-{m}.jsonl").write_text(
            "".join(json.dumps(l, ensure_ascii=False) + "\n" for l in ls), encoding="utf-8")
    return kdir


def test_read_archive_ascending_and_first_date(dirs):
    live, arch = dirs
    _seed_archive(arch, "market_tape", [
        ("20260717", "2026-07-17T18:00:00", "c"),
        ("20260715", "2026-07-15T18:00:00", "a"),
        ("20260716", "2026-07-16T18:00:00", "b"),
    ])
    out = sa.read_archive("market_tape", archive_dir=arch)
    assert out["kind"] == "market_tape"
    assert [r["trade_date"] for r in out["rows"]] == ["20260715", "20260716", "20260717"]  # 升序
    assert out["first_date"] == "20260715"
    assert out["rows"][0]["payload"]["m"] == "a"


def test_read_archive_asof_pit_filter(dirs):
    """asof 给定 → 只见 archived_at <= asof 的行(晚于 asof 落盘的不可见);first_date 亦按 PIT。"""
    live, arch = dirs
    _seed_archive(arch, "market_tape", [
        ("20260715", "2026-07-15T18:00:00", "a"),
        ("20260716", "2026-07-16T18:00:00", "b"),
        ("20260717", "2026-07-17T18:00:00", "c"),
    ])
    out = sa.read_archive("market_tape", asof="2026-07-16T20:00:00", archive_dir=arch)
    assert [r["trade_date"] for r in out["rows"]] == ["20260715", "20260716"]   # 17 号晚于 asof 不可见
    assert out["first_date"] == "20260715"


def test_read_archive_skips_dirty_lines(dirs):
    live, arch = dirs
    kdir = arch / "market_tape"
    kdir.mkdir(parents=True, exist_ok=True)
    good = {"trade_date": "20260717", "archived_at": "2026-07-17T18:00:00",
            "kind": "market_tape", "payload": {}}
    (kdir / "snapshots.jsonl").write_text(
        "not json at all\n"
        + json.dumps(good, ensure_ascii=False) + "\n"
        + '{"archived_at": "x"}\n'          # 缺 trade_date → 脏行跳过
        + "\n",                              # 空行
        encoding="utf-8")
    out = sa.read_archive("market_tape", archive_dir=arch)
    assert [r["trade_date"] for r in out["rows"]] == ["20260717"]
    assert out["first_date"] == "20260717"


def test_read_archive_cross_month_merges_rotated_and_main(dirs):
    live, arch = dirs
    _seed_archive(arch, "market_tape", [
        ("20260630", "2026-06-30T18:00:00", "jun"),      # → snapshots-202606.jsonl
        ("20260701", "2026-07-01T18:00:00", "jul1"),     # → 主文件(当月)
        ("20260717", "2026-07-17T18:00:00", "jul17"),
    ])
    out = sa.read_archive("market_tape", start="20260625", end="20260717", archive_dir=arch)
    assert [r["trade_date"] for r in out["rows"]] == ["20260630", "20260701", "20260717"]
    assert out["first_date"] == "20260630"


def test_read_archive_recent_window_touches_only_main_file(dirs, monkeypatch):
    """尾读红线:区间落在当月 → 只碰主文件,绝不打开月度归档文件(结构性证)。"""
    live, arch = dirs
    _seed_archive(arch, "market_tape", [
        ("20260630", "2026-06-30T18:00:00", "jun"),
        ("20260701", "2026-07-01T18:00:00", "jul1"),
        ("20260717", "2026-07-17T18:00:00", "jul17"),
    ])
    opened = []
    orig = sa._read_jsonl
    monkeypatch.setattr(sa, "_read_jsonl", lambda p: opened.append(Path(p).name) or orig(p))
    out = sa.read_archive("market_tape", start="20260701", end="20260717", archive_dir=arch)
    assert [r["trade_date"] for r in out["rows"]] == ["20260701", "20260717"]
    assert "snapshots-202606.jsonl" not in opened          # 月度归档未被打开(尾读红线)
    assert "snapshots.jsonl" in opened


def test_read_archive_empty_is_honest_not_raise(dirs):
    """空归档 → rows=[], first_date=None 诚实返回,不 raise、不造数。"""
    live, arch = dirs
    out = sa.read_archive("fundflow_concept", archive_dir=arch)
    assert out == {"kind": "fundflow_concept", "rows": [], "first_date": None}


# ── Task 5: 守护段(契约不变 / 无网络接缝 / append-only / tmp 隔离 / 冻结签名)──────
import inspect  # noqa: E402


def test_guard_read_tape_read_live_signatures_unchanged():
    """SWR 热路径签名逐参不变(纯加法 phase 绝不改 read_tape/read_live 契约)。"""
    assert list(inspect.signature(mt.read_tape).parameters) == ["fresh_within_s"]
    assert list(inspect.signature(ff.read_live).parameters) == [
        "kind", "refresh", "cache_dir", "ttl_s", "now", "sector_fn", "build_fn"]


def test_guard_read_tape_warming_payload_keys_unchanged(monkeypatch, tmp_path):
    """read_tape warming 返回键集不变(逐键快照)。"""
    monkeypatch.setattr(mt, "_MEM_CACHE", {"data": None})
    monkeypatch.setattr(mt, "_CACHE_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *a, **k: True)
    out = mt.read_tape()
    assert set(out) == {"ok", "warming", "pulled_at", "sources", "derived", "freshness", "note"}


def test_guard_read_live_payload_keys_unchanged(tmp_path):
    """read_live 返回键 = build payload 键 ∪ {freshness}(_annotate_live 只加 freshness,不改契约)。
    经 build_fn 注入 canned payload,全离线、tmp 缓存,绝不触网。"""
    canned = {"ok": True, "kind": "concept", "pulled_at": "2026-07-17T15:03:00",
              "trading": False, "market": {"date": "2026-07-17"}, "breadth": {},
              "boards": [], "src_host": "push2", "notes": []}

    def _fake_build(kind, refresh=False, sector_fn=None, now=None, **kw):
        return dict(canned, kind=ff._norm_kind(kind))
    out = ff.read_live("concept", cache_dir=tmp_path,
                       now=datetime(2026, 7, 17, 18, 0, 0), build_fn=_fake_build)
    assert set(out) == set(canned) | {"freshness"}


def test_guard_no_network_seams_on_archive_and_read(dirs, monkeypatch):
    """归档全路径(archive_eod + read_archive)probe/sector_fn/_refresh 桩上被调即 fail。"""
    live, arch = dirs
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"),
                concept=_ff_cache(kind="concept"), industry=_ff_cache(kind="industry"))

    import guanlan_v2.datafeed.live_client as lc
    import guanlan_v2.fundflow.sources as ffsrc

    def _boom(*a, **k):
        raise AssertionError("归档/读取路径绝不触网")
    for mod, name in [(mt, "_refresh"), (mt, "_trigger_refresh"),
                      (ff, "_refresh_live"), (ff, "build_live"), (ff, "_trigger_live_refresh"),
                      (lc, "probe")]:
        monkeypatch.setattr(mod, name, _boom)
    for name in ("fetch_sector", "fetch_market", "fetch_market_minute"):
        if hasattr(ffsrc, name):
            monkeypatch.setattr(ffsrc, name, _boom)

    sa.archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    out = sa.read_archive("market_tape", archive_dir=arch)
    assert out["first_date"] == "20260717"


def test_guard_append_only_prior_rows_byte_identical(dirs):
    """append-only:后一交易日归档绝不改已有行(逐行 + 追加顺序守护)。"""
    live, arch = dirs
    _write_live(live, mt_payload=_mt_cache(board_date="20260716"))
    sa.archive_eod(now=datetime(2026, 7, 16, 18, 0, 0), cache_dir=live, archive_dir=arch)
    main = arch / "market_tape" / "snapshots.jsonl"
    day1_line = main.read_text(encoding="utf-8").splitlines()[0]

    _write_live(live, mt_payload=_mt_cache(board_date="20260717"))
    sa.archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    lines = main.read_text(encoding="utf-8").splitlines()
    assert lines[0] == day1_line                                   # 已有行逐字节不变
    assert [json.loads(x)["trade_date"] for x in lines] == ["20260716", "20260717"]  # 追加保序


def test_guard_tmp_isolation_real_var_untouched(dirs):
    """tmp 隔离:注入 tmp 后,真 var/archive 树零变化(旧教训:测试污染真 store)。"""
    live, arch = dirs
    real_default = sa._ARCHIVE_DIR_DEFAULT
    before = set(real_default.rglob("*")) if real_default.exists() else set()
    _write_live(live, mt_payload=_mt_cache(board_date="20260717"),
                concept=_ff_cache(kind="concept"), industry=_ff_cache(kind="industry"))
    sa.archive_eod(now=datetime(2026, 7, 17, 18, 0, 0), cache_dir=live, archive_dir=arch)
    sa.read_archive("market_tape", archive_dir=arch)
    after = set(real_default.rglob("*")) if real_default.exists() else set()
    assert before == after                                          # 真 var/archive 未被创建/写入
    assert str(arch).startswith(str(live.parent))                   # 注入路径确在 tmp 内
    assert (arch / "market_tape" / "snapshots.jsonl").exists()      # 数据只落 tmp


def test_guard_read_archive_frozen_signature():
    """三方契约冻结签名:read_archive 前四参恒 (kind, start, end, asof)——防单方漂移。"""
    params = list(inspect.signature(sa.read_archive).parameters)
    assert params[:4] == ["kind", "start", "end", "asof"]
