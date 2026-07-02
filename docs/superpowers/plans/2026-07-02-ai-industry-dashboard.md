# AI 投研看板(guanlan_v2/industry)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec `docs/superpowers/specs/2026-07-02-ai-industry-dashboard-design.md`:五层 AI 产业链逻辑框架(YAML)+ DeepSeek 读研报抽取管线(手动增量)+ 行情/研报双轴聚合 + `/industry/*` API + `ui/industry/` 看板页。

**Architecture:** 三层流水线——框架文件(`frameworks/ai_chain.yaml` 唯一事实源)→ per-doc 抽取库(`store/extractions.jsonl`,append-only,doc_id 溯源)→ 请求时聚合(`GET /industry/board`,TTL 缓存)。跨仓只读 `G:\stocks\stock_data\text_source`(env 可覆盖),LLM 复用引擎 `LLMClient`(deepseek-chat + json_object)。

**Tech Stack:** FastAPI(薄壳挂载)、pandas/pyarrow(引擎 venv 已有)、PyYAML(引擎 venv 已有)、引擎 `LLMClient`、前端无构建 React18 UMD + 浏览器 Babel JSX。

## Global Constraints(每个任务隐含遵守)

- 运行时/测试解释器一律引擎 venv:`G:/financial-analyst/.venv/Scripts/python.exe`;测试命令 `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/<file> -v`(仓根 `G:\guanlan-v2` 下执行;`tests/conftest.py` 已顶层 prepend 引擎路径)。
- 引擎 primitive/pandas 一律函数体内延迟 import(seats/screen 先例);模块顶层只 import 标准库。
- 诚实失败:一切对外 API 失败返回 `{ok: False, "reason": <str>}`,HTTP 200;单信号缺失 → 该字段 `None` + `reason`,不静默补零、不冒充。
- 协程内严禁同步 I/O → `asyncio.to_thread`(9999 看门狗红线)。
- 写盘:state JSON 先写 `.tmp` 再 `os.replace` 原子覆盖;`extractions.jsonl` append-only。
- LLM:显式 `config_path = 仓根/config/llm.yaml`,`deepseek-chat` + `response_format={"type":"json_object"}`,`asyncio.wait_for` 90s/篇,`asyncio.Semaphore(3)` 限并发,真失败 `{ok:False}` 绝不伪造。
- ID 约定(spec §3.2):环节 `A1..A6,B1..B5,C1..C5,I1..I4,M1..M2,F1..F4,G1..G4`(G3/G4 相邻链 stub);驱动 `D1..D7`;传导边 `T1..T15`;叙事 `N1..N8`。
- 代码码式统一 `SH688498`/`SZ300308`(qlib 式)。
- 前端:无构建,`ui/industry/` HTML 薄壳 + jsx(浏览器 Babel),引 `../_shared/tokens.css`,手写 SVG 零图表库,改 jsx 必 bump `?v=`。
- 改后端要重启 9999 才生效(代际看门狗 `check_9999.ps1`,杀掉后 ~41s 自愈)。
- 提交信息:`feat(industry): ...` 中文一行,结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 帷幄(console)工具**不接**——不改 `console/tools.py`、不动守护计数(spec §6 二期)。

---

### Task 1: 框架文件 ai_chain.yaml + framework.py 加载校验

**Files:**
- Create: `guanlan_v2/industry/__init__.py`
- Create: `guanlan_v2/industry/frameworks/ai_chain.yaml`
- Create: `guanlan_v2/industry/framework.py`
- Test: `tests/test_industry_framework.py`

**Interfaces:**
- Produces: `load_framework(path=None) -> dict`(缓存;坏引用抛 `FrameworkError`)、`segment_ids(fw) -> list[str]`、`segment_pool(fw, sid) -> list[str]`、`all_pool_codes(fw) -> set[str]`、`framework_digest(fw) -> str`(给 LLM prompt 的紧凑框架摘要)、常量 `FRAMEWORKS_DIR`、异常 `FrameworkError`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_industry_framework.py
# -*- coding: utf-8 -*-
"""AI投研框架文件加载/校验(spec 2026-07-02-ai-industry-dashboard §3)。"""
import pytest


def test_load_framework_ok():
    from guanlan_v2.industry.framework import load_framework, segment_ids
    fw = load_framework()
    assert fw["meta"]["id"] == "ai_chain"
    sids = segment_ids(fw)
    # 28 满信号环节 + 2 相邻链 stub
    assert len(sids) == 30
    full = [s for s in fw["segments"] if not s.get("adjacent")]
    assert len(full) == 28
    assert {"A1", "C2", "I3", "M1", "F4", "G2"} <= set(sids)


def test_ids_are_consistent():
    from guanlan_v2.industry.framework import load_framework
    fw = load_framework()
    sids = {s["id"] for s in fw["segments"]}
    dids = {d["id"] for d in fw["drivers"]}
    assert len(fw["drivers"]) == 7 and len(fw["edges"]) == 15 and len(fw["narratives"]) == 8
    for e in fw["edges"]:
        for ref in e["from"] + e["to"]:
            assert ref in sids | dids, f"edge {e['id']} 引用了不存在的 {ref}"
    for n in fw["narratives"]:
        for a in n["activates"]:
            assert a["segment"] in sids, f"narrative {n['id']} 引用了不存在的 {a['segment']}"


def test_pool_and_digest():
    from guanlan_v2.industry.framework import load_framework, segment_pool, all_pool_codes, framework_digest
    fw = load_framework()
    pool = segment_pool(fw, "C2")
    assert "SH688498" in pool                      # 源杰科技锚票
    assert all(c[:2] in ("SH", "SZ", "BJ") for c in all_pool_codes(fw))
    dg = framework_digest(fw)
    assert "C2" in dg and "光芯片" in dg and len(dg) < 8000   # 摘要必须紧凑,能塞进 prompt


def test_bad_reference_raises(tmp_path):
    from guanlan_v2.industry.framework import load_framework, FrameworkError
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "meta: {id: x, name: x, version: 1}\n"
        "drivers: [{id: D1, name: d, indicators: []}]\n"
        "groups: [{id: A, name: g}]\n"
        "segments: [{id: A1, name: s, group: A, logic: l, keywords: [k], stocks: []}]\n"
        "edges: [{id: T1, from: [D9], to: [A1], sign: '+', mechanism: m, lag: l, validation: [v]}]\n"
        "narratives: []\nsignal_defs: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(FrameworkError):
        load_framework(str(bad))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_framework.py -v`
Expected: FAIL(`ModuleNotFoundError: guanlan_v2.industry`)

- [ ] **Step 3: 写 `guanlan_v2/industry/__init__.py`**

```python
# -*- coding: utf-8 -*-
"""guanlan_v2.industry — AI投研看板(行业逻辑框架)。

随 cards/seats/screen 先例:导出 build_industry_router,挂在薄壳 create_app 上。
spec: docs/superpowers/specs/2026-07-02-ai-industry-dashboard-design.md
"""
from __future__ import annotations

__all__ = ["build_industry_router"]


def build_industry_router():
    from .api import build_industry_router as _b
    return _b()
```

(Task 7 之前 `.api` 尚不存在——本任务测试只 import `framework`,不触发;`__init__` 的函数体内延迟 import 保证这一点。)

- [ ] **Step 4: 写 `guanlan_v2/industry/frameworks/ai_chain.yaml`(框架唯一事实源,内容=spec §3 全量转写)**

完整文件如下(锚票码带 `note: 待核` 的在 Task 10 票池工具跑完后人工复核;全球坐标/逻辑/关键词逐字来自 spec §3.2/§3.3):

```yaml
meta: {id: ai_chain, name: AI产业链, version: 1, updated: "2026-07-02"}

drivers:
  - {id: D1, name: 北美CSP capex, indicators: [四大云厂财报capex指引, TrendForce口径]}
  - {id: D2, name: 国内智算capex, indicators: [运营商资本开支, 大厂采购, 智算中心招标]}
  - {id: D3, name: 模型迭代与推理成本, indicators: [版本发布, token单价, 日均token调用量]}
  - {id: D4, name: 供给约束与涨价, indicators: [DRAM/NAND合约价环比, HBM/CoWoS产能, EML缺口]}
  - {id: D5, name: 国内政策, indicators: [安全可靠测评名单, 算电协同, 人工智能+专项行动]}
  - {id: D6, name: 出口管制/地缘, indicators: [对华芯片禁令变化]}
  - {id: D7, name: 海外技术路线, indicators: [GTC/GB300/Rubin, 谷歌TPU/OCS, 台积电COUPE]}

groups:
  - {id: A, name: 芯片材料}
  - {id: B, name: 服务器配套}
  - {id: C, name: 光网络}
  - {id: I, name: 算力基建}
  - {id: M, name: 模型数据}
  - {id: F, name: 应用}
  - {id: G, name: 端侧具身}

segments:
  - id: A1
    name: 国产AI芯片
    group: A
    logic: 国产替代+推理放量,大厂真实采购兑现
    keywords: [国产GPU, 昇腾, DCU, ASIC, 算力芯片, 推理芯片, 950PR, 950DT]
    ths_concepts: [中芯国际概念, 华为昇腾, 东数西算(算力)]
    stocks:
      - {code: SH688256, name: 寒武纪, role: anchor}
      - {code: SH688041, name: 海光信息, role: anchor}
      - {code: SH688795, name: 摩尔线程, role: anchor}
      - {code: SH688802, name: 沐曦股份, role: pool}
    global: {intl: NVIDIA垄断90%+/博通ASIC崛起, cn_position: 短板→追赶, moat: CUDA生态+先进制程受限, equity_logic: [Ω], prospect: Ω→兑现过渡, stars: 0}
  - id: A2
    name: 先进制程代工
    group: A
    logic: 国产算力的产能咽喉
    keywords: [先进制程, 代工, N+2, 晶圆厂, 扩产]
    ths_concepts: [中芯国际概念]
    stocks:
      - {code: SH688981, name: 中芯国际, role: anchor}
      - {code: SH688347, name: 华虹公司, role: anchor}
    global: {intl: 台积电垄断2/3nm, cn_position: 短板, moat: EUV禁售, equity_logic: [Ω, β], prospect: Ω+产能扩张β, stars: 0}
  - id: A3
    name: 半导体设备
    group: A
    logic: 扩产+国产化双击
    keywords: [刻蚀, 薄膜沉积, 光刻, 测试机, 量测, 设备国产化]
    ths_concepts: [光刻机, 中芯国际概念]
    stocks:
      - {code: SZ002371, name: 北方华创, role: anchor}
      - {code: SH688012, name: 中微公司, role: anchor}
      - {code: SZ300604, name: 长川科技, role: pool}
    global: {intl: AMAT/ASML/LAM主导, cn_position: 追赶, moat: 技术积累+专利, equity_logic: [Ω], prospect: Ω兑现型订单可见, stars: 1}
  - id: A4
    name: 先进封装
    group: A
    logic: HBM/算力芯片封装瓶颈(CoWoS)
    keywords: [先进封装, CoWoS, Chiplet, 2.5D, 封测]
    ths_concepts: [先进封装]
    stocks:
      - {code: SZ002156, name: 通富微电, role: anchor}
      - {code: SH600584, name: 长电科技, role: anchor}
      - {code: SZ002185, name: 华天科技, role: pool}
    global: {intl: 台积电CoWoS主导, cn_position: 并跑, moat: 产能+工艺, equity_logic: [β, Ω], prospect: β+国产配套Ω, stars: 0}
  - id: A5
    name: 存储/HBM
    group: A
    logic: 超级周期合约价环比大涨,业绩兑现型
    keywords: [存储, HBM, DRAM, NAND, 涨价, 内存接口, 模组]
    ths_concepts: [存储芯片]
    stocks:
      - {code: SZ301308, name: 江波龙, role: anchor}
      - {code: SH603986, name: 兆易创新, role: anchor}
      - {code: SH688525, name: 佰维存储, role: pool}
      - {code: SH688008, name: 澜起科技, role: pool}
      - {code: SZ300475, name: 香农芯创, role: pool}
    global: {intl: 海力士/三星/美光三寡头HBM全海外, cn_position: 短板, moat: 工艺+专利+产能, equity_logic: [Δ, Ω], prospect: Δ主模组吃涨价+Ω长鑫链, stars: 0}
  - id: A6
    name: 上游材料
    group: A
    logic: 价值量沿链上移(特气/靶材/PPO树脂/CCL)
    keywords: [电子特气, 靶材, PPO, 覆铜板, CCL, 树脂, 电子材料]
    ths_concepts: [PCB概念, 先进封装]
    stocks:
      - {code: SH688146, name: 中船特气, role: anchor}
      - {code: SZ300666, name: 江丰电子, role: anchor}
      - {code: SH601208, name: 东材科技, role: pool}
      - {code: SH600183, name: 生益科技, role: pool}
    global: {intl: 信越/杜邦等, cn_position: 部分领先, moat: 认证周期, equity_logic: [Δ, β], prospect: Δ+扩产β 股王发源地, stars: 1}
  - id: B1
    name: AI服务器/ODM
    group: B
    logic: 训练+推理整机放量,毛利改善
    keywords: [AI服务器, ODM, 整机, 代工, 超节点]
    ths_concepts: [液冷服务器, 英伟达概念]
    stocks:
      - {code: SH601138, name: 工业富联, role: anchor}
      - {code: SZ000977, name: 浪潮信息, role: anchor}
      - {code: SH603019, name: 中科曙光, role: pool}
      - {code: SH603296, name: 华勤技术, role: pool}
    global: {intl: 需求在海外制造在中国, cn_position: 领先, moat: 客户绑定+规模, equity_logic: [β], prospect: β兑现高确定低弹性, stars: 1}
  - id: B2
    name: 高速PCB
    group: B
    logic: 订单+60%排产到2027,三龙头占高端70%
    keywords: [PCB, 高多层, HDI, 服务器板, 背板]
    ths_concepts: [PCB概念]
    stocks:
      - {code: SZ002463, name: 沪电股份, role: anchor}
      - {code: SZ300476, name: 胜宏科技, role: anchor}
      - {code: SZ002916, name: 深南电路, role: pool}
      - {code: SZ002938, name: 鹏鼎控股, role: pool}
    global: {intl: 全球高端AI板中国主导, cn_position: 领先, moat: 高多层工艺+认证, equity_logic: [β], prospect: β最硬订单排2027, stars: 2}
  - id: B3
    name: 服务器电源/HVDC
    group: B
    logic: GB300单卡1.4kW→800V重构,独立主战场
    keywords: [服务器电源, HVDC, 800V, PSU, 电源模块]
    ths_concepts: [液冷服务器, 英伟达概念]
    stocks:
      - {code: SZ002851, name: 麦格米特, role: anchor}
      - {code: SZ002364, name: 中恒电气, role: anchor}
      - {code: SZ300593, name: 新雷能, role: pool}
      - {code: SZ300870, name: 欧陆通, role: pool}
    global: {intl: 台达/Vertiv, cn_position: 并跑, moat: 认证+功率密度, equity_logic: [β, Θ], prospect: β+Θ 800V路线, stars: 1}
  - id: B4
    name: 液冷
    group: B
    logic: 渗透率14%→31%,UQD/CDU零部件独立成炒作细分
    keywords: [液冷, 冷板, 浸没式, UQD, CDU, 快接头, 温控]
    ths_concepts: [液冷服务器]
    stocks:
      - {code: SZ002837, name: 英维克, role: anchor}
      - {code: SZ301018, name: 申菱环境, role: anchor}
      - {code: SZ300499, name: 高澜股份, role: pool}
      - {code: SZ300547, name: 川环科技, role: pool}
    global: {intl: 中国厂进MGX生态+谷歌认证, cn_position: 领先, moat: 认证+交付能力, equity_logic: [β], prospect: 渗透率β, stars: 1}
  - id: B5
    name: 铜连接/高速连接器
    group: B
    logic: 超节点架构受益,窄而纯
    keywords: [铜缆, 高速连接器, 背板连接, DAC, 铜互连]
    ths_concepts: [铜缆高速连接]
    stocks:
      - {code: SZ002475, name: 立讯精密, role: anchor}
      - {code: SH688629, name: 华丰科技, role: anchor}
      - {code: SZ002179, name: 中航光电, role: pool}
      - {code: SZ002130, name: 沃尔核材, role: pool, note: 待核}
    global: {intl: 安费诺主导, cn_position: 追赶, moat: 高速信号完整性技术, equity_logic: [Θ], prospect: Θ超节点架构, stars: 0}
  - id: C1
    name: 光模块
    group: C
    logic: 1.6T十倍放量,业绩最硬(盯CPO替代)
    keywords: [光模块, 800G, 1.6T, 可插拔, 光互连]
    ths_concepts: [共封装光学(CPO)]
    stocks:
      - {code: SZ300308, name: 中际旭创, role: anchor}
      - {code: SZ300502, name: 新易盛, role: anchor}
      - {code: SZ000988, name: 华工科技, role: pool}
    global: {intl: 全球前二+前十占七, cn_position: 领先, moat: 规模+迭代速度, equity_logic: [β], prospect: β最硬盯CPO替代, stars: 2}
  - id: C2
    name: 光芯片/光器件
    group: C
    logic: EML缺口25-30%涨价,十倍股发源地
    keywords: [光芯片, EML, DFB, CW光源, 光器件, 陶瓷套管]
    ths_concepts: [共封装光学(CPO)]
    stocks:
      - {code: SH688498, name: 源杰科技, role: anchor}
      - {code: SH688313, name: 仕佳光子, role: anchor}
      - {code: SH688048, name: 长光华芯, role: pool}
      - {code: SZ300394, name: 天孚通信, role: pool}
      - {code: SZ300570, name: 太辰光, role: pool}
    global: {intl: Lumentum/Coherent主导EML 2028产能售罄, cn_position: 追赶→突破, moat: 良率+可靠性认证12-18月, equity_logic: [Δ, Ω], prospect: Δ+Ω双击 十倍股发源地, stars: 2}
  - id: C3
    name: CPO/硅光
    group: C
    logic: 商业化元年但预期分歧大——与C1分开建
    keywords: [CPO, 硅光, COUPE, NPO, 光引擎, 共封装]
    ths_concepts: [共封装光学(CPO)]
    stocks:
      - {code: SZ300308, name: 中际旭创, role: anchor}
      - {code: SZ300502, name: 新易盛, role: pool}
      - {code: SZ300394, name: 天孚通信, role: pool}
      - {code: SZ300757, name: 罗博特科, role: pool}
    global: {intl: 台积电COUPE/NV定调, cn_position: 并跑, moat: 工艺平台, equity_logic: [Θ], prospect: Θ高波动高分歧, stars: 0}
  - id: C4
    name: OCS全光交换
    group: C
    logic: 谷歌链从零爆发(新兴)
    keywords: [OCS, 全光交换, 光交换机, MEMS]
    ths_concepts: []
    stocks:
      - {code: SZ300620, name: 光库科技, role: anchor}
      - {code: SH600703, name: 三安光电, role: pool}
    global: {intl: 谷歌自研生态, cn_position: 追赶, moat: 客户独占代工, equity_logic: [Ψ, β], prospect: Ψ→β小而陡, stars: 0}
  - id: C5
    name: 交换机/网络设备
    group: C
    logic: 集群组网 400G→800G
    keywords: [交换机, 800G交换, 白牌, 交换芯片, 组网]
    ths_concepts: [东数西算(算力)]
    stocks:
      - {code: SZ301165, name: 锐捷网络, role: anchor, note: 待核}
      - {code: SZ000938, name: 紫光股份, role: anchor}
      - {code: SH688702, name: 盛科通信, role: pool}
    global: {intl: 博通芯片垄断, cn_position: 追赶, moat: 交换芯片, equity_logic: [β, Ω], prospect: 国内β+芯片Ω, stars: 0}
  - id: I1
    name: IDC/AIDC
    group: I
    logic: AIDC量价通胀+超节点,6-7月最新口径
    keywords: [IDC, AIDC, 智算中心, 数据中心, 机柜, 上架率]
    ths_concepts: [数据中心(AIDC), 东数西算(算力)]
    stocks:
      - {code: SZ300442, name: 润泽科技, role: anchor}
      - {code: SZ300383, name: 光环新网, role: anchor}
      - {code: SH603881, name: 数据港, role: pool}
    global: {intl: 国内自主市场, cn_position: 国内市场, moat: 电力指标+区位卡位, equity_logic: [β], prospect: 量价通胀β超节点, stars: 1}
  - id: I2
    name: 算力租赁/智算服务
    group: I
    logic: 推理需求承接层,纯标的稀缺
    keywords: [算力租赁, 智算服务, 算力调度, GPU云]
    ths_concepts: [算力租赁]
    stocks:
      - {code: SZ300846, name: 首都在线, role: anchor}
      - {code: SZ300857, name: 协创数据, role: anchor}
    global: {intl: 国内市场, cn_position: 国内市场, moat: 卡量+客户, equity_logic: [β], prospect: 推理放量β竞争加剧, stars: 0}
  - id: I3
    name: 算电协同/电力设备
    group: I
    logic: 国家级新基建,规模1800亿+85%
    keywords: [算电协同, 绿电直连, 柴发, 变压器, 特高压, HVDC输电]
    ths_concepts: []
    stocks:
      - {code: SH603861, name: 白云电器, role: anchor, note: 票池待概念补全}
      - {code: SZ002335, name: 科华数据, role: pool, note: 待核}
    global: {intl: 国内政策市场, cn_position: 国内市场, moat: 电网资源, equity_logic: [Θ], prospect: 政策新基建, stars: 0}
  - id: I4
    name: 运营商/云平台
    group: I
    logic: 国内capex主体+推理入口
    keywords: [运营商, 智算, 资本开支, 云计算, MaaS]
    ths_concepts: [云计算]
    stocks:
      - {code: SH600941, name: 中国移动, role: anchor}
      - {code: SH601728, name: 中国电信, role: anchor}
      - {code: SH600050, name: 中国联通, role: pool}
    global: {intl: 国内市场, cn_position: 国内市场, moat: 牌照+资源, equity_logic: [β], prospect: 红利+算力资产重估, stars: 0}
  - id: M1
    name: 基础大模型/MaaS
    group: M
    logic: 生态映射炒作(DeepSeek链/豆包链/千问链)
    keywords: [大模型, DeepSeek, 豆包, 千问, MaaS, 开源模型]
    ths_concepts: [DeepSeek概念, 多模态AI]
    stocks:
      - {code: SZ002230, name: 科大讯飞, role: anchor}
      - {code: SZ300418, name: 昆仑万维, role: anchor}
      - {code: SH601360, name: 三六零, role: pool}
    global: {intl: OpenAI/谷歌领先, cn_position: 短板, moat: 算力+人才, equity_logic: [Ψ], prospect: Ψ映射 A股无纯标的, stars: 0}
  - id: M2
    name: 数据要素/语料
    group: M
    logic: 窄而纯(AI语料52只)
    keywords: [数据要素, 语料, 数据标注, 数据集]
    ths_concepts: [AI语料, 数据要素]
    stocks:
      - {code: SH688787, name: 海天瑞声, role: anchor}
      - {code: SZ300229, name: 拓尔思, role: anchor}
    global: {intl: 数据主权市场, cn_position: 国内市场, moat: 牌照+稀缺, equity_logic: [Ψ], prospect: 主题性, stars: 0}
  - id: F1
    name: AI办公/Agent
    group: F
    logic: 金融Agent渗透>30%、WPS AI千万用户,最先兑现
    keywords: [Agent, 智能体, AI办公, WPS, Copilot, 订阅]
    ths_concepts: [AI智能体]
    stocks:
      - {code: SH688111, name: 金山办公, role: anchor}
      - {code: SH600588, name: 用友网络, role: anchor}
      - {code: SZ300687, name: 赛意信息, role: pool}
    global: {intl: Copilot/Agentforce成熟, cn_position: 追赶, moat: 场景数据+粘性, equity_logic: [Ψ], prospect: 业绩验证型选择性, stars: 1}
  - id: F2
    name: AI金融
    group: F
    logic: 券商8/10接大模型
    keywords: [AI金融, 智能投顾, 券商AI, 金融大模型]
    ths_concepts: [AI应用]
    stocks:
      - {code: SZ300033, name: 同花顺, role: anchor}
      - {code: SH600570, name: 恒生电子, role: anchor}
      - {code: SZ300059, name: 东方财富, role: pool}
    global: {intl: 国内自主, cn_position: 国内市场, moat: 牌照+入口, equity_logic: [Ψ], prospect: 业绩验证型, stars: 0}
  - id: F3
    name: AIGC/传媒游戏
    group: F
    logic: Token经济到AIGC叙事
    keywords: [AIGC, AI视频, AI游戏, 内容生成, 数字人]
    ths_concepts: [AIGC, AI视频]
    stocks:
      - {code: SZ300418, name: 昆仑万维, role: anchor}
      - {code: SZ300624, name: 万兴科技, role: anchor}
      - {code: SZ002558, name: 巨人网络, role: pool}
    global: {intl: 海外工具领先, cn_position: 追赶, moat: IP+流量, equity_logic: [Ψ], prospect: Ψ主题轮动, stars: 0}
  - id: F4
    name: AI垂直行业
    group: F
    logic: 医疗/教育/工业AI,专项行动政策面,轮动性强
    keywords: [AI医疗, AI教育, 工业AI, 物理AI, 垂直大模型]
    ths_concepts: [AI应用]
    stocks:
      - {code: SZ300253, name: 卫宁健康, role: anchor}
      - {code: SZ002230, name: 科大讯飞, role: pool}
      - {code: SZ300378, name: 鼎捷数智, role: pool}
    global: {intl: 分化, cn_position: 追赶, moat: 行业数据, equity_logic: [Ψ], prospect: Ψ主题轮动, stars: 0}
  - id: G1
    name: AI手机/PC/端侧SoC
    group: G
    logic: AI手机渗透35%,SoC/存储模组受益
    keywords: [AI手机, AI PC, 端侧, SoC, NPU, 端侧模型]
    ths_concepts: [AI手机, AI PC]
    stocks:
      - {code: SZ002475, name: 立讯精密, role: anchor}
      - {code: SH603296, name: 华勤技术, role: anchor}
      - {code: SH688608, name: 恒玄科技, role: pool}
      - {code: SH603893, name: 瑞芯微, role: pool}
    global: {intl: 高通/苹果芯片主导, cn_position: 领先, moat: 制造供应链+SoC细分突破, equity_logic: [β], prospect: 新品周期β, stars: 0}
  - id: G2
    name: AI眼镜/XR
    group: G
    logic: 出货破2368万台,供应链80%在中国
    keywords: [AI眼镜, 智能眼镜, XR, 光波导, 硅基OLED]
    ths_concepts: [AI眼镜]
    stocks:
      - {code: SZ002241, name: 歌尔股份, role: anchor}
      - {code: SZ301479, name: 弘景光电, role: anchor}
    global: {intl: Meta定义品类, cn_position: 领先, moat: 光学/声学工艺, equity_logic: [β], prospect: 新品周期β, stars: 1}
  - id: G3
    name: 机器人/具身智能
    group: G
    adjacent: true
    logic: 相邻链——只挂接口不建信号
    keywords: [人形机器人, 具身智能]
    ths_concepts: [人形机器人]
    stocks: []
  - id: G4
    name: 智能驾驶
    group: G
    adjacent: true
    logic: 相邻链——只挂接口不建信号
    keywords: [智能驾驶, 无人驾驶]
    ths_concepts: [无人驾驶]
    stocks: []

edges:
  - {id: T1, from: [D1], to: [C1, B1, B2, B3, B4, B5], sign: "+", mechanism: 云厂开支→NV/ASIC订单→中国供应链, lag: 1-2季, validation: [订单排产, 月度出货, CoWoS产能]}
  - {id: T2, from: [D2], to: [A1, B1, C5, I1], sign: "+", mechanism: 智算招标→国产整机/芯片采购, lag: 0-2季, validation: [招标金额, 运营商capex, 中标公告]}
  - {id: T3, from: [D3], to: [F1, F2, F3, F4, A1, A5], sign: "+", mechanism: Jevons效应 便宜→用量爆炸→算力/显存需求(反馈环), lag: 1-2季, validation: [token调用量, API价格, 扩容公告]}
  - {id: T4, from: [D4], to: [A5, C2, A6], sign: "+", mechanism: 缺口→合约价↑→业绩弹性, lag: 当季, validation: [合约价环比, EML交期]}
  - {id: T5, from: [D5], to: [A1, A2, A3, I3], sign: "+", mechanism: 测评名单+采购倾斜+新基建, lag: 事件驱动, validation: [名单, 文件, 项目数]}
  - {id: T6, from: [D6], to: [A1, A2, A3, A5], sign: "+", mechanism: 断供风险→替代紧迫性→估值溢价, lag: 即时, validation: [管制清单变化]}
  - {id: T7, from: [D7], to: [C3, B3, B5, C4], sign: "+", mechanism: 路线定调→期权重估, lag: 即时情绪+2-4季兑现, validation: [GTC, COUPE量产, GB300出货]}
  - {id: T8, from: [C2], to: [C1], sign: "+", mechanism: EML缺口制约出货+抬成本, lag: 当季, validation: [EML价格, 交期]}
  - {id: T9, from: [B2], to: [A6], sign: "+", mechanism: PCB扩产→PPO/CCL/特气量价, lag: 1-2季, validation: [CCL提价函, 特气价格]}
  - {id: T10, from: [B1], to: [B3, B4], sign: "+", mechanism: 机柜功率密度→供电/散热重构, lag: 1-2季, validation: [GB300机柜功率, 液冷渗透率]}
  - {id: T11, from: [A1], to: [A2, A4], sign: "+", mechanism: 国产芯片放量订单沿链传导, lag: 1季, validation: [中芯产能利用率, 封测稼动率]}
  - {id: T12, from: [A5], to: [A5], sign: "+", mechanism: HBM挤占产能→存储全线涨价(链内自反), lag: 当季, validation: [合约价环比]}
  - {id: T13, from: [M1], to: [F1, F2, F3, F4], sign: "+", mechanism: 能力阈值跨越→场景可用, lag: 1-2季, validation: [版本发布, 评测]}
  - {id: T14, from: [I1], to: [I3, B4], sign: "+", mechanism: 智算中心建设→配电/温控采购, lag: 1-2季, validation: [IDC招标明细]}
  - {id: T15, from: [C3], to: [C1], sign: "-", mechanism: CPO起量威胁可插拔(替代边), lag: 2-4季, validation: [COUPE良率, CPO端口占比]}

narratives:
  - id: N1
    name: 英伟达链
    status: 活跃·高位分歧
    activates: [{segment: C1, weight: 0.9}, {segment: B2, weight: 0.9}, {segment: B3, weight: 0.9}, {segment: B4, weight: 0.9}, {segment: B1, weight: 0.9}, {segment: B5, weight: 0.5}, {segment: C3, weight: 0.5}]
    validation: [NV财报, GTC, GB300出货]
    risks: [CPO替代, 砍单]
  - id: N2
    name: 国产算力链
    status: 主升
    activates: [{segment: A1, weight: 0.9}, {segment: A2, weight: 0.9}, {segment: A3, weight: 0.9}, {segment: A4, weight: 0.9}, {segment: A6, weight: 0.5}, {segment: B1, weight: 0.5}, {segment: C5, weight: 0.5}]
    validation: [昇腾950节点, 测评名单, 大厂订单]
    risks: [兑现斜率]
  - id: N3
    name: ASIC/TPU链
    status: 活跃
    activates: [{segment: B2, weight: 0.9}, {segment: C1, weight: 0.9}, {segment: C4, weight: 0.9}, {segment: B3, weight: 0.5}]
    validation: [谷歌TPU出货, 博通Marvell指引]
    risks: [单一客户依赖]
  - id: N4
    name: 存储超级周期
    status: 主升·业绩兑现
    activates: [{segment: A5, weight: 0.9}, {segment: A4, weight: 0.5}, {segment: A6, weight: 0.5}]
    validation: [合约价环比, 龙头月营收]
    risks: [周期见顶]
  - id: N5
    name: 推理放量
    status: Q2新叙事·扩散中
    activates: [{segment: A1, weight: 0.9}, {segment: A5, weight: 0.9}, {segment: I2, weight: 0.5}, {segment: I1, weight: 0.5}, {segment: F1, weight: 0.5}]
    validation: [token调用量, 950DT量产]
    risks: [需求增速证伪]
  - id: N6
    name: 应用商业化
    status: 回调蓄势
    activates: [{segment: F1, weight: 0.9}, {segment: F2, weight: 0.9}, {segment: F3, weight: 0.5}, {segment: F4, weight: 0.5}, {segment: M2, weight: 0.5}]
    validation: [付费数据, 专项行动落地]
    risks: [付费环境弱]
  - id: N7
    name: 端侧AI
    status: 温和
    activates: [{segment: G1, weight: 0.9}, {segment: G2, weight: 0.9}, {segment: A5, weight: 0.5}]
    validation: [新品发布, 出货量]
    risks: [出货不及预期]
  - id: N8
    name: 算电协同
    status: 政策酝酿·扩散承接
    activates: [{segment: I3, weight: 0.9}, {segment: I1, weight: 0.5}, {segment: B3, weight: 0.5}]
    validation: [绿电直连项目数, 枢纽政策]
    risks: [落地节奏]

signal_defs:
  quant: [momentum20, excess20_vs_eqw, amount_share_delta20, fundflow5, v4_pct_mean, breadth]
  text:
    stance: [多, 中, 空]
    catalyst_types: [订单, 涨价, 扩产, 技术突破, 政策, 业绩, 认证, 新品]
    global_fields: [国产化率, 份额, 技术差距, 认证]
    half_life_days: 7
    window_days: 30
```

- [ ] **Step 5: 写 `guanlan_v2/industry/framework.py`**

```python
# -*- coding: utf-8 -*-
"""行业框架文件(YAML)加载 + 校验 + 派生工具。

框架 = 唯一事实源:drivers/segments/edges/narratives/signal_defs(spec §3)。
坏引用 fail fast(FrameworkError),绝不带病服务。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

FRAMEWORKS_DIR = Path(__file__).resolve().parent / "frameworks"
DEFAULT_FRAMEWORK = FRAMEWORKS_DIR / "ai_chain.yaml"

_cache: dict = {}
_lock = threading.Lock()


class FrameworkError(Exception):
    """框架文件缺字段/坏引用。"""


def _validate(fw: dict, path: str) -> None:
    for key in ("meta", "drivers", "groups", "segments", "edges", "narratives", "signal_defs"):
        if key not in fw:
            raise FrameworkError(f"{path}: 缺顶层字段 {key}")
    sids = {s.get("id") for s in fw["segments"]}
    dids = {d.get("id") for d in fw["drivers"]}
    gids = {g.get("id") for g in fw["groups"]}
    if len(sids) != len(fw["segments"]):
        raise FrameworkError(f"{path}: segment id 重复")
    for s in fw["segments"]:
        for k in ("id", "name", "group", "logic", "keywords"):
            if k not in s:
                raise FrameworkError(f"{path}: segment {s.get('id')} 缺 {k}")
        if s["group"] not in gids:
            raise FrameworkError(f"{path}: segment {s['id']} 引用不存在的 group {s['group']}")
    for e in fw["edges"]:
        for ref in list(e.get("from", [])) + list(e.get("to", [])):
            if ref not in sids | dids:
                raise FrameworkError(f"{path}: edge {e.get('id')} 引用不存在的节点 {ref}")
    for n in fw["narratives"]:
        for a in n.get("activates", []):
            if a.get("segment") not in sids:
                raise FrameworkError(f"{path}: narrative {n.get('id')} 引用不存在的环节 {a.get('segment')}")


def load_framework(path: Optional[str] = None) -> dict:
    """加载并校验框架;带进程内缓存(mtime 失效)。"""
    import yaml  # 延迟 import(引擎 venv 自带)

    p = Path(path) if path else DEFAULT_FRAMEWORK
    key = str(p)
    mtime = p.stat().st_mtime
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    fw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(fw, dict):
        raise FrameworkError(f"{key}: 不是 YAML mapping")
    _validate(fw, key)
    with _lock:
        _cache[key] = (mtime, fw)
    return fw


def segment_ids(fw: dict) -> list:
    return [s["id"] for s in fw["segments"]]


def segment_pool(fw: dict, sid: str) -> list:
    for s in fw["segments"]:
        if s["id"] == sid:
            return [x["code"] for x in s.get("stocks", [])]
    return []


def all_pool_codes(fw: dict) -> set:
    out: set = set()
    for s in fw["segments"]:
        out |= {x["code"] for x in s.get("stocks", [])}
    return out


def framework_digest(fw: dict) -> str:
    """给 LLM 抽取 prompt 的紧凑框架摘要(环节/边/叙事 id 白名单+语义)。"""
    lines = ["【环节】(id|名称|关键词)"]
    for s in fw["segments"]:
        if s.get("adjacent"):
            continue
        lines.append(f"{s['id']}|{s['name']}|{','.join(s['keywords'][:6])}")
    lines.append("【传导边】(id|from→to|机制)")
    for e in fw["edges"]:
        lines.append(f"{e['id']}|{','.join(e['from'])}→{','.join(e['to'])}|{e['mechanism']}")
    lines.append("【叙事】(id|名称)")
    for n in fw["narratives"]:
        lines.append(f"{n['id']}|{n['name']}")
    return "\n".join(lines)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_framework.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add guanlan_v2/industry tests/test_industry_framework.py
git commit -m "feat(industry): T1 框架唯一事实源 ai_chain.yaml(7驱动/28+2环节/T15边/N8叙事·全球坐标五型)+loader校验fail-fast

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: store.py 抽取库 + 状态水位

**Files:**
- Create: `guanlan_v2/industry/store.py`
- Test: `tests/test_industry_store.py`

**Interfaces:**
- Produces: `append_extraction(rec: dict) -> None`、`load_extractions(window_days: int|None=None, now=None) -> list[dict]`(按 `publish_ts` 过滤)、`load_state() -> dict`(默认 `{"watermark": None, "failed_docs": [], "totals": {"docs": 0, "prompt_tokens": 0, "completion_tokens": 0}, "last_ingest_at": None}`)、`save_state(state: dict) -> None`(原子写)。
- 环境变量 `GL_INDUSTRY_STORE` 可覆盖 store 目录(测试用 tmp_path)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_industry_store.py
# -*- coding: utf-8 -*-
import json


def test_append_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path))
    from guanlan_v2.industry import store
    store.append_extraction({"doc_id": "d1", "publish_ts": "2026-06-30", "segments": []})
    store.append_extraction({"doc_id": "d2", "publish_ts": "2026-05-01", "segments": []})
    allrecs = store.load_extractions()
    assert [r["doc_id"] for r in allrecs] == ["d1", "d2"]
    recent = store.load_extractions(window_days=30, now="2026-07-02")
    assert [r["doc_id"] for r in recent] == ["d1"]


def test_state_roundtrip_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path))
    from guanlan_v2.industry import store
    st = store.load_state()
    assert st["watermark"] is None and st["totals"]["docs"] == 0
    st["watermark"] = "2026-07-01"
    st["totals"]["docs"] = 3
    store.save_state(st)
    again = store.load_state()
    assert again["watermark"] == "2026-07-01" and again["totals"]["docs"] == 3
    assert not list(tmp_path.glob("*.tmp"))
    json.loads((tmp_path / "ingest_state.json").read_text(encoding="utf-8"))


def test_corrupt_jsonl_line_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path))
    from guanlan_v2.industry import store
    store.append_extraction({"doc_id": "ok1", "publish_ts": "2026-06-30"})
    with open(tmp_path / "extractions.jsonl", "a", encoding="utf-8") as f:
        f.write("{broken json\n")
    store.append_extraction({"doc_id": "ok2", "publish_ts": "2026-06-30"})
    assert [r["doc_id"] for r in store.load_extractions()] == ["ok1", "ok2"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_store.py -v`
Expected: FAIL(`No module named 'guanlan_v2.industry.store'`)

- [ ] **Step 3: 写 `guanlan_v2/industry/store.py`**

```python
# -*- coding: utf-8 -*-
"""抽取库(append-only jsonl)+ ingest 状态(水位/失败清单/token 计量)。

坏行跳过不崩(chunks 表有 corrupt 历史的教训);state 原子写。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

_lock = threading.Lock()


def _store_dir() -> Path:
    d = os.environ.get("GL_INDUSTRY_STORE")
    p = Path(d) if d else Path(__file__).resolve().parent / "store"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _extractions_path() -> Path:
    return _store_dir() / "extractions.jsonl"


def _state_path() -> Path:
    return _store_dir() / "ingest_state.json"


def append_extraction(rec: dict) -> None:
    line = json.dumps(rec, ensure_ascii=False)
    with _lock:
        with open(_extractions_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")


def load_extractions(window_days: Optional[int] = None, now: Optional[str] = None) -> list:
    p = _extractions_path()
    if not p.exists():
        return []
    out = []
    cutoff = None
    if window_days is not None:
        import pandas as pd
        base = pd.Timestamp(now) if now else pd.Timestamp.now()
        cutoff = base - pd.Timedelta(days=window_days)
    for raw in p.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:  # noqa: BLE001 — 坏行跳过,诚实容错
            continue
        if cutoff is not None:
            ts = rec.get("publish_ts")
            try:
                import pandas as pd
                if ts is None or pd.Timestamp(str(ts)[:10]) < cutoff:
                    continue
            except Exception:  # noqa: BLE001
                continue
        out.append(rec)
    return out


_DEFAULT_STATE = {
    "watermark": None,
    "failed_docs": [],
    "totals": {"docs": 0, "prompt_tokens": 0, "completion_tokens": 0},
    "last_ingest_at": None,
}


def load_state() -> dict:
    p = _state_path()
    if not p.exists():
        return json.loads(json.dumps(_DEFAULT_STATE))
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — state 损坏按初始态,不崩
        return json.loads(json.dumps(_DEFAULT_STATE))
    for k, v in _DEFAULT_STATE.items():
        st.setdefault(k, json.loads(json.dumps(v)))
    return st


def save_state(state: dict) -> None:
    p = _state_path()
    tmp = p.parent / (p.name + ".tmp")     # 注意:不能用 with_suffix(会吃掉 .json)
    with _lock:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_store.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/industry/store.py tests/test_industry_store.py
git commit -m "feat(industry): T2 抽取库 append-only jsonl+状态水位原子写(坏行跳过·GL_INDUSTRY_STORE可覆盖)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: corpus.py 跨仓语料读取层

**Files:**
- Create: `guanlan_v2/industry/corpus.py`
- Test: `tests/test_industry_corpus.py`

**Interfaces:**
- Produces: `text_source_root() -> Path`(env `GL_TEXT_SOURCE_ROOT` 优先,默认 `G:/stocks/stock_data/text_source`)、`scan_new_docs(watermark, pool_codes, keywords, limit=None) -> dict`(`{ok, docs:[{doc_id, doc_type, title, org, publish_ts, text_path, stock_codes}], reason, skipped_unparsed}`,按 `publish_ts` 升序)、`read_doc_text(text_path, max_chars=20000) -> str`(超长取头 70%+尾 30%,中缝标 `…[中略]…`)、`corpus_freshness() -> dict`(`{ok, latest_publish_ts, n_docs, n_industry, reason}`)。
- 筛选规则(spec §4.2 实现口径):`doc_type == "industry_research"` 全收;`company_research` 收 `stock_codes ∩ pool` 命中 或 标题命中任一 keyword(正文级判定由 LLM 兜底——不相关研报 LLM 返回空 segments);`status != "parsed"` 或 `text_chars == 0` 跳过并计数(212 篇扫描版先例)。

- [ ] **Step 1: 写失败测试(tmp 合成 documents.parquet)**

```python
# tests/test_industry_corpus.py
# -*- coding: utf-8 -*-
import pandas as pd


def _mk_corpus(tmp_path):
    txt = tmp_path / "text" / "a.txt"
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text("EML 缺口 25-30%" + "x" * 30000, encoding="utf-8")
    df = pd.DataFrame([
        {"doc_id": "d1", "doc_type": "industry_research", "title": "光通信行业深度", "org": "某券商",
         "publish_ts": "2026-06-30", "text_path": str(txt), "stock_codes": "", "status": "parsed", "text_chars": 30015},
        {"doc_id": "d2", "doc_type": "company_research", "title": "源杰科技点评", "org": "某券商",
         "publish_ts": "2026-06-29", "text_path": str(txt), "stock_codes": "SH688498", "status": "parsed", "text_chars": 30015},
        {"doc_id": "d3", "doc_type": "company_research", "title": "某白酒公司点评", "org": "某券商",
         "publish_ts": "2026-06-28", "text_path": str(txt), "stock_codes": "SH600519", "status": "parsed", "text_chars": 30015},
        {"doc_id": "d4", "doc_type": "company_research", "title": "液冷龙头跟踪", "org": "某券商",
         "publish_ts": "2026-06-27", "text_path": str(txt), "stock_codes": "SH600000", "status": "parsed", "text_chars": 30015},
        {"doc_id": "d5", "doc_type": "industry_research", "title": "扫描版", "org": "某券商",
         "publish_ts": "2026-06-26", "text_path": str(txt), "stock_codes": "", "status": "parse_failed", "text_chars": 0},
        {"doc_id": "d0", "doc_type": "industry_research", "title": "水位前旧文", "org": "某券商",
         "publish_ts": "2026-01-01", "text_path": str(txt), "stock_codes": "", "status": "parsed", "text_chars": 30015},
    ])
    df.to_parquet(tmp_path / "documents.parquet")
    return tmp_path


def test_scan_filter_and_watermark(tmp_path, monkeypatch):
    root = _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(root))
    from guanlan_v2.industry.corpus import scan_new_docs
    r = scan_new_docs(watermark="2026-06-01", pool_codes={"SH688498"}, keywords=["液冷"])
    assert r["ok"] is True
    ids = [d["doc_id"] for d in r["docs"]]
    # d1 行业研报全收;d2 票池码命中;d4 标题关键词命中;d3 白酒不收;d5 parse_failed 跳过;d0 在水位前
    assert ids == ["d4", "d2", "d1"]          # publish_ts 升序
    assert r["skipped_unparsed"] == 1


def test_read_doc_text_truncates(tmp_path, monkeypatch):
    root = _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(root))
    from guanlan_v2.industry.corpus import read_doc_text
    txt = read_doc_text(str(root / "text" / "a.txt"), max_chars=20000)
    assert len(txt) <= 20000 + 50 and "EML 缺口 25-30%" in txt and "…[中略]…" in txt


def test_missing_root_honest(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(tmp_path / "nope"))
    from guanlan_v2.industry.corpus import scan_new_docs, corpus_freshness
    r = scan_new_docs(None, set(), [])
    assert r["ok"] is False and r["reason"]
    f = corpus_freshness()
    assert f["ok"] is False and f["reason"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_corpus.py -v`
Expected: FAIL(module 不存在)

- [ ] **Step 3: 写 `guanlan_v2/industry/corpus.py`**

```python
# -*- coding: utf-8 -*-
"""跨仓只读 G:\\stocks text_source(研报已解析库)。

env GL_TEXT_SOURCE_ROOT 可覆盖(仿 GL_F10_ROOT 先例);一切失败诚实 ok:False。
PIT 红线:只按 publish_ts 增量,不回改历史。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

_DEFAULT_ROOT = r"G:/stocks/stock_data/text_source"


def text_source_root() -> Path:
    return Path(os.environ.get("GL_TEXT_SOURCE_ROOT") or _DEFAULT_ROOT)


def _load_documents():
    import pandas as pd
    p = text_source_root() / "documents.parquet"
    if not p.exists():
        raise FileNotFoundError(f"documents.parquet 不存在: {p}")
    return pd.read_parquet(p)


def scan_new_docs(watermark: Optional[str], pool_codes: set, keywords: Iterable[str],
                  limit: Optional[int] = None) -> dict:
    try:
        df = _load_documents()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "docs": [], "reason": f"语料库不可读: {exc}", "skipped_unparsed": 0}
    try:
        df = df.copy()
        df["publish_ts"] = df["publish_ts"].astype(str).str[:10]
        if watermark:
            df = df[df["publish_ts"] > str(watermark)[:10]]
        unparsed = df[(df.get("status") != "parsed") | (df.get("text_chars", 0) == 0)]
        df = df.drop(index=unparsed.index)
        kws = [k for k in (keywords or []) if k]

        def _hit(row) -> bool:
            if row.get("doc_type") == "industry_research":
                return True
            codes = {c.strip() for c in str(row.get("stock_codes") or "").replace(";", ",").split(",") if c.strip()}
            if codes & pool_codes:
                return True
            title = str(row.get("title") or "")
            return any(k in title for k in kws)

        keep = df[df.apply(_hit, axis=1)].sort_values("publish_ts")
        if limit:
            keep = keep.head(int(limit))
        cols = ["doc_id", "doc_type", "title", "org", "publish_ts", "text_path", "stock_codes"]
        docs = []
        for _, row in keep.iterrows():
            docs.append({c: (None if c not in row or row[c] is None else str(row[c])) for c in cols})
        return {"ok": True, "docs": docs, "reason": None, "skipped_unparsed": int(len(unparsed))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "docs": [], "reason": f"扫描失败: {exc}", "skipped_unparsed": 0}


def read_doc_text(text_path: str, max_chars: int = 20000) -> str:
    txt = Path(text_path).read_text(encoding="utf-8", errors="replace")
    if len(txt) <= max_chars:
        return txt
    head = int(max_chars * 0.7)
    tail = max_chars - head
    return txt[:head] + "\n…[中略]…\n" + txt[-tail:]


def corpus_freshness() -> dict:
    try:
        df = _load_documents()
        ts = df["publish_ts"].astype(str).str[:10]
        n_ind = int((df.get("doc_type") == "industry_research").sum())
        return {"ok": True, "latest_publish_ts": str(ts.max()), "n_docs": int(len(df)),
                "n_industry": n_ind, "reason": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latest_publish_ts": None, "n_docs": None, "n_industry": None,
                "reason": f"语料库不可读: {exc}"}
```

**实现前必核**:真实 `documents.parquet` 的列名(`doc_id/doc_type/title/org/publish_ts/text_path/stock_codes/status/text_chars`——取证时确认存在 `pdf_path/text_path/stock_codes/industries/publish_ts/visible_ts/parser/status`,若 `title/org/text_chars/doc_id` 名称不同,以 `python -c "import pandas as pd; print(pd.read_parquet(r'G:/stocks/stock_data/text_source/documents.parquet').columns.tolist())"` 实测为准,在 `_load_documents()` 里 rename 成本模块约定列名,测试合成数据即为约定形状)。

- [ ] **Step 4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_corpus.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/industry/corpus.py tests/test_industry_corpus.py
git commit -m "feat(industry): T3 跨仓语料读取层(行业研报全收+个股票池∪标题词·水位增量·parse_failed跳过计数·断供诚实)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: llmx.py DeepSeek 抽取 + 校验

**Files:**
- Create: `guanlan_v2/industry/llmx.py`
- Test: `tests/test_industry_llmx.py`

**Interfaces:**
- Consumes: `framework_digest(fw)`(Task 1)。
- Produces: `async extract_one(doc: dict, text: str, fw: dict, client=None, timeout=90.0) -> dict`——成功 `{ok: True, extraction: {...}, model, prompt_tokens, completion_tokens}`;失败 `{ok: False, reason}`。`client` 可注入(测试 fake;None 时函数内建引擎 LLMClient)。`validate_extraction(raw: dict, fw: dict, text: str) -> dict`。`_norm_code(c: str) -> str|None`(供 Task 10 复用)。
- extraction 落库记录形状(后续任务依赖,字段名精确):

```json
{"doc_id": "d1", "title": "…", "org": "…", "publish_ts": "2026-06-30", "doc_type": "industry_research",
 "extracted_at": "2026-07-02T18:00:00", "model": "deepseek-chat",
 "segments": [{"segment_id": "C2", "stance": "多", "strength": 2, "quote": "原文子串或null", "quote_dropped": false}],
 "catalysts": [{"type": "涨价", "desc": "…", "date_hint": "2026-06"}],
 "edges": [{"edge_id": "T4", "verdict": "支持", "evidence": "…"}],
 "narratives": [{"narrative_id": "N4", "stance": "多"}],
 "global_updates": [{"segment_id": "C2", "field": "国产化率", "content": "…"}],
 "stocks": [{"code": "SH688498", "stance": "多", "logic": "…"}]}
```

- [ ] **Step 1: 写失败测试(fake client,不打网络)**

```python
# tests/test_industry_llmx.py
# -*- coding: utf-8 -*-
import asyncio
import json


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.model = "deepseek-chat"
        self.prompt_tokens = 100
        self.completion_tokens = 50


class _FakeClient:
    def __init__(self, payload=None, raise_exc=None):
        self._payload = payload
        self._exc = raise_exc

    async def chat(self, messages, **kw):
        if self._exc:
            raise self._exc
        return _FakeResp(json.dumps(self._payload, ensure_ascii=False))


def _doc():
    return {"doc_id": "d1", "title": "光芯片深度", "org": "某券商",
            "publish_ts": "2026-06-30", "doc_type": "industry_research"}


def test_extract_ok_and_validation():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.llmx import extract_one
    fw = load_framework()
    text = "报告指出 EML 缺口 25-30%,订单排至 2027 年。"
    payload = {
        "segments": [
            {"segment_id": "C2", "stance": "多", "strength": 3, "quote": "EML 缺口 25-30%"},
            {"segment_id": "ZZ9", "stance": "多", "strength": 1, "quote": "x"},
            {"segment_id": "C1", "stance": "多", "strength": 2, "quote": "编造的引句"},
        ],
        "catalysts": [{"type": "涨价", "desc": "EML涨价", "date_hint": "2026-06"}],
        "edges": [{"edge_id": "T4", "verdict": "支持", "evidence": "缺口涨价"},
                  {"edge_id": "T99", "verdict": "支持", "evidence": "x"}],
        "narratives": [{"narrative_id": "N4", "stance": "多"}],
        "global_updates": [{"segment_id": "C2", "field": "国产化率", "content": "良率接近海外"}],
        "stocks": [{"code": "688498.SH", "stance": "多", "logic": "量产爬坡"}],
    }
    r = asyncio.run(extract_one(_doc(), text, fw, client=_FakeClient(payload)))
    assert r["ok"] is True
    ex = r["extraction"]
    segs = {s["segment_id"]: s for s in ex["segments"]}
    assert set(segs) == {"C2", "C1"}
    assert segs["C2"]["quote"] == "EML 缺口 25-30%" and segs["C2"]["quote_dropped"] is False
    assert segs["C1"]["quote"] is None and segs["C1"]["quote_dropped"] is True
    assert [e["edge_id"] for e in ex["edges"]] == ["T4"]
    assert ex["stocks"][0]["code"] == "SH688498"          # 码式归一
    assert ex["doc_id"] == "d1" and ex["extracted_at"]


def test_extract_llm_failure_honest():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.llmx import extract_one
    fw = load_framework()
    r = asyncio.run(extract_one(_doc(), "文", fw, client=_FakeClient(raise_exc=RuntimeError("boom"))))
    assert r["ok"] is False and "boom" in r["reason"]


def test_extract_bad_json_honest():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.llmx import extract_one

    class _BadClient(_FakeClient):
        async def chat(self, messages, **kw):
            return _FakeResp("这不是JSON")

    fw = load_framework()
    r = asyncio.run(extract_one(_doc(), "文", fw, client=_BadClient()))
    assert r["ok"] is False and "JSON" in r["reason"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_llmx.py -v`
Expected: FAIL(module 不存在)

- [ ] **Step 3: 写 `guanlan_v2/industry/llmx.py`**

**实现前必核**:打开 `guanlan_v2/screen/llm.py`(75-101 行 `_call_llm_json`)确认引擎 `LLMClient.for_agent` 签名与 `chat()` 返回对象属性名(`content/model/prompt_tokens/completion_tokens`);若实际不同(如 `resp.text`),以引擎为准同步改本文件与测试 fake client。

```python
# -*- coding: utf-8 -*-
"""DeepSeek 逐篇研报结构化抽取(照 screen/llm.py 模式)。

红线:显式 config_path=仓内 llm.yaml;json_object;真失败 ok:False 绝不伪造;
quote 必须是原文子串,不是则降级 quote=None+quote_dropped 标注。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LLM_CONFIG = _REPO_ROOT / "config" / "llm.yaml"

_STANCES = {"多", "中", "空"}
_CATALYSTS = {"订单", "涨价", "扩产", "技术突破", "政策", "业绩", "认证", "新品"}
_GLOBAL_FIELDS = {"国产化率", "份额", "技术差距", "认证"}
_VERDICTS = {"支持", "否证"}

_SYSTEM = (
    "你是 A 股行业研究抽取器。只依据给定研报原文抽取,禁止编造原文没有的数字/事件。"
    "只输出 JSON(无多余文字)。所有 id 必须取自给定框架白名单;quote 字段必须是原文的连续子串;"
    "研报与 AI 产业链无关时输出 {\"segments\": []}。"
)


def _norm_code(c: str) -> Optional[str]:
    c = (c or "").strip().upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    m = re.match(r"^(SH|SZ|BJ)?(\d{6})$", c)
    if not m:
        return None
    pre, num = m.group(1), m.group(2)
    if pre:
        return pre + num
    if num.startswith(("6", "9")):
        return "SH" + num
    if num.startswith(("0", "3")):
        return "SZ" + num
    return "BJ" + num


def _prompt(doc: dict, text: str, digest: str) -> str:
    schema = {
        "segments": [{"segment_id": "C2", "stance": "多|中|空", "strength": 1, "quote": "原文子串"}],
        "catalysts": [{"type": "订单|涨价|扩产|技术突破|政策|业绩|认证|新品", "desc": "一句话", "date_hint": "可空"}],
        "edges": [{"edge_id": "T4", "verdict": "支持|否证", "evidence": "一句话"}],
        "narratives": [{"narrative_id": "N4", "stance": "多|中|空"}],
        "global_updates": [{"segment_id": "C2", "field": "国产化率|份额|技术差距|认证", "content": "一句话"}],
        "stocks": [{"code": "SH688498", "stance": "多|中|空", "logic": "一句话"}],
    }
    return (
        f"## 框架白名单\n{digest}\n\n"
        f"## 研报元数据\n标题: {doc.get('title')}\n机构: {doc.get('org')}\n日期: {doc.get('publish_ts')}\n\n"
        f"## 研报原文\n{text}\n\n"
        f"## 输出 JSON 形状(字段名精确一致,列表可为空)\n{json.dumps(schema, ensure_ascii=False)}"
    )


def validate_extraction(raw: dict, fw: dict, text: str) -> dict:
    sids = {s["id"] for s in fw["segments"]}
    eids = {e["id"] for e in fw["edges"]}
    nids = {n["id"] for n in fw["narratives"]}
    out: dict = {"segments": [], "catalysts": [], "edges": [], "narratives": [], "global_updates": [], "stocks": []}
    for s in raw.get("segments") or []:
        sid = s.get("segment_id")
        if sid not in sids or s.get("stance") not in _STANCES:
            continue
        try:
            strength = max(1, min(3, int(s.get("strength") or 1)))
        except Exception:  # noqa: BLE001
            strength = 1
        quote = s.get("quote")
        dropped = False
        if not (isinstance(quote, str) and quote and quote in text):
            quote, dropped = None, True
        out["segments"].append({"segment_id": sid, "stance": s["stance"], "strength": strength,
                                "quote": quote, "quote_dropped": dropped})
    for c in raw.get("catalysts") or []:
        if c.get("type") in _CATALYSTS and c.get("desc"):
            out["catalysts"].append({"type": c["type"], "desc": str(c["desc"]), "date_hint": c.get("date_hint")})
    for e in raw.get("edges") or []:
        if e.get("edge_id") in eids and e.get("verdict") in _VERDICTS:
            out["edges"].append({"edge_id": e["edge_id"], "verdict": e["verdict"], "evidence": str(e.get("evidence") or "")})
    for n in raw.get("narratives") or []:
        if n.get("narrative_id") in nids and n.get("stance") in _STANCES:
            out["narratives"].append({"narrative_id": n["narrative_id"], "stance": n["stance"]})
    for g in raw.get("global_updates") or []:
        if g.get("segment_id") in sids and g.get("field") in _GLOBAL_FIELDS and g.get("content"):
            out["global_updates"].append({"segment_id": g["segment_id"], "field": g["field"], "content": str(g["content"])})
    for st in raw.get("stocks") or []:
        code = _norm_code(st.get("code") or "")
        if code and st.get("stance") in _STANCES:
            out["stocks"].append({"code": code, "stance": st["stance"], "logic": str(st.get("logic") or "")})
    return out


async def extract_one(doc: dict, text: str, fw: dict, client=None, timeout: float = 90.0) -> dict:
    from .framework import framework_digest
    if client is None:
        from financial_analyst.llm.client import LLMClient  # 延迟 import
        client = LLMClient.for_agent("industry_extract", config_path=str(_LLM_CONFIG))
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _prompt(doc, text, framework_digest(fw))}]
    try:
        resp = await asyncio.wait_for(
            client.chat(messages, response_format={"type": "json_object"}, temperature=0.1),
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — 真失败诚实
        return {"ok": False, "reason": f"LLM 调用失败: {exc}"}
    content = getattr(resp, "content", None) or ""
    try:
        raw = json.loads(content)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", content)
        if not m:
            return {"ok": False, "reason": "LLM 返回非 JSON"}
        try:
            raw = json.loads(m.group(0))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"LLM JSON 解析失败: {exc}"}
    import pandas as pd
    ex = validate_extraction(raw, fw, text)
    ex.update({
        "doc_id": doc.get("doc_id"), "title": doc.get("title"), "org": doc.get("org"),
        "publish_ts": doc.get("publish_ts"), "doc_type": doc.get("doc_type"),
        "extracted_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "model": getattr(resp, "model", None),
    })
    return {"ok": True, "extraction": ex, "model": getattr(resp, "model", None),
            "prompt_tokens": getattr(resp, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(resp, "completion_tokens", 0) or 0}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_llmx.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/industry/llmx.py tests/test_industry_llmx.py
git commit -m "feat(industry): T4 DeepSeek逐篇抽取(白名单id过滤·quote原文子串校验降级·码式归一·失败ok:False不伪造)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: ingest.py 编排(单飞+后台线程+水位)

**Files:**
- Create: `guanlan_v2/industry/ingest.py`
- Test: `tests/test_industry_ingest.py`

**Interfaces:**
- Consumes: Task 1-4 全部接口。
- Produces: `start_ingest(limit=None, client=None) -> dict`(`{ok, accepted, running, reason}`;单飞:已在跑返回 `accepted:False, running:True`)、`ingest_state() -> dict`(state + `running` + `progress {done, total}`)。
- 水位规则:批内**全部成功**才把 `watermark` 推到本批最大 `publish_ts`;有失败则水位不动、失败 doc_id 进 `failed_docs`(带 reason),下次重跑自动覆盖(成功篇目重复抽取的去重靠 doc_id——聚合端 `load_extractions` 后按 doc_id 保留 `extracted_at` 最新一条,Task 7 实现)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_industry_ingest.py
# -*- coding: utf-8 -*-
import json
import time

import pandas as pd


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.model = "deepseek-chat"
        self.prompt_tokens = 10
        self.completion_tokens = 5


class _OkClient:
    async def chat(self, messages, **kw):
        return _FakeResp(json.dumps(
            {"segments": [{"segment_id": "C2", "stance": "多", "strength": 1, "quote": None}]},
            ensure_ascii=False))


class _FailOnD2Client(_OkClient):
    async def chat(self, messages, **kw):
        joined = "".join(m["content"] for m in messages)
        if "标题D2" in joined:
            raise RuntimeError("boom-d2")
        return await super().chat(messages, **kw)


def _mk_corpus(tmp_path, n=2):
    txt = tmp_path / "t.txt"
    txt.write_text("正文", encoding="utf-8")
    rows = []
    for i in range(1, n + 1):
        rows.append({"doc_id": f"d{i}", "doc_type": "industry_research", "title": f"标题D{i}", "org": "x",
                     "publish_ts": f"2026-06-2{i}", "text_path": str(txt), "stock_codes": "",
                     "status": "parsed", "text_chars": 2})
    pd.DataFrame(rows).to_parquet(tmp_path / "documents.parquet")


def _wait_done(mod, timeout=10):
    for _ in range(int(timeout * 20)):
        if not mod.ingest_state()["running"]:
            return
        time.sleep(0.05)
    raise AssertionError("ingest 未在时限内结束")


def test_ingest_ok_advances_watermark(tmp_path, monkeypatch):
    _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import ingest, store
    r = ingest.start_ingest(client=_OkClient())
    assert r["ok"] and r["accepted"]
    _wait_done(ingest)
    st = store.load_state()
    assert st["watermark"] == "2026-06-22" and st["totals"]["docs"] == 2
    assert len(store.load_extractions()) == 2
    assert st["failed_docs"] == []


def test_ingest_partial_failure_keeps_watermark(tmp_path, monkeypatch):
    _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import ingest, store
    ingest.start_ingest(client=_FailOnD2Client())
    _wait_done(ingest)
    st = store.load_state()
    assert st["watermark"] is None                      # 有失败,水位不动
    assert [f["doc_id"] for f in st["failed_docs"]] == ["d2"]
    assert len(store.load_extractions()) == 1           # d1 成功已落库


def test_ingest_single_flight(tmp_path, monkeypatch):
    _mk_corpus(tmp_path, n=1)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import ingest

    class _SlowClient(_OkClient):
        async def chat(self, messages, **kw):
            import asyncio
            await asyncio.sleep(0.4)
            return await super().chat(messages, **kw)

    r1 = ingest.start_ingest(client=_SlowClient())
    r2 = ingest.start_ingest(client=_SlowClient())
    assert r1["accepted"] is True
    assert r2["accepted"] is False and r2["running"] is True
    _wait_done(ingest)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_ingest.py -v`
Expected: FAIL(module 不存在)

- [ ] **Step 3: 写 `guanlan_v2/industry/ingest.py`**

```python
# -*- coding: utf-8 -*-
"""手动增量批处理编排:扫描→抽取→落库→水位。

单飞 = 进程内互斥(threading.Lock + running 标志;9999 单进程,毋需跨进程锁);
后台 daemon 线程内 asyncio.run + Semaphore(3);全部成功才推水位。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional

_run_lock = threading.Lock()
_running = False
_progress = {"done": 0, "total": 0}


def _extract_keywords(fw: dict) -> list:
    kws: list = []
    for s in fw["segments"]:
        kws.extend(s.get("keywords", []))
    return kws


async def _run_batch(docs: list, fw: dict, client) -> dict:
    from . import corpus, llmx, store
    sem = asyncio.Semaphore(3)
    totals = {"n_ok": 0, "n_fail": 0, "prompt_tokens": 0, "completion_tokens": 0, "failed": []}

    async def _one(doc: dict):
        async with sem:
            try:
                text = await asyncio.to_thread(corpus.read_doc_text, doc["text_path"])
            except Exception as exc:  # noqa: BLE001
                totals["n_fail"] += 1
                totals["failed"].append({"doc_id": doc["doc_id"], "reason": f"读文失败: {exc}"})
                _progress["done"] += 1
                return
            r = await llmx.extract_one(doc, text, fw, client=client)
            if r.get("ok"):
                await asyncio.to_thread(store.append_extraction, r["extraction"])
                totals["n_ok"] += 1
                totals["prompt_tokens"] += r.get("prompt_tokens", 0)
                totals["completion_tokens"] += r.get("completion_tokens", 0)
            else:
                totals["n_fail"] += 1
                totals["failed"].append({"doc_id": doc["doc_id"], "reason": r.get("reason")})
            _progress["done"] += 1

    await asyncio.gather(*(_one(d) for d in docs))
    return totals


def _worker(limit: Optional[int], client) -> None:
    global _running, _progress
    from . import corpus, store
    from .framework import all_pool_codes, load_framework
    try:
        import pandas as pd
        fw = load_framework()
        st = store.load_state()
        scan = corpus.scan_new_docs(st.get("watermark"), all_pool_codes(fw), _extract_keywords(fw), limit=limit)
        if not scan["ok"]:
            st["failed_docs"] = [{"doc_id": None, "reason": scan["reason"]}]
            st["last_ingest_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
            store.save_state(st)
            return
        docs = scan["docs"]
        _progress = {"done": 0, "total": len(docs)}
        if not docs:
            st["last_ingest_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
            store.save_state(st)
            return
        totals = asyncio.run(_run_batch(docs, fw, client))
        st = store.load_state()
        st["failed_docs"] = totals["failed"]
        st["totals"]["docs"] += totals["n_ok"]
        st["totals"]["prompt_tokens"] += totals["prompt_tokens"]
        st["totals"]["completion_tokens"] += totals["completion_tokens"]
        if totals["n_fail"] == 0 and docs:
            st["watermark"] = max(d["publish_ts"] for d in docs)
        st["last_ingest_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
        store.save_state(st)
    finally:
        _running = False


def start_ingest(limit: Optional[int] = None, client=None) -> dict:
    global _running
    with _run_lock:
        if _running:
            return {"ok": True, "accepted": False, "running": True, "reason": "已有批处理在跑(单飞)"}
        _running = True
    t = threading.Thread(target=_worker, args=(limit, client), daemon=True, name="industry-ingest")
    t.start()
    return {"ok": True, "accepted": True, "running": True, "reason": None}


def ingest_state() -> dict:
    from . import store
    st = store.load_state()
    st["running"] = _running
    st["progress"] = dict(_progress)
    return st
```

- [ ] **Step 4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_ingest.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/industry/ingest.py tests/test_industry_ingest.py
git commit -m "feat(industry): T5 手动增量批处理编排(单飞·后台线程·Semaphore(3)·全成才推水位·失败清单可重跑)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: aggregate.py 量化侧信号

**Files:**
- Create: `guanlan_v2/industry/aggregate.py`(本任务只做量化侧;文本侧 Task 7 追加)
- Test: `tests/test_industry_aggregate.py`

**Interfaces:**
- Consumes: `load_framework`(T1)。
- Produces: `quant_signals(fw, quotes=None) -> dict`——`{sid: {"momentum20": float|None, "excess20": float|None, "amount_share_delta20": float|None, "fundflow5": float|None, "v4_pct_mean": float|None, "breadth": float|None, "quote_date": str|None, "reason": str|None}}`;`quotes` 可注入 `{code: DataFrame(trade_date, close, amount)}`(测试免引擎);真数据 `_fetch_quotes(codes, days=45)` 用 `loader.fetch_quote(code, start, end, "day")` 逐票取、单票失败跳过(照 `guanlan_v2/seats/api.py:784-812` 先例)。v4 分位读 `guanlan_v2.strategy.paths.V4_RANKING_PARQUET`,基准读 `EQW_MARKET_RET_PARQUET`;资金流 `fundflow5` = 池内个股近5日主力净流入合计,读 `<GL_PARQUET_ROOT|G:/stocks/stock_data/parquet>/stock_fund_flow_daily.parquet`(列名以实测为准,helper `_fundflow_map()` 内 rename;文件缺/列不识 → 全部 None + reason)。产物缺 → 字段 None + reason。
- 口径(spec §3.6):`momentum20 = 票池各票 close[-1]/close[-21]-1 等权均值`;`excess20 = momentum20 − eqw 近20日累计收益`;`amount_share_delta20 = 池内 amount 5日均/20日均 − 1`(v1 简化);`breadth = 池内动量>0 家数占比`;`v4_pct_mean = 池内 v4 pct 均值`。

- [ ] **Step 1: 写失败测试(注入合成 quotes,不碰引擎)**

```python
# tests/test_industry_aggregate.py
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd


def _quotes_for(codes, trend=0.02, days=45):
    out = {}
    dates = pd.date_range("2026-05-01", periods=days, freq="B")
    for i, c in enumerate(codes):
        base = 10 + i
        close = base * (1 + trend) ** np.arange(days)
        out[c] = pd.DataFrame({"trade_date": dates.strftime("%Y-%m-%d"), "close": close,
                               "amount": np.full(days, 1e8)})
    return out


def test_quant_signals_with_injected_quotes():
    from guanlan_v2.industry.framework import load_framework, segment_pool
    from guanlan_v2.industry.aggregate import quant_signals
    fw = load_framework()
    pool = segment_pool(fw, "C2")
    quotes = _quotes_for(pool)
    sig = quant_signals(fw, quotes=quotes)
    c2 = sig["C2"]
    assert c2["momentum20"] is not None and c2["momentum20"] > 0.3   # 每日+2%,20日≈+48.6%
    assert c2["breadth"] == 1.0
    assert c2["quote_date"] == quotes[pool[0]]["trade_date"].iloc[-1]
    assert ("v4_pct_mean" in c2) and ("excess20" in c2)   # 产物缺→None+reason 而非崩


def test_adjacent_stub_excluded():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.aggregate import quant_signals
    fw = load_framework()
    sig = quant_signals(fw, quotes={})
    assert "G3" not in sig and "G4" not in sig


def test_empty_quotes_honest():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.aggregate import quant_signals
    fw = load_framework()
    sig = quant_signals(fw, quotes={})
    assert sig["C2"]["momentum20"] is None and sig["C2"]["reason"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_aggregate.py -v`
Expected: FAIL(module 不存在)

- [ ] **Step 3: 写 `guanlan_v2/industry/aggregate.py`(量化侧)**

```python
# -*- coding: utf-8 -*-
"""环节聚合:量化侧(Task 7 追加文本侧与 board 组装)。

一切产物缺失 → 字段 None + reason,绝不静默补零(诚实红线)。
"""
from __future__ import annotations

from typing import Optional


def _fetch_quotes(codes: list, days: int = 45) -> dict:
    """真数据路径:引擎 loader 逐票取(照 seats/api.py:784-812 先例)。单票失败跳过。"""
    out: dict = {}
    try:
        import pandas as pd
        from financial_analyst.data import loader_factory as _lf
        loader = _lf.get_default_loader()
        end = str(pd.Timestamp.now().date())
        start = str((pd.Timestamp.now() - pd.Timedelta(days=days + 30)).date())
        for c in codes:
            try:
                df = loader.fetch_quote(c, start, end, "day")
                if df is not None and len(df) and "close" in df.columns:
                    out[c] = df
            except Exception:  # noqa: BLE001 — 单票失败=该票缺
                continue
    except Exception:  # noqa: BLE001 — loader 整体失败=全缺
        return {}
    return out


def _v4_pct_map() -> Optional[dict]:
    try:
        import pandas as pd
        from guanlan_v2.strategy.paths import V4_RANKING_PARQUET
        df = pd.read_parquet(V4_RANKING_PARQUET)
        codecol = "code" if "code" in df.columns else ("ts_code" if "ts_code" in df.columns else None)
        pctcol = "pct" if "pct" in df.columns else None
        if not codecol or not pctcol:
            return None
        return dict(zip(df[codecol].astype(str), df[pctcol]))
    except Exception:  # noqa: BLE001
        return None


def _fundflow_map() -> Optional[dict]:
    """近5日主力净流入 {code: 合计};文件缺/列不识 → None(诚实降级)。列名以实测 rename。"""
    try:
        import os
        import pandas as pd
        from pathlib import Path
        root = Path(os.environ.get("GL_PARQUET_ROOT") or r"G:/stocks/stock_data/parquet")
        p = root / "stock_fund_flow_daily.parquet"
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        # 实现时实测列名后 rename 成 code/date/main_net;不识则返回 None
        cols = {c.lower(): c for c in df.columns}
        codec = cols.get("code") or cols.get("ts_code") or cols.get("stock_code")
        datec = cols.get("date") or cols.get("trade_date")
        netc = cols.get("main_net") or cols.get("main_net_inflow") or cols.get("主力净流入")
        if not (codec and datec and netc):
            return None
        df = df.rename(columns={codec: "code", datec: "date", netc: "main_net"})
        df["date"] = df["date"].astype(str).str[:10]
        last5 = sorted(df["date"].unique())[-5:]
        sub = df[df["date"].isin(last5)]
        return sub.groupby("code")["main_net"].sum().to_dict()
    except Exception:  # noqa: BLE001
        return None


def _eqw_ret20() -> Optional[float]:
    try:
        import pandas as pd
        from guanlan_v2.strategy.paths import EQW_MARKET_RET_PARQUET
        df = pd.read_parquet(EQW_MARKET_RET_PARQUET)
        retcol = "ret" if "ret" in df.columns else df.columns[-1]
        r = df[retcol].astype(float).tail(20)
        if len(r) < 20:
            return None
        return float((1 + r).prod() - 1)
    except Exception:  # noqa: BLE001
        return None


def quant_signals(fw: dict, quotes: Optional[dict] = None) -> dict:
    import numpy as np

    all_codes = sorted({x["code"] for s in fw["segments"] if not s.get("adjacent") for x in s.get("stocks", [])})
    if quotes is None:
        quotes = _fetch_quotes(all_codes)
    v4map = _v4_pct_map()
    eqw20 = _eqw_ret20()
    ffmap = _fundflow_map()

    out: dict = {}
    for s in fw["segments"]:
        if s.get("adjacent"):
            continue
        codes = [x["code"] for x in s.get("stocks", [])]
        moms, amts5, amts20, v4s = [], [], [], []
        qdate = None
        for c in codes:
            df = quotes.get(c)
            if df is None or len(df) < 21:
                continue
            close = df["close"].astype(float).to_numpy()
            moms.append(close[-1] / close[-21] - 1.0)
            if "amount" in df.columns:
                amt = df["amount"].astype(float).to_numpy()
                if len(amt) >= 20:
                    amts5.append(float(amt[-5:].mean()))
                    amts20.append(float(amt[-20:].mean()))
            if "trade_date" in df.columns:
                qdate = max(qdate or "", str(df["trade_date"].iloc[-1])[:10])
            if v4map:
                hit = v4map.get(c) or v4map.get(c[2:]) or v4map.get(f"{c[2:]}.{c[:2]}")
                if hit is not None:
                    v4s.append(float(hit))
        if not moms:
            out[s["id"]] = {"momentum20": None, "excess20": None, "amount_share_delta20": None,
                            "fundflow5": None, "v4_pct_mean": None, "breadth": None, "quote_date": None,
                            "reason": "票池行情不可得"}
            continue
        mom = float(np.mean(moms))
        ff = None
        if ffmap:
            hits = [ffmap.get(c) or ffmap.get(c[2:]) for c in codes]
            hits = [h for h in hits if h is not None]
            ff = float(np.sum(hits)) if hits else None
        out[s["id"]] = {
            "momentum20": mom,
            "excess20": (mom - eqw20) if eqw20 is not None else None,
            "amount_share_delta20": (float(np.sum(amts5) / np.sum(amts20)) - 1.0) if amts20 and np.sum(amts20) > 0 else None,
            "fundflow5": ff,
            "v4_pct_mean": (float(np.mean(v4s)) if v4s else None),
            "breadth": float(np.mean([1.0 if m > 0 else 0.0 for m in moms])),
            "quote_date": qdate,
            "reason": None if (eqw20 is not None and v4map and ffmap) else "部分产物缺失(eqw/v4/资金流)→对应字段null",
        }
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_aggregate.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/industry/aggregate.py tests/test_industry_aggregate.py
git commit -m "feat(industry): T6 量化侧环节信号(动量/超额vs eqw/量能占比/v4分位/广度·quotes可注入·产物缺失null显形)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 文本侧聚合 + board 组装 + api.py 五端点

**Files:**
- Modify: `guanlan_v2/industry/aggregate.py`(文件尾部追加)
- Create: `guanlan_v2/industry/api.py`
- Test: `tests/test_industry_api.py`

**Interfaces:**
- Produces(aggregate.py 追加):
  - `research_signals(fw, extractions, now=None) -> dict`——`{sid: {"score", "n30", "bull", "bear", "neutral", "disagreement"}}`;`score = Σ stance值(多=1,中=0,空=-1) × strength × 0.5^(age_days/7)`(半衰 7 天,窗 30 天);`disagreement = stance 值方差`(n≥2 才有,否则 None)。**同 doc_id 多条记录只保留 extracted_at 最新一条再聚合(重跑去重)。**
  - `edge_verdicts(fw, extractions, now=None) -> dict`——`{eid: {"support", "refute"}}`(30 天窗)。
  - `narrative_temps(fw, qsig, extractions, now=None) -> list`——`[{id, name, status, temp, plus7, minus7}]`;`temp = 100 × Σ(wᵢ × rankpct(momentum20ᵢ)) / Σwᵢ`(rankpct=该环节动量在 28 环节中的分位,缺行情的环节不参与;全缺 → temp=None);`plus7/minus7` = 近 7 天该叙事多/空篇数。
  - `quadrant(q, r, rankpct) -> str`——`"hh"|"hl"|"lh"|"ll"`:行情热 = rankpct≥0.5(缺失算冷);研报热 = score>0。
  - `build_board(refresh=False) -> dict`、`segment_detail(sid) -> dict`、`doc_detail(doc_id) -> dict`(形状见下面测试)。
- Produces(api.py):`build_industry_router() -> APIRouter`:`GET /industry/board?refresh=`、`POST /industry/ingest`(body `{"limit": N}` 可选)、`GET /industry/ingest_state`、`GET /industry/segment/{sid}`、`GET /industry/doc/{doc_id}`。全部 `asyncio.to_thread`;失败 `{ok:False, reason}` HTTP 200。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_industry_api.py
# -*- coding: utf-8 -*-
import json

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app():
    from guanlan_v2.industry import build_industry_router
    app = FastAPI()
    app.include_router(build_industry_router())
    return TestClient(app)


def _seed_store(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(tmp_path / "nope"))  # 语料断供→徽章显形
    from guanlan_v2.industry import store
    now = pd.Timestamp.now()
    store.append_extraction({
        "doc_id": "dX", "title": "光芯片深度", "org": "某券商",
        "publish_ts": str((now - pd.Timedelta(days=2)).date()), "doc_type": "industry_research",
        "extracted_at": now.isoformat(timespec="seconds"), "model": "deepseek-chat",
        "segments": [{"segment_id": "C2", "stance": "多", "strength": 3, "quote": "EML缺口", "quote_dropped": False}],
        "catalysts": [{"type": "涨价", "desc": "EML涨价", "date_hint": None}],
        "edges": [{"edge_id": "T4", "verdict": "支持", "evidence": "缺口"}],
        "narratives": [{"narrative_id": "N4", "stance": "多"}],
        "global_updates": [], "stocks": [{"code": "SH688498", "stance": "多", "logic": "量产"}],
    })


def test_board_shape_and_honesty(tmp_path, monkeypatch):
    _seed_store(tmp_path, monkeypatch)
    c = _app()
    r = c.get("/industry/board", params={"refresh": 1}).json()
    assert r["ok"] is True
    assert len(r["drivers"]) == 7 and len(r["narratives"]) == 8 and len(r["edges"]) == 15
    segs = {s["id"]: s for s in r["segments"]}
    assert len(segs) == 30 and segs["G3"]["adjacent"] is True
    c2 = segs["C2"]
    assert c2["research"]["n30"] == 1 and c2["research"]["bull"] == 1 and c2["research"]["score"] > 0
    assert c2["quadrant"] in ("hh", "hl", "lh", "ll")
    edge = {e["id"]: e for e in r["edges"]}["T4"]
    assert edge["verdict_counts"]["support"] == 1
    assert r["freshness"]["corpus"]["ok"] is False        # 语料断供诚实显形
    assert r["freshness"]["extracted_total"] == 0          # state.totals 未走 ingest,不冒充


def test_segment_and_doc_detail(tmp_path, monkeypatch):
    _seed_store(tmp_path, monkeypatch)
    c = _app()
    seg = c.get("/industry/segment/C2").json()
    assert seg["ok"] and seg["segment"]["id"] == "C2"
    assert seg["opinions"][0]["doc_id"] == "dX" and seg["opinions"][0]["quote"] == "EML缺口"
    doc = c.get("/industry/doc/dX").json()
    assert doc["ok"] and doc["extraction"]["title"] == "光芯片深度"
    miss = c.get("/industry/doc/nope").json()
    assert miss["ok"] is False and miss["reason"]
    bad = c.get("/industry/segment/ZZ9").json()
    assert bad["ok"] is False


def test_ingest_endpoints(tmp_path, monkeypatch):
    _seed_store(tmp_path, monkeypatch)
    c = _app()
    st = c.get("/industry/ingest_state").json()
    assert "watermark" in st and "running" in st
    r = c.post("/industry/ingest", content=json.dumps({"limit": 1}),
               headers={"Content-Type": "application/json"}).json()
    assert r["ok"] is True and "accepted" in r
    # 等 worker 结束(语料断供,应快速落 failed_docs 而非崩)
    import time
    from guanlan_v2.industry import ingest as ing
    for _ in range(100):
        if not ing.ingest_state()["running"]:
            break
        time.sleep(0.05)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_api.py -v`
Expected: FAIL(api 模块不存在)

- [ ] **Step 3: aggregate.py 尾部追加文本侧 + board**

```python
# ── 文本侧 + board 组装(Task 7)────────────────────────────────

_STANCE_VAL = {"多": 1.0, "中": 0.0, "空": -1.0}
_BOARD_CACHE: dict = {}
_BOARD_TTL = 600.0


def _dedupe_latest(extractions: list) -> list:
    """同 doc_id 保留 extracted_at 最新一条(失败重跑会产生重复)。"""
    best: dict = {}
    for rec in extractions:
        k = rec.get("doc_id")
        if k not in best or str(rec.get("extracted_at") or "") > str(best[k].get("extracted_at") or ""):
            best[k] = rec
    return list(best.values())


def _age_days(ts, now) -> float:
    import pandas as pd
    try:
        return max(0.0, (now - pd.Timestamp(str(ts)[:10])).total_seconds() / 86400.0)
    except Exception:  # noqa: BLE001
        return 9e9


def research_signals(fw: dict, extractions: list, now=None) -> dict:
    import numpy as np
    import pandas as pd
    now = pd.Timestamp(now) if now else pd.Timestamp.now()
    out = {s["id"]: {"score": 0.0, "n30": 0, "bull": 0, "bear": 0, "neutral": 0, "vals": []}
           for s in fw["segments"] if not s.get("adjacent")}
    for rec in _dedupe_latest(extractions):
        age = _age_days(rec.get("publish_ts"), now)
        if age > 30:
            continue
        decay = 0.5 ** (age / 7.0)
        for seg in rec.get("segments", []):
            sid = seg.get("segment_id")
            if sid not in out:
                continue
            v = _STANCE_VAL.get(seg.get("stance"), 0.0)
            out[sid]["score"] += v * float(seg.get("strength", 1)) * decay
            out[sid]["n30"] += 1
            out[sid]["vals"].append(v)
            if v > 0:
                out[sid]["bull"] += 1
            elif v < 0:
                out[sid]["bear"] += 1
            else:
                out[sid]["neutral"] += 1
    for sid, d in out.items():
        vals = d.pop("vals")
        d["disagreement"] = float(np.var(vals)) if len(vals) >= 2 else None
        d["score"] = round(d["score"], 3)
    return out


def edge_verdicts(fw: dict, extractions: list, now=None) -> dict:
    import pandas as pd
    now = pd.Timestamp(now) if now else pd.Timestamp.now()
    out = {e["id"]: {"support": 0, "refute": 0} for e in fw["edges"]}
    for rec in _dedupe_latest(extractions):
        if _age_days(rec.get("publish_ts"), now) > 30:
            continue
        for e in rec.get("edges", []):
            eid = e.get("edge_id")
            if eid in out:
                out[eid]["support" if e.get("verdict") == "支持" else "refute"] += 1
    return out


def _mom_rankpct(qsig: dict) -> dict:
    moms = {sid: d["momentum20"] for sid, d in qsig.items() if d.get("momentum20") is not None}
    if not moms:
        return {}
    ordered = sorted(moms, key=lambda k: moms[k])
    n = len(ordered)
    return {sid: (i + 0.5) / n for i, sid in enumerate(ordered)}


def narrative_temps(fw: dict, qsig: dict, extractions: list, now=None) -> list:
    import pandas as pd
    now = pd.Timestamp(now) if now else pd.Timestamp.now()
    rank = _mom_rankpct(qsig)
    plus: dict = {}
    minus: dict = {}
    for rec in _dedupe_latest(extractions):
        if _age_days(rec.get("publish_ts"), now) > 7:
            continue
        for n in rec.get("narratives", []):
            nid = n.get("narrative_id")
            if n.get("stance") == "多":
                plus[nid] = plus.get(nid, 0) + 1
            elif n.get("stance") == "空":
                minus[nid] = minus.get(nid, 0) + 1
    out = []
    for n in fw["narratives"]:
        num, den = 0.0, 0.0
        for a in n.get("activates", []):
            rp = rank.get(a["segment"])
            if rp is None:
                continue
            num += a["weight"] * rp
            den += a["weight"]
        out.append({"id": n["id"], "name": n["name"], "status": n.get("status"),
                    "temp": round(100.0 * num / den, 1) if den > 0 else None,
                    "plus7": plus.get(n["id"], 0), "minus7": minus.get(n["id"], 0)})
    return out


def quadrant(q: dict, r: dict, rankpct) -> str:
    hot_q = rankpct is not None and rankpct >= 0.5
    hot_r = (r or {}).get("score", 0) > 0
    return ("h" if hot_q else "l") + ("h" if hot_r else "l")


def build_board(refresh: bool = False) -> dict:
    import time
    import pandas as pd
    from . import corpus, store
    from .framework import load_framework
    if not refresh:
        hit = _BOARD_CACHE.get("board")
        if hit and time.time() - hit[0] < _BOARD_TTL:
            return hit[1]
    try:
        fw = load_framework()
        qsig = quant_signals(fw)
        ext = store.load_extractions(window_days=45)
        rsig = research_signals(fw, ext)
        rank = _mom_rankpct(qsig)
        ev = edge_verdicts(fw, ext)
        st = store.load_state()
        segments = []
        qdate = None
        for s in fw["segments"]:
            if s.get("adjacent"):
                segments.append({"id": s["id"], "name": s["name"], "group": s["group"],
                                 "adjacent": True, "logic": s["logic"]})
                continue
            q = qsig.get(s["id"], {})
            r = rsig.get(s["id"], {})
            qdate = q.get("quote_date") or qdate
            g = s.get("global", {})
            segments.append({
                "id": s["id"], "name": s["name"], "group": s["group"], "adjacent": False,
                "logic": s["logic"], "stars": g.get("stars", 0),
                "equity_logic": g.get("equity_logic", []), "global": g,
                "quant": q, "research": r,
                "quadrant": quadrant(q, r, rank.get(s["id"])),
            })
        board = {
            "ok": True, "reason": None,
            "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "freshness": {"corpus": corpus.corpus_freshness(),
                          "last_ingest_at": st.get("last_ingest_at"),
                          "extracted_total": st.get("totals", {}).get("docs", 0),
                          "quote_date": qdate},
            "drivers": fw["drivers"], "groups": fw["groups"], "segments": segments,
            "edges": [dict(e, verdict_counts=ev.get(e["id"], {"support": 0, "refute": 0})) for e in fw["edges"]],
            "narratives": narrative_temps(fw, qsig, ext),
        }
        _BOARD_CACHE["board"] = (time.time(), board)
        return board
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"board 组装失败: {exc}"}


def segment_detail(sid: str) -> dict:
    from . import store
    from .framework import load_framework
    try:
        fw = load_framework()
        seg = next((s for s in fw["segments"] if s["id"] == sid), None)
        if seg is None:
            return {"ok": False, "reason": f"环节不存在: {sid}"}
        ext = _dedupe_latest(store.load_extractions(window_days=30))
        opinions = []
        for rec in ext:
            for s in rec.get("segments", []):
                if s.get("segment_id") == sid:
                    opinions.append({"doc_id": rec.get("doc_id"), "title": rec.get("title"),
                                     "org": rec.get("org"), "publish_ts": rec.get("publish_ts"),
                                     "stance": s.get("stance"), "strength": s.get("strength"),
                                     "quote": s.get("quote"), "quote_dropped": s.get("quote_dropped")})
        opinions.sort(key=lambda x: str(x.get("publish_ts")), reverse=True)
        qsig = quant_signals(fw)
        rsig = research_signals(fw, ext)
        return {"ok": True, "reason": None, "segment": seg, "quant": qsig.get(sid),
                "research": rsig.get(sid), "opinions": opinions, "stocks": seg.get("stocks", [])}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"segment 明细失败: {exc}"}


def doc_detail(doc_id: str) -> dict:
    from . import store
    try:
        recs = [r for r in store.load_extractions() if r.get("doc_id") == doc_id]
        if not recs:
            return {"ok": False, "reason": f"无此 doc: {doc_id}"}
        recs.sort(key=lambda r: str(r.get("extracted_at") or ""), reverse=True)
        return {"ok": True, "reason": None, "extraction": recs[0]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"doc 明细失败: {exc}"}
```

- [ ] **Step 4: 写 `guanlan_v2/industry/api.py`**

```python
# -*- coding: utf-8 -*-
"""AI投研看板路由(薄壳挂载,无 prefix,路由自带 /industry/)。"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel


class IngestReq(BaseModel):
    limit: Optional[int] = None


def build_industry_router() -> APIRouter:
    router = APIRouter()

    @router.get("/industry/board")
    async def board(refresh: int = 0):
        from . import aggregate
        return await asyncio.to_thread(aggregate.build_board, bool(refresh))

    @router.post("/industry/ingest")
    async def ingest_start(req: Optional[IngestReq] = None):
        from . import ingest
        limit = req.limit if req else None
        return await asyncio.to_thread(ingest.start_ingest, limit)

    @router.get("/industry/ingest_state")
    async def ingest_state():
        from . import ingest
        return await asyncio.to_thread(ingest.ingest_state)

    @router.get("/industry/segment/{sid}")
    async def segment(sid: str):
        from . import aggregate
        return await asyncio.to_thread(aggregate.segment_detail, sid)

    @router.get("/industry/doc/{doc_id}")
    async def doc(doc_id: str):
        from . import aggregate
        return await asyncio.to_thread(aggregate.doc_detail, doc_id)

    return router
```

- [ ] **Step 5: 跑测试确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_api.py tests/test_industry_aggregate.py -v`
Expected: 全部 passed(board 测试在无引擎行情环境 quant 全 null 也应 ok:True——诚实降级)

- [ ] **Step 6: Commit**

```bash
git add guanlan_v2/industry/aggregate.py guanlan_v2/industry/api.py tests/test_industry_api.py
git commit -m "feat(industry): T7 文本侧聚合(半衰7天双轴/分歧度/边验证计数/叙事温度/doc_id去重)+board组装TTL缓存+5端点

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: server.py 接线 + 重启 9999 + 真机冒烟

**Files:**
- Modify: `guanlan_v2/server.py`(现有 include_router 链尾,搜 `build_market_router` 定位,照 screen 先例)
- Test: 全量回归 + 真机 curl

- [ ] **Step 1: server.py 接线(market 挂载之后加两行)**

```python
    from guanlan_v2.industry import build_industry_router  # AI投研看板(2026-07-02 spec)
    app.include_router(build_industry_router())
```

- [ ] **Step 2: 全量测试回归**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/ -x -q`
Expected: 全绿(基线 760+,新增 industry 约 16)

- [ ] **Step 3: 重启 9999(代际看门狗 ~41s 自愈)**

```powershell
Stop-Process -Id (Get-NetTCPConnection -LocalPort 9999 -State Listen).OwningProcess -Force -Confirm:$false
1..12 | ForEach-Object { Start-Sleep 5; try { (Invoke-WebRequest -Uri "http://127.0.0.1:9999/screen/health" -UseBasicParsing -TimeoutSec 3).StatusCode } catch { "down" } }
```

Expected: 若干 "down" 后出现 200。

- [ ] **Step 4: 真机冒烟**

```powershell
# board:框架+行情信号出真值,研报侧此时为 0(尚未 ingest)
Invoke-RestMethod "http://127.0.0.1:9999/industry/board?refresh=1" | ConvertTo-Json -Depth 4 | Select-Object -First 60
# 小批量真实抽取(≤10 篇,真调 DeepSeek;DEEPSEEK_API_KEY 已在 9999 进程环境)
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:9999/industry/ingest" -ContentType "application/json" -Body '{"limit": 10}'
# 轮询直到 running=false,查 totals/failed_docs
Invoke-RestMethod "http://127.0.0.1:9999/industry/ingest_state"
# 抽取质量人工抽查:环节归属/quote 是否靠谱
Invoke-RestMethod "http://127.0.0.1:9999/industry/segment/C2" | ConvertTo-Json -Depth 5 | Select-Object -First 80
```

Expected: board `ok:true` 且 freshness.corpus 有真实 latest_publish_ts;ingest 受理并完成;抽查 opinions 环节归属合理、quote 为原文子串。**行业研报全文若仍为 0 篇(G:\stocks 侧未回填),ingest 吃到的是命中票池的个股研报——预期内(spec §4 现状诚实说明)。**

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/server.py
git commit -m "feat(industry): T8 薄壳接线 /industry/* 五端点上线(真机冒烟:board真值+小批量真实抽取验质)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: 前端 ui/industry/ 看板页 + 导航第三门面

**Files:**
- Create: `ui/industry/观澜 · AI投研.html`
- Create: `ui/industry/industry-data.jsx`
- Create: `ui/industry/industry-app.jsx`
- Modify: `ui/_shared/guanlan-nav.js`(MODULES 数组加一项)

设计参照 `ui/_mockups/industry-mockup.html`(布局/配色已获用户认可,功能版先行;正式设计稿后续用户提供再重排)。CSS:把 mockup `<style>` 里 `.drivers/.drv/.river/.col/.ghead/.node*/.legend/.nar*/.detail/.panel/.op*/.stbl*/.chip/.btn-ingest` 段整体拷进 HTML `<style>`,另加 `.infobar { display:flex; gap:8px; align-items:center; padding:10px 18px; border-bottom:1px solid var(--line); flex-wrap:wrap; }`。

- [ ] **Step 1: HTML 薄壳**

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>观澜 · AI投研</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;600;700&family=Noto+Serif+SC:wght@400;500;600;700&family=JetBrains+Mono:wght@300;400;500;600&display=swap" />
<link rel="stylesheet" href="../_shared/tokens.css" />
<style>
  html, body { height: 100%; margin: 0; }
  body { font-family: var(--sans); color: var(--ink); background: var(--paper); }
  /* ↓ 此处整体拷入 mockup 的 .drivers/.drv/.river/.col/.ghead/.node*/.legend/.nar*/.detail/.panel/.op*/.stbl*/.chip/.btn-ingest 样式段 + .infobar */
</style>
<script crossorigin src="https://unpkg.com/react@18.3.1/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js"></script>
</head>
<body class="paper-bg">
<div id="root"></div>
<script>
  window.GUANLAN_BACKEND = (location.protocol === "http:" || location.protocol === "https:") ? location.origin : "";
</script>
<script src="../_shared/guanlan-bus.js?v=4"></script>
<script src="../_shared/guanlan-nav.js"></script>
<script type="text/babel" data-presets="env,react" src="industry-data.jsx?v=1"></script>
<script type="text/babel" data-presets="env,react" src="industry-app.jsx?v=1"></script>
</body>
</html>
```

- [ ] **Step 2: industry-data.jsx(取数层,诚实降级)**

```jsx
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
```

- [ ] **Step 3: industry-app.jsx(渲染层)**

```jsx
/* 观澜 · AI投研 — 渲染层。数据全真:board ok:false 整页断供卡;单信号 null 显 “—”+title=reason,绝不编数。 */
const { useState, useEffect } = React;

function Badge({ label, val, warn }) {
  return <span className="chip" title={warn || ""} style={warn ? { borderColor: "var(--yin)", color: "var(--yin)" } : {}}>
    {label} <b className="num">{val == null ? "—" : val}</b></span>;
}

function fmtPct(x) { return x == null ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`; }

function NodeCard({ s, selected, onSelect }) {
  const q = s.quant || {}, r = s.research || {};
  if (s.adjacent) return <div className="node stub"><div className="nr"><span className="nm">{s.name} · 相邻链 ↗</span></div></div>;
  const mom = q.momentum20;
  return (
    <div className={`node q-${s.quadrant || "ll"}${selected ? " sel" : ""}`} onClick={() => onSelect(s.id)}>
      <div className="nr">
        <span className="nm">{s.name}</span>
        {s.stars > 0 && <span className="star">{"★".repeat(s.stars)}</span>}
        <span className={`mom ${mom > 0 ? "up" : mom < 0 ? "dn" : ""}`} title={q.reason || ""}>{fmtPct(mom)}</span>
      </div>
      <div className="sig">
        <div className="therm"><i style={{ width: `${Math.min(100, Math.max(4, ((mom || 0) * 100 + 50)))}%`,
          background: s.quadrant === "hh" ? "var(--zhu)" : s.quadrant === "lh" ? "var(--jin)" : s.quadrant === "hl" ? "var(--zhu-soft)" : "var(--ink-3)" }} /></div>
        <span className="rp">研{r.n30 == null ? "—" : r.n30}</span>
        <span className="lg">{(s.equity_logic || []).join("·")}</span>
      </div>
    </div>
  );
}

function App() {
  const [board, setBoard] = useState(null);
  const [sel, setSel] = useState("C2");
  const [detail, setDetail] = useState(null);
  const [ing, setIng] = useState(null);
  useEffect(() => { glFetchBoard(false).then(setBoard); glIngestState().then(setIng); }, []);
  useEffect(() => { if (sel) glFetchSegment(sel).then(setDetail); }, [sel]);
  if (!board) return <div style={{ padding: 40, color: "var(--ink-3)" }}>加载中…</div>;
  if (!board.ok) return <div style={{ padding: 40 }} className="serif">看板不可用:{board.reason}</div>;
  const groups = board.groups.map((g) => ({ ...g, segs: board.segments.filter((s) => s.group === g.id) }));
  const fresh = board.freshness || {};
  const corpusWarn = fresh.corpus && !fresh.corpus.ok ? fresh.corpus.reason : null;
  return (
    <div className="page">
      <div className="infobar">
        <Badge label="语料" val={fresh.corpus && fresh.corpus.latest_publish_ts} warn={corpusWarn} />
        <Badge label="行情" val={fresh.quote_date} />
        <Badge label="已抽取" val={fresh.extracted_total} />
        <Badge label="上次批处理" val={fresh.last_ingest_at} />
        <button className="btn-ingest" onClick={async () => { const r = await glStartIngest();
          alert(r.accepted ? "已受理,后台处理中" : `未受理:${r.reason || ""}`); glIngestState().then(setIng); }}>
          處理新研報{ing && ing.running ? " · 处理中…" : ""}</button>
        <button className="chip" onClick={() => glFetchBoard(true).then(setBoard)}>↻ 刷新</button>
      </div>
      <div className="drivers">{board.drivers.map((d) => (
        <div className="drv" key={d.id}><div className="n">{d.name} <i>{d.id}</i></div>
          <div className="v" style={{ fontSize: 10, color: "var(--ink-3)" }}>{(d.indicators || []).join(" · ")}</div></div>))}
      </div>
      <div className="river">{groups.map((g) => (
        <div className="col" key={g.id}>
          <div className="ghead"><span className="gname">{g.name}</span><span className="gsub">{g.id}</span></div>
          {g.segs.map((s) => <NodeCard key={s.id} s={s} selected={s.id === sel} onSelect={setSel} />)}
        </div>))}
      </div>
      <div className="nar">{board.narratives.map((n) => (
        <div className="narc" key={n.id}><div className="nh"><span className="nn">{n.name}</span>
          <span className="st">{n.status}</span></div>
          <div className="bar"><i style={{ width: `${n.temp == null ? 0 : n.temp}%`,
            background: (n.temp || 0) >= 70 ? "var(--zhu)" : (n.temp || 0) >= 45 ? "var(--jin)" : "var(--dai-soft)" }} /></div>
          <div className="meta"><span>{n.temp == null ? "—" : `${n.temp}°`}</span>
            <span className="plus">研+{n.plus7}</span><span className="minus">-{n.minus7}</span></div></div>))}
      </div>
      {detail && detail.ok && (
        <div className="detail">
          <div className="panel">
            <div className="ph"><span className="t">{detail.segment.name}</span>
              <span className="s">{detail.segment.logic}</span></div>
            <div style={{ padding: "10px 14px", fontSize: 12, lineHeight: 1.8 }}>
              {detail.segment.global && (<div>
                <div>国际:{detail.segment.global.intl}</div>
                <div>国内:<b style={{ color: "var(--zhu)" }}>{detail.segment.global.cn_position}</b></div>
                <div>壁垒:{detail.segment.global.moat}</div>
                <div>逻辑:<span className="lg">{(detail.segment.global.equity_logic || []).join("+")}</span> · {detail.segment.global.prospect}</div>
              </div>)}
            </div>
          </div>
          <div className="panel">
            <div className="ph"><span className="t">研报观点流</span><span className="r">近30日 {detail.opinions.length} 条</span></div>
            <div className="flow">{detail.opinions.length === 0 && <div style={{ color: "var(--ink-3)", fontSize: 12 }}>无研报覆盖</div>}
              {detail.opinions.map((o, i) => (
                <div className="op" key={i}>
                  <div className="oh"><span className={`stance ${o.stance === "多" ? "bull" : o.stance === "空" ? "bear" : "neut"}`}>{o.stance}</span>
                    <span className="strength">{"●".repeat(o.strength || 1)}</span>
                    <span className="org">{o.org} · {o.publish_ts}</span></div>
                  <div className="ttl">{o.title}</div>
                  {o.quote && <div className="quote">“{o.quote}”</div>}
                  {o.quote_dropped && <div style={{ fontSize: 9.5, color: "var(--ink-3)" }}>引句未过原文校验,已省略</div>}
                  <div className="of"><span className="trace">溯源 {o.doc_id}</span></div>
                </div>))}
            </div>
          </div>
          <div className="panel">
            <div className="ph"><span className="t">票池</span><span className="r">{(detail.stocks || []).length} 只</span></div>
            <div className="stbl"><table><thead><tr><th>个股</th><th>角色</th></tr></thead><tbody>
              {(detail.stocks || []).map((st) => (
                <tr key={st.code}><td>{st.name}<span className="code">{st.code}</span></td>
                  <td>{st.role}{st.note ? ` · ${st.note}` : ""}</td></tr>))}
            </tbody></table></div>
          </div>
        </div>)}
      <div className="foot"><span>数据:研报抽取 DeepSeek · 行情引擎产物 · 单字段缺失显示 — 不编数</span></div>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById("root")).render(<App />);
```

- [ ] **Step 4: 导航加第三门面**

`ui/_shared/guanlan-nav.js` 的 `MODULES` 数组(第 6-9 行附近)加一项:

```js
  { label: "AI投研", file: "../industry/观澜 · AI投研.html" },
```

- [ ] **Step 5: 浏览器验证**

打开 `http://127.0.0.1:9999/ui/industry/观澜 · AI投研.html`:
- 顶部徽章真值(语料 publish_ts/行情日期/已抽取 N);
- 河图 30 节点渲染,点节点明细联动,无研报环节显「无研报覆盖」;
- 「處理新研報」POST 受理;
- console 无错误(favicon 404 除外);
- 顶栏「AI投研」tab 出现且选股/落子页顶栏同步、不串位。

- [ ] **Step 6: Commit**

```bash
git add "ui/industry" ui/_shared/guanlan-nav.js
git commit -m "feat(industry): T9 AI投研看板页(河图/叙事温度/环节明细全真数据·断供与无覆盖诚实显形)+导航第三门面

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: 票池候选生成工具(离线 CLI,人审后手动并入 YAML)

**Files:**
- Create: `guanlan_v2/industry/pool_candidates.py`
- Test: `tests/test_industry_pool_candidates.py`

**Interfaces:**
- Consumes: `load_framework`(T1)、`_norm_code`(T4)。
- Produces: 纯函数 `build_candidates(fw, constituents_df, index_df) -> dict`(`{segment_id: [{code, name, concept, already_in_pool}]}`)+ CLI `python -m guanlan_v2.industry.pool_candidates [--out var/industry_pool_candidates.json]`(读 `<GL_PARQUET_ROOT|G:/stocks/stock_data/parquet>/concept_ths_{index,constituent}.parquet`)。**只产候选文件,绝不自动改 YAML**(spec §3.2:自动生成候选再人工修)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_industry_pool_candidates.py
# -*- coding: utf-8 -*-
import pandas as pd


def test_build_candidates_marks_existing():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.pool_candidates import build_candidates
    fw = load_framework()
    idx = pd.DataFrame([{"concept_name": "共封装光学(CPO)", "concept_code": "886001"}])
    cons = pd.DataFrame([
        {"concept_code": "886001", "stock_code": "300308", "stock_name": "中际旭创"},
        {"concept_code": "886001", "stock_code": "688498", "stock_name": "源杰科技"},
        {"concept_code": "886001", "stock_code": "301301", "stock_name": "某新票"},
    ])
    cands = build_candidates(fw, cons, idx)
    c2 = {c["code"]: c for c in cands["C2"]}
    assert c2["SH688498"]["already_in_pool"] is True      # 已是锚票
    assert c2["SZ301301"]["already_in_pool"] is False     # 新候选
    assert all(v["concept"] == "共封装光学(CPO)" for v in c2.values())


def test_segment_without_concept_gets_empty():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.pool_candidates import build_candidates
    fw = load_framework()
    cands = build_candidates(fw, pd.DataFrame(columns=["concept_code", "stock_code", "stock_name"]),
                             pd.DataFrame(columns=["concept_name", "concept_code"]))
    assert cands["C4"] == []                              # C4 ths_concepts 为空
```

- [ ] **Step 2: 跑测试确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_pool_candidates.py -v`
Expected: FAIL

- [ ] **Step 3: 写 `guanlan_v2/industry/pool_candidates.py`**

**实现前必核**:真实概念 parquet 列名以 `python -c "import pandas as pd; print(pd.read_parquet(r'G:/stocks/stock_data/parquet/concept_ths_index.parquet').columns.tolist())"` 实测为准;不同则在 `main()` rename 成 `build_candidates` 约定列(`concept_name/concept_code/stock_code/stock_name`),纯函数接口不变。

```python
# -*- coding: utf-8 -*-
"""票池候选生成(离线 CLI):同花顺概念成分 → 每环节候选清单 JSON。

只产候选,绝不自动改 ai_chain.yaml——人工审核后手动并入(spec §3.2)。
用法: <引擎python> -m guanlan_v2.industry.pool_candidates --out var/industry_pool_candidates.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .framework import load_framework
from .llmx import _norm_code

_DEFAULT_PARQUET_ROOT = r"G:/stocks/stock_data/parquet"


def build_candidates(fw: dict, constituents, index_df) -> dict:
    name2code = dict(zip(index_df["concept_name"].astype(str), index_df["concept_code"].astype(str)))
    out: dict = {}
    for s in fw["segments"]:
        if s.get("adjacent"):
            continue
        pool = {x["code"] for x in s.get("stocks", [])}
        rows = []
        for cname in s.get("ths_concepts", []):
            ccode = name2code.get(cname)
            if not ccode:
                continue
            sub = constituents[constituents["concept_code"].astype(str) == ccode]
            for _, r in sub.iterrows():
                code = _norm_code(str(r["stock_code"]))
                if not code:
                    continue
                rows.append({"code": code, "name": str(r.get("stock_name") or ""),
                             "concept": cname, "already_in_pool": code in pool})
        seen: set = set()
        uniq = []
        for r in rows:
            if r["code"] in seen:
                continue
            seen.add(r["code"])
            uniq.append(r)
        out[s["id"]] = uniq
    return out


def main() -> None:
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="var/industry_pool_candidates.json")
    args = ap.parse_args()
    root = Path(os.environ.get("GL_PARQUET_ROOT") or _DEFAULT_PARQUET_ROOT)
    cons = pd.read_parquet(root / "concept_ths_constituent.parquet")
    idx = pd.read_parquet(root / "concept_ths_index.parquet")
    fw = load_framework()
    cands = build_candidates(fw, cons, idx)
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(len(v) for v in cands.values())
    print(f"candidates written: {outp} segments={len(cands)} rows={n}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过 + 真跑一次**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_industry_pool_candidates.py -v`
Expected: 2 passed

Run(真机): `G:/financial-analyst/.venv/Scripts/python.exe -m guanlan_v2.industry.pool_candidates --out var/industry_pool_candidates.json`
Expected: 打印 segments=28 rows=数百;人工浏览 JSON,把靠谱候选与「待核」锚票逐一核对后手动并进 `ai_chain.yaml`(本任务不强制并入)。

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/industry/pool_candidates.py tests/test_industry_pool_candidates.py
git commit -m "feat(industry): T10 票池候选CLI(同花顺概念成分→每环节候选JSON·只产候选人工并入不自动改YAML)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 验收清单(全任务完成后)

1. `pytest tests/ -q` 全绿(引擎 venv)。
2. `GET /industry/board` 真机:28 环节行情信号真值、freshness 真实、无抽取时研报侧全 0 不编。
3. 小批量真实 ingest(≤10 篇)后:`segment/C2` 出现真观点流,quote 均为原文子串,doc 溯源可查。
4. 前端 `/ui/industry/观澜 · AI投研.html`:徽章真值、点选环节联动、断供/无覆盖诚实显形;顶栏三门面。
5. 帷幄守护计数测试未被触动(未改 console)。

## 挂账(不在本计划)

- 帷幄 ww_industry_* 工具(四处同步)/每日定时 ingest/新闻层深挖/第二行业框架/驱动指标自动采集(spec §6)。
- 正式 UI 设计稿到位后按设计重排 ui/industry(用户自行提供设计);**T9 功能版有意未做**:链图传导边 SVG overlay、全球坐标矩阵视图、双轴象限散点(spec §4.5 提及,mockup 已示形)——随设计稿一并落。
- G:\stocks 侧行业研报全文回填(用户管线,到货自动纳入)。
