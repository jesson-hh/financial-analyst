# ww_news_live 实时新闻现拉 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** agent 一调 `ww_news_live(code, limit)` 秒级拿到本票当下新闻(个股新闻+快讯现拉,stocks 富 parquet 顺带读),`/seats/news?mode=live` 同装配器顺带升级落子图 live 泳道。

**Architecture:** 升级 `guanlan_v2/seats/news_marks.py::_assemble_live` 为三路合并(①引擎现成 `fetch_stock_news` ②引擎现成 `fetch_kuaixun` 按票/名分级 ③可选纯文件读 stocks parquet),零新采集代码、不触发不等待不调度;`guanlan_v2/console/tools.py` 注册 `ww_news_live` 薄壳(守护计数同步)。

**Tech Stack:** Python/FastAPI(既有路由不改);akshare/eastmoney(经引擎 `news_pulse` 已有封装);pandas 读 parquet;pytest。

## Global Constraints

- **回测不碰**:`_assemble_pit`/`mode=pit` 一字不改;既有 PIT 8 测全绿为证。
- **零新采集代码**:只调引擎现成 `financial_analyst.data.news_pulse.fetch_stock_news / fetch_kuaixun`;stocks 侧只 `pd.read_parquet` 纯文件读(**不 import stocks 代码**)。
- **诚实降级**:任一路失败→该路空 + note;三路全空→`items:[]` 恒 `ok:True`;parquet 缺/错→`freshness.rich_available:false`;绝不编造。
- **provider 兼容**:显式传 `provider` 时维持旧 headlines 语义(既有两测 `test_live_uses_provider_headlines`/`test_live_provider_failure_is_empty` 逐字不回归)。
- **item 形状不变**:`{ts,date,title,source,code,level,body_head}`;level ∈ stock/macro/event/policy;响应新增 `freshness:{pulled_at,rich_asof,rich_available}`。
- **测试命令**:repo 根 `python -m pytest tests/test_news_marks.py -q`(测试文件自 prepend engine;**别用 `set PYTHONPATH=` cmd 语法**)。
- **守护计数**:加工具后 `tests/test_console_tools.py` 的计数断言会红(613/619/620/1084/1086 行:ww_=48→49,console=73→74),红了就是同步清单——逐处 +1 并补注释;另 `grep -rn "ww_news_search" guanlan_v2 docs --include=*.py --include=*.md` 找到 _SYSTEM_PROMPT/spec 列名面,给 `ww_news_live` 补同款一行。
- **GateGuard**:首个 Bash/新文件 Write 会被拦要事实,陈述后原样重试即过。
- 逐 Task 在 main 小步提交,信息中文 conventional,尾行 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

---

### Task 1: `_assemble_live` 三路合并(TDD)

**Files:**
- Modify: `guanlan_v2/seats/news_marks.py`(`_assemble_live` 107-118 行重写;`assemble_news_marks` 121-125 行扩签名;顶部 import 加 `from datetime import datetime, timedelta`)
- Test: `tests/test_news_marks.py`(追加 4 测)

**Interfaces:**
- Consumes: `news_pulse.fetch_stock_news(code, limit)->[{time(16字符"YYYY-MM-DD HH:MM"),title,summary,source}]`(失败自返 `[]`);`news_pulse.fetch_kuaixun(limit)->[{time(16),title,summary,codes[qlib如"SZ000630"]}]`;`financial_analyst.data.stock_basic.get_basic(code)->{name,...}|None`。
- Produces: `assemble_news_marks(code, asof="", mode="pit", window=250, *, reader=None, provider=None, stock_news_fn=None, kuaixun_fn=None, parquet_path=None, limit=20) -> dict`;live 响应含 `freshness:{pulled_at,rich_asof,rich_available}`(Task 2 依赖)。

- [ ] **Step 1: 写失败测试** —— 追加到 `tests/test_news_marks.py`:

```python
def test_live_three_way_merge_and_dedupe(tmp_path):
    import pandas as pd
    pq = tmp_path / "news_events.parquet"
    pd.DataFrame([
        {"publish_ts": "2026-07-04 09:00:00", "title": "公告:拟增持", "content": "x",
         "source": "eastmoney_announcement", "stock_codes": "SZ000630", "is_policy": False},
        {"publish_ts": "2026-07-04 08:00:00", "title": "政策:降准", "content": "y",
         "source": "gov_policy", "stock_codes": "", "is_policy": True},
    ]).to_parquet(pq)
    sn = lambda code, limit=20: [
        {"time": "2026-07-04 10:00", "title": "个股新闻A", "summary": "a", "source": "东方财富"},
        {"time": "2026-07-04 09:30", "title": "重复标题", "summary": "b", "source": "东方财富"},
    ]
    kx = lambda limit=200: [
        {"time": "2026-07-04 10:05", "title": "重复标题", "summary": "", "codes": ["SZ000630"]},
        {"time": "2026-07-04 10:03", "title": "宏观快讯Z", "summary": "", "codes": []},
    ]
    out = nm.assemble_news_marks("000630", mode="live", stock_news_fn=sn, kuaixun_fn=kx,
                                 parquet_path=pq, limit=10)
    titles = [it["title"] for it in out["items"]]
    assert titles.count("重复标题") == 1                      # ①②同标题去重
    lv = {it["title"]: it["level"] for it in out["items"]}
    assert lv["个股新闻A"] == "stock" and lv["公告:拟增持"] == "event"
    assert lv["政策:降准"] == "policy" and lv["宏观快讯Z"] == "macro"
    assert out["freshness"]["rich_available"] is True
    assert out["freshness"]["rich_asof"] == "2026-07-04T09:00"
    ts = [it["ts"] for it in out["items"]]
    assert ts == sorted(ts, reverse=True)                     # 展示按 ts 降序


def test_live_limit_prioritizes_stock_items():
    sn = lambda code, limit=20: [{"time": f"2026-07-04 09:{i:02d}", "title": f"股{i}",
                                  "summary": "", "source": ""} for i in range(5)]
    kx = lambda limit=200: [{"time": f"2026-07-04 10:{i:02d}", "title": f"宏{i}",
                             "summary": "", "codes": []} for i in range(5)]
    out = nm.assemble_news_marks("SZ000630", mode="live", stock_news_fn=sn, kuaixun_fn=kx,
                                 parquet_path="Z:/__none__.parquet", limit=6)
    lv = [it["level"] for it in out["items"]]
    assert len(out["items"]) == 6 and lv.count("stock") == 5 and lv.count("macro") == 1


def test_live_single_source_failure_degrades():
    def sn(code, limit=20):
        raise RuntimeError("akshare down")
    kx = lambda limit=200: [{"time": "2026-07-04 10:00", "title": "快讯B", "summary": "",
                             "codes": ["SZ000630"]}]
    out = nm.assemble_news_marks("SZ000630", mode="live", stock_news_fn=sn, kuaixun_fn=kx,
                                 parquet_path="Z:/__none__.parquet", limit=5)
    assert out["ok"] is True and [it["title"] for it in out["items"]] == ["快讯B"]
    assert "个股新闻" in out["coverage"]["note"]


def test_live_parquet_absent_rich_unavailable():
    out = nm.assemble_news_marks("SZ000630", mode="live",
                                 stock_news_fn=lambda code, limit=20: [],
                                 kuaixun_fn=lambda limit=200: [],
                                 parquet_path="Z:/__none__.parquet", limit=5)
    assert out["ok"] is True and out["items"] == []
    assert out["freshness"]["rich_available"] is False and out["freshness"]["pulled_at"]
```

- [ ] **Step 2: 跑红** —— `python -m pytest tests/test_news_marks.py -q`;Expected: 新 4 测 FAIL(`unexpected keyword argument 'stock_news_fn'`),既有 8 测 PASS。

- [ ] **Step 3: 实现** —— `guanlan_v2/seats/news_marks.py`:顶部 import 区加 `from datetime import datetime, timedelta`;模块级常量(与 `_LIVE` 相邻)加:

```python
_STOCKS_NEWS_PARQUET = Path(r"G:\stocks\_news_staging\normalized\news_events.parquet")
```

把 `_assemble_live`(107-118 行)整体替换为:

```python
def _stock_name(norm: str) -> str:
    """本票名称(快讯名称匹配用);失败→''(只按 code 匹配,诚实降级)。"""
    try:
        from financial_analyst.data.stock_basic import get_basic
        return str((get_basic(norm) or {}).get("name") or "")
    except Exception:  # noqa: BLE001
        return ""


def _t16(s) -> str:
    """'2026-07-04 09:31[:00]' / Timestamp → '2026-07-04T09:31';空/坏→''。"""
    t = str(s or "").strip().replace(" ", "T")[:16]
    return t if len(t) >= 10 else ""


def _assemble_live(code: str, provider=None, *, stock_news_fn=None,
                   kuaixun_fn=None, parquet_path=None, limit: int = 20) -> dict:
    norm = _norm_code(code)
    pulled_at = datetime.now().isoformat(timespec="seconds")
    # —— 兼容:显式传 provider(既有测试/旧调用)→ 维持原 headlines 语义,不走三路 ——
    if provider is not None:
        try:
            heads = provider.headlines(code) or []
        except Exception:  # noqa: BLE001
            heads = []
        items = [{"ts": "", "date": "", "title": h, "source": "eastmoney_kuaixun",
                  "code": norm, "level": "stock", "body_head": ""} for h in heads]
        return {"ok": True, "code": norm, "mode": "live", "asof": "", "items": items,
                "coverage": {"partial": False, "note": ""},
                "freshness": {"pulled_at": pulled_at, "rich_asof": None, "rich_available": False},
                "provenance": {"source": "kuaixun", "rows": len(items)}}
    notes: List[str] = []
    stock_items: List[Dict[str, Any]] = []
    broad_items: List[Dict[str, Any]] = []
    # ① akshare 东财个股新闻(引擎现成 fetch_stock_news;其自身失败已降级 [])
    try:
        fn = stock_news_fn
        if fn is None:
            from financial_analyst.data.news_pulse import fetch_stock_news as fn
        rows = fn(norm, limit=max(int(limit or 20), 20)) or []
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        notes.append("个股新闻源不可用或无条目")
    for r in rows:
        ts = _t16(r.get("time"))
        stock_items.append({"ts": ts, "date": ts[:10], "title": str(r.get("title") or "").strip(),
                            "source": str(r.get("source") or "") or "eastmoney_stock_news",
                            "code": norm, "level": "stock", "body_head": _head(r.get("summary"))})
    # ② 东财 7×24 快讯(codes 已 qlib 归一):命中本票(code或名)→stock,其余→macro 补位
    try:
        kfn = kuaixun_fn
        if kfn is None:
            from financial_analyst.data.news_pulse import fetch_kuaixun as kfn
        flash = kfn(limit=200) or []
    except Exception:  # noqa: BLE001
        flash = []
        notes.append("快讯源不可用")
    name = _stock_name(norm)
    for it in flash:
        ts = _t16(it.get("time"))
        title = str(it.get("title") or "").strip()
        hit = (norm in (it.get("codes") or [])) or (
            bool(name) and (name in title or name in str(it.get("summary") or "")))
        rec = {"ts": ts, "date": ts[:10], "title": title, "source": "eastmoney_kuaixun",
               "code": norm if hit else None, "level": "stock" if hit else "macro",
               "body_head": _head(it.get("summary"))}
        (stock_items if hit else broad_items).append(rec)
    # ③ stocks 富 parquet(公告/政策)可选加菜:纯文件读、近3日窗;缺/错→rich_available False
    rich_asof = None
    rich_available = False
    ppath = Path(parquet_path) if parquet_path else _STOCKS_NEWS_PARQUET
    try:
        if ppath.exists():
            import pandas as pd
            df = pd.read_parquet(ppath)
            rich_asof = _t16(df["publish_ts"].max())
            rich_available = True
            cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
            recent = df[df["publish_ts"].astype(str) >= cutoff].copy()
            sc = recent["stock_codes"].astype(str)
            mine = recent[sc.str.contains(norm, na=False)]
            pol = recent[recent["is_policy"].fillna(False).astype(bool)
                         & ~sc.str.contains(norm, na=False)]
            for _, r in mine.iterrows():
                ts = _t16(r.get("publish_ts"))
                stock_items.append({"ts": ts, "date": ts[:10],
                                    "title": str(r.get("title") or "").strip(),
                                    "source": str(r.get("source") or "stocks_staging"),
                                    "code": norm, "level": "event",
                                    "body_head": _head(r.get("content"))})
            for _, r in pol.iterrows():
                ts = _t16(r.get("publish_ts"))
                broad_items.append({"ts": ts, "date": ts[:10],
                                    "title": str(r.get("title") or "").strip(),
                                    "source": str(r.get("source") or "stocks_staging"),
                                    "code": None, "level": "policy",
                                    "body_head": _head(r.get("content"))})
        else:
            notes.append("stocks 富 parquet 不在,公告/政策层缺席")
    except Exception:  # noqa: BLE001
        rich_available = False
        notes.append("stocks 富 parquet 读取失败")
    # 去重(①②同源标题重叠;seen 跨两组共享)→ 本票优先补位 → 展示按 ts 降序
    seen: set = set()

    def _dedup(arr):
        out = []
        for x in arr:
            if x["title"] and x["title"] not in seen:
                seen.add(x["title"])
                out.append(x)
        return out

    stock_items = _dedup(sorted(stock_items, key=lambda x: x["ts"], reverse=True))
    broad_items = _dedup(sorted(broad_items, key=lambda x: x["ts"], reverse=True))
    lim = max(1, int(limit or 20))
    picked = stock_items[:lim]
    if len(picked) < lim:
        picked += broad_items[:lim - len(picked)]
    picked.sort(key=lambda x: x["ts"], reverse=True)
    return {"ok": True, "code": norm, "mode": "live", "asof": "", "items": picked,
            "coverage": {"partial": False, "note": ";".join(notes)},
            "freshness": {"pulled_at": pulled_at, "rich_asof": rich_asof,
                          "rich_available": rich_available},
            "provenance": {"source": "stock_news+kuaixun+staging_parquet", "rows": len(picked)}}
```

`assemble_news_marks`(尾部)替换为:

```python
def assemble_news_marks(code: str, asof: str = "", mode: str = "pit",
                        window: int = 250, *, reader=None, provider=None,
                        stock_news_fn=None, kuaixun_fn=None,
                        parquet_path=None, limit: int = 20) -> dict:
    if mode == "live":
        return _assemble_live(code, provider=provider, stock_news_fn=stock_news_fn,
                              kuaixun_fn=kuaixun_fn, parquet_path=parquet_path, limit=limit)
    return _assemble_pit(code, asof, window, reader=reader)
```

(`List/Dict/Any` 已在顶部 typing import;确认即可。)

- [ ] **Step 4: 跑绿** —— `python -m pytest tests/test_news_marks.py -q`;Expected: 12 passed(8 旧含 provider 两测逐字不回归 + 4 新)。

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/seats/news_marks.py tests/test_news_marks.py
git commit -m "feat(seats): _assemble_live 三路合并——个股新闻+快讯现拉+stocks富parquet可选(零新采集)

引擎现成 fetch_stock_news/fetch_kuaixun 现拉秒回;快讯 codes/名命中→stock 宏观补位;
stocks news_events.parquet 纯文件读(公告event/政策policy)带 freshness.rich_asof;
逐路诚实降级;provider 兼容旧语义;PIT 侧零改动。12/12 绿。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `ww_news_live` 工具注册 + 守护计数同步(TDD)

**Files:**
- Modify: `guanlan_v2/console/tools.py`(`news_search_impl` 附近加 `news_live_impl`;WW_TOOL_TABLE 在 `ww_f10` 条目(约 2040-2054 行)后插注册项)
- Modify: `tests/test_console_tools.py`(计数守护 613/619/620/1084/1086 行 +1;新增 2 测)
- Modify: `grep -rn "ww_news_search" guanlan_v2 docs --include=*.py --include=*.md | grep -v test` 揭示的 _SYSTEM_PROMPT/spec 列名面(每处给 ww_news_live 补同款一行)

**Interfaces:**
- Consumes: Task 1 的 `assemble_news_marks(code, mode="live", limit=)`。
- Produces: `news_live_impl(code, limit=20) -> {ok, code, items, freshness, note}`;`ww_news_live` ∈ CONSOLE_ALLOWED(表派生,自动)。

- [ ] **Step 1: 写失败测试** —— 追加到 `tests/test_console_tools.py`:

```python
def test_ww_news_live_registered():
    import guanlan_v2.console.tools as ct
    assert "ww_news_live" in {t["name"] for t in ct.WW_TOOL_TABLE}
    assert "ww_news_live" in ct.CONSOLE_ALLOWED


def test_news_live_impl_wraps_assembler(monkeypatch):
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(
        "guanlan_v2.seats.news_marks.assemble_news_marks",
        lambda code, mode="live", limit=20, **k: {
            "ok": True, "code": "SZ000630",
            "items": [{"title": "t", "ts": "2026-07-04T10:00", "level": "stock"}],
            "freshness": {"pulled_at": "x", "rich_available": False, "rich_asof": None},
            "coverage": {"note": "n"}})
    out = ct.news_live_impl("000630", limit=5)
    assert out["ok"] and out["items"][0]["title"] == "t" and out["note"] == "n"
```

- [ ] **Step 2: 跑红** —— `python -m pytest tests/test_console_tools.py -q -k "news_live"`;Expected: 2 FAIL(无 news_live_impl / 未注册)。

- [ ] **Step 3: 实现** —— `guanlan_v2/console/tools.py`:在 `news_search_impl` 定义后加:

```python
def news_live_impl(code: str, limit: int = 20) -> dict:
    """实时新闻现拉(个股新闻+快讯,stocks 公告/政策富层在场顺带;秒回,绝不编造)。"""
    from guanlan_v2.seats.news_marks import assemble_news_marks
    out = assemble_news_marks(code, mode="live", limit=int(limit or 20))
    return {"ok": out.get("ok", False), "code": out.get("code"),
            "items": out.get("items", []), "freshness": out.get("freshness", {}),
            "note": (out.get("coverage") or {}).get("note", "")}
```

WW_TOOL_TABLE 里 `ww_f10` 条目之后插:

```python
    {"name": "ww_news_live",
     "description":
         "现拉本票实时新闻(秒回):akshare 东财个股新闻 + 东财7×24快讯(命中本票标 stock,宏观补位),"
         "stocks 富层(公告 event/政策 policy)在场顺带读并以 freshness.rich_asof 标截止,缺料诚实空不编造。"
         "经验回滚/复盘问『此刻这只票有什么新闻』时用。live realtime news pull.",
     "input_schema": {"type": "object", "properties": {
         "code": {"type": "string", "description": "股票代码,如 SZ000630 或 000630"},
         "limit": {"type": "integer", "default": 20, "description": "最多条数,本票优先"}},
      "required": ["code"]},
     "impl": news_live_impl, "cost": "seconds", "confirm": False,
     "reachable": []},
```

- [ ] **Step 4: 守护计数同步** —— `python -m pytest tests/test_console_tools.py -q` 会红出计数断言,逐处 +1(已知:613 行 `== 48`→49 并在行尾注释追加"+1 ww_news_live 实时新闻";619-620 行 `73`→74、`48`→49;1084 行 `48`→49;1086 行 `73`→74;若还有别处红,同样处理)。再跑 `grep -rn "ww_news_search" guanlan_v2 docs --include=*.py --include=*.md | grep -v test`,在揭示的 _SYSTEM_PROMPT / specs 列名面各补 ww_news_live 一行(措辞仿邻行)。

- [ ] **Step 5: 跑绿** —— `python -m pytest tests/test_console_tools.py tests/test_news_marks.py -q`;Expected: 全 PASS(计数守护 49/74 落定)。

- [ ] **Step 6: 提交**

```bash
git add guanlan_v2/console/tools.py tests/test_console_tools.py
git commit -m "feat(console): ww_news_live 实时新闻工具——现拉秒回接经验回滚(计数49/74四点同步)

薄壳调 assemble_news_marks(mode=live);注册表+CONSOLE_ALLOWED(派生)+_SYSTEM_PROMPT/spec
列名+守护计数同步;monkeypatch 单测2条。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(若 Step 4 改了 tools.py 之外的 prompt/spec 文件,一并 `git add`。)

---

### Task 3: 真机验收 + 记忆

**Files:**
- (无代码改动;验收 + 记忆文件)

**Interfaces:**
- Consumes: Task 1/2 全部;`guanlan-v2-verify9998` launch config(避杀 9999 看门狗)。

- [ ] **Step 1: 全量回归** —— `python -m pytest -q`;Expected: 全绿(允许既有 2 条 `test_industry_ingest` 环境失败,与本特性无关,须注明)。

- [ ] **Step 2: 真机现拉烟测(有网)** —— repo 根:

```bash
python -c "import sys; sys.path.insert(0,'engine'); from guanlan_v2.console.tools import news_live_impl; import json; o=news_live_impl('SZ000630', limit=8); print(json.dumps({'ok':o['ok'],'n':len(o['items']),'freshness':o['freshness'],'titles':[i['title'][:30] for i in o['items'][:4]]}, ensure_ascii=False))"
```

Expected: `ok:true`、`n>0`、`pulled_at`=当刻、titles 为真实新闻(逐条肉眼可判真);若断网诚实记录"待有网复验"。

- [ ] **Step 3: 路由面验收** —— preview_start `guanlan-v2-verify9998` → `GET /seats/news?code=SZ000630&mode=live&window=250` → 响应 `items` 混含 level stock/macro(比旧版只 headlines 富)、带 `freshness`;注明"9999 需重启吃新后端,落子图 live 泳道届时自动升级"。

- [ ] **Step 4: 记忆** —— `luozi-news-markers.md` 追加一段:ww_news_live 现拉三路(07-04)/`provider` 兼容语义/守护计数 49/74/stocks parquet 为可选加菜其刷新归 stocks;`MEMORY.md` 对应钩子行补记。

- [ ] **Step 5: 检查点** —— 全量绿 + 真机现拉证据留存;按用户既有指向推送 main(若无新授权则提交后询问)。

---

## 计划自审

- **Spec 覆盖**:§2.1 零新采集→Task 1(只 import 引擎函数);§2.2 不触发不等待→无任何 subprocess/调度代码;§2.3 parquet 可选加菜+rich_asof→Task 1 ③ + freshness;§2.4 工具四点同步→Task 2 Step 3/4;§3 三处改动面→Tasks 1/2 一一对应;§5 降级表→Task 1 notes/兼容分支 + 测 3/4;§6 测试 1-5→Task 1 四测+既有两测,测 6→Task 3 Step 2;§7 验收→Task 3。
- **占位扫描**:无 TBD;计数同步是"跑守护→红处+1"的确定程序 + 已知 5 行号,非占位。
- **类型一致**:`assemble_news_marks(..., stock_news_fn, kuaixun_fn, parquet_path, limit)` Task 1 定义 = Task 2 消费;`news_live_impl` 返回键 `{ok,code,items,freshness,note}` = Task 2 测断言;item/freshness 键全程一致。
