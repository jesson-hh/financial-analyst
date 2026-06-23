# 工作流 CPCV 验证节点(③)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AI 工作流 DAG 里新增「CPCV 验证节点」—— 选一个 registry 模型(prod 或变体)→ 调 ① 的 `/screen/model/validate`(快验内联 / 严格异步轮询)→ 在结果抽屉出 DSR / 夏普分布 / IC 分布卡(诚实缺席)。

**Architecture:** **纯前端**,改 `ui/factor/workflow.jsx` 一个文件 + host HTML 的 `?v` cache-buster。新增节点类型 `validate`:注册到 `SPECS`/`CATALOG`、把 `validation` 登记为终端 dt、加 `NODE_EXEC.validate` 执行器、给节点 body 挂复用的 `ModelLibPanel` 选择器、给 `ResultsDrawer` 加一个验证结果卡。复用 ① 后端(`/screen/model/validate` + `/status`)与 ② 选择器,**零后端 / 零 `v4.py` 改动**。

**Tech Stack:** React(in-browser Babel JSX,无构建步骤)。无前端单测 —— 每步以「`git diff` 仅新增 + 重读确认 JSX 配平」为验证;真机浏览器点测受并发限制(9999 服 main 树无①)**延后到合并后**,与 ① 的 L1 一起验。

**Spec:** `docs/superpowers/specs/2026-06-23-workflow-validation-node-design.md`

**并发 / 工作树:** 本计划在既有 worktree **`G:/guanlan-v2/.claude/worktrees/cpcv-validation`**(分支 `feat/cpcv-validation`)实施。不占主工作树(让给并发 dl-ensemble 会话)。

**红线:** fill-not-rebuild(只加节点 + 结果分支,绝不重构/重命名/重排既有元素);复用现有 `_post`/`_get`/`ModelLibPanel`/`RCard`;诚实缺席(`ready=false` 显 `note`,绝不编数);① 后端 + `v4.py` 零改;改后 bump `?v`;提交信息以 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` 结尾。

---

## 文件结构

| 文件 | 责任 | 改动 |
|---|---|---|
| `ui/factor/workflow.jsx` | 节点注册 + 终端登记 + 执行器 + 选择器挂载 + 结果卡 | 全部新增(5 处插入) |
| `ui/factor/观澜 · AI 工作流.html` | `workflow.jsx?v=` cache-buster | bump 87→88 |

**关键事实(已核实,实施时照此):**
- `SPECS`(line 23–49)节点注册表;每项 `{title,cat,inputs,outputs,params}`。`cat` 取值见 `CAT`(io/fe/ml/mf/fa/bt)。
- `CATALOG`(line 51–57)左侧目录分组。
- `NODE_EXEC`(line 309+)执行器注册表;`model` 执行器(line 338)用模块级 `_get`;其它用 `ctx.post`。模块级 `_post`(line 210,POST→解析body,HTTP错抛)/ `_get`(line 233,GET→解析JSON或失败回 null)均可直接用。
- 终端判定有**两处**:`runGraph` 的 `TERMINAL_DT`(line 790)+ 其载荷谓词(line 829);`validateGraph`(LLM图校验)的 `TERM`(line 1086)。
- 节点 body 渲染在 `Node`(line 1823);param 渲染支持 `type:'step'`(◀▶ 步进,line 1863)/ `type:'select'`(下拉,line 1869)/ 其它(文本,line 1874);子面板按 `node.type` 在 line 1881–1886 挂载。
- `ModelLibPanel`(line 1726)签名 `{node,onParam}`,弹 `FactorLibModal` 选模型 → `onParam('model_id',...)`/`onParam('model_name',...)`。
- `ResultsDrawer`(line 2046)按 `result` 形状分支返回;`RCard`(line 1936)卡片壳;`expanded`/`onClose`/`setExpanded` 已在作用域。

---

# Task 1:注册 `validate` 节点 + 登记终端 dt

**Files:** Modify `ui/factor/workflow.jsx`(SPECS / CATALOG / TERMINAL_DT / 谓词 / TERM)

- [ ] **Step 1: 在 `SPECS` 里加 `validate` 节点。** 在 line 27 的 `model:` 行之后插入新的一行(紧跟 model,因同属"选模型"语义):

```js
  validate:  { title: 'CPCV 验证', cat: 'fa', inputs: [{ id: 'model', label: '模型', dt: 'series' }], outputs: [{ id: 'report', label: '验证', dt: 'validation' }], params: [{ id: 'model_id', label: '已选模型', type: 'text', value: '' }, { id: 'model_name', label: '模型名', type: 'text', value: '' }, { id: 'tier', label: '档位', type: 'select', value: 'strict', options: [{ value: 'strict', label: '严格(全历史 retrain-CPCV)' }, { value: 'quick', label: '快验(读冻结快照·秒级)' }], hint: '严格=全历史按组合净化交叉验证(CPCV)重训出路径分布(~分钟·异步);快验=读已积累的真OOS快照算夏普/DSR(秒级·零看未来,目前仅生产prod有快照,变体会拿到prod数据)。' }, { id: 'n_groups', label: '组数 N', type: 'step', value: 6, step: 1, hint: 'CPCV 连续切 N 段(严格档生效)。' }, { id: 'k', label: '测试组 k', type: 'step', value: 2, step: 1, hint: '每次取 C(N,k) 组当测试段;N=6,k=2 → 15 条路径。' }, { id: 'purge', label: 'purge 日', type: 'step', value: 5, step: 1, hint: '挖掉测试段前 N 个交易日(标签窗会探入测试段,防泄漏)。' }, { id: 'embargo', label: 'embargo 日', type: 'step', value: 5, step: 1, hint: '剔掉测试段后 N 个交易日(防紧邻泄漏)。' }] },
```

- [ ] **Step 2: 在 `CATALOG` 加一个验证分组。** 在 line 56 的 `'05 · 回测相关'` 行之后插入(line 57 的 `]` 之前):

```js
  { g: '06 · 模型验证', items: ['validate'] },
```

- [ ] **Step 3: 把 `validation` 登记为终端 dt(两处)+ 谓词。**
  - `runGraph` 的 `TERMINAL_DT`(line 790):把结尾 `tvbeta: 1 }` 改为 `tvbeta: 1, validation: 1 }`。
  - 载荷谓词(line 829):在那串 `|| payload.method === 'tvbeta'` 之后、右括号 `)` 之前,追加 `|| payload.__dt === 'validation'`。即该 `if` 条件末尾变为 `... || payload.method === 'tvbeta' || payload.__dt === 'validation')`。
  - `validateGraph` 的 `TERM`(line 1086):把 `relstat: 1 }` 改为 `relstat: 1, validation: 1 }`(让 LLM 生成的、以验证节点收尾的图通过"≥1 终端"校验)。

- [ ] **Step 4: 自查(无单测 → 人工核)。** 运行 `git -C G:/guanlan-v2/.claude/worktrees/cpcv-validation diff ui/factor/workflow.jsx` 确认:**只有新增**(SPECS 多 1 行、CATALOG 多 1 行、TERMINAL_DT/谓词/TERM 各为同行内追加),无任何既有行被删/改语义。重读这 5 处确认括号/逗号配平、`validate` 节点的 `inputs/outputs/params` 数组闭合。

- [ ] **Step 5: 提交**

```bash
cd "G:/guanlan-v2/.claude/worktrees/cpcv-validation"
git add "ui/factor/workflow.jsx"
git commit -m "$(cat <<'EOF'
feat(workflow-ui): 注册 CPCV 验证节点(SPECS/CATALOG)+ 登记 validation 终端 dt(两处+谓词)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

# Task 2:`NODE_EXEC.validate` 执行器

**Files:** Modify `ui/factor/workflow.jsx`(`NODE_EXEC`,在 `model` 执行器 line 345 之后插入)

- [ ] **Step 1: 在 `NODE_EXEC` 里加 `validate` 执行器。** 紧跟 `model:` 执行器结束(line 345 `},`)之后插入:

```js
  // —— CPCV 验证节点: 取 model_id(优先上游 model 节点 → 否则本节点选择器)→ 调 ① /screen/model/validate。
  //    quick: 内联取 result(秒级)。strict: POST 起异步子进程 → 每 4s 轮询 /status 至 done(~分钟级,上限~10min)。
  //    出 dt=validation 终端载荷送抽屉。诚实: ready=false 原样带 note 不编数; 启动失败抛错; 轮询超时诚实标注。——
  validate: async (inputs, params, ctx) => {
    const id = String((inputs.model && inputs.model.model_id) || params.model_id || '').trim();
    if (!id) throw new Error('CPCV 验证: 未选模型 —— 接一个「模型(研究库)」节点,或点本节点「研究库」选一个');
    const name = String((inputs.model && inputs.model._label) || params.model_name || '') || id;
    const tier = String(params.tier || 'strict');
    if (tier === 'quick') {
      const r = await _post('/screen/model/validate', { id, tier: 'quick' });
      if (!r || !r.ok) throw new Error('CPCV 快验: ' + ((r && r.reason) || '/screen/model/validate 失败'));
      return { report: Object.assign({ __dt: 'validation', tier: 'quick', model_id: id, model_name: name }, r.result || {}) };
    }
    const r = await _post('/screen/model/validate', { id, tier: 'strict', n_groups: +params.n_groups || 6, k: +params.k || 2, purge: +params.purge || 5, embargo: +params.embargo || 5 });
    if (!r || !r.ok) throw new Error('CPCV 严格验证: ' + ((r && r.reason) || '启动失败'));
    let st = {}, polls = 0;
    while (polls++ < 160) {                                  // ~10.6 分钟上限(strict 通常 1–数分钟)
      await new Promise(res => setTimeout(res, 4000));
      const s = await _get('/screen/model/validate/status');
      st = (s && s.state) || st;
      if (!st.running && st.phase === 'done') break;
    }
    if (st.phase !== 'done') return { report: { __dt: 'validation', tier: 'strict', model_id: id, model_name: name, ready: false, note: '严格验证轮询超时(>10min)—— 后端可能仍在跑,稍后到模型工坊看结果' } };
    return { report: Object.assign({ __dt: 'validation', tier: 'strict', model_id: id, model_name: name }, st.result || {}, st.ok ? {} : { error: st.error, ready: false, note: '严格验证失败: ' + (st.error || '') }) };
  },
```

- [ ] **Step 2: 自查。** `git diff` 确认仅新增该执行器块(在 `model:` 与 `feature:` 之间);重读确认 `async (inputs, params, ctx)` 签名、用模块级 `_post`/`_get`(非 `ctx`)、`while` 循环有上限、两个 `return` 均产 `{ report: {__dt:'validation', ...} }`。无既有执行器被改。

- [ ] **Step 3: 提交**

```bash
cd "G:/guanlan-v2/.claude/worktrees/cpcv-validation"
git add "ui/factor/workflow.jsx"
git commit -m "$(cat <<'EOF'
feat(workflow-ui): NODE_EXEC.validate 执行器(quick内联/strict轮询·上游model_id优先·诚实缺席+超时)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

# Task 3:节点 body 挂模型选择器

**Files:** Modify `ui/factor/workflow.jsx`(`Node` 组件,line 1884 附近的子面板挂载区)

- [ ] **Step 1: 给 validate 节点复用 `ModelLibPanel`。** 在 line 1884 的 `{node.type === 'model' ? <ModelLibPanel node={node} onParam={onParam} /> : null}` 之后插入一行:

```jsx
        {node.type === 'validate' ? <ModelLibPanel node={node} onParam={onParam} /> : null}
```

(`ModelLibPanel` 用 `node.params.model_id/model_name`,与 validate 节点同字段,可直接复用。上游 model 节点连入时,执行器优先用 `inputs.model.model_id`,选择器作 standalone/override。)

- [ ] **Step 2: 自查。** `git diff` 确认仅新增这一行(紧邻 model 的面板挂载);无既有挂载行被改。

- [ ] **Step 3: 提交**

```bash
cd "G:/guanlan-v2/.claude/worktrees/cpcv-validation"
git add "ui/factor/workflow.jsx"
git commit -m "$(cat <<'EOF'
feat(workflow-ui): CPCV 验证节点 body 复用 ModelLibPanel 选择器(独立选/可被上游覆盖)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

# Task 4:`ResultsDrawer` 验证结果卡

**Files:** Modify `ui/factor/workflow.jsx`(`ResultsDrawer`,在 line 2052 的个股时序IC分支 `if (result && result.codes_tsic ...)` 之前插入新分支)

- [ ] **Step 1: 加验证结果分支。** 在 line 2052(`// —— 个股时序IC 结果...` 注释)之前、即 `_hv` 定义(line 2051)之后,插入:

```jsx
  // —— CPCV 验证结果:独立视图(DSR / 夏普分布 / IC;诚实缺席 ready=false 显 note,不编数) ——
  if (result && result.__dt === 'validation' && !loading && !error) {
    const ready = result.ready === true;
    const sd = result.sharpe_dist || {};
    const f2 = v => (v == null || v !== v) ? '—' : (+v).toFixed(2);
    const f3 = v => (v == null || v !== v) ? '—' : (+v).toFixed(3);
    const icMid = (result.ic_mean != null) ? result.ic_mean : (result.ic_dist && result.ic_dist.median);
    const tierLabel = result.tier === 'quick' ? '快验 · 读冻结快照' : '严格 · 全历史 retrain-CPCV';
    return (
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: expanded ? 'calc(100vh - 90px)' : 304, background: 'var(--paper)', borderTop: '1px solid var(--ink)', boxShadow: '0 -12px 32px rgba(28,24,20,0.12)', zIndex: 8, display: 'flex', flexDirection: 'column', animation: 'fadeIn .3s ease', transition: 'height .2s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 18px', borderBottom: '1px solid var(--line-soft)' }}>
          <span style={{ width: 20, height: 20, borderRadius: 5, background: ready ? 'var(--dai)' : 'var(--ink-3)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{ready ? '✓' : 'ⓘ'}</span>
          <span className="serif" style={{ fontSize: 14, fontWeight: 600 }}>CPCV 验证 · {result.model_name || result.model_id}</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{tierLabel}{result.asof ? ' · ' + result.asof : ''}</span>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <span onClick={() => setExpanded(e => !e)} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>{expanded ? '⤡ 收起' : '⤢ 展开'}</span>
            <span onClick={onClose} style={{ fontSize: 16, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 4px' }}>✕</span>
          </span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '13px 18px' }}>
          {!ready ? (
            <div style={{ padding: '10px 13px', background: 'rgba(28,24,20,0.03)', border: '1px solid var(--line-soft)', borderRadius: 8, fontSize: 12, color: 'var(--ink-1)', lineHeight: 1.5 }}>
              <b>证据不足 / 未就绪</b>
              <div style={{ marginTop: 4, color: 'var(--ink-2)' }}>{result.note || result.error || '无可用验证结果'}</div>
            </div>
          ) : (
            <>
              <div style={{ display: 'flex', gap: 22, marginBottom: 14, flexWrap: 'wrap' }}>
                {[['Deflated Sharpe (DSR)', f2(result.dsr), ((result.dsr != null && result.dsr >= 0.5) ? 'var(--zhu)' : 'var(--dai)')],
                  ['夏普 · 中位', f2(sd.median), 'var(--ink)'],
                  ['夏普 · 5%', f2(sd.p05), 'var(--ink-1)'],
                  ['夏普 · 95%', f2(sd.p95), 'var(--ink-1)'],
                  ['IC · 中位', f3(icMid), 'var(--ink-1)'],
                  ['路径数', (result.n_paths != null ? result.n_paths : '—'), 'var(--ink-1)'],
                  ['试验数(deflate)', (result.n_trials != null ? result.n_trials : '—'), 'var(--ink-1)']].map(([l, v, c], i) => (
                  <div key={i}><div className="mono" style={{ fontSize: 8.5, letterSpacing: '.08em', color: 'var(--ink-3)' }}>{l}</div><div className="mono" style={{ fontSize: 17, fontWeight: 600, color: c, marginTop: 2 }}>{v}</div></div>
                ))}
              </div>
              <RCard title="说明">
                <div style={{ fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.5 }}>{result.note || ''}</div>
                <div style={{ marginTop: 6, fontSize: 10, color: 'var(--ink-3)', lineHeight: 1.5 }}>DSR = 扣除「试了多个变体」的运气后,真夏普 &gt; 噪声基准的概率(0–1);越接近 1 越可信。{result.tier === 'quick' ? '快验读已积累的真 OOS 快照(零看未来)。' : ('严格档全历史按组合净化交叉验证(purge+embargo)重训,出 ' + (result.n_paths || 0) + ' 条路径分布。')}</div>
              </RCard>
            </>
          )}
        </div>
      </div>
    );
  }
```

- [ ] **Step 2: 自查。** `git diff` 确认仅新增此分支(在 `_hv` 之后、`codes_tsic` 分支之前);重读确认:JSX 标签配平、三元 `!ready ? (...) : (<>...</>)` 闭合、KPI `.map` 数组闭合、复用了 `RCard`/`expanded`/`onClose`/`setExpanded`。无既有分支被改。

- [ ] **Step 3: 提交**

```bash
cd "G:/guanlan-v2/.claude/worktrees/cpcv-validation"
git add "ui/factor/workflow.jsx"
git commit -m "$(cat <<'EOF'
feat(workflow-ui): ResultsDrawer 加 CPCV 验证结果卡(DSR/夏普分布/IC·ready=false 诚实显 note)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

# Task 5:bump `?v` + 整合自查

**Files:** Modify `ui/factor/观澜 · AI 工作流.html`

- [ ] **Step 1: bump cache-buster。** 把 line 37 的 `workflow.jsx?v=87` 改为 `workflow.jsx?v=88`。

- [ ] **Step 2: 整合自查(全文件)。**
  - `git -C G:/guanlan-v2/.claude/worktrees/cpcv-validation diff` 复核全部改动 = 纯新增(节点注册 / 终端登记 / 执行器 / 面板挂载 / 结果卡)+ 一处 `?v` bump;零既有元素删除/重排/重命名(fill-not-rebuild 红线)。
  - 重读 `validate` 一条链是否自洽:SPECS 有 `validate`(outputs dt='validation')→ TERMINAL_DT/谓词认 'validation' → NODE_EXEC.validate 产 `{report:{__dt:'validation',...}}` → ResultsDrawer 认 `result.__dt==='validation'`。字段一致:执行器产 `dsr/sharpe_dist/ic_mean/ic_dist/n_paths/n_trials/asof/ready/note`,结果卡按这些键读。
  - 确认无 `TODO`/占位;无引用未定义的 helper(`_post`/`_get`/`ModelLibPanel`/`RCard`/`expanded`/`onClose`/`setExpanded` 均已存在)。

- [ ] **Step 3: 提交**

```bash
cd "G:/guanlan-v2/.claude/worktrees/cpcv-validation"
git add "ui/factor/观澜 · AI 工作流.html"
git commit -m "$(cat <<'EOF'
chore(workflow-ui): bump workflow.jsx ?v=88(CPCV 验证节点上线)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: 真机浏览器验证(延后/可选)。** 因 9999 服并发会话的 main 树(无 ① 代码),live-browser 点测**延后到 ②+① 合并后**一起做:届时 9999 服 ① 代码 → 工作流加「CPCV 验证」节点 → 选 prod/变体 → 跑 → 抽屉出 DSR/夏普分布(或 prod 快验诚实显「证据不足」)、0 babel 报错。**可选提前**:临时从 worktree 起服务到空闲端口浏览器点测(用户按需)。

---

## 自审清单

1. **spec 覆盖**:新 `validate` 节点(Task1)、两档 quick/strict + 上游预填(Task2 执行器)、ModelLibPanel 选择器(Task3)、终端结果卡 + 诚实缺席(Task4)、`?v`(Task5)—— 对齐 spec 全部要点。✔
2. **占位符**:无 TBD;每处给完整可贴代码 + 精确行锚 + 提交信息。✔
3. **类型一致**:执行器产键 `__dt/tier/model_id/model_name/dsr/sharpe_dist{median,p05,p95,std}/ic_mean/ic_dist/n_paths/n_trials/asof/ready/note/error` 与结果卡读取键逐一对应;`dt:'validation'` 在 SPECS 输出、TERMINAL_DT、TERM、谓词、结果卡五处一致。✔
4. **红线**:fill-not-rebuild(全新增)、复用现有 helper/面板、诚实缺席(ready=false 显 note)、① 后端 + v4.py 零改、?v bump。✔

> **挂账(③ 之外,见 spec)**:promote 节点输出 variant_id edge(自动验刚建变体);TopBar/工坊 CPCV 直方图;跨变体 PBO(CSCV)。
