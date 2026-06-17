<p align="center">
  <img src="docs/brand/hero.png" alt="觀瀾 · Financial Analyst — A 股 AI 智能投研工作台" width="900">
</p>

<p align="center">
  <h1 align="center">觀瀾 · Financial Analyst</h1>
</p>

<p align="center">
  <strong>A 股「研究 → 交易」一体化研究工作台</strong>
</p>

<p align="center">
  <em>研报 · 因子 · 经验卡 · 席位 · 落子 —— 五个模块在同一个本地档案库里流转成一条研究闭环;<br>底层一套 vendored <code>financial_analyst</code> 引擎(24 智能体 / 440+ alpha 因子 / buddy 工具)供给真数据。</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-Apache_2.0-yellow?style=flat" alt="License">
  <img src="https://img.shields.io/badge/python-3.13-blue?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/frontend-no--build_React_18-61DAFB?style=flat&logo=react&logoColor=white" alt="Frontend">
  <br>
  <img src="https://img.shields.io/badge/agents-24-7C3AED?style=flat" alt="Agents">
  <img src="https://img.shields.io/badge/alpha_factors-440+-FF6B6B?style=flat" alt="Alphas">
  <img src="https://img.shields.io/badge/buddy_tools-31-0F766E?style=flat" alt="Tools">
  <img src="https://img.shields.io/badge/mcp_tools-20-FF6B35?style=flat" alt="MCP">
  <img src="https://img.shields.io/badge/modules-5-2563EB?style=flat" alt="Modules">
</p>

<p align="center">
  <a href="#-是什么">是什么</a> &nbsp;·&nbsp;
  <a href="#-研究闭环--五模块">五模块</a> &nbsp;·&nbsp;
  <a href="#-快速开始">快速开始</a> &nbsp;·&nbsp;
  <a href="#-架构三层">架构</a> &nbsp;·&nbsp;
  <a href="#-技术栈">技术栈</a>
</p>

> ⚠️ **个人研究工作台,非开箱即用的产品**。运行需要一份本地 A 股数据(日线 / 新闻 / 因子),默认指向作者机器上的数据目录,需经 `config/loaders.yaml` 或环境变量改指到你自己的数据。引擎已 vendored 进仓库 `engine/`,**自包含**;仅数据外部、且不随仓库分发。

---

## 💡 是什么

**一个像买方分析师一样思考的 A 股研究工作台。** 把一套经过验证的 `financial_analyst` 引擎(行情 / 资金流 / 新闻 / 研报 / 因子 / 盯盘)包成中式克制风格的多模块前端;研究到交易拆成五个环节,用一个共享的本地档案库串成闭环。

底层引擎按 **4 个信任层级**编排智能体(数据 → 分析 → 决策辩论 → 自审):研报「评级 / 归因 / 可证伪」,只有 `report-writer` 能落盘,Tier-1 不可信源(新闻 / F10)JSON-schema 锁死防注入。记忆是 markdown —— 改一个 `.md`,下一篇研报就用上。

<p align="center">
  <img src="docs/architecture/architecture.png" alt="觀瀾 · 智能体架构 —— 24 agents in 4 trust tiers" width="900">
</p>

---

## 🔄 研究闭环 · 五模块

```
研报/素材(research) → 炼因子(factor) → 验证成经验卡(card) → 装配成席位(seat) → 落子/交易决策(decision)
```

| 模块 | 路径 | 页面 | 职责 |
|---|---|---|---|
| 🗺 研究图谱 | `ui/graph/` | 研究图谱 | 首页 / 中枢:档案库五类物料的关系总览 |
| 💬 对话 · 研报 | `ui/chat/` | 交互原型 | 自然语言 A 股研究助手:流式回复 + 多步工具链 + 深度研报 |
| 🧪 因子 · 工作流 | `ui/factor/` | AI 工作流 | 可视化编排:数据源 / 因子库 / 特征工程 / ML(XGB·LGBM·SVM·RF·MLP·LSTM)/ PCA·IC / 向量化回测 |
| 🃏 经验卡 | `ui/cards/` | 经验验证区 | 研报 / 因子炼成「经验卡」→ deepseek 对话精炼 → 单因子回测验证 → 沉淀方法论 |
| ♟ 席位 · 落子 | `ui/seats/` | 落子 | 经验卡 + 因子装配成「席位」,落子 = 交易决策 |

物料带 `refs` 互相引用构成研究闭环的图,跨模块跳转用 `GL.handoff` 带上下文。后端自有因子库 `guanlan_v2/factorlib/`:价量 + 财务 + **TA 指标族**(MACD / RSI / KDJ / BOLL / WR,用引擎 `sma`=EMA 等算子重建,经 `/factor/report` 实测后注册进引擎 zoo)。

---

## ⚡ 快速开始

```bash
# 需 Python 3.13
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
pip install -e .                  # 装依赖(引擎已 vendored 在 engine/,运行期上 sys.path)

cp .env.example .env              # 填 DEEPSEEK_API_KEY(研报综述 / 经验卡精炼 / 盘中研判用)
# 数据目录:改 config/loaders.yaml 或设环境变量,指向你自己的 A 股数据(日线 / 新闻);
# 默认指向作者本地路径,不改则数据相关端点不可用。

python -m guanlan_v2.server       # 起服务(自带服务静态前端)
# 浏览器开 http://127.0.0.1:9999/  → 研究图谱首页
```

- 引擎源默认 = 仓库内 `engine/`;配置默认 = 仓库内 `config/`(deepseek `llm.yaml` + `loaders.yaml`)。
- 数据经单入口 `financial_analyst.data.paths.get_data_paths()` 解析(env > `loaders.yaml` > 本地 fallback);盘中实时行情走引擎内腾讯实时源。
- `GUANLAN_FA_SRC` 可覆盖引擎源做 A/B。

---

## 🏗 架构(三层)

```
前端   ui/<module>/*.html + *.jsx     无构建,浏览器内 Babel 即时编译 JSX
  └─ 共享 _shared/ (设计 tokens / 全局导航 / 档案库总线 / 共用组件)
后端壳  guanlan_v2/server.py           FastAPI:import 引擎 build_app() + StaticFiles 服务 ui/
引擎   engine/financial_analyst        buddy SSE 后端 + 全部工具;数据经 get_data_paths 只读引用
```

- **无构建前端**:无 webpack / vite / node_modules;每页独立 HTML 引 React 18 UMD + `@babel/standalone` 浏览器内编译。多页 + 整页导航(非 SPA)。改完刷新即生效(改 jsx 后 bump HTML 的 `?v=` 缓存串)。
- **薄壳后端**:`server.py` 把引擎 `build_app()`(全部真实端点)+ guanlan 自有路由(`/cards/*` · `/seats/*` · `/factorlib/*` · `/screen/*` · workflow 节点)缝起来并服务静态前端。
- **档案库总线**:`ui/_shared/guanlan-bus.js` 的 `window.GL`(localStorage 持久化)是五模块唯一事实源。

详见 [ARCHITECTURE.md](ARCHITECTURE.md) · [docs/module_map.md](docs/module_map.md) · [docs/dev_guide.md](docs/dev_guide.md)。

---

## 🧰 技术栈

- **后端**:FastAPI 薄壳 + vendored `financial_analyst` 引擎(Python 3.13)
- **前端**:无构建 React 18(UMD)+ `@babel/standalone`;中式视觉 —— 宣纸暖白 / 月夜深墨、朱砂红(涨)/ 黛绿(跌)/ 印章红、Noto Serif/Sans SC + JetBrains Mono、品牌符号「觀」印
- **LLM**:deepseek(研报综述 / 经验卡精炼 / 盘中研判);key 从环境读,绝不入库
- **数据**:A 股日线 / 新闻 / 因子,只读引用(几十 GB,不随仓库分发)

---

## 📐 约束(硬规则)

- **数据只引用不复制** —— 行情 / 新闻数据留外部,经 `get_data_paths` 只读引用。
- **密钥不入库** —— 真 `.env` 不提交;引擎从环境读 key(见 [.env.example](.env.example))。
- **运行态不入库** —— 研判记录 / 台账 / 验证产物 / 生成研报 / 大数据 artifact 留本地(见 [.gitignore](.gitignore))。

---

## 📄 License

Apache-2.0 · **仅研究 / 教育用途**。产出为供合格专业人士复核的分析底稿,不构成投资建议、不执行交易、不向任何账本下单。© [@jesson-hh](https://github.com/jesson-hh)
