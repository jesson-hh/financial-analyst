# 帷幄工具可发现性补丁(#3 + #4)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 修审计工具发现侧 #3(提示词↔工具表漂移)与 #4(引擎 news_collect 悬空引用):在 `_SYSTEM_PROMPT` 具名缺失工具 + 加新闻路由纪律,并加守护测试。

**Architecture:** 纯 `_SYSTEM_PROMPT` 文本补充(`guanlan_v2/console/api.py`)+ 2 个守护测试;不动 engine、不增删工具(守护计数 26/44 不变)。

**Tech Stack:** Python 3.13;pytest。规格见 `docs/superpowers/specs/2026-06-17-weiwo-tool-discoverability-design.md`。

**仓库注记:** 本仓**不是 git 仓库**——「Commit」替换为「跑全量 `pytest tests/ -q` 全绿」检查点;后端改动经杀 9999 监听 PID(看门狗 ~10s 拉新码)生效,真机在最后做。

---

### Task 1: 提示词具名补全 + 新闻路由纪律 + 守护测试

**Files:**
- Modify: `guanlan_v2/console/api.py`(`_SYSTEM_PROMPT`,:26-47)
- Test: `tests/test_console_api.py`(追加 2 守护测试)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_console_api.py` 末尾)

```python
def test_system_prompt_names_all_ww_tools():
    """#3 守护:每个 ww_ 工具名都必须在 _SYSTEM_PROMPT 出现(防提示词↔工具表漂移)。"""
    from guanlan_v2.console.api import _SYSTEM_PROMPT
    import guanlan_v2.console.tools as ct
    missing = [t["name"] for t in ct.WW_TOOL_TABLE if t["name"] not in _SYSTEM_PROMPT]
    assert missing == [], f"这些 ww_ 工具未在系统提示词具名: {missing}"


def test_system_prompt_routes_news_collect():
    """#4 守护:提示词把裸 news_collect 路由到 ww_news_collect 并警示别直接调。
    用纪律12 的独有短语断言(不能用『调不到』——它已在 :34 ww_endpoints 描述出现,会让守护空转)。"""
    from guanlan_v2.console.api import _SYSTEM_PROMPT
    assert "改用 ww_news_collect" in _SYSTEM_PROMPT   # 路由纪律:裸 news_collect → ww_news_collect
    assert "别直接调" in _SYSTEM_PROMPT                # 警示别调裸名(纪律12 独有)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd G:/guanlan-v2 && PYTHONIOENCODING=utf-8 python -m pytest tests/test_console_api.py -k "system_prompt" -v`
Expected: `test_system_prompt_names_all_ww_tools` FAIL(missing=`['ww_f10','ww_screen_factors']`);`test_system_prompt_routes_news_collect` FAIL(纪律12 独有短语「改用 ww_news_collect」「别直接调」均不存在 → FAIL)。

- [ ] **Step 3: 编辑 `_SYSTEM_PROMPT`(三处)**

**编辑 A** — 把泛指句点名高价值引擎工具。找到(`api.py:30`):

```
报告库 ww_reports_query,以及行情/财务/新闻/经验检索等查询工具。
```

替换为:

```
报告库 ww_reports_query,以及一键多维速览 stock_brief、财务基本面 financials、本地历史新闻库 news_query、行情/资金/经验检索等查询工具。
```

**编辑 B** — 具名补全两个缺失 ww_ 工具。找到(`api.py:33`,因子合成那一行):

```
另有:因子合成 ww_factor_compose、物化特征 ww_feature_build、查 DSL 字段 ww_factor_fields(写因子表达式前先查合法字段名)、ETF 研报 ww_etf_report_run(后台,需确认)。
```

替换为(在其后追加一行 ww_f10/ww_screen_factors):

```
另有:因子合成 ww_factor_compose、物化特征 ww_feature_build、查 DSL 字段 ww_factor_fields(写因子表达式前先查合法字段名)、ETF 研报 ww_etf_report_run(后台,需确认)。
另有:F10 基本面 ww_f10(估值/总股本/公告/龙虎榜两融/券商目标价)、列因子库 ww_screen_factors(写选股 factors 前查 id+IC)。
```

**编辑 C** — 加新闻路由纪律 12。找到纪律 11 结尾(`api.py:47`):

```
11. 遇到平台确实没有的能力,或某工具反复失败,诚实告诉用户『这个我目前做不到/需在界面操作』,并用 ww_memory_write 把这个能力缺口记下来(scope=global),供后续补齐;绝不假装做到。"""
```

替换为(纪律 11 后加纪律 12,再闭合 `"""`):

```
11. 遇到平台确实没有的能力,或某工具反复失败,诚实告诉用户『这个我目前做不到/需在界面操作』,并用 ww_memory_write 把这个能力缺口记下来(scope=global),供后续补齐;绝不假装做到。
12. 新闻路由:任何工具结果提示『调 news_collect 刷新』时,实际改用 ww_news_collect(需确认);查本地历史新闻库用 news_query(只读);实时新闻情绪/快讯用 ww_news_search。news_collect 这个裸名字你调不到,别直接调。"""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd G:/guanlan-v2 && PYTHONIOENCODING=utf-8 python -m pytest tests/test_console_api.py -k "system_prompt" -v`
Expected: 两条均 PASS(所有 26 个 ww_ 工具具名;新闻路由纪律含 ww_news_collect + 「别直接调」)。

- [ ] **Step 5: 检查点(全量 + 守护计数不变)**

Run: `cd G:/guanlan-v2 && PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 全绿(525:之前 523 + 本任务 2 守护测试);`test_registry_derivation_consistent`/`test_engine_profile_excludes_ww_but_console_whitelist_resolves`(26/44)仍绿(本任务不增删工具)。

---

### Task 2: 真机验证 + 还原现场

**Files:** 无代码改动;验证 + 清理。

- [ ] **Step 1: 重启后端**

杀 9999 监听 PID(看门狗 ~10s 拉新码),`curl -s -m8 http://127.0.0.1:9999/console/sessions` 确认服务回来、PID 变化。

- [ ] **Step 2: 真机 #3 — ww_f10 可发现**

`POST /console/send` 全新会话:「贵州茅台 600519 的总股本是多少、最近有什么公告、券商目标价?」
Expected: tool_call 选中 `ww_f10`(现已具名);读 `var/console/sessions/<sid>/events.jsonl` 取证。

- [ ] **Step 3: 真机 #4 — 新闻不走裸 news_collect**

全新会话:「看看中国平安 601318 最近的新闻情绪」
Expected: 选 `ww_news_search`(或本地 `news_query`),**不出现对裸 `news_collect` 的 tool_call**(撞模块门);读 events.jsonl 确认 tool_call 名单里无裸 `news_collect`。

- [ ] **Step 4: 还原现场**

`DELETE /console/sessions/<sid>` 删测试会话;确认 `var/console/memory.md` 未被污染(本批不写记忆,应天然干净)。

---

## Self-Review(对照 spec)

- **#3 提示词具名**:编辑 A(引擎工具点名)+ 编辑 B(ww_f10/ww_screen_factors)→ Task 1 ✓;守护 `test_system_prompt_names_all_ww_tools` ✓
- **#4 新闻路由纪律**:编辑 C(纪律 12)→ Task 1 ✓;守护 `test_system_prompt_routes_news_collect` ✓
- **不动 engine / 不增删工具(26/44)**:Task 1 只改 `_SYSTEM_PROMPT` 文本 + 测试,Step 5 验证计数守护仍绿 ✓
- **真机 + 还原**:Task 2 ✓
- **Placeholder 扫描**:无 TBD;三处编辑给出确切 old→new 文本;测试完整 ✓
- **一致性**:守护测试断言的 `ww_news_collect`/「别直接调」与编辑 C 文案一致;`ww_f10`/`ww_screen_factors` 与编辑 B 一致 ✓
