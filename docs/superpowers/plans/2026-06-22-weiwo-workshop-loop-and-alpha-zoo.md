# 帷幄工坊闭环 + 引擎因子研究线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让帷幄补齐 v4 工坊闭环(删除 + 设为默认变体)并放行引擎 alpha-zoo 因子研究线(7 工具)。

**Architecture:** 三层——(1) `model_registry` 加「默认变体指针」存储 + 删除联动;(2) `screen/api` 在 `/screen/run` 单点解析默认指针(没设=prod 零变化)+ 新 `POST /screen/model/default` 端点 + `/screen/models` 标注默认;(3) `console/tools` 加 `ww_model_delete`/`ww_model_set_default` 两个 ww_ 工具 + 把 7 个引擎 alpha-zoo 工具加入白名单。生产 prod v4 文件全程只读不动。

**Tech Stack:** Python 3.13 / FastAPI / pytest;引擎 fork 在 `engine/`(测试需 `PYTHONPATH=engine;.`,本仓 `tests/conftest.py` 已 prepend engine)。

## Global Constraints

- **prod v4 永不被覆写**:「设为默认」只是 `MODELS_DIR/_default.json` 指针,清除即回官方;不触碰 prod 排名文件。
- **零行为变化保证**:没设默认指针时 `/screen/run` 行为逐字节不变(回归守护测试钉死)。
- **诚实**:响应 `model` 字段照实回报实际所用模型,不伪装 prod;工具失败回 `ok:False` + 原因,不崩、不假装成功。
- **帷幄工具改动四处同步**:`WW_TOOL_TABLE`(`guanlan_v2/console/tools.py`)+ `_ALLOWED_ENGINE_TOOLS`(同文件)+ `_SYSTEM_PROMPT`(`guanlan_v2/console/api.py`)+ 守护计数测试(`tests/test_console_tools.py`)。
- **ww_ impl 回包形**:`{"ok": bool, "content": str, "artifact": None, "raw": {...}}`;调后端用 `_self_post(path, payload)` / `_self_get(path)`(同进程自 HTTP,见 `console/tools.py:137/155`)。
- **引擎 alpha-zoo 工具跑不通的不接**:Task 4 逐个真机验证,只白名单存活的,剔除项诚实列出。
- 提交信息结尾:`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

---

### Task 1: 默认变体指针(model_registry 存储层)+ 删除联动

**Files:**
- Modify: `guanlan_v2/screen/model_registry.py`(在 `delete_variant` 之后追加;并改 `delete_variant` body)
- Test: `tests/test_model_registry.py`

**Interfaces:**
- Produces:
  - `get_default_model() -> Optional[str]` — 读 `MODELS_DIR/_default.json`;无/损坏/指向已删变体 → `None`。
  - `set_default_model(model_id: Optional[str]) -> None` — 变体 id 校验存在后写指针;`None`/`""`/`"prod"` 删指针;不存在抛 `ValueError`。
  - `delete_variant(vid)` 改为:删变体目录前,若该 vid 是当前默认指针 → 先清指针。

- [ ] **Step 1: 写失败测试**

在 `tests/test_model_registry.py` 末尾追加(顶部已 `import pandas as pd, pytest` 和 `from guanlan_v2.screen import model_registry as reg`):

```python
def _stub_variant(root, vid):
    """造一个最小变体目录(只需 ranking 文件存在;set/get_default 只看 .exists())。"""
    d = root / vid
    d.mkdir(parents=True, exist_ok=True)
    (d / "v4_ranking.parquet").write_bytes(b"stub")
    return d


def test_default_model_set_get_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    _stub_variant(tmp_path / "models", "m_a")
    assert reg.get_default_model() is None            # 缺省 = None(=prod)
    reg.set_default_model("m_a")
    assert reg.get_default_model() == "m_a"
    reg.set_default_model("prod")                       # "prod" = 清除
    assert reg.get_default_model() is None
    reg.set_default_model("m_a")
    reg.set_default_model(None)                         # None = 清除
    assert reg.get_default_model() is None


def test_set_default_unknown_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    with pytest.raises(ValueError):
        reg.set_default_model("m_nope")                # 变体不存在 → 诚实失败


def test_get_default_degrades_when_variant_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    _stub_variant(tmp_path / "models", "m_a")
    reg.set_default_model("m_a")
    reg.delete_variant("m_a")                           # 删默认变体
    assert reg.get_default_model() is None              # 指针自愈 + 被清
    assert not (tmp_path / "models" / "_default.json").exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_model_registry.py -k "default" -q`
Expected: FAIL（`AttributeError: module ... has no attribute 'get_default_model'`)

- [ ] **Step 3: 实现**

在 `guanlan_v2/screen/model_registry.py` 顶部确认 `import json`(已存在);把现有 `delete_variant`(66-71 行)整体替换为下面版本,并在其后追加两个新函数:

```python
def delete_variant(vid) -> None:
    if vid == "prod":
        raise ValueError("生产 v4(prod)不可删")
    # 删的若是当前默认变体 → 先清默认指针(回落 prod),避免悬空(读原始指针,不经 get 的自愈)
    p = _default_path()
    if p.exists():
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("id") == vid:
                p.unlink()
        except Exception:
            pass
    d = _dir(vid)
    if d.exists():
        shutil.rmtree(d)


def _default_path():
    return MODELS_DIR / "_default.json"


def get_default_model():
    """当前「默认变体」id;无/损坏/指向已删变体 → None(= 用生产 prod,诚实降级)。"""
    p = _default_path()
    if not p.exists():
        return None
    try:
        mid = json.loads(p.read_text(encoding="utf-8")).get("id")
    except Exception:
        return None
    if not mid or not variant_ranking_path(mid).exists():
        return None
    return mid


def set_default_model(model_id) -> None:
    """设/清默认变体。变体 id → 校验存在后写指针;None/""/"prod" → 删指针(回落官方 prod)。"""
    p = _default_path()
    if model_id in (None, "", "prod"):
        if p.exists():
            p.unlink()
        return
    if not variant_ranking_path(model_id).exists():
        raise ValueError(f"变体不存在: {model_id}")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": model_id}, ensure_ascii=False), encoding="utf-8")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_model_registry.py -q`
Expected: PASS（含原有 fixture 用例;若原有 `save_variant` 1 行 fixture 用例仍红,那是另一处用户在处理的工坊 fixture,不在本任务范围——只确认本任务三个 default 用例 + delete 用例绿)

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/screen/model_registry.py tests/test_model_registry.py
git commit -m "feat(registry): 默认变体指针 get/set_default_model + 删除联动清指针"
```

---

### Task 2: /screen/run 默认解析 + `POST /screen/model/default` 端点 + /screen/models 标注

**Files:**
- Modify: `guanlan_v2/screen/api.py`(`_screen_via_v4` 顶部 672 行;`screen_models` 1308-1311;在 `screen_model_delete` 1363-1369 后加新端点;文件内合适位置加模块级 `_resolve_model_id`)
- Test: `tests/test_screen_api.py`

**Interfaces:**
- Consumes: `model_registry.get_default_model`/`set_default_model`(Task 1)。
- Produces:
  - `_resolve_model_id(m: Optional[str]) -> str`(模块级,可单测)。
  - `POST /screen/model/default {id}` → `{"ok": True, "default": <id|None>}` / `{"ok": False, "reason": ...}`。
  - `GET /screen/models` → 增 `default_model: <id|None>`,每个 variant 增 `is_default: bool`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_screen_api.py` 末尾追加(顶部已有 `from fastapi import FastAPI` / `TestClient` / `_client()` / `build_screen_router`):

```python
def test_resolve_model_id_default_pointer(tmp_path, monkeypatch):
    from guanlan_v2.screen import api as sapi, model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    (tmp_path / "models" / "m_x").mkdir(parents=True)
    (tmp_path / "models" / "m_x" / "v4_ranking.parquet").write_bytes(b"stub")
    assert sapi._resolve_model_id("prod") == "prod"        # 没设默认 = prod(零变化)
    assert sapi._resolve_model_id("") == "prod"
    assert sapi._resolve_model_id("m_y") == "m_y"           # 显式变体原样(解析不校验存在)
    reg.set_default_model("m_x")
    assert sapi._resolve_model_id("prod") == "m_x"          # 设了默认 → 变体
    assert sapi._resolve_model_id("m_y") == "m_y"           # 显式仍优先于默认
    reg.set_default_model(None)
    assert sapi._resolve_model_id("prod") == "prod"         # 清除 → 回 prod


def test_model_default_endpoint_and_models_flag(tmp_path, monkeypatch):
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    (tmp_path / "models" / "m_x").mkdir(parents=True)
    (tmp_path / "models" / "m_x" / "meta.json").write_text(
        '{"id": "m_x", "name": "测试"}', encoding="utf-8")
    (tmp_path / "models" / "m_x" / "v4_ranking.parquet").write_bytes(b"stub")
    c = _client()
    assert c.get("/screen/models").json()["default_model"] is None
    r = c.post("/screen/model/default", json={"id": "m_x"}).json()
    assert r["ok"] is True and r["default"] == "m_x"
    j = c.get("/screen/models").json()
    assert j["default_model"] == "m_x"
    assert any(v.get("is_default") for v in j["variants"])
    assert c.post("/screen/model/default", json={"id": "m_nope"}).json()["ok"] is False  # 诚实拒
    assert c.post("/screen/model/default", json={"id": "prod"}).json()["default"] is None  # 清除
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_screen_api.py -k "default or resolve" -q`
Expected: FAIL（`_resolve_model_id` 不存在 / `default_model` KeyError）

- [ ] **Step 3: 实现**

(a) 在 `guanlan_v2/screen/api.py` 模块级(`_screen_via_v4` 定义之前,如 663 行附近)加:

```python
def _resolve_model_id(m):
    """model 省略/'prod' → 查默认变体指针;显式变体 id 原样(解析不校验存在)。
    没设指针 → 'prod'(零行为变化)。"""
    mid = (m or "prod").strip() or "prod"
    if mid != "prod":
        return mid
    try:
        from guanlan_v2.screen.model_registry import get_default_model
        return get_default_model() or "prod"
    except Exception:
        return "prod"
```

(b) 把 `_screen_via_v4` 内 672 行
`    _mid = getattr(body, "model", "prod") or "prod"`
改为
`    _mid = _resolve_model_id(getattr(body, "model", "prod"))`

(c) 把 `screen_models`(1308-1311)替换为:

```python
    @router.get("/models")
    def screen_models():
        from guanlan_v2.screen.model_registry import list_variants, get_default_model
        dflt = get_default_model()
        vs = list_variants()
        for v in vs:
            v["is_default"] = (v.get("id") == dflt)
        return JSONResponse({"ok": True, "variants": vs, "default_model": dflt})
```

(d) 在 `screen_model_delete`(1363-1369)之后加新端点:

```python
    @router.post("/model/default")
    def screen_model_default(body: dict = Body(default={})):
        from guanlan_v2.screen.model_registry import set_default_model, get_default_model
        try:
            set_default_model(str(body.get("id") or "") or None)
            return JSONResponse({"ok": True, "default": get_default_model()})
        except ValueError as e:
            return JSONResponse({"ok": False, "reason": str(e)})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_screen_api.py -q`
Expected: PASS（全 screen 用例绿;契约形 chosen/benched/... 不破)

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/screen/api.py tests/test_screen_api.py
git commit -m "feat(screen): /screen/run 解析默认变体指针 + /model/default 端点 + /models 标注默认"
```

---

### Task 3: 帷幄工坊工具(ww_model_delete + ww_model_set_default + list 标默认)

**Files:**
- Modify: `guanlan_v2/console/tools.py`(加 `model_delete_impl`/`model_set_default_impl`;改 `model_list_impl`;`WW_TOOL_TABLE` 加 2 条)
- Modify: `guanlan_v2/console/api.py`(`_SYSTEM_PROMPT` 34 行)
- Modify: `tests/test_console_tools.py`(守护计数 511/517/518/982/984)
- Test: `tests/test_console_tools.py`(新增 impl 用例)

**Interfaces:**
- Consumes: `POST /screen/model/delete`(已存在)、`POST /screen/model/default`(Task 2)、`GET /screen/models`(Task 2,带 `default_model`)。
- Produces: `WW_TOOL_TABLE` 增 `ww_model_delete`、`ww_model_set_default`;`ww_` 计数 28→30,`CONSOLE_ALLOWED` 46→48。

- [ ] **Step 1: 写失败测试**

在 `tests/test_console_tools.py` 末尾追加:

```python
def test_model_delete_impl(monkeypatch):
    import guanlan_v2.console.tools as ct
    calls = {}
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, **k: calls.setdefault(path, payload) or {"ok": True})
    monkeypatch.setattr(ct, "_self_get", lambda path, **k: {"variants": [{"id": "m_b", "name": "乙"}]})
    res = ct.model_delete_impl(id="m_a")
    assert res["ok"] is True
    assert calls["/screen/model/delete"] == {"id": "m_a"}
    assert "m_b" in res["content"]                                  # 回报剩余变体


def test_model_delete_impl_refuses_prod(monkeypatch):
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, **k: {"ok": False, "reason": "生产 v4(prod)不可删"})
    res = ct.model_delete_impl(id="prod")
    assert res["ok"] is False and "prod" in res["content"]


def test_model_set_default_impl(monkeypatch):
    import guanlan_v2.console.tools as ct
    calls = {}
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, **k: calls.setdefault(path, payload) or {"ok": True, "default": payload.get("id") or None})
    res = ct.model_set_default_impl(id="m_x")
    assert res["ok"] is True and calls["/screen/model/default"] == {"id": "m_x"}
    res2 = ct.model_set_default_impl(id="prod")                     # 清除回 prod
    assert res2["ok"] is True


def test_model_list_impl_marks_default(monkeypatch):
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_self_get", lambda path, **k: {
        "variants": [{"id": "m_x", "name": "甲", "n_features": 5}], "default_model": "m_x"})
    res = ct.model_list_impl()
    assert res["ok"] is True and "默认" in res["content"]            # 标出当前默认
```

并把守护计数改为新值(改这 5 处):
- 511 行 `== 28` → `== 30`
- 517 行 `out["console_n"] == 46` → `== 48`
- 518 行 `out["explicit_n"] == 46 and out["explicit_ww_n"] == 28` → `== 48 and ... == 30`
- 982 行 `== 28` → `== 30`
- 984 行 `len(ct.CONSOLE_ALLOWED) == 46` → `== 48`

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_console_tools.py -k "model_delete or set_default or marks_default or registered_ww or derives" -q`
Expected: FAIL（impl 不存在 / 计数 28≠30)

- [ ] **Step 3: 实现**

(a) `guanlan_v2/console/tools.py` 中 `model_train_impl`(337-367)之后加两个 impl:

```python
def model_delete_impl(id: str = "") -> Dict[str, Any]:
    """删一个 v4 变体(生产 prod 不可删)。删的若是当前默认变体,后端连带回落 prod。需用户确认。"""
    vid = (id or "").strip()
    if not vid:
        return {"ok": False, "content": "请给要删除的变体 id(ww_model_list 查)", "artifact": None}
    try:
        r = _self_post("/screen/model/delete", {"id": vid})
    except Exception as e:
        return {"ok": False, "content": f"删除失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"未删除: {r.get('reason')}", "artifact": None}
    try:
        left = (_self_get("/screen/models").get("variants") or [])
    except Exception:
        left = []
    tail = ("剩余变体: " + "、".join(m.get("id") for m in left)) if left else "已无自训变体(默认=生产 prod)。"
    return {"ok": True, "artifact": None, "raw": {"id": vid, "left": [m.get("id") for m in left]},
            "content": f"已删除变体 {vid}。{tail}"}


def model_set_default_impl(id: str = "") -> Dict[str, Any]:
    """设为默认变体:之后选股页/ww_screen_run 不指定模型时缺省用它;id=prod/省略=清除回官方 prod。
    生产 prod 文件不动,随时可切回。需用户确认。"""
    vid = (id or "").strip()
    try:
        r = _self_post("/screen/model/default", {"id": vid})
    except Exception as e:
        return {"ok": False, "content": f"设置失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"未设置: {r.get('reason')}", "artifact": None}
    cur = r.get("default")
    msg = (f"已设默认变体 = {cur}(选股缺省用它,显式 model 仍优先;ww_model_set_default id=prod 可切回官方)。"
           if cur else "已清除默认变体,选股缺省回生产 prod。")
    return {"ok": True, "artifact": None, "raw": {"default": cur}, "content": msg}
```

(b) 改 `model_list_impl`(314-334):把整个函数体替换为下面版本(读 `default_model` 并行尾标默认):

```python
def model_list_impl() -> Dict[str, Any]:
    """列出已训练的 v4 变体(供 ww_screen_run 的 model 取 id;生产 v4 隐含=prod)。"""
    try:
        r = _self_get("/screen/models")
    except Exception as e:
        return {"ok": False, "content": f"变体列表拉取失败: {e}", "artifact": None}
    vs = r.get("variants") or []
    dflt = r.get("default_model")
    if not vs:
        return {"ok": True, "artifact": None, "raw": {"n": 0},
                "content": "暂无训练好的 v4 变体(生产 v4=model 省略或 prod)。"
                           "用 ww_model_train 选基础特征+库因子训练一个。"}
    lines = []
    for m in vs:
        oi = m.get("oos_ic")
        oid = "" if oi is None else f" 留出OOS IC {float(oi):+.3f}"
        uns = m.get("unsupported_factors") or []
        unl = f" ⚠{len(uns)}未用" if uns else ""
        star = " ★默认" if m.get("id") == dflt else ""
        lines.append(f"{m.get('id')}「{m.get('name')}」· {m.get('n_features', '—')}特征{oid}{unl}{star}")
    dl = f"(当前默认 = {dflt})" if dflt else "(当前默认 = 生产 prod)"
    head = f"已训练 v4 变体 {len(vs)} 个{dl}(ww_screen_run 传 model=<id> 用其选股;省略=默认):\n"
    return {"ok": True, "content": head + "\n".join(lines), "artifact": None,
            "raw": {"n": len(vs), "ids": [m.get("id") for m in vs], "default": dflt}}
```

(c) `WW_TOOL_TABLE` 中 `ww_model_train` 条目(1089-1103)之后插入两条:

```python
    {"name": "ww_model_delete",
     "description":
         "删除一个已训练的 v4 模型变体(生产 prod 不可删;删的若是当前默认变体,自动回落 prod)。"
         "用户说『删掉变体 X/不要这个模型了』时用。需用户确认。",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "变体 id(ww_model_list 查)"}},
      "required": ["id"]},
     "impl": model_delete_impl, "cost": "instant", "confirm": True,
     "reachable": ["/screen/model/delete"]},
    {"name": "ww_model_set_default",
     "description":
         "把某个 v4 变体设为平台默认(之后选股页/ww_screen_run 不指定模型时缺省用它;显式 model 仍优先)。"
         "id=prod 或省略 = 清除回官方生产 prod。生产 prod 文件不动,随时可切回。需用户确认。"
         "用户说『把这个变体设为默认/上线/以后默认用它』时用。",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "变体 id(ww_model_list 查);传 prod/省略=清除回官方"}}},
     "impl": model_set_default_impl, "cost": "instant", "confirm": True,
     "reachable": ["/screen/model/default"]},
```

(d) `guanlan_v2/console/api.py` `_SYSTEM_PROMPT` 第 34 行(列 v4 变体那句)末尾,把句末
「……训练自己的模型,后台~4min,需确认,生产 v4 不动)。」
改为
「……训练自己的模型,后台~4min,需确认,生产 v4 不动)、删除变体 ww_model_delete(需确认)、设默认变体 ww_model_set_default(把某变体设为平台缺省/『上线』,或 id=prod 清除回官方,需确认)。」

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_console_tools.py tests/test_console_api.py -q`
Expected: PASS（含 test_console_api.py:932 每个 ww_ 在提示词的守护——新两个工具已在 34 行具名)

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/console/tools.py guanlan_v2/console/api.py tests/test_console_tools.py
git commit -m "feat(weiwo): ww_model_delete + ww_model_set_default(默认变体)+ list 标默认 + 提示词/计数同步"
```

---

### Task 4: 引擎 alpha-zoo 因子研究线放行(验证 + 白名单 + 提示词组 + 计数)

**Files:**
- Modify: `guanlan_v2/console/tools.py`(`_ALLOWED_ENGINE_TOOLS` 1305-1312)
- Modify: `guanlan_v2/console/api.py`(`_SYSTEM_PROMPT` 加一行 alpha-zoo 组)
- Modify: `tests/test_console_tools.py`(守护计数 console_n / explicit_n)
- Test: `tests/test_console_tools.py`(断言存活工具在白名单)

**Interfaces:**
- Consumes: 引擎 `financial_analyst.buddy.tools.TOOL_REGISTRY`(已注册 442-alpha 研究工具)。
- Produces: `_ALLOWED_ENGINE_TOOLS` 增存活工具;`CONSOLE_ALLOWED` 48 → `48 + N`(N=存活数,目标 7)。

候选 7:`alpha_list`、`alpha_show`、`alpha_compare`、`alpha_bench`、`event_report`、`alpha_forge`、`factor_report`(`alpha_list`/`alpha_show` 已真机验证可用)。

- [ ] **Step 1: 逐个真机验证(决定 N)**

用同进程口径逐个跑小样(只读 list/show 直接跑;compute 类传最小窗 + 小 universe;`run()` 是同步方法):

Run:
```bash
PYTHONPATH="G:/guanlan-v2/engine;G:/guanlan-v2" python - <<'PY'
from financial_analyst.buddy import tools as bt
reg = {t.name: t for t in bt.TOOL_REGISTRY}
probes = {
  "alpha_list": {}, "alpha_show": {"name": "alpha019"},
  "alpha_compare": {"items": ["alpha019", "rank(-delta(close,5))"], "max_codes": 40, "since": "2024-06-01", "until": "2024-09-30"},
  "alpha_bench": {"top": 5, "universe": "csi300_active", "since": "2024-06-01", "until": "2024-09-30"},
  "event_report": {"expr_or_name": "cross(close, sma(close,20))", "universe": "csi300_active", "start": "2024-06-01", "end": "2024-12-31"},
  "alpha_forge": {"idea": "5日反转", "save": False, "since": "2024-06-01", "until": "2024-09-30"},
  "factor_report": {"expr_or_name": "rank(-delta(close,5))", "universe": "csi300", "start": "2024-06-01", "end": "2024-12-31"},
}
for name, kw in probes.items():
    try:
        r = reg[name].run(**kw)
        ok = not getattr(r, "is_error", False)
        print(("LIVE-OK  " if ok else "LIVE-ERR ") + name, str(getattr(r, "content", r))[:80].replace(chr(10), " "))
    except Exception as e:
        print("LIVE-FAIL", name, type(e).__name__, str(e)[:120])
PY
```
Expected: 记录每个工具 LIVE-OK / LIVE-FAIL。**N = LIVE-OK 数**。把 FAIL 的从候选剔除并在最终交付说明里列出原因(缺 universe/面板数据)。

- [ ] **Step 2: 写失败测试**

在 `tests/test_console_tools.py` 末尾追加(把 `_SURVIVORS` 替换为 Step 1 实测 LIVE-OK 列表):

```python
def test_alpha_zoo_tools_whitelisted():
    import guanlan_v2.console.tools as ct
    _SURVIVORS = ["alpha_list", "alpha_show", "alpha_compare", "alpha_bench",
                  "event_report", "alpha_forge", "factor_report"]  # ← 用 Step 1 实测存活列表
    for name in _SURVIVORS:
        assert name in ct._ALLOWED_ENGINE_TOOLS
        assert name in ct.CONSOLE_ALLOWED
```

并更新守护计数(`N` = 存活数;全 7 活则 console=55):
- 517 行 `out["console_n"] == 48` → `== 48 + N`(如 `== 55`)
- 518 行 `out["explicit_n"] == 48` → `== 48 + N`
- 984 行 `len(ct.CONSOLE_ALLOWED) == 48` → `== 48 + N`

（`ww_` 计数 30 不变——本任务只加引擎工具。）

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/test_console_tools.py -k "alpha_zoo or registered_ww or derives" -q`
Expected: FAIL（工具未在白名单 / 计数不符)

- [ ] **Step 4: 实现**

(a) `guanlan_v2/console/tools.py` `_ALLOWED_ENGINE_TOOLS`(1305-1312)末尾加存活工具(示例全活):

```python
    # B 类:引擎 alpha-zoo 因子研究线(442 学术因子库/事件研究/炼因子;各自带 confirm 门或只读)
    "alpha_list", "alpha_show", "alpha_compare", "alpha_bench",
    "event_report", "alpha_forge", "factor_report",
```
（Step 1 有 FAIL 的就删掉对应名;列表与 Step 2 的 `_SURVIVORS` 一致。)

(b) `guanlan_v2/console/api.py` `_SYSTEM_PROMPT`,在第 35 行(自省/能力地图那句)之前插一行(与实际放行工具一致;FAIL 剔除的不写):

```
另有(引擎 alpha-zoo 因子研究线,与 guanlan 自有 ww_factor_analyze/ww_backtest 是两套并行体系):列因子 alpha_list、看因子 alpha_show、并排对比 alpha_compare、全库跑分 alpha_bench、事件研究 event_report、炼因子 alpha_forge(自然语言想法→因子,save 写引擎自有库非 guanlan factorlib,默认不存)、单因子完整评测 factor_report。学术因子/事件型用这套;guanlan 面板上的因子分析/回测/合成仍用 ww_factor_analyze/ww_backtest/ww_factor_compose。
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_console_tools.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add guanlan_v2/console/tools.py guanlan_v2/console/api.py tests/test_console_tools.py
git commit -m "feat(weiwo): 放行引擎 alpha-zoo 因子研究线进白名单(验证存活N个)+ 提示词组 + 计数"
```

---

### Task 5: 全量回归 + 真机 e2e + 还原

**Files:** 无源改动(验证任务)

- [ ] **Step 1: 全量回归**

Run: `python -m pytest -q`
Expected: 仅剩用户在处理的 4 个工坊 fixture 红(`test_model_registry`/`test_model_train`/`test_screen_api::test_model_endpoints` 的 1 行 fixture vs ≥100 guard,**非本计划引入**);本计划新增/改动的用例全绿。若出现其它红,定位修复。

- [ ] **Step 2: 重启 9999 加载新后端**

Run(杀监听 PID,看门狗 ~10s 拉新代码,轮询 /openapi.json 到 200)：
```bash
powershell -c "(Get-NetTCPConnection -LocalPort 9999 -State Listen).OwningProcess | Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force }"
```

- [ ] **Step 3: 真机 e2e — 工坊默认/删除**

用 HTTP 验证(需至少一个真实变体;无则先经 ww_model_train 训一个或用现有):
1. `POST /screen/model/default {id:m_x}` → `GET /screen/models` `default_model==m_x` → `POST /screen/run`(不传 model)响应 `model==m_x`。
2. `POST /screen/model/default {id:prod}` → `/screen/run` 响应 `model==prod`(回滚)。
3. `POST /screen/model/delete {id:m_x}`(若 m_x 是默认)→ `default_model` 变 None、`/screen/run` 回 prod。
Expected: 三步均符合;**没设默认时 `/screen/run` 与改动前逐字节同形**(契约 chosen/benched/... 不变)。

- [ ] **Step 4: 真机 e2e — 引擎研究线**

对 Task 4 存活工具,同进程 `reg[name].run(...)` 各跑一次确认回真结果(非报错)。Expected: 全部回真数据。

- [ ] **Step 5: 还原现场**

确认无遗留临时态(删除测试中训的临时变体/清掉测试设的默认指针);9999 跑新代码。汇报:存活引擎工具数 N、最终 `CONSOLE_ALLOWED` 计数、剔除项(若有)及原因。

- [ ] **Step 6: 提交(若 Step 1 有顺带修复)**

```bash
git add -A
git commit -m "test(weiwo): 工坊闭环+alpha-zoo 全量回归 + 真机 e2e 证据"
```

---

## 计数总账(自查锚)

| 阶段 | ww_ | CONSOLE_ALLOWED |
|---|---|---|
| 起点 | 28 | 46 |
| Task 3 后 | 30 | 48 |
| Task 4 后 | 30 | 48 + N(全活=55) |

守护测试断言点:`tests/test_console_tools.py` 511/517/518/982/984;`tests/test_console_api.py:932`(每个 ww_ 在提示词)。
