// 观澜 · AI 工作流 — 节点编排编辑器 (可交互内核)
const { useState, useRef, useCallback, useEffect } = React;
// 帷幄融合旗:EMBED=被帷幄嵌入(隐藏页头身份区);LEGACY=找回页内 agent 窗口(默认全局隐藏,spec §3.7)
const WW_EMBED = new URLSearchParams(location.search).get('embed') === '1';
const WW_LEGACY = new URLSearchParams(location.search).get('legacy') === '1';
// 帷幄研究空间隔离:新壳传 ws=<workspace_id>；显式 workspace_id 也兼容。
// 旧版 ws=<session_id> 仍用于 handoff/本地缓存隔离，但不会冒充 workspace_id 请求私有 API。
// 无研究空间时缓存键与 API 调用均维持旧全局行为。
const WW_QUERY = new URLSearchParams(location.search);
const WW_WS = WW_QUERY.get('ws') || '';
const WW_WORKSPACE_ID = WW_QUERY.get('workspace_id') || (/^rw_[0-9A-Za-z_-]+$/.test(WW_WS) ? WW_WS : '');
const WW_READONLY = WW_QUERY.get('readonly') === '1';
const WW_SESSION_ID = /^[0-9A-Za-z_-]{1,100}$/.test(WW_QUERY.get('session_id') || '') ? WW_QUERY.get('session_id') : '';
const WW_NS_ID = WW_WORKSPACE_ID || WW_WS;
const WW_NS = WW_NS_ID ? (':' + WW_NS_ID) : '';
const WW_BACK_TO_CHAT_EVENT = 'guanlan:ww:back-to-conversation';

function wwBackToConversation() {
  // 嵌入帷幄时不能在 iframe 内跳转：由父壳收起右栏工作台/全宽 Stage，
  // 才能保留当前 sid、流式任务与研究空间上下文。
  if (WW_EMBED && window.parent !== window) {
    window.parent.postMessage({ type: WW_BACK_TO_CHAT_EVENT }, location.origin);
    return;
  }
  // 独立窗口回到现行帷幄入口；旧「投研台」已经归档，不再存在。
  window.location.href = '../console/观澜 · 帷幄.html' + (WW_SESSION_ID ? '?session_id=' + encodeURIComponent(WW_SESSION_ID) : '');
}

// ───────── 布局常量 ─────────
const HEADER = 34, PAD = 8, ROW = 26, W = 200;
const CAT = {
  io:  { c: 'var(--ink-3)', hd: 'rgba(28,24,20,0.06)' },
  fe:  { c: 'var(--dai)',   hd: 'rgba(74,107,92,0.13)' },
  ml:  { c: 'var(--jin)',   hd: 'rgba(138,111,63,0.14)' },
  mf:  { c: 'var(--zhu)',   hd: 'rgba(185,74,61,0.12)' },
  fa:  { c: 'var(--yin)',   hd: 'rgba(168,57,45,0.14)' },
  bt:  { c: 'var(--ink-1)', hd: 'rgba(28,24,20,0.09)' },
};

// ───────── 节点规格 (目录 + 行为) ─────────
const SPECS = {
  data_screen: { title: '数据筛选 Skill', cat: 'io', inputs: [{ id: 'src', label: '数据源', dt: 'series' }], outputs: [{ id: 'candidates', label: '候选', dt: 'data_screen' }], params: [{ id: 'preset', label: '筛选模板', type: 'select', value: 'ma_trend', options: [{ value: 'ma_trend', label: '沿均线上升' }], hint: '模板会编译为受控硬规则并逐票执行，不走 v4 排名或问财近似。' }, { id: 'ma_window', label: '均线窗口', type: 'step', value: 5, step: 1 }, { id: 'rising_transitions', label: '严格上升次数', type: 'step', value: 3, step: 1, hint: '3 次变化会检查最近 4 个移动均线值，任一次持平也不通过。' }, { id: 'below_lookback', label: '回看天数', type: 'step', value: 5, step: 1 }, { id: 'max_consecutive_below', label: '最多连续跌破', type: 'step', value: 1, step: 1 }, { id: 'min_bias', label: '最小乖离率', type: 'step', value: 0, step: 0.005, dec: 3 }, { id: 'max_bias', label: '最大乖离率', type: 'step', value: 0.03, step: 0.005, dec: 3 }, { id: 'top_n', label: '返回数量', type: 'step', value: 5, step: 1 }, { id: 'min_avg_amount_yi', label: '日均成交额≥(亿)', type: 'step', value: 1, step: 0.5, dec: 1 }, { id: 'amount_window', label: '成交额窗口', type: 'step', value: 20, step: 1 }] },
  source:   { title: '数据源', cat: 'io', inputs: [], outputs: [{ id: 'data', label: '数据', dt: 'series' }], params: [{ id: 'scope', label: '范围', type: 'select', value: '小池', options: ['个股', '自选', '小池', '全市场'] }, { id: 'code', label: '标的/池', type: 'text', value: '600519' }, { id: 'codes', label: '自选代码', type: 'text', value: '', hint: '自组股池:填股票代码(逗号/空格分隔,如 600519,000858,SH600036)。填了就用这批票(覆盖下面「股票池」);留空走股票池。⚠ 小池/单票的排名类截面因子会退化,单票更适合阶段B共振/时序。' }, { id: 'benchmark', label: '对标指数', type: 'select', value: '', options: [{ value: '', label: '不对标' }, { value: 'csi300', label: '沪深300(真实指数)' }], hint: '共振用:选了就给面板注入大盘日收益 idx_ret,公式可写 correlation(returns, idx_ret, 20)=个股与大盘20日共振。目前真实指数仅沪深300;其它宽基请用 csmean(returns) 在所选池上做等权代理大盘。' }, { id: 'leader', label: '龙头代码', type: 'text', value: '', hint: '共振用:填一只龙头股代码(如 600519),给面板注入它的日收益 ref_ret,公式可写 correlation(returns, ref_ret, 20)=个股跟随龙头的20日共振。留空则不注入。' }, { id: 'universe', label: '股票池', type: 'select', value: '自动', options: [{ value: '自动', label: '自动·按标的' }, { value: 'csi300', label: '沪深300' }, { value: 'csi500', label: '中证500' }, { value: 'csi800', label: '中证800' }, { value: 'csi1000', label: '中证1000' }, { value: 'all', label: '全市场A股' }, { value: 'csi_fast', label: '快速测试·100' }, { value: 'sample30', label: '样本30' }, { value: 'etf', label: 'ETF' }, { value: 'csi300_active', label: '沪深300·活跃' }, { value: 'csi300_2024h2', label: '沪深300·24H2' }] }, { id: 'start', label: '起始日', type: 'text', value: '' }, { id: 'end', label: '截止日', type: 'text', value: '' }, { id: 'freq', label: '频率', type: 'select', value: 'day', options: [{ value: 'day', label: '日线' }] }, { id: 'oos_frac', label: '样本外占比', type: 'select', value: '0', options: [{ value: '0', label: '不切(全样本)' }, { value: '0.2', label: '末20%留样本外' }, { value: '0.3', label: '末30%留样本外' }, { value: '0.4', label: '末40%留样本外' }], hint: 'W7 过拟合体检:把时间窗末段留作"样本外(OOS)"。下游因子分析/回测/模型会把样本内(IS)与样本外(OOS)的 RankIC/Sharpe 并排 + 衰减%;样本外塌缩=疑似过拟合(wiki:样本内≈样本外才算验证)。' }, { id: 'wf_refit', label: '滚动重训', type: 'select', value: '否', options: ['否', '是'], hint: 'W7 真滚动前进重训(仅 ML 节点):逐折 expanding-train 重训新模型→预测下一段样本外,拼接算整体 RankIC(真前进验证,非切片统计)。K× 训练成本,默认否。结果在抽屉脚注/警告显示「滚动重训(K折)」。' }] },
  formula:  { title: '公式输入', cat: 'io', inputs: [], outputs: [{ id: 'out', label: '公式', dt: 'series' }], params: [{ id: 'expr', label: '表达式', type: 'text', value: 'close' }] },
  factorlib:{ title: '因子库', cat: 'io', inputs: [], outputs: [{ id: 'out', label: '因子', dt: 'series' }], params: [{ id: 'name', label: '已选因子', type: 'text', value: '' }, { id: 'expr', label: '表达式', type: 'text', value: '' }] },
  model:    { title: '模型(研究库)', cat: 'io', inputs: [], outputs: [{ id: 'out', label: '排名', dt: 'series' }], params: [{ id: 'model_id', label: '已选模型', type: 'text', value: '' }, { id: 'model_name', label: '模型名', type: 'text', value: '' }] },
  validate: { title: 'CPCV 验证', cat: 'fa', inputs: [{ id: 'model', label: '模型', dt: 'series' }], outputs: [{ id: 'report', label: '验证', dt: 'validation' }], params: [{ id: 'model_id', label: '已选模型', type: 'text', value: '' }, { id: 'model_name', label: '模型名', type: 'text', value: '' }, { id: 'tier', label: '档位', type: 'select', value: 'strict', options: [{ value: 'strict', label: '严格(全历史 retrain-CPCV)' }, { value: 'quick', label: '快验(读冻结快照·秒级)' }], hint: '严格=全历史按组合净化交叉验证(CPCV)重训出路径分布(~分钟·异步);快验=读已积累的真OOS快照算夏普/DSR(秒级·零看未来,目前仅生产prod有快照,变体会拿到prod数据)。' }, { id: 'n_groups', label: '组数 N', type: 'step', value: 6, step: 1, hint: 'CPCV 连续切 N 段(严格档生效)。' }, { id: 'k', label: '测试组 k', type: 'step', value: 2, step: 1, hint: '每次取 C(N,k) 组当测试段;N=6,k=2 → 15 条路径。' }, { id: 'purge', label: 'purge 日', type: 'step', value: 5, step: 1, hint: '挖掉测试段前 N 个交易日(标签窗会探入测试段,防泄漏)。' }, { id: 'embargo', label: 'embargo 日', type: 'step', value: 5, step: 1, hint: '剔掉测试段后 N 个交易日(防紧邻泄漏)。' }] },
  feature:  { title: '特征工程构建', cat: 'fe', inputs: [{ id: 'feat', label: '特征公式', dt: 'series' }, { id: 'label', label: '标签公式', dt: 'series' }, { id: 'src', label: '数据源(可选)', dt: 'series' }], outputs: [{ id: 'fe', label: '特征工程', dt: 'fe' }], params: [{ id: 'tag', label: '标签(默认收益)', type: 'text', value: 'IC', hint: '不连上面「标签公式」端口时的默认预测目标;IC / fwd_ret 都 = 未来收益,一般不用改。想自定义预测目标,就把一个「公式输入」连到「标签公式」端口。' }] },
  xgb:      { title: 'XGBoost 模型', cat: 'ml', inputs: [{ id: 'fe', label: '特征工程', dt: 'fe' }], outputs: [{ id: 'model', label: '模型', dt: 'model' }], params: [{ id: 'trees', label: '决策树数量', type: 'step', value: 100, step: 10 }, { id: 'depth', label: '最大深度', type: 'step', value: 3, step: 1 }, { id: 'lr', label: '学习率', type: 'step', value: 0.1, step: 0.01, dec: 2 }, { id: 'sub', label: '子样本比例', type: 'step', value: 1.0, step: 0.1, dec: 2 }] },
  lgbm:     { title: 'LightGBM 模型', cat: 'ml', inputs: [{ id: 'fe', label: '特征工程', dt: 'fe' }], outputs: [{ id: 'model', label: '模型', dt: 'model' }], params: [{ id: 'leaves', label: '叶子数', type: 'step', value: 31, step: 1 }, { id: 'lr', label: '学习率', type: 'step', value: 0.05, step: 0.01, dec: 2 }] },
  svm:      { title: 'SVM 模型', cat: 'ml', inputs: [{ id: 'fe', label: '特征工程', dt: 'fe' }], outputs: [{ id: 'model', label: '模型', dt: 'model' }], params: [{ id: 'c', label: '惩罚 C', type: 'step', value: 1.0, step: 0.1, dec: 1 }] },
  rf:       { title: '随机森林', cat: 'ml', inputs: [{ id: 'fe', label: '特征工程', dt: 'fe' }], outputs: [{ id: 'model', label: '模型', dt: 'model' }], params: [{ id: 'trees', label: '树数量', type: 'step', value: 200, step: 10 }] },
  nn:       { title: 'MLP 神经网络', cat: 'ml', inputs: [{ id: 'fe', label: '特征工程', dt: 'fe' }], outputs: [{ id: 'model', label: '模型', dt: 'model' }], params: [{ id: 'hidden', label: '隐层神经元', type: 'step', value: 64, step: 16 }, { id: 'layers', label: '隐层数', type: 'step', value: 1, step: 1 }, { id: 'lr', label: '学习率', type: 'step', value: 0.001, step: 0.001, dec: 3 }, { id: 'epochs', label: '迭代轮数', type: 'step', value: 200, step: 50 }, { id: 'alpha', label: 'L2 正则', type: 'step', value: 0.0001, step: 0.0001, dec: 4 }] },
  lstm:     { title: 'LSTM 序列网络', cat: 'ml', inputs: [{ id: 'fe', label: '特征工程', dt: 'fe' }], outputs: [{ id: 'model', label: '模型', dt: 'model' }], params: [{ id: 'seq_len', label: '序列长度', type: 'step', value: 10, step: 1 }, { id: 'hidden', label: '隐层单元', type: 'step', value: 32, step: 8 }, { id: 'layers', label: 'LSTM 层数', type: 'step', value: 1, step: 1 }, { id: 'lr', label: '学习率', type: 'step', value: 0.001, step: 0.001, dec: 3 }, { id: 'epochs', label: '迭代轮数', type: 'step', value: 40, step: 10 }] },
  pca:      { title: 'PCA 因子构建', cat: 'mf', inputs: [{ id: 'fe', label: '特征工程', dt: 'fe' }], outputs: [{ id: 'factor', label: 'Factor', dt: 'factor' }], params: [{ id: 'k', label: '主成分数', type: 'step', value: 5, step: 1 }] },
  spearman: { title: 'Spearman 因子', cat: 'mf', inputs: [{ id: 'fe', label: '特征工程', dt: 'fe' }], outputs: [{ id: 'factor', label: 'Factor', dt: 'factor' }], params: [] },
  iccalc:   { title: '因子 IC 计算', cat: 'mf', inputs: [{ id: 'factor', label: '因子值', dt: 'factor' }], outputs: [{ id: 'ic', label: 'IC', dt: 'ic' }], params: [{ id: 'period', label: '周期', type: 'step', value: 5, step: 1 }] },
  mf:       { title: '多因子构建（ML）', cat: 'mf', inputs: [{ id: 'm1', label: '模型 1', dt: 'model' }, { id: 'f1', label: '特征 1', dt: 'fe' }, { id: 'm2', label: '模型 2', dt: 'model' }, { id: 'f2', label: '特征 2', dt: 'fe' }], outputs: [{ id: 'factor', label: 'Factor', dt: 'factor' }], params: [{ id: 'combine', label: '合成加权', type: 'select', value: 'equal', options: [{ value: 'equal', label: '等权' }, { value: 'ic', label: 'IC 加权' }, { value: 'icir', label: 'ICIR 加权' }], hint: '多个因子如何合成:等权 / 按样本内 rank-IC / 按 rank-ICIR(惩罚 IC 不稳定的因子)加权。IC/ICIR 仅用样本内(前60%调仓日)估权,防前视;负 IC 的腿自动反向。' }] },
  orthogonalize: { title: '因子正交化', cat: 'mf', inputs: [{ id: 'signal', label: '原始因子', dt: 'series' }], outputs: [{ id: 'factor', label: '正交因子', dt: 'factor' }], params: [{ id: 'industry', label: '行业控制', type: 'select', value: '是', options: ['是', '否'], hint: '使用当前运行面板的静态行业映射做截面残差化；未提供历史行业 vintage，严格 PIT=false 会在审计证据显形；未知行业行会诚实排除。' }, { id: 'size', label: '规模控制', type: 'select', value: '总市值', options: ['总市值', '流通市值', '不控制'], hint: '控制 log(总市值/流通市值)，不再用成交额冒充规模。' }, { id: 'styles', label: '风格控制', type: 'select', value: 'beta,volatility', options: [{ value: '', label: '不控制风格' }, { value: 'beta,volatility', label: 'Beta + 波动率' }, { value: 'beta,volatility,liquidity,momentum', label: '完整风格组' }], hint: '全部只使用截至当日的滚动值：60日Beta、20日波动、流动性、20日动量。' }, { id: 'old_factors', label: '已有因子', type: 'text', value: '', hint: '可选：填因子库名称或合法 DSL；多个因子请用分号或换行分隔（DSL 函数内的逗号不会被拆开）。' }, { id: 'min_obs', label: '每日最小样本', type: 'step', value: 20, step: 5 }, { id: 'max_condition_number', label: '条件数上限', type: 'text', value: '100000000', hint: '设计矩阵超过上限即该日失败关闭，不用不稳定残差。' }] },
  analysis: { title: '因子分析', cat: 'fa', inputs: [{ id: 'factor', label: '因子值', dt: 'factor' }], outputs: [{ id: 'report', label: '分析', dt: 'report' }], params: [{ id: 'rebal', label: '调仓频率', type: 'select', value: 'month', options: [{ value: 'day', label: '日频' }, { value: 'week', label: '周频' }, { value: 'month', label: '月频' }] }, { id: 'groups', label: '分组数量', type: 'step', value: 10, step: 1, hint: '分层回测的分位组数(经 /factor/report2 真生效);常用 5 或 10。' }, { id: 'dir', label: '因子方向', type: 'select', value: '0', options: [{ value: '0', label: '原始' }, { value: '-1', label: '取反 ×(-1)' }], hint: '取反 = 因子值乘 -1(把"越小越好"翻成"越大越好");经 /factor/report2 真生效。' }, { id: 'neutral', label: '中性化', type: 'select', value: '否', options: ['否', '是'], hint: '行业 + 市值中性化:逐日把因子对「行业哑变量 + log市值」做截面 OLS,取残差替代原值,剥离行业/规模暴露后再评 IC/分层(经 /factor/report2 真算)。诚实降级:面板缺市值→仅行业;行业全未知→仅市值;两者皆缺→跳过沿用原值(结果在告警显形)。' }] },
  tsic: { title: '个股时序IC', cat: 'fa', inputs: [{ id: 'factor', label: '因子', dt: 'series' }, { id: 'src', label: '数据源', dt: 'series' }], outputs: [{ id: 'tsic', label: '时序IC', dt: 'tsic' }], params: [{ id: 'fwd_days', label: '未来收益窗口', type: 'step', value: 20, step: 5, hint: '个股/单票口径:每股因子值 vs 它自己未来 N 日收益的 Spearman 相关。单票没有截面、IC 退化 → 用这个。接 公式因子 + 数据源(scope=个股/自选);不要套 rank、用时序/共振/财务因子。' }] },
  event: { title: '事件研究', cat: 'fa', inputs: [{ id: 'trigger', label: '触发', dt: 'series' }, { id: 'src', label: '数据源', dt: 'series' }], outputs: [{ id: 'event', label: '事件', dt: 'event' }], params: [{ id: 'horizons', label: '前向窗口', type: 'text', value: '1,5,10,20', hint: '事件触发后看几日收益(逗号分隔交易日)。事件研究=把 >0 触发式当离散事件,统计触发后 CAR/命中率/t值/逐年/盈亏比;反弹/异动/放量/跳空/消息面等稀疏触发用它,不要套 rank。跑股票池(市场调整=池内均值),单票退化看原始收益。' }, { id: 'direction', label: '方向解读', type: 'select', value: '0', options: [{ value: '0', label: '原始' }, { value: '-1', label: '取反' }], hint: '仅展示元数据,不改触发的 firing 判定。' }] },
  relstat: { title: '关系稳定度', cat: 'fa', inputs: [{ id: 'factor', label: '关系因子', dt: 'series' }, { id: 'src', label: '数据源', dt: 'series' }], outputs: [{ id: 'relstat', label: '稳定度', dt: 'relstat' }], params: [] },
  backtest: { title: '向量化回测', cat: 'bt', inputs: [{ id: 'factor', label: '因子', dt: 'factor' }, { id: 'pf', label: '组合(可选)', dt: 'portfolio' }], outputs: [{ id: 'result', label: '结果', dt: 'result' }], params: [{ id: 'cash', label: '初始资金', type: 'text', value: '1000000' }, { id: 'topn', label: 'TopN', type: 'step', value: 30, step: 5 }, { id: 'rebalance', label: '调仓频率', type: 'select', value: 'month', options: [{ value: 'day', label: '日频' }, { value: 'week', label: '周频' }, { value: 'month', label: '月频' }] }, { id: 'commission', label: '佣金率', type: 'step', value: 0.0003, step: 0.0001, dec: 4 }, { id: 'stamp_tax', label: '印花税', type: 'step', value: 0.0005, step: 0.0001, dec: 4 }, { id: 'slippage', label: '滑点bps', type: 'step', value: 5, step: 1 }, { id: 'combine', label: '多因子合成', type: 'select', value: 'equal', options: [{ value: 'equal', label: '等权' }, { value: 'ic', label: 'IC 加权' }, { value: 'icir', label: 'ICIR 加权' }], hint: '当上游是多因子(特征>1)时如何合成:等权 / 样本内 rank-IC / rank-ICIR 加权(防前视)。单因子时无效。' }, { id: 'weighting', label: '持仓定权', type: 'select', value: 'equal', options: [{ value: 'equal', label: '等权' }, { value: 'mktcap', label: '市值加权' }, { value: 'inv_vol', label: '反波动' }, { value: 'risk_parity', label: '风险平价(近似)' }, { value: 'min_var', label: '最小方差' }, { value: 'max_sharpe', label: '最大夏普' }, { value: 'true_risk_parity', label: '真风险平价' }, { value: 'black_litterman', label: 'Black-Litterman(LLM观点)' }], hint: '每期 TopN 持仓如何分配权重(真按此权重序列回测,非只等权)。反波动/风险平价用截至各调仓日的滚动波动(防前视)。接了「组合构建」节点时以其定权为准。' }, { id: 'vol_forecast', label: '波动口径', type: 'select', value: 'hist', options: [{ value: 'hist', label: '历史波动' }, { value: 'ewma', label: 'EWMA预测' }, { value: 'garch', label: 'GARCH预测' }], hint: '反波动/最小方差/风险平价等用哪种「波动」定权:历史=截至各调仓日的滚动波动(默认·快);EWMA/GARCH=条件波动预测(前瞻·捕捉波动聚集,用预测下期 σ 而非回看 σ)。GARCH 每票季度级重拟合,回测较慢。仅对用波动的定权法生效。' }] },
  overfit: { title: '过拟合审计', cat: 'fa', inputs: [{ id: 'result', label: '回测结果', dt: 'result' }], outputs: [{ id: 'audit', label: '审计报告', dt: 'audit' }], params: [{ id: 'confidence', label: '置信度', type: 'select', value: '0.95', options: [{ value: '0.95', label: '95%' }, { value: '0.99', label: '99%' }] }, { id: 'pbo_max', label: 'PBO 上限', type: 'step', value: 0.30, step: 0.05, dec: 2 }, { id: 'n_blocks', label: 'CSCV 分块', type: 'select', value: '8', options: [{ value: '8', label: '8块·快速' }, { value: '12', label: '12块·严格' }, { value: '16', label: '16块·深度' }], hint: '必须是偶数。完整试验矩阵由因子工厂/TrialLedger提供；单次手工回测会诚实显示“证据不完整”。' }] },
  portfolio:{ title: '组合构建', cat: 'bt', inputs: [{ id: 'factor', label: '因子', dt: 'factor' }], outputs: [{ id: 'pf', label: '组合', dt: 'portfolio' }], params: [{ id: 'topn', label: '持仓数', type: 'step', value: 30, step: 5 }, { id: 'weighting', label: '加权方式', type: 'select', value: 'equal', options: [{ value: 'equal', label: '等权' }, { value: 'mktcap', label: '市值加权' }, { value: 'inv_vol', label: '反波动' }, { value: 'risk_parity', label: '风险平价(近似)' }, { value: 'min_var', label: '最小方差' }, { value: 'max_sharpe', label: '最大夏普' }, { value: 'true_risk_parity', label: '真风险平价' }, { value: 'black_litterman', label: 'Black-Litterman(LLM观点)' }] }, { id: 'max_weight', label: '单票上限', type: 'step', value: 0, step: 0.05, dec: 2, hint: '单只股票权重上限(0 = 不限);如设 0.10 即每只 ≤10%,超出截断后剩余按比例重新归一。' }, { id: 'industry_neutral', label: '行业中性', type: 'select', value: '否', options: ['否', '是'], hint: '是 → 各行业等权、行业内再按加权方式分配(近似行业中性,需 industry 列;缺失自动跳过)。' }, { id: 'vol_forecast', label: '波动口径', type: 'select', value: 'hist', options: [{ value: 'hist', label: '历史波动' }, { value: 'ewma', label: 'EWMA预测' }, { value: 'garch', label: 'GARCH预测' }], hint: '反波动/最小方差/风险平价等用哪种「波动」定权:历史(默认·快)/ EWMA / GARCH 条件波动预测(前瞻·捕捉波动聚集)。仅对用波动的定权法生效。' }] },
  risk:     { title: '风险度量', cat: 'bt', inputs: [{ id: 'factor', label: '因子', dt: 'series' }], outputs: [{ id: 'risk', label: '风险', dt: 'risk' }], params: [{ id: 'topn', label: 'TopN', type: 'step', value: 30, step: 5, hint: '建 TopN 多头组合,算其损失分布尾部风险:VaR/CVaR(历史·参数·蒙特卡罗三法)+ EVT(POT+GPD 极值尾部)+ Kupiec VaR 回测。默认周频+3年窗(VaR 需更密更长的组合收益样本;月频1年仅~12期不够)。接 公式输入/因子库 因子表达式(像 tsic/事件研究),配数据源设股票池。' }] },
  garch:    { title: '条件波动预测', cat: 'bt', inputs: [{ id: 'factor', label: '因子', dt: 'series' }], outputs: [{ id: 'garch', label: '波动', dt: 'garch' }], params: [{ id: 'topn', label: 'TopN', type: 'step', value: 30, step: 5, hint: '建 TopN 多头组合,算其净值期收益 → 拟合 EWMA + GARCH(1,1):出条件波动路径(捕捉波动聚集)+ 向前多步波动预测(均值回复)。默认周频+3年窗。接 公式输入/因子库 因子表达式(像 tsic/risk),配数据源设股票池。与「风险度量」互补:risk 看尾部巨亏,garch 看波动随时间的演化与预测。' }, { id: 'horizon', label: '预测步数', type: 'step', value: 12, step: 1, hint: '向前多步预测的步数(单位=调仓频率;周频 12≈一季度)。GARCH 预测随步数均值回复到无条件波动。' }] },
  attrib:   { title: '风格归因', cat: 'bt', inputs: [{ id: 'factor', label: '因子', dt: 'series' }], outputs: [{ id: 'attrib', label: '归因', dt: 'attrib' }], params: [{ id: 'topn', label: 'TopN', type: 'step', value: 30, step: 5, hint: '建 TopN 多头组合算其期收益 → 对四风格因子收益(市场MKT/规模SMB小盘减大盘/价值HML高BM减低BM/动量WML赢家减输家)做 OLS+Newey-West HAC:出因子暴露β(各风格载荷+显著性)+alpha(风格无法解释的超额)+R²(风格解释力)+各因子收益贡献。默认月频+3年窗。接 公式输入/因子库 因子表达式(像 risk/garch),配数据源设股票池。看「收益来自小盘/价值/动量哪种风格、有没有真alpha」。' }] },
  tvbeta:   { title: '时变β(Kalman)', cat: 'bt', inputs: [{ id: 'factor', label: '因子', dt: 'series' }], outputs: [{ id: 'tvbeta', label: '时变β', dt: 'tvbeta' }], params: [{ id: 'topn', label: 'TopN', type: 'step', value: 30, step: 5, hint: '建 TopN 多头组合算其期收益 → 对市场期收益(默认沪深300,可在 source 节点设 benchmark;取不到回退全池等权)做时变参数回归 r=α_t+β_t·m_t:Kalman 滤波(因果·实时β)+ RTS 平滑(全样本·去噪β),平滑度由浓缩似然自动 MLE 选(平滑线平=无统计显著漂移,非bug)。出 β(t) 演化路径+静态β对照。默认周频+3年窗。接 公式输入/因子库 因子表达式(像 risk/garch/attrib)。看「我对大盘的暴露随时间怎么漂、现在高β还是低β」(attrib 看静态β,tvbeta 看 β 演化)。' }] },
};

// Historical rule replay is a distinct typed path; snapshot candidates cannot feed it.
SPECS.history_screen = { title: '历史逐日筛选', cat: 'io', inputs: [{ id: 'src', label: '日线数据源', dt: 'series' }], outputs: [{ id: 'history', label: '逐日候选历史', dt: 'screen_history' }], params: [
  { id: 'label', label: '策略名称', type: 'text', value: '自定义逐日筛选' },
  { id: 'evaluation_start', label: '评估起日', type: 'text', value: '', hint: '数据源起日包含预热；此处填写实际开始记账的 YYYY-MM-DD。' },
  { id: 'ranking', label: '统一排序', type: 'select', value: 'nearest_ma10_ma20', options: [{ value: 'nearest_ma10_ma20', label: '贴近 MA10/20' }] },
  { id: 'branches', label: '硬规则分支', type: 'history_branches', value: [] },
] };
SPECS.daily_backtest = { title: '日频模拟调仓', cat: 'bt', inputs: [{ id: 'history', label: '逐日候选历史', dt: 'screen_history' }], outputs: [{ id: 'result', label: '模拟结果', dt: 'result' }], params: [
  { id: 'topn', label: '固定槽位数', type: 'step', value: 10, step: 1 },
  { id: 'rebalance', label: '调仓频率', type: 'select', value: 'day', options: [{ value: 'day', label: '日频' }] },
  { id: 'weight_mode', label: '仓位方式', type: 'select', value: 'fixed_slots', options: [{ value: 'fixed_slots', label: '固定槽位留现金' }] },
  { id: 'initial_cash', label: '初始资金(元)', type: 'text', value: 1000000 },
  { id: 'commission', label: '双边佣金率', type: 'step', value: 0.0003, step: 0.0001, dec: 4 },
  { id: 'sell_tax', label: '卖出税率', type: 'step', value: 0.0005, step: 0.0001, dec: 4 },
  { id: 'slippage_bps', label: '双边滑点bp', type: 'step', value: 5, step: 1 },
] };

// Backtest dates remain optional to preserve the existing one-year default, but
// become explicit/replayable when an audit needs a longer track record.
SPECS.source.params.push({ id: 'research_freq', label: '研究采样', type: 'select', value: '', options: [
  { value: '', label: '兼容原节点默认' }, { value: 'day', label: '日频研究' },
  { value: 'week', label: '周频研究' }, { value: 'month', label: '月频研究' },
], hint: '研究采样与标签周期，不是行情周期；5交易日指标选日频。调仓仍由终端单独设置。留空保留旧图默认。' });
SPECS.orthogonalize.inputs.push({ id: 'src', label: '数据源(可选)', dt: 'series' });
SPECS.backtest.params.splice(1, 0,
  { id: 'start', label: '回测起日', type: 'text', value: '', hint: '可选 YYYY-MM-DD；留空沿用旧默认窗口。若后接过拟合审计，月频建议至少 24–36 个月。' },
  { id: 'end', label: '回测止日', type: 'text', value: '', hint: '可选 YYYY-MM-DD；留空到当前最新可用数据。' },
);
const _overfitBlocksParam = SPECS.overfit.params.find(p => p.id === 'n_blocks');
if (_overfitBlocksParam) _overfitBlocksParam.hint = '必须是 4–16 的偶数，8 块至少需 16 个收益期；月频建议回测 24–36 个月。完整试验矩阵由因子工厂/TrialLedger提供；单次手工回测会诚实显示“证据不完整”。';

// 数据筛选 Skill 使用真实全 A 当前上市快照；旧 all 是指数成分并集，不能继续
// 在选择器里冒充全市场。两个口径都保留，老工作流仍可按原值重放。
const _sourceUniverseParam = SPECS.source.params.find(p => p.id === 'universe');
if (_sourceUniverseParam && Array.isArray(_sourceUniverseParam.options)) {
  const legacyAll = _sourceUniverseParam.options.find(o => o && o.value === 'all');
  if (legacyAll) legacyAll.label = '旧版指数并集 · 非全A';
  if (!_sourceUniverseParam.options.some(o => o && o.value === 'all_a')) {
    const legacyIndex = _sourceUniverseParam.options.indexOf(legacyAll);
    _sourceUniverseParam.options.splice(legacyIndex < 0 ? 1 : legacyIndex, 0,
      { value: 'all_a', label: '全A · 筛选 Skill 专用' });
  }
}
const _screenBiasMin = SPECS.data_screen.params.find(p => p.id === 'min_bias');
const _screenBiasMax = SPECS.data_screen.params.find(p => p.id === 'max_bias');
if (_screenBiasMin) _screenBiasMin.hint = '使用小数口径；0 表示收盘不低于均线。';
if (_screenBiasMax) _screenBiasMax.hint = '使用小数口径；0.03 表示最多高于均线 3%。';

const CATALOG = [
  { g: '01 · 基础工具', items: ['source', 'data_screen', 'history_screen', 'formula', 'factorlib', 'model'] },
  { g: '02 · 特征工程', items: ['feature'] },
  { g: '03 · 机器学习', items: ['xgb', 'lgbm', 'svm', 'rf', 'nn', 'lstm'] },
  { g: '04 · 因子相关', items: ['pca', 'spearman', 'orthogonalize', 'iccalc', 'mf', 'analysis', 'tsic', 'event', 'relstat'] },
  { g: '05 · 回测相关', items: ['portfolio', 'backtest', 'daily_backtest', 'risk', 'garch', 'attrib', 'tvbeta'] },
  { g: '06 · 验证与审计', items: ['validate', 'overfit'] },
];

// ───────── 工具: 节点布局 / 端口坐标 ─────────
function rowsOf(spec) {
  return [
    ...spec.inputs.map(p => ({ kind: 'in', port: p })),
    ...spec.outputs.map(p => ({ kind: 'out', port: p })),
    ...spec.params.map(p => ({ kind: 'param', param: p })),
  ];
}
function nodeHeight(spec) { return HEADER + PAD * 2 + rowsOf(spec).length * ROW + (spec === SPECS.history_screen ? 7 * ROW : 0); }
function portXY(node, portId, side) {
  const spec = SPECS[node.type]; const rows = rowsOf(spec);
  const i = rows.findIndex(r => (r.kind === 'in' || r.kind === 'out') && r.port.id === portId && r.kind === side);
  const cy = node.y + HEADER + PAD + i * ROW + ROW / 2;
  return { x: side === 'in' ? node.x : node.x + W, y: cy };
}

// ───────── 种子图 ─────────
let _id = 0; const nid = () => 'n' + (++_id);
function seedGraph() {
  // 单模型示例必须如实走 model → mf → factor；旧图把两条 XGB 分支
  // 同时塞进 mf，但执行器只采用第一个模型报告，画面与实际计算不一致。
  return tplG({ scope: '小池', code: '', universe: 'csi300', oos_frac: '0.3' }, [
    { type: 'formula', params: { expr: 'rank(ts_sum(returns,20))' } },
    { type: 'feature', params: { tag: 'IC' } },
    { type: 'xgb', params: { trees: 100, depth: 3, lr: 0.1, sub: 1.0 } },
    { type: 'mf', params: { combine: 'equal' } },
    { type: 'analysis', params: { rebal: 'month', groups: 10, dir: '0' } },
  ]);
}

// ───────── 链式建图 (左→右、按端口类型自动连线) ─────────
function compatiblePorts(sourceNode, targetNode) {
  const sourceSpec = SPECS[sourceNode && sourceNode.type];
  const targetSpec = SPECS[targetNode && targetNode.type];
  if (!sourceSpec || !targetSpec) return null;
  // 目标输入顺序表达节点作者的首选语义，例如 feature.feat、mf.m1、
  // backtest.factor。src 只可能匹配 source.data，不会抢走普通 series。
  for (const targetPort of targetSpec.inputs) {
    for (const sourcePort of sourceSpec.outputs) {
      if (dtCompatible(sourcePort.dt, targetPort.dt, targetNode.type, sourceNode.type, targetPort.id)) {
        return { sourcePort, targetPort };
      }
    }
  }
  return null;
}

function connectCompatible(sourceNode, targetNode) {
  const ports = compatiblePorts(sourceNode, targetNode);
  if (!ports) {
    const from = SPECS[sourceNode.type] ? SPECS[sourceNode.type].title : sourceNode.type;
    const to = SPECS[targetNode.type] ? SPECS[targetNode.type].title : targetNode.type;
    throw new Error('无法自动连接「' + from + '」→「' + to + '」：没有兼容端口');
  }
  return { from: [sourceNode.id, ports.sourcePort.id], to: [targetNode.id, ports.targetPort.id] };
}

function chain(steps) {
  const nodes = steps.map((s, i) => {
    const spec = SPECS[s && s.type];
    if (!spec) throw new Error('未知节点类型: ' + (s && s.type));
    return { id: nid(), type: s.type, x: 60 + i * 282, y: 230 - nodeHeight(spec) / 2, params: Object.assign({}, ...spec.params.map(p => ({ [p.id]: p.value })), s.params || {}) };
  });
  const edges = [];
  for (let i = 0; i < nodes.length - 1; i++) {
    edges.push(connectCompatible(nodes[i], nodes[i + 1]));
  }
  return { nodes, edges };
}

// ───────── 策略模板建图(W8a)─────────
// 在 chain 头部接一个真连线的「数据源」:source.data → feature.src。formula 无入口,纯 chain
// 时 source 会孤立(只全局 universe 生效);显式连到 feature.src 后,池 / 起止日 / 自选代码 /
// 对标指数 / 样本外占比 才经 _universeForNode 回溯真下传 → 模板里的共振/样本外/绩效真生效。
function tplG(srcParams, steps) {
  const src = { id: nid(), type: 'source', x: 60, y: 230, params: Object.assign({}, ...SPECS.source.params.map(p => ({ [p.id]: p.value })), srcParams || {}) };
  const c = chain(steps);
  c.nodes.forEach(n => { n.x += 282; });   // 给 source 腾出最左列
  const contextNode = c.nodes.find(n => n.type === 'data_screen') || c.nodes.find(n => n.type === 'feature') || c.nodes.find(n => n.type === 'orthogonalize');
  const edges = c.edges.slice();
  if (contextNode) edges.push(connectCompatible(src, contextNode));
  return { nodes: [src, ...c.nodes], edges };
}

// 空间私有因子不是一张已保存工作流；打开时只把受作用域校验的原表达式
// 确定性铺成可审阅图，不自动运行。单票/ETF 本体使用时序 IC，成分池/自定义池
// 才使用截面 IC，避免把单票因子误套到 rank/分组口径。
function workspaceFactorGraph(asset) {
  const expr = String((asset && asset.expr) || '').trim();
  const applicability = String((asset && asset.applicability) || 'subject');
  const subject = (asset && asset.subject) || {};
  const code = String(subject.code || '').trim();
  if ((applicability === 'subject' || applicability === 'single_security') && code) {
    const src = { id: nid(), type: 'source', x: 60, y: 230,
      params: Object.assign({}, ...SPECS.source.params.map(p => ({ [p.id]: p.value })),
        { scope: '个股', code, codes: code }) };
    const c = chain([
      { type: 'formula', params: { expr } },
      { type: 'tsic', params: { fwd_days: 20 } },
    ]);
    c.nodes.forEach(n => { n.x += 282; });
    const tsic = c.nodes.find(n => n.type === 'tsic');
    const edges = c.edges.slice();
    if (tsic) edges.push({ from: [src.id, 'data'], to: [tsic.id, 'src'] });
    return { nodes: [src, ...c.nodes], edges };
  }
  // 资产当前只有自由文本 scope_detail，没有可执行的成分代码/自定义池 ID。
  // 因而绝不把 ETF 列表或 csi300 冒充原池；保留「自动」让用户先选真实池。
  return tplG({ scope: '自选', code: '', codes: '', universe: '自动', oos_frac: '0.3', _workspace_pool_unresolved: true }, [
    { type: 'formula', params: { expr } },
    { type: 'feature', params: { tag: 'IC' } },
    { type: 'spearman', params: {} },
    { type: 'analysis', params: { rebal: 'month', groups: 10, dir: '0' } },
  ]);
}

function graphNeedsPoolReview(nodes) {
  return Array.isArray(nodes) && nodes.some(n => {
    if (!n || n.type !== 'source') return false;
    const p = n.params || {};
    const unresolvedSource = p._workspace_pool_unresolved === true || p.scope === '自选';
    return unresolvedSource && (!p.universe || p.universe === '自动') && !_parseCodes(p.codes).length;
  });
}

// ───────── 经验卡 → 工作流模板 ─────────
// W8a 一键策略模板:全用真 16 字段、数据源真连线、载入即可跑;并示范这轮新能力
// (e1/e3 样本外体检、e3 全绩效回测、e7 大盘共振、e8 行业共振)。质量/成长财务因子 W1b 未接 → 不做。
const CARD_GRAPH = {
  e1: () => tplG({ universe: 'csi300', oos_frac: '0.3' }, [ { type: 'formula', params: { expr: 'rank(ts_sum(returns,20))' } }, { type: 'feature', params: { tag: 'IC' } }, { type: 'spearman', params: {} }, { type: 'analysis', params: { rebal: 'month', groups: 10, dir: '0' } } ]),
  e2: () => tplG({ universe: 'csi500' }, [ { type: 'formula', params: { expr: '-rank(ts_sum(returns,5))' } }, { type: 'feature', params: { tag: 'IC' } }, { type: 'spearman', params: {} }, { type: 'iccalc', params: { period: 5 } } ]),
  e3: () => tplG({ universe: 'csi800', oos_frac: '0.3' }, [ { type: 'formula', params: { expr: 'rank(-stddev(returns,20))' } }, { type: 'feature', params: { tag: 'fwd_ret' } }, { type: 'backtest', params: { topn: 50, rebalance: 'month' } } ]),
  e4: () => tplG({ universe: 'csi300' }, [ { type: 'formula', params: { expr: 'rank(-pb)' } }, { type: 'feature', params: { tag: 'IC' } }, { type: 'spearman', params: {} }, { type: 'analysis', params: { rebal: 'month', groups: 10, dir: '0' } } ]),
  e5: () => tplG({ universe: 'csi300' }, [ { type: 'formula', params: { expr: 'rank(dv_ttm)' } }, { type: 'feature', params: { tag: 'IC' } }, { type: 'spearman', params: {} }, { type: 'analysis', params: { rebal: 'month', groups: 5, dir: '0' } } ]),
  e6: () => tplG({ universe: 'csi500' }, [ { type: 'formula', params: { expr: '-rank(turnover_rate)' } }, { type: 'feature', params: { tag: 'IC' } }, { type: 'spearman', params: {} }, { type: 'analysis', params: { rebal: 'week', groups: 5, dir: '0' } } ]),
  e7: () => tplG({ universe: 'csi300', benchmark: 'csi300' }, [ { type: 'formula', params: { expr: 'correlation(returns, idx_ret, 20)' } }, { type: 'feature', params: { tag: 'IC' } }, { type: 'spearman', params: {} }, { type: 'analysis', params: { rebal: 'week', groups: 10, dir: '0' } } ]),
  e8: () => tplG({ universe: 'csi300' }, [ { type: 'formula', params: { expr: 'correlation(returns, indmean(returns, industry), 20)' } }, { type: 'feature', params: { tag: 'IC' } }, { type: 'spearman', params: {} }, { type: 'analysis', params: { rebal: 'week', groups: 10, dir: '0' } } ]),
};

// ───────── LLM 一句话 → 工作流 (关键词解析) ─────────
function generateFromText(q) {
  const raw = String(q || '');
  const t = raw.toLowerCase();
  const srcParams = { scope: /自选/.test(raw) ? '自选' : /全市场|全部/.test(raw) ? '全市场' : '小池', universe: /500/.test(raw) ? 'csi500' : /800/.test(raw) ? 'csi800' : 'csi300' };
  const expr = /反转/.test(raw) ? '-rank(ts_sum(returns,5))' : /动量/.test(raw) ? 'rank(ts_sum(returns,20))' : /低波|波动/.test(raw) ? 'rank(-stddev(returns,20))' : /价值|估值|低估/.test(raw) ? 'rank(-pb)' : /股息/.test(raw) ? 'rank(dv_ttm)' : 'close';
  const wantsOrthogonalize = /正交|去相关|去共线|剥离.{0,6}暴露/.test(raw);
  const wantsAudit = /过拟合|审计|\bpbo\b|\bpsr\b|\bdsr\b|mintrl/.test(t);
  const explicitDataScreen = /\bdata[_\s-]*screen(?:\s+skill)?\b/.test(t) || /数据筛选\s*skill/i.test(raw);
  const chineseDataScreen = /筛出|找出|选出|选股|沿.{0,8}(日线|均线)|不破.{0,6}(日线|均线)/.test(raw)
    && /(日线|均线|ma[\s_-]*\d+)/i.test(raw);
  const englishDataScreen = /\b(?:screen|select|find)\b/.test(t) && /\bma[\s_-]*\d{1,3}\b/.test(t);
  const wantsDataScreen = explicitDataScreen || chineseDataScreen || englishDataScreen;
  if (wantsDataScreen) {
    const m = raw.match(/(\d{1,3})\s*日(?:线|均线)/) || raw.match(/\bma[\s_-]*(\d{1,3})\b/i);
    const maWindow = m ? Math.max(2, Math.min(250, +m[1])) : 5;
    const fastPool = /\bcsi[\s_-]*fast\b/.test(t) || /快速测试池/.test(raw);
    const screenSource = fastPool
      ? { scope: '小池', universe: 'csi_fast' }
      : { scope: '全市场', universe: 'all_a' };
    return tplG(screenSource, [
      { type: 'data_screen', params: { preset: 'ma_trend', ma_window: maWindow,
        rising_transitions: 3, below_lookback: 5, max_consecutive_below: 1,
        min_bias: 0, max_bias: 0.03, top_n: 5, min_avg_amount_yi: 1, amount_window: 20 } },
    ]);
  }
  if (wantsOrthogonalize) {
    const steps = [
      { type: 'formula', params: { expr } },
      { type: 'orthogonalize', params: {} },
      { type: 'backtest', params: wantsAudit ? { start: new Date(Date.now() - 3 * 365.25 * 86400000).toISOString().slice(0, 10), rebalance: 'month' } : {} },
    ];
    if (wantsAudit) steps.push({ type: 'overfit', params: {} });
    return tplG(srcParams, steps);
  }
  if (wantsAudit) {
    return tplG(srcParams, [
      { type: 'formula', params: { expr } },
      { type: 'feature', params: { tag: 'IC' } },
      { type: 'spearman', params: {} },
      { type: 'backtest', params: { start: new Date(Date.now() - 3 * 365.25 * 86400000).toISOString().slice(0, 10), rebalance: 'month' } },
      { type: 'overfit', params: {} },
    ]);
  }
  // srcParams 在函数入口已经按用户文本解析完成。source 不能放进 chain：
  // formula 无入口，那会使 source 孤立。统一交给 tplG 建 source.data→feature.src。
  const steps = [{ type: 'formula', params: { expr } }, { type: 'feature', params: { tag: 'IC' } }];
  let modelType = '';
  if (/lightgbm|lgbm/.test(t)) modelType = 'lgbm';
  else if (/随机森林|forest|\brf\b/.test(t)) modelType = 'rf';
  else if (/\bsvm\b/.test(t)) modelType = 'svm';
  else if (/机器学习|\bml\b|xgb|模型/.test(t)) modelType = 'xgb';
  if (modelType) steps.push({ type: modelType, params: {} }, { type: 'mf', params: {} });

  const wantsBacktest = /回测|backtest|实战/.test(t);
  const wantsIC = /\bic\b|体检|相关/.test(t);
  // feature.fe 是特征工程产物，不是可直接交给 analysis/iccalc 的 factor。
  // 非 ML 链先经 spearman 物化为 factor；ML 链由 mf 将 model 物化为 factor。
  if (!modelType && !wantsBacktest) steps.push({ type: 'spearman', params: {} });
  if (wantsBacktest) steps.push({ type: 'backtest', params: {} });
  else if (wantsIC) steps.push({ type: 'iccalc', params: {} });
  else steps.push({ type: 'analysis', params: {} });
  return tplG(srcParams, steps);
}

// ───────── 克隆图 (重新分配 id，保留位置/参数) ─────────
function cloneGraph(nodes, edges) {
  const map = {};
  const ns = nodes.map(n => { const id = nid(); map[n.id] = id; return { ...n, id, params: { ...n.params } }; });
  ns.forEach(n => { if (n.params.context_source_id) n.params.context_source_id = map[n.params.context_source_id] || n.params.context_source_id; });
  const es = edges.map(e => ({ from: [map[e.from[0]], e.from[1]], to: [map[e.to[0]], e.to[1]] }));
  return { nodes: ns, edges: es };
}

// ───────── 历史工作流存储 (localStorage) ─────────
const WF_KEY = 'guanlan:wf:list:v1' + WW_NS;
function wfLoadList() {
  try { const r = JSON.parse(localStorage.getItem(WF_KEY)); if (Array.isArray(r)) return r; } catch (e) {}
  // 研究空间首次进入保持空历史；示例只属于无 workspace 的旧全局页面，避免把演示项伪装为空间资产。
  if (WW_WORKSPACE_ID) return [];
  // 旧全局首次:预置两份示例
  const seed = [
    { id: 'w_demo1', name: '示例 · 反转 + ML · 周频', ts: Date.now() - 86400000 * 2, ...generateFromText('反转因子加机器学习并回测 沪深300') },
    { id: 'w_demo2', name: '示例 · 动量 · LightGBM 日频', ts: Date.now() - 3600000 * 5, ...generateFromText('动量 LightGBM 回测 中证500') },
  ];
  try { localStorage.setItem(WF_KEY, JSON.stringify(seed)); } catch (e) {}
  return seed;
}
function wfSaveList(list) { try { localStorage.setItem(WF_KEY, JSON.stringify(list)); } catch (e) {} }
function wfIsGlobalTemplate(w) {
  return !!WW_WORKSPACE_ID && !!w && (w.read_only === true || w.is_template === true || w.scope === 'global');
}
function wfNormalizeRemote(w) {
  if (!w || typeof w !== 'object') return w;
  const scope = w.scope || (w.workspace_id ? 'workspace' : 'global');
  const template = !!WW_WORKSPACE_ID && scope === 'global';
  return { ...w, scope, read_only: w.read_only === true || template, is_template: w.is_template === true || template };
}

// 每个工作流的「最近一次运行报告」缓存(按工作流名 key)→ 切换工作流再切回来报告不丢、刷新也在。
const WF_REP_KEY = 'guanlan:wf:reports:v1' + WW_NS;   // 帷幄嵌入按会话隔离(WW_NS),独立页全局
function wfLoadReports() { try { return JSON.parse(localStorage.getItem(WF_REP_KEY) || '{}') || {}; } catch (e) { return {}; } }
function wfSaveReports(map) {
  try { localStorage.setItem(WF_REP_KEY, JSON.stringify(map)); }
  catch (e) {                                   // 报告可能很大 → 配额溢出时只保留最近 8 份再试
    try {
      const keys = Object.keys(map), keep = {};
      keys.slice(Math.max(0, keys.length - 8)).forEach(k => { keep[k] = map[k]; });
      localStorage.setItem(WF_REP_KEY, JSON.stringify(keep));
    } catch (e2) {}
  }
}
function wfScopeOf(w) {
  if (w && w.scope === 'global') return 'global';
  if (w && (w.scope === 'workspace' || w.workspace_id)) return 'workspace';
  return WW_WORKSPACE_ID ? 'workspace' : 'global';
}
function wfReportKey(name, scope) {
  // 同一空间内同名保存是覆盖语义，因此 scope + name 是稳定且足够的身份。
  // 这也让“全局只读模板”和“当前空间同名副本”的最近结果严格分开。
  return (scope || (WW_WORKSPACE_ID ? 'workspace' : 'global')) + ':' + String(name || '');
}

// 「上次会话」=最近运行/载入过的工作流(名+图)。进页恢复它 → 刷新/关页再回来接着上次,不必重跑。
const WF_LAST_KEY = 'guanlan:wf:last:v1' + WW_NS;     // 帷幄嵌入按会话隔离(WW_NS),独立页全局
function wfLoadLast() { try { return JSON.parse(localStorage.getItem(WF_LAST_KEY) || 'null'); } catch (e) { return null; } }
function wfSaveLast(obj) { try { localStorage.setItem(WF_LAST_KEY, JSON.stringify(obj)); } catch (e) {} }
// 服务端持有的长回测只在浏览器保存 run_id + 图快照。切页/刷新回来按 id
// 重连进度和结果；不再让 React 组件或一次 fetch 充当任务所有者。
const WF_RUN_KEY = 'guanlan:wf:run:v1' + WW_NS;
function wfLoadRunRef() { try { return JSON.parse(localStorage.getItem(WF_RUN_KEY) || 'null'); } catch (e) { return null; } }
function wfSaveRunRef(ref) { try { localStorage.setItem(WF_RUN_KEY, JSON.stringify(ref)); } catch (e) {} }
function wfClearRunRef() { try { localStorage.removeItem(WF_RUN_KEY); } catch (e) {} }
function wfNewRunId() {
  const raw = (window.crypto && crypto.randomUUID)
    ? crypto.randomUUID().replace(/-/g, '')
    : (Date.now().toString(16) + Math.random().toString(16).slice(2));
  return 'wr_' + raw.slice(0, 24).padEnd(12, '0');
}
function wfRunStatusLabel(status, rec) {
  const displayStatus = wfRunIsInterrupted(rec) ? 'interrupted' : status;
  return ({ queued: '排队中', running: '运行中', completed: '已完成', completed_with_errors: '完成 · 含节点错误', failed: '失败', interrupted: '已中断' })[displayStatus] || String(displayStatus || '状态未知');
}
function wfRunIsInterrupted(rec) {
  // Older server records retain status=failed for compatibility; this is display-only.
  return !!rec && (rec.status === 'interrupted' || (rec.status === 'failed' &&
    (rec.interrupted === true || rec.failure_kind === 'orphaned' || rec.failure_kind === 'interrupted')));
}
function wfPinRunLocation(runId) {
  // 从历史图手动启动新任务后，刷新必须跟随新 id，不能仍打开旧 ?run 图。
  if (!/^wr_[0-9a-f]{12,40}$/.test(String(runId || ''))) return;
  try {
    const url = new URL(location.href);
    ['run', 'import', 'load', 'asset', 'q'].forEach(key => url.searchParams.delete(key));
    url.searchParams.set('run', runId);
    window.history.replaceState(null, '', url.pathname + url.search + url.hash);
  } catch (e) { /* URL 不可更新时仍保留本地 run_id 和服务端历史 */ }
}
function wfRunRecordError(rec, workspaceId) {
  if (!rec || rec.ok === false || !/^wr_[0-9a-f]{12,40}$/.test(String(rec.id || ''))) return (rec && rec.reason) || '缺少有效运行记录';
  if (String(rec.workspace_id || '') !== String(workspaceId || '')) return '运行记录不属于当前研究空间';
  if (!rec.graph || !Array.isArray(rec.graph.nodes) || !rec.graph.nodes.length || !Array.isArray(rec.graph.edges)) return '运行记录没有可恢复的图快照';
  return validateGraph(rec.graph.nodes, rec.graph.edges) || '';
}
function wfAcceptGraphMessage(event, token, workspaceId, sessionId) {
  if (!token || event.origin !== location.origin || event.source !== window.parent || window.parent === window) return null;
  const msg = event.data || {}, p = msg.payload || {};
  if (msg.type !== 'guanlan.workflow.import' || msg.token !== token || p.workspace_id !== workspaceId || (p.session_id || '') !== (sessionId || '')) return null;
  return p;
}
function wfNeedsReview(w) {
  const pending = w && w.migration && w.migration.needs_review;
  return !!(w && ((w.legacy_recovery && w.legacy_recovery.auto_repairable === false) || (Array.isArray(pending) && pending.length) || (w.validation && w.validation.ok === false)));
}
function wfReviewReason(w) {
  const legacy = w && w.legacy_recovery;
  if (legacy && legacy.auto_repairable === false) {
    const label = legacy.classification === 'historical_model_board' ? '历史模型看板' : '旧研究草图';
    const blocker = (legacy.blockers || []).find(x => x && x.reason);
    return label + '不可自动执行；原图保持不变，需要另建研究工作流。' + (blocker ? ' ' + blocker.reason : '');
  }
  const pending = w && w.migration && w.migration.needs_review;
  const first = Array.isArray(pending) && pending.length ? pending[0] : null;
  return (first && first.reason) || ((w && w.validation && w.validation.errors) || [])[0] || '旧版节点没有现行执行契约，请保留原图并按新节点重建';
}

// 画布端口的“可执行兼容”必须与两套执行器保持一致。公式/因子库的
// series 本来就能由因子分析、回测和组合按 expr/name 物化；特征工程
// fe 则只有回测/组合能直接消费多特征 spec，不能放宽给 analysis/iccalc。
function dtCompatible(sourceDt, targetDt, targetType, sourceType, targetPort) {
  if (targetDt === 'screen_history' || sourceDt === 'screen_history') return sourceDt === 'screen_history' && targetDt === 'screen_history' && sourceType === 'history_screen' && targetType === 'daily_backtest' && targetPort === 'history';
  // source.data 是上下文载体，不是可计算因子序列；即使 dt 同为 series 也只能进 src。
  if (targetPort === 'src') return sourceType === 'source' && sourceDt === 'series' && targetDt === 'series';
  if (sourceType === 'source') return false;
  if (sourceDt === targetDt) return true;
  if (sourceDt === 'series' && targetDt === 'factor'
      && (sourceType === 'formula' || sourceType === 'factorlib')) return true;
  if (sourceDt === 'fe' && targetDt === 'factor' && sourceType === 'feature'
      && (targetType === 'backtest' || targetType === 'portfolio')) return true;
  return false;
}

const WF_TERMINAL_DT = { data_screen: 1, report: 1, ic: 1, result: 1, audit: 1, portfolio: 1, tsic: 1, event: 1, relstat: 1, risk: 1, garch: 1, attrib: 1, tvbeta: 1, validation: 1 };

function requiredInputGroups(node) {
  const type = node && node.type;
  const params = (node && node.params) || {};
  if (type === 'source' || type === 'formula' || type === 'factorlib' || type === 'model') return [];
  if (type === 'feature') return [['feat']];                         // label/src 可选
  if (type === 'mf') return [['m1', 'm2', 'f1', 'f2']];            // 至少一个可物化信号
  if (type === 'backtest') return [['factor', 'pf']];                // factor 或已构建组合
  if (type === 'history_screen') return [['src']];
  if (type === 'daily_backtest') return [['history']];
  if (type === 'validate') return String(params.model_id || '').trim() ? [] : [['model']];
  if (type === 'orthogonalize') return [['signal']];                 // src 可选
  if (type === 'tsic' || type === 'relstat') return [['factor']];    // src 可选
  if (type === 'event') return [['trigger']];                        // src 可选
  const spec = SPECS[type];
  return spec ? spec.inputs.filter(p => p.id !== 'src' && p.id !== 'label').map(p => [p.id]) : [];
}

// 唯一图契约入口。保存、导入、载入、深链、AI 和运行都调用同一个纯函数；
// 画布拖线规则也复用 dtCompatible，避免“能画不能跑”或“能导入但一运行才报错”。
function graphValidationErrors(gn, ge) {
  const errors = [];
  const add = msg => { if (msg && !errors.includes(msg)) errors.push(msg); };
  if (!Array.isArray(gn) || !gn.length) return ['图为空 — 无节点'];
  if (!Array.isArray(ge)) return ['图的 edges 必须是列表'];

  const byId = {};
  for (let i = 0; i < gn.length; i++) {
    const n = gn[i];
    if (!n || typeof n !== 'object') { add('第 ' + (i + 1) + ' 个节点不是对象'); continue; }
    if (typeof n.id !== 'string' || !n.id.trim()) { add('存在无 id 节点'); continue; }
    if (byId[n.id]) add('节点 id 重复: ' + n.id);
    else byId[n.id] = n;
    if (!SPECS[n.type]) add('未知节点类型: ' + n.type);
    if (n.params != null && (typeof n.params !== 'object' || Array.isArray(n.params))) add('节点 ' + n.id + ' 的 params 必须是对象');
    const p = (n.params && typeof n.params === 'object') ? n.params : {};
    if (n.type === 'formula') {
      const expr = String(p.expr || '').trim();
      if (!expr) add('公式输入 ' + n.id + ' 缺少表达式');
      else if (/__|\bimport\b|\blambda\b/i.test(expr)) add('公式输入 ' + n.id + ' 含不允许的表达式片段');
    }
    if (n.type === 'factorlib' && !String(p.name || p.expr || '').trim()) add('因子库 ' + n.id + ' 尚未选择因子或填写表达式');
    if (n.type === 'model' && !String(p.model_id || '').trim()) add('模型节点 ' + n.id + ' 尚未选择研究库模型');
    if (n.type === 'history_screen') {
      try { wfParseHistoryBranches(JSON.stringify(p.branches)); } catch (e) { add('历史逐日筛选 ' + n.id + ': ' + e.message); }
      if ((p.ranking || 'nearest_ma10_ma20') !== 'nearest_ma10_ma20') add('历史逐日筛选只支持固定贴线度排序');
    }
    if (n.type === 'daily_backtest') {
      if ((p.rebalance || 'day') !== 'day' || (p.weight_mode || 'fixed_slots') !== 'fixed_slots') add('日频模拟调仓仅支持日频与固定槽位，未用槽位保留现金');
    }
    if (n.type === 'source' && p.universe != null && String(p.universe).trim()) {
      const universeParam = SPECS.source.params.find(x => x.id === 'universe');
      const allowed = (universeParam && universeParam.options || []).map(x => typeof x === 'object' ? String(x.value) : String(x));
      if (!allowed.includes(String(p.universe))) add('数据源 ' + n.id + ' 的股票池不在可选范围: ' + p.universe);
    }
  }
  if (gn.filter(n => n && n.type === 'overfit').length > 1) add('每张工作流最多一个「过拟合审计」节点');

  const validEdges = [];
  const structuralEdges = [];
  const seenEdge = new Set();
  const seenInput = new Set();
  const incoming = {};
  const screenSource = {};
  const allASourceEdges = {};
  gn.filter(n => n && n.type === 'source' && String((n.params && n.params.universe) || '').trim() === 'all_a')
    .forEach(n => { allASourceEdges[n.id] = 0; });

  for (let i = 0; i < ge.length; i++) {
    const e = ge[i];
    if (!e || typeof e !== 'object' || !Array.isArray(e.from) || e.from.length !== 2 || !Array.isArray(e.to) || e.to.length !== 2) {
      add('第 ' + (i + 1) + ' 条连线的 from/to 必须是 [节点id, 端口id]'); continue;
    }
    const sN = byId[e.from[0]], tN = byId[e.to[0]];
    if (!sN || !tN) { add('连线指向不存在的节点: ' + e.from[0] + ' → ' + e.to[0]); continue; }
    if (!SPECS[sN.type] || !SPECS[tN.type]) continue;
    if (sN.id === tN.id) { add('存在自连线: ' + sN.id); continue; }
    structuralEdges.push(e);
    const sP = SPECS[sN.type].outputs.find(o => o.id === e.from[1]);
    const tP = SPECS[tN.type].inputs.find(x => x.id === e.to[1]);
    if (!sP) { add('输出端口不存在: ' + sN.type + '.' + e.from[1]); continue; }
    if (!tP) { add('输入端口不存在: ' + tN.type + '.' + e.to[1]); continue; }
    if (tP.id === 'src' && !(sN.type === 'source' && sP.id === 'data')) { add('上下文 src 口只能由「数据源」的 data 口连接'); continue; }
    if (sN.type === 'source' && sP.id === 'data' && tP.id !== 'src') { add('数据源 data 口只能连接下游的上下文 src 口'); continue; }
    if (!dtCompatible(sP.dt, tP.dt, tN.type, sN.type, tP.id)) { add('连线类型不匹配: ' + sN.type + '.' + sP.id + '(' + sP.dt + ') → ' + tN.type + '.' + tP.id + '(' + tP.dt + ')'); continue; }
    if (sP.dt === 'model' && !(tN.type === 'mf' && (tP.id === 'm1' || tP.id === 'm2'))) { add('model 只能经「多因子构建」的 m1/m2 接入'); continue; }

    const edgeKey = sN.id + '\u0000' + sP.id + '\u0000' + tN.id + '\u0000' + tP.id;
    if (seenEdge.has(edgeKey)) { add('存在重复连线: ' + sN.id + '.' + sP.id + ' → ' + tN.id + '.' + tP.id); continue; }
    seenEdge.add(edgeKey);
    const inputKey = tN.id + '\u0000' + tP.id;
    const multiInput = tN.type === 'feature' && tP.id === 'feat';
    if (seenInput.has(inputKey) && !multiInput) { add('输入端口 ' + tN.type + '.' + tP.id + ' 只能接一条连线'); continue; }
    seenInput.add(inputKey);
    validEdges.push(e);
    (incoming[tN.id] = incoming[tN.id] || new Set()).add(tP.id);

    if (Object.prototype.hasOwnProperty.call(allASourceEdges, sN.id)) {
      allASourceEdges[sN.id] += 1;
      if (!(sP.id === 'data' && tN.type === 'data_screen' && tP.id === 'src')) add('「全A·筛选 Skill 专用」数据源只能连接「数据筛选 Skill」的 src 口');
    }
    if (sN.type === 'source' && sP.id === 'data' && tN.type === 'data_screen' && tP.id === 'src') screenSource[tN.id] = sN;
  }

  for (const n of gn) {
    if (!n || !SPECS[n.type]) continue;
    const got = incoming[n.id] || new Set();
    for (const group of requiredInputGroups(n)) {
      if (!group.some(port => got.has(port))) add('节点「' + SPECS[n.type].title + '」(' + n.id + ') 缺少必填输入: ' + group.join(' / '));
    }
    if (n.type === 'history_screen') {
      const edge = validEdges.find(e => e.to[0] === n.id && e.to[1] === 'src');
      const p = edge && byId[edge.from[0]] && byId[edge.from[0]].params || {};
      if (edge && (p.research_freq || p.freq || 'day') !== 'day') add('历史逐日筛选仅支持日线研究，不能把周/月频静默改成日频');
      if (edge && !_parseCodes(p.codes).length && (!p.universe || p.universe === '自动')) add('历史逐日筛选的数据源必须指定股票池或自选代码');
    }
    if (n.type === 'data_screen') {
      const source = screenSource[n.id];
      if (!source) add('「数据筛选 Skill」必须显式连接「数据源」的 data → src');
      else {
        const universe = String((source.params && source.params.universe) || '').trim();
        const explicitCodes = _parseCodes(source.params && source.params.codes);
        if (!explicitCodes.length && (!universe || universe === '自动')) add('数据筛选的数据源必须选择「全A·筛选 Skill 专用」、具名指数，或填写自选代码；空代码时不能使用「自动」');
      }
    }
  }
  for (const sourceId of Object.keys(allASourceEdges)) if (!allASourceEdges[sourceId]) add('「全A·筛选 Skill 专用」数据源必须连接「数据筛选 Skill」');

  // Kahn：拓扑执行要求整张图无环，不能像旧 topoOrder 那样把环节点追加到末尾继续跑。
  const indegree = {}; const next = {};
  Object.keys(byId).forEach(id => { indegree[id] = 0; next[id] = []; });
  structuralEdges.forEach(e => { indegree[e.to[0]] += 1; next[e.from[0]].push(e.to[0]); });
  const queue = Object.keys(indegree).filter(id => indegree[id] === 0); let visited = 0;
  for (let qi = 0; qi < queue.length; qi++) {
    const id = queue[qi]; visited += 1;
    for (const to of next[id]) { indegree[to] -= 1; if (indegree[to] === 0) queue.push(to); }
  }
  if (visited !== Object.keys(indegree).length) add('工作流存在环路，无法按拓扑顺序执行');

  const validNext = {}; Object.keys(byId).forEach(id => { validNext[id] = []; });
  validEdges.forEach(e => validNext[e.from[0]].push(e.to[0]));
  const roots = gn.filter(n => n && SPECS[n.type] && requiredInputGroups(n).length === 0).map(n => n.id);
  const reachable = new Set(roots);
  for (let ri = 0; ri < roots.length; ri++) {
    for (const to of validNext[roots[ri]] || []) if (!reachable.has(to)) { reachable.add(to); roots.push(to); }
  }

  const terminals = gn.filter(n => n && SPECS[n.type] && SPECS[n.type].outputs.some(o => WF_TERMINAL_DT[o.dt]));
  if (!terminals.length) add('缺少终端节点 (数据筛选 / 因子分析 / IC / 回测 / 审计)');
  else if (!terminals.some(n => reachable.has(n.id))) add('终端节点不可从任何数据或因子根节点到达');
  return errors;
}

function validateGraph(gn, ge) {
  return graphValidationErrors(gn, ge)[0] || '';
}

function stableJson(value) {
  if (Array.isArray(value)) return '[' + value.map(stableJson).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + stableJson(value[k])).join(',') + '}';
  return JSON.stringify(value);
}
function wfGraphSnapshot(nodes, edges) {
  return {
    nodes: (nodes || []).map(n => ({ ...n, params: { ...(n.params || {}) } })),
    edges: (edges || []).map(e => ({ from: [e.from[0], e.from[1]], to: [e.to[0], e.to[1]] })),
  };
}
const WF_DURABLE_TYPES = {
  source: 1, data_screen: 1, history_screen: 1, daily_backtest: 1, formula: 1, factorlib: 1, feature: 1,
  xgb: 1, lgbm: 1, svm: 1, rf: 1, nn: 1, lstm: 1,
  pca: 1, spearman: 1, mf: 1, analysis: 1, iccalc: 1,
  orthogonalize: 1, backtest: 1, overfit: 1, portfolio: 1,
  tsic: 1, event: 1, relstat: 1, risk: 1, garch: 1, attrib: 1, tvbeta: 1,
};
const WF_DURABLE_MAIN_TYPES = { daily_backtest: 1, overfit: 1, backtest: 1, analysis: 1, iccalc: 1, data_screen: 1, portfolio: 1, tsic: 1, event: 1, relstat: 1, risk: 1, garch: 1, attrib: 1, tvbeta: 1 };
function wfCanRunDurably(nodes) {
  return Array.isArray(nodes) && nodes.some(n => n && WF_DURABLE_MAIN_TYPES[n.type])
    && nodes.every(n => n && WF_DURABLE_TYPES[n.type]);
}
function wfAgo(ts) {
  const d = (Date.now() - ts) / 1000;
  if (d < 60) return '刚刚'; if (d < 3600) return Math.floor(d / 60) + ' 分钟前';
  if (d < 86400) return Math.floor(d / 3600) + ' 小时前'; return Math.floor(d / 86400) + ' 天前';
}

// ───────── 贝塞尔连线 ─────────
function wirePath(a, b) {
  const dx = Math.max(40, Math.min(130, Math.abs(b.x - a.x) / 2));
  return `M ${a.x},${a.y} C ${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`;
}

// ───────── 直连引擎数据层 + 图→因子调用 (填充: 让节点真跑, 不改界面) ─────────
const _API = () => (window.GUANLAN_BACKEND || '');
async function _post(path, payload) {
  const base = _API();
  if (!base) throw new Error('无后端 — 请经 http://127.0.0.1:9999/ 同源打开');
  const res = await fetch(base + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  let b = null; try { b = await res.json(); } catch (e) {}
  if (!res.ok) {
    const detail = b && (b.error || b.detail || b.reason);
    const message = detail == null ? ('HTTP ' + res.status)
      : (typeof detail === 'string' ? detail : JSON.stringify(detail));
    const err = new Error(message);
    err.httpStatus = res.status;
    throw err;
  }
  return b;
}
function _withWorkspace(payload) {
  return WW_WORKSPACE_ID ? { ...(payload || {}), workspace_id: WW_WORKSPACE_ID } : (payload || {});
}
function _scopedPath(path, includeGlobal) {
  if (!WW_WORKSPACE_ID) return path;
  const sep = path.indexOf('?') >= 0 ? '&' : '?';
  return path + sep + 'workspace_id=' + encodeURIComponent(WW_WORKSPACE_ID)
    + (includeGlobal ? '&include_global=true' : '');
}
function _workflowPath(path) { return _scopedPath(path, true); }
function _reportPath(path) { return _scopedPath(path, false); }
// GET 拉因子清单: 先试 guanlan 自有 /factorlib/list, 失败回退引擎 /factor/list。两者皆形如 {registered,user}。
async function _list() {
  const base = _API();
  if (!base) throw new Error('无后端 — 请经 http://127.0.0.1:9999/ 同源打开');
  for (const path of ['/factorlib/list', '/factor/list']) {
    try {
      const res = await fetch(base + path, { headers: { 'Accept': 'application/json' } });
      if (!res.ok) continue;
      const b = await res.json();
      if (b && !b.error) return b;
    } catch (e) { /* 试下一个端点 */ }
  }
  throw new Error('因子库: /factorlib/list 与 /factor/list 均不可达');
}
// GET 单端点 JSON; 失败回 null (供工作流持久化做"服务端可用则用、否则降级 localStorage")。仓内无 _get, 不抛、失败回 null。
async function _get(path) {
  const base = _API(); if (!base) return null;
  try {
    const res = await fetch(base + path, { headers: { 'Accept': 'application/json' } });
    if (!res.ok) return null;
    const b = await res.json();
    return (b && !b.error) ? b : null;
  } catch (e) { return null; }
}
const _n2 = (v, d = 2) => (v == null || (typeof v === 'number' && isNaN(v))) ? '—' : (typeof v === 'number' ? v.toFixed(d) : v);
const _pct = (v, d = 1) => (v == null || (typeof v === 'number' && isNaN(v))) ? '—' : (v * 100).toFixed(d) + '%';
function _universeOf(p) {
  const u = ((p && p.universe) || '').toString().trim();
  if (u && u !== '自动') return u;   // 数据源「股票池」显式指定 → 直接用 (引擎 config/universes/*.txt 实体)
  const code = ((p && p.code) || '').toString().toLowerCase();
  const scope = (p && p.scope) || '';
  if (code.includes('500')) return 'csi500';
  if (code.includes('800')) return 'csi800';
  if (code.includes('300')) return 'csi300_active';
  if (scope === '全市场') return 'all';
  return 'csi_fast';
}
// 从画布导出真因子调用: 公式/Python 节点 → 因子表达式; 数据源 → universe; 有「多因子构建」且≥2 表达式 → 合成
function deriveCall(nodes) {
  const src = nodes.find(n => n.type === 'source');
  const universe = src ? _universeOf(src.params) : 'csi_fast';
  const exprNodes = nodes.filter(n => n.type === 'formula');
  const exprs = exprNodes.map(n => (n.params.expr || '').toString().trim()).filter(Boolean);
  if (!exprs.length) return null;
  const multi = nodes.some(n => n.type === 'mf') && exprs.length >= 2;
  return { universe, exprs, multi };
}

// ML 训练共享求值器: 4 个模型节点 (xgb/lgbm/svm/rf) 唯一差异 = kind + 超参映射, 余皆同 →
//   收 inputs.fe (P2 /feature/build 输出) 里的可复算 fe spec, 叠加本节点超参 (按 hpMap 映射到
//   后端超参名) + ctx.universe, POST /model/<kind>, 回模型节点载荷 (含 OOS 报告)。
//   hpMap: { 后端超参名: 本节点 params 字段 } —— 仅透传非空者, 缺省由后端兜底。
async function _trainModel(kind, inputs, params, ctx, hpMap) {
  const fe = inputs.fe;
  if (!fe) throw new Error(kind + ': 上游缺特征工程 (需「特征工程构建」直连 fe 端口)');
  if (fe.ok === false) throw new Error(kind + ': 上游特征工程未物化 (' + (fe.reason || 'feature/build 失败') + ')');
  const spec = fe.fe || {};   // P2 可复算 fe spec (features/feature_names/label/fwd_days/universe/start/end/freq/winsorize…)
  const p = {};
  for (const k in hpMap) { const v = params[hpMap[k]]; if (v != null && v !== '') p[k] = (typeof v === 'string' && v.trim() !== '' && !isNaN(+v)) ? +v : v; }
  const payload = Object.assign({}, spec, {
    kind,
    universe: (ctx.universe || spec.universe || 'csi_fast'),
    params: p,
  });
  const r = await ctx.post('/model/' + kind, payload);
  if (!r || r.ok === false) throw new Error(kind + ': ' + ((r && r.reason) || ('/model/' + kind + ' 失败')));
  // 模型载荷透传后端真返回 (OOS 报告 ic/portfolio/quantile + 预测因子), 附元信息供 mf/抽屉。
  const label = (fe._label || (spec.features && spec.features.join('+')) || kind);
  const _w = (r.warnings || []).slice();
  const wf = r.walkforward_refit;   // W7 真滚动重训摘要 → 进警告 → 抽屉可见
  if (wf && wf.enabled) _w.push('滚动重训(' + wf.folds + '折·真前进验证): 拼接样本外 RankIC ' + (wf.stitched_oos_rank_ic != null ? (+wf.stitched_oos_rank_ic).toFixed(4) : '—') + ' · 逐折正占比 ' + (wf.pos_ratio != null ? Math.round(wf.pos_ratio * 100) + '%' : '—'));
  else if (wf && wf.reason) _w.push('滚动重训未执行: ' + wf.reason);
  return { model: Object.assign({ __dt: 'model' }, r, { _kind: kind, _universe: payload.universe, _label: label, _walkforward_refit: wf, _warnings: _w }) };
}

// 从一个模型节点载荷里取「OOS 报告」(ic/portfolio/quantile 三件套)。后端 /model/<kind> 把
// build_report 结果置于顶层 (同 /factor/report 形); 兼容嵌套于 .report / .composite 的形态。
function _modelReport(m) {
  if (!m || typeof m !== 'object') return null;
  if (m.ic || m.portfolio || m.quantile) return m;
  if (m.report && (m.report.ic || m.report.portfolio || m.report.quantile)) return m.report;
  if (m.composite && (m.composite.ic || m.composite.portfolio || m.composite.quantile)) return m.composite;
  return null;
}

// ───────── 节点执行内核: NODE_EXEC 注册表 (纯求值器, 复用上方数据层) ─────────
// 每项 = 一个纯节点求值器 async (inputs, params, ctx) => output。
//   inputs: { [本节点输入端口id]: 上游同名 output 端口载荷 } (未连端口缺省)
//   params: node.params (字段同 SPECS[type].params[].id)
//   ctx   : { universe, node, post, list, allExprs } — universe 由 runGraph 全局算好; post 即 _post; list 即 _list (GET 因子清单)
// 返回值 = { [本节点输出端口id]: 载荷 }; executor 按边喂给下游。载荷形状对齐 SPECS 的 dt。
const NODE_EXEC = {
  history_screen: async (inputs, params, ctx) => {
    if (!inputs.src || inputs.src.__dt !== 'series') throw new Error('历史逐日筛选必须连接日线数据源');
    wfParseHistoryBranches(JSON.stringify(params.branches));
    const r = await ctx.post('/workflow/history-screen', { params, context: { universe: ctx.universe, start: ctx.start, end: ctx.end, codes: ctx.codes || [], research_freq: 'day' }, workspace_id: WW_WORKSPACE_ID || null });
    if (!r || r.ok === false) throw new Error((r && r.reason) || '历史逐日筛选失败');
    return { history: { ...r, __dt: 'screen_history' } };
  },
  daily_backtest: async (inputs, params, ctx) => {
    if (!inputs.history || inputs.history.__dt !== 'screen_history') throw new Error('日频模拟调仓仅接受历史逐日筛选，不能使用单日候选快照');
    const r = await ctx.post('/backtest/daily-screen', { history: inputs.history, params, workspace_id: WW_WORKSPACE_ID || null });
    if (!r || r.ok === false) throw new Error((r && r.reason) || '日频模拟调仓失败');
    return { result: { ...r, __dt: 'result', method: 'daily_screen_backtest', _label: r._label || inputs.history.label || '日频模拟调仓' } };
  },
  // —— IO 层: source 给 universe series, formula 透传表达式 ——
  source: async (inputs, params, ctx) => ({ data: { __dt: 'series', universe: _universeOf(params) } }),
  formula: async (inputs, params, ctx) => ({ out: { __dt: 'series', expr: String(params.expr || '').trim() } }),
  // —— 因子库: 拉 /factorlib/list (无则回退引擎 /factor/list), 按「指定名」精确或「检索」模糊匹配,
  //    输出选中因子的 名/表达式 作 series.expr 给下游 (analysis/iccalc/mf 经 expr_or_name 求值) ——
  factorlib: async (inputs, params, ctx) => {
    // 名称是因子库的权威引用。结构化因子若退回缓存的 params.expr，会静默丢掉
    // factor_spec 里的正交变换；因此有 name 时先向服务端解析，普通旧因子仍兼容 expr。
    const picked = String(params.expr || '').trim();
    const requestedName = String(params.name || '').trim();
    const expectedSpec = (params.factor_spec && params.factor_spec.schema === 'guanlan.factor_spec.v1') ? params.factor_spec : null;
    const expectedDigest = String(params.factor_spec_digest || '').trim();
    const structuredRef = !!(expectedSpec || expectedDigest);
    if (structuredRef && !requestedName) throw new Error('因子库: 结构化因子快照缺名称，拒绝退回基础公式');
    if (!requestedName && picked) return { out: { __dt: 'series', expr: picked, _label: '因子', _factorName: '' } };
    if (!requestedName) throw new Error('因子库: 未选因子 —— 点节点里「浏览因子库」选一个');
    const lib = await ctx.list();           // 引擎 /factor/list {registered,user} 或 仓内 /factorlib/list {factors}
    const reg = (lib && lib.registered) || [];
    const usr = (lib && lib.user) || [];
    const fac = (lib && lib.factors) || [];
    const all = [
      ...reg.map(s => ({ name: s.name, expr: s.formula || s.name, family: s.family || 'zoo', factor_spec: s.factor_spec, factor_spec_digest: s.factor_spec_digest })),
      ...usr.map(u => ({ name: u.name, expr: u.expr || u.formula || u.name, family: u.family || 'user', factor_spec: u.factor_spec, factor_spec_digest: u.factor_spec_digest })),
      ...fac.map(u => ({ name: u.name, expr: u.expr || u.formula || u.name, family: u.family || 'library', factor_spec: u.factor_spec, factor_spec_digest: u.factor_spec_digest })),
    ];
    const name = requestedName.toLowerCase();
    const exactHit = name ? all.find(f => String(f.name).toLowerCase() === name) : null;
    let hit = exactHit || (!structuredRef && name ? all.find(f => String(f.name).toLowerCase().includes(name)) : null);
    if (structuredRef && !exactHit) throw new Error('因子库: 结构化因子「' + requestedName + '」已缺失，拒绝用缓存基础公式降级运行');
    if (!hit && picked) return { out: { __dt: 'series', expr: picked, _label: requestedName, _factorName: requestedName } };
    if (!hit) throw new Error('因子库: 查无因子「' + requestedName + '」');
    if (structuredRef) {
      if (!hit.factor_spec || hit.factor_spec.schema !== 'guanlan.factor_spec.v1') {
        throw new Error('因子库: 「' + requestedName + '」已不再是原结构化因子，拒绝降级运行');
      }
      if (expectedDigest && (!hit.factor_spec_digest || hit.factor_spec_digest !== expectedDigest)) {
        throw new Error('因子库: 「' + requestedName + '」定义摘要已变化，拒绝执行非原始版本');
      }
      if (expectedSpec && stableJson(hit.factor_spec) !== stableJson(expectedSpec)) {
        throw new Error('因子库: 「' + requestedName + '」结构化定义已变化，拒绝执行非原始版本');
      }
    }
    if (hit.factor_spec && hit.factor_spec.schema === 'guanlan.factor_spec.v1') {
      return { out: { __dt: 'series', expr: hit.name, _label: hit.name, _factorName: hit.name,
        factor_spec: hit.factor_spec, factor_spec_digest: hit.factor_spec_digest || null,
        fe: { features: [hit.name] } } };
    }
    return { out: { __dt: 'series', expr: hit.expr, _label: hit.name, _factorName: hit.name, _matched: all.length } };
  },
  // 正交化节点只生成可重放 factor-spec；真残差由下游分析/组合/回测在同一面板上统一物化。
  orthogonalize: async (inputs, params, ctx) => {
    const src = inputs.signal;
    const expr = src && (src.expr || src._factorName || src._label) ? String(src.expr || src._factorName || src._label).trim() : '';
    if (!expr) throw new Error('因子正交化: 上游缺可复算公式/因子库表达式');
    const styles = String(params.styles || '').split(',').map(s => s.trim()).filter(Boolean);
    const oldText = String(params.old_factors || '').trim();
    let old_factors = oldText ? oldText.split(/[;；\r\n]+/).map(s => s.trim()).filter(Boolean) : [];
    if (old_factors.length === 1 && oldText.indexOf('(') < 0 && oldText.indexOf(')') < 0) old_factors = oldText.replace(/，/g, ',').split(',').map(s => s.trim()).filter(Boolean);
    old_factors = [...new Set(old_factors)];
    const size = ({ '总市值': 'total_mv', '流通市值': 'circ_mv', '不控制': 'none' })[params.size] || String(params.size || 'total_mv');
    const spec = {
      version: 'orthogonalization-v1', method: 'ols', industry: params.industry !== '否', size,
      styles, existing_factors: old_factors,
      min_obs: (params.min_obs != null && !isNaN(+params.min_obs)) ? +params.min_obs : 20,
      max_condition_number: (params.max_condition_number != null && !isNaN(+params.max_condition_number)) ? +params.max_condition_number : 1e8,
    };
    if (!spec.industry && size === 'none' && !styles.length && !old_factors.length) throw new Error('因子正交化: 至少选择一个控制项');
    const label = String(src._label || expr) + ' · 正交';
    return { factor: { __dt: 'factor', expr, features: [expr], fe: { features: [expr], orthogonalization: spec }, _label: label, _universe: ctx.universe, _orthogonalization: spec, factor_spec: { schema: 'guanlan.factor_spec.v1', kind: 'orthogonalized', base_features: [expr], transform: spec } } };
  },
  // —— 模型(研究库): 引用一个 registry 模型(prod/工坊/工作流变体)→ 拉其最新截面排名
  //    GET /screen/model/ranking?id=<id> → {date, rows:[{code,score}]} → 输出 ranking 载荷
  //    (code→score 字典作 series), 供下游(回测/组合/IC)消费。诚实失败: 未选 / 排名不可达 → 抛错。——
  model: async (inputs, params, ctx) => {
    const id = String(params.model_id || '').trim();
    if (!id) throw new Error('模型节点: 未选模型 —— 点节点里「研究库」选一个');
    const j = await _get('/screen/model/ranking?id=' + encodeURIComponent(id));
    if (!j || !j.ok) throw new Error('模型节点: ' + ((j && j.reason) || '排名不可达 (/screen/model/ranking)'));
    const rows = j.rows || [];
    return { out: { __dt: 'series', kind: 'ranking', model_id: id, _label: String(params.model_name || '模型') || id, date: j.date, rows, series: Object.fromEntries(rows.map(r => [r.code, r.score])) } };
  },
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
  // —— 特征工程构建: 收上游 feat/label 表达式 + params.tag + ctx.universe → POST /feature/build,
  //    后端经 compile_factor→winsorize/zscore→forward_simple_returns 在 universe 面板物化真 X/y,
  //    回真统计 + 可复算 fe spec (供 P3 ML 重建训练集)。dt=fe 非终端 → 不送抽屉, 仅置节点 done。
  //    标签语义: label 端口连了公式 → 用其 expr 作公式标签; 否则用 params.tag (IC/fwd_ret/空 → 后端走前向收益)。——
  feature: async (inputs, params, ctx) => {
    // feat 口多边聚合为多特征 (保序去重, 与后端 run_graph/_exec_feature、deriveRecipeForNode 同语义)
    const rawFeat = Array.isArray(inputs.feat) ? inputs.feat : [inputs.feat];
    const featExprs = [];
    rawFeat.forEach(p => { const e = (p && p.expr ? String(p.expr) : '').trim(); if (e && featExprs.indexOf(e) < 0) featExprs.push(e); });
    if (!featExprs.length) throw new Error('特征工程: 上游未提供特征表达式 (需「公式输入 / 因子库」直连特征端口)');
    const labelExpr = (inputs.label && inputs.label.expr ? String(inputs.label.expr) : '').trim();
    const label = labelExpr || String(params.tag == null ? '' : params.tag).trim();   // 连了标签公式→公式标签; 否则 tag(IC/fwd_ret→前向收益)
    const r = await ctx.post('/feature/build', { features: featExprs, label, universe: ctx.universe });
    if (!r || r.ok === false) throw new Error('特征工程: ' + ((r && r.reason) || 'feature/build 失败'));
    return { fe: Object.assign({ __dt: 'fe' }, r, { _universe: ctx.universe, _label: featExprs.join('+'), _warnings: r.warnings || [] }) };
  },
  // —— ML 训练 (xgb/lgbm/svm/rf): 收上游特征工程 (P2 /feature/build 输出, 内含可复算 fe spec)
  //    + 本节点超参 (trees/depth/lr/leaves/c…) + ctx.universe → POST /model/<kind>。
  //    后端按 fe spec 原样重建训练集 → 时序切 train/test → fit → 预测分 (仅 test 行) →
  //    reindex 整面板 build_report 出 OOS 报告 (ic/portfolio/quantile, 同 /factor/report 形)。
  //    dt=model 非终端 → 不直接送抽屉; 经 mf 消费预测报告 → analysis 透传出报告。
  //    诚实失败: fe 缺失 / 后端 ok:false (含「<lib> 未装」) / HTTP 非 200 → 抛错, 原样显示。——
  xgb: (inputs, params, ctx) => _trainModel('xgboost', inputs, params, ctx, { n_estimators: 'trees', max_depth: 'depth', learning_rate: 'lr', subsample: 'sub' }),
  lgbm: (inputs, params, ctx) => _trainModel('lightgbm', inputs, params, ctx, { num_leaves: 'leaves', learning_rate: 'lr' }),
  svm: (inputs, params, ctx) => _trainModel('svm', inputs, params, ctx, { C: 'c' }),
  rf: (inputs, params, ctx) => _trainModel('rf', inputs, params, ctx, { n_estimators: 'trees' }),
  // —— MLP 神经网络 (前馈多层感知机, sklearn MLPRegressor/adam): 同 4 个 ML 节点同形 (收 fe + 超参 + universe
  //    → POST /model/mlp)。hpMap = { 后端 _build_model 取值键: 本节点 params 字段 } —— _trainModel 据此
  //    把 params[字段] 塞进 payload.params[取值键], 后端 _i("hidden")/_i("layers")/_f("lr")/_i("epochs")/
  //    _f("alpha") 按这些键读出, 构 hidden_layer_sizes=(hidden,)*layers + adam + L2 alpha。——
  nn: (inputs, params, ctx) => _trainModel('mlp', inputs, params, ctx, { hidden: 'hidden', layers: 'layers', lr: 'lr', epochs: 'epochs', alpha: 'alpha' }),
  // —— LSTM 序列网络 (PyTorch nn.LSTM): 同 ML 节点同形 (收 fe + 超参 + universe → POST /model/lstm)。
  //    后端 _lstm_eval 逐 code 按日期滑窗 seq_len 期构真序列 → 训 LSTM → 预测分截面因子 → build_report
  //    OOS 报告 (与 4 ML 节点同形)。hpMap = { 后端 _i/_f 取值键: 本节点 params 字段 };torch 未装 → 诚实 ok:false。——
  lstm: (inputs, params, ctx) => _trainModel('lstm', inputs, params, ctx, { seq_len: 'seq_len', hidden: 'hidden', layers: 'layers', lr: 'lr', epochs: 'epochs' }),
  // —— PCA 因子构建: 收上游特征工程 (P2 /feature/build 输出, 内含可复算 fe spec) + params.k
  //    (主成分数) + ctx.universe → POST /factor/pca。后端按 fe spec 原样重建多特征 X →
  //    sklearn PCA 降维取主成分作截面因子 → reindex 全面板 build_report 出 OOS 报告
  //    (ic/portfolio/quantile, 同 /factor/report 顶层形)。仿 _trainModel/mf: 顶层并入报告三件套
  //    + composite 标记 (truthy → analysis 命中透传分支) + _compose (终端判定) → analysis 出真报告。
  //    诚实失败: fe 缺失 / 后端 ok:false (含「sklearn 未装」) / HTTP 非 200 → 抛错, 原样显示。——
  pca: async (inputs, params, ctx) => {
    const fe = inputs.fe;
    if (!fe) throw new Error('PCA: 上游缺特征工程 (需「特征工程构建」直连 fe 端口)');
    if (fe.ok === false) throw new Error('PCA: 上游特征工程未物化 (' + (fe.reason || 'feature/build 失败') + ')');
    const spec = fe.fe || {};   // P2 可复算 fe spec (features/feature_names/label/fwd_days/universe/start/end/freq/winsorize…)
    const k = (params.k != null && params.k !== '' && !isNaN(+params.k)) ? +params.k : undefined;
    const payload = Object.assign({}, spec, {
      universe: (ctx.universe || spec.universe || 'csi_fast'),
      params: (k != null ? { k } : {}),
    });
    if (k != null) payload.k = k;   // 后端 PCAFactorIn 顶层取 k (主成分数), 同时 params.k 兜底
    const r = await ctx.post('/factor/pca', payload);
    if (!r || r.ok === false) throw new Error('PCA: ' + ((r && r.reason) || '/factor/pca 失败'));
    if (r.status && r.status !== 'ok') throw new Error('PCA: ' + r.status + (r.error ? ' · ' + r.error : ''));
    // 顶层并入报告三件套 (后端已对齐 /factor/report 形), 置 composite/_compose → analysis 透传为终端报告。
    const label = (spec.features && spec.features.join('+')) || ('PCA · PC' + (r.k || k || 1));
    return { factor: Object.assign({}, r, { composite: true, _model: 'pca', _label: label, _universe: payload.universe, _compose: true, _warnings: r.warnings || [] }) };
  },
  // —— Spearman 因子: 同 PCA 链路, POST /factor/spearman (后端按 fe spec 重建 X → Spearman 截面
  //    秩相关合成因子 → OOS build_report)。仿 mf 模型分支顶层并入报告三件套 + composite 标记。——
  spearman: async (inputs, params, ctx) => {
    const fe = inputs.fe;
    if (!fe) throw new Error('Spearman: 上游缺特征工程 (需「特征工程构建」直连 fe 端口)');
    if (fe.ok === false) throw new Error('Spearman: 上游特征工程未物化 (' + (fe.reason || 'feature/build 失败') + ')');
    const spec = fe.fe || {};
    const payload = Object.assign({}, spec, {
      universe: (ctx.universe || spec.universe || 'csi_fast'),
      params: {},
    });
    const r = await ctx.post('/factor/spearman', payload);
    if (!r || r.ok === false) throw new Error('Spearman: ' + ((r && r.reason) || '/factor/spearman 失败'));
    if (r.status && r.status !== 'ok') throw new Error('Spearman: ' + r.status + (r.error ? ' · ' + r.error : ''));
    const label = (spec.features && spec.features.join('+')) || 'Spearman 合成因子';
    return { factor: Object.assign({}, r, { composite: true, _model: 'spearman', _label: label, _universe: payload.universe, _compose: true, _warnings: r.warnings || [] }) };
  },
  // —— 因子 IC 计算: 终端 dt=ic → 送抽屉。两条来源:
  //    (a) 上游已带报告的因子 (pca/spearman/mf 顶层含 ic 块) → 直接透传其 ic (含 portfolio/quantile
  //        供抽屉一并渲染), 不重复 POST;
  //    (b) 上游是公式因子 (.expr/.​_label) → POST /factor/report 取 ic 块。
  //    period 对齐: /factor/report 的 horizon 由 freq 决定 (无数值 period 字段) →
  //    period≥20→month, ≥5→week, 否则→day, 让节点「周期」参数真生效。
  //    诚实失败: 上游缺因子 / 后端 status≠ok / HTTP 非 200 → 抛错或占位, 不谎报。——
  iccalc: async (inputs, params, ctx) => {
    const f = inputs.factor;
    if (!f) return { ic: { __dt: 'ic', __pending: 'iccalc:上游缺因子 (需「因子」端口直连)' } };
    // 单因子可复算(直连公式因子,或 单特征 Spearman/PCA 退化)→ 按精确周期经 report2 重算 IC(period 真生效);
    // 真·多因子/模型复合 → 透传上游 ic(本节点周期对其不适用)。
    const _feats = (f.fe && Array.isArray(f.fe.features)) ? f.fe.features : null;
    const _reExpr = (_feats && _feats.length === 1 && (f._model === 'spearman' || f._model === 'pca')) ? _feats[0]
      : ((!f.composite && (f.expr || f._label)) ? (f.expr || f._label) : null);
    if (!_reExpr && (f.ic || f.portfolio || f.quantile)) {
      return { ic: Object.assign({ __dt: 'ic' }, f, { _label: f._label || 'IC', _universe: f._universe || ctx.universe, _compose: f._compose, _warnings: f._warnings || f.warnings || [] }) };
    }
    const expr = _reExpr || (f.expr || f._label) || null;
    if (!expr) return { ic: { __dt: 'ic', __pending: 'iccalc:上游非公式因子且无报告,无法求IC' } };
    const period = (params.period != null && params.period !== '' && !isNaN(+params.period)) ? +params.period : 5;
    const freq = period >= 20 ? 'month' : period >= 5 ? 'week' : 'day';
    // #3: period 作精确前向窗口(fwd_days)→ IC 按该周期真算(经 /factor/report2);freq 仅驱动调仓重采样。
    const orthogonalization = f._orthogonalization || (f.fe && f.fe.orthogonalization) || null;
    const r = await ctx.post('/factor/report2', { expr_or_name: expr, universe: ctx.universe, freq, fwd_days: period, orthogonalization });
    if (r && r.ok === false) throw new Error('IC 计算: ' + (r.reason || '/factor/report2 失败'));
    if (r && r.status && r.status !== 'ok') throw new Error(r.status + (r.error ? ' · ' + r.error : ''));
    return { ic: Object.assign({ __dt: 'ic' }, r, { factor_spec: f.factor_spec || r.factor_spec || null,
      _label: f._label || expr, _universe: ctx.universe, _warnings: r.warnings || [] }) };
  },
  // —— 因子分析: /factor/report (已通); 上游 mf 合成结果直接透传当报告 (终端 dt=report) ——
  analysis: async (inputs, params, ctx) => {
    const f = inputs.factor;
    // 单因子可复算时(直连公式因子,或 单特征 Spearman/PCA 退化 = 该因子本身)→ 用本节点
    // 分组/调仓/方向 经 /factor/report2 重算,旋钮真生效;真·多因子/模型复合(不可由单一表达式
    // 复现)→ 透传上游已算报告(这些旋钮对其不适用)。
    const _feats = (f && f.fe && Array.isArray(f.fe.features)) ? f.fe.features : null;
    const _reExpr = (_feats && _feats.length === 1 && (f._model === 'spearman' || f._model === 'pca')) ? _feats[0]
      : ((f && !f.composite && (f.expr || f._label)) ? (f.expr || f._label) : null);
    if (!_reExpr && f && f.composite) return { report: f };
    const expr = _reExpr || (f && (f.expr || f._label)) || null;
    if (!expr) throw new Error('因子分析: 上游未提供因子表达式 (需公式输入直连, 或多因子构建合成)');
    // #1: 分组/调仓/方向 三旋钮经 /factor/report2 真生效(引擎 /factor/report 不吃这些入参)。
    const freq = String(params.rebal || 'month');
    const n_groups = (params.groups != null && params.groups !== '' && !isNaN(+params.groups)) ? +params.groups : 10;
    const direction = (params.dir != null && params.dir !== '' && !isNaN(+params.dir)) ? +params.dir : 0;
    const orthogonalization = f._orthogonalization || (f.fe && f.fe.orthogonalization) || null;
    // 正交化已包含明确的控制项，不再叠加旧的“行业+市值”快捷中性化，避免二次残差化。
    const neutralize = orthogonalization ? false : (params.neutral === '是');
    const r = await ctx.post('/factor/report2', { expr_or_name: expr, universe: ctx.universe, freq, n_groups, direction, neutralize, orthogonalization });
    if (r && r.ok === false) throw new Error('因子分析: ' + (r.reason || '/factor/report2 失败'));
    if (r && r.status && r.status !== 'ok') throw new Error(r.status + (r.error ? ' · ' + r.error : ''));
    return { report: Object.assign({}, r, { factor_spec: f.factor_spec || r.factor_spec || null,
      _label: f._label || expr, _universe: ctx.universe, _warnings: r.warnings || [] }) };
  },
  // —— 个股时序IC: 收 inputs.factor (公式/因子库 series 带 expr) → POST /factor/tsic(单票/小池:
  //    每股因子 vs 自身未来收益 Spearman 相关)。src 口接 source 仅供 universe/codes 回溯,值不用。——
  tsic: async (inputs, params, ctx) => {
    const f = inputs.factor;
    if (!f) throw new Error('个股时序IC: 上游缺因子 (需「因子」端口直连 公式/因子库)');
    const expr = (f.expr || f._factorName || f._label) ? String(f.expr || f._factorName || f._label).trim() : '';
    if (!expr) throw new Error('个股时序IC: 上游因子无表达式 (需公式输入直连)');
    const fwd_days = (params.fwd_days != null && params.fwd_days !== '' && !isNaN(+params.fwd_days)) ? +params.fwd_days : 20;
    const r = await ctx.post('/factor/tsic', { expr_or_name: expr, universe: ctx.universe, fwd_days });
    if (!r || r.ok === false) throw new Error('个股时序IC: ' + ((r && r.reason) || '/factor/tsic 失败'));
    if (r.status && r.status !== 'ok') throw new Error('个股时序IC: ' + r.status);
    return { tsic: Object.assign({ __dt: 'tsic' }, r, { _label: expr, _universe: ctx.universe, _warnings: r.warnings || [] }) };
  },
  // —— 事件研究: 收 inputs.trigger (公式/因子库 series 带 expr, >0 即 firing) → POST /workflow/event
  //    (CAR/命中率/t值/逐年/盈亏比)。离散触发(反弹/异动/放量/跳空/消息面)的正确口径。codes/benchmark
  //    经 ctx.post(_postWC) 自动随源节点下传 → 单票/自选/共振自然生效。——
  event: async (inputs, params, ctx) => {
    const f = inputs.trigger;
    if (!f) throw new Error('事件研究: 上游缺触发式 (需「触发」端口直连 公式/因子库)');
    const expr = (f.expr || f._factorName || f._label) ? String(f.expr || f._factorName || f._label).trim() : '';
    if (!expr) throw new Error('事件研究: 上游触发式无表达式 (需公式输入直连)');
    const hz = String(params.horizons || '1,5,10,20').split(',').map(s => parseInt(String(s).trim(), 10)).filter(n => n >= 1);
    const direction = (params.direction != null && params.direction !== '' && !isNaN(+params.direction)) ? +params.direction : 0;
    const r = await ctx.post('/workflow/event', { trigger: expr, universe: ctx.universe, horizons: hz.length ? hz : [1, 5, 10, 20], direction });
    if (!r || r.ok === false) throw new Error('事件研究: ' + ((r && r.reason) || '/workflow/event 失败'));
    if (r.status && r.status !== 'ok') throw new Error('事件研究: ' + r.status);
    return { event: Object.assign({ __dt: 'event' }, r, { _label: expr, _universe: ctx.universe, _warnings: r.warnings || [] }) };
  },
  // —— 关系稳定度: 收 inputs.factor (关系因子 series 带 expr) → POST /workflow/relstat
  //    (逐股 均值水平/波动/lag1自相关粘性/正占比;共振/跟随的描述性体检,tsic 的补充)。——
  relstat: async (inputs, params, ctx) => {
    const f = inputs.factor;
    if (!f) throw new Error('关系稳定度: 上游缺关系因子 (需「关系因子」端口直连 公式/因子库)');
    const expr = (f.expr || f._factorName || f._label) ? String(f.expr || f._factorName || f._label).trim() : '';
    if (!expr) throw new Error('关系稳定度: 上游因子无表达式 (需公式输入直连)');
    const r = await ctx.post('/workflow/relstat', { expr_or_name: expr, universe: ctx.universe });
    if (!r || r.ok === false) throw new Error('关系稳定度: ' + ((r && r.reason) || '/workflow/relstat 失败'));
    if (r.status && r.status !== 'ok') throw new Error('关系稳定度: ' + r.status);
    return { relstat: Object.assign({ __dt: 'relstat' }, r, { _label: expr, _universe: ctx.universe, _warnings: r.warnings || [] }) };
  },
  // —— 多因子构建 (ML): 上游若含模型 (m1/m2 = /model/<kind> 输出, 内带 OOS 预测报告) → 直接消费
  //    模型预测因子的报告作 factor 出口 (顶层带 ic/portfolio/quantile + composite 标记 → analysis 原样
  //    透传为终端报告); 多模型时取首个有报告者 (单模型链最常见)。无模型报告 → 回退原逻辑:
  //    上游公式表达式≥2 → /factor/compose equal 合成。两路都让 feature→…→mf→analysis 出真报告。——
  mf: async (inputs, params, ctx) => {
    const mReps = [inputs.m1, inputs.m2].map(_modelReport).filter(Boolean);
    if (mReps.length) {
      const rep = mReps[0];                       // 模型 OOS 报告 (ic/portfolio/quantile 顶层)
      const srcM = [inputs.m1, inputs.m2].find(m => _modelReport(m) === rep) || {};
      const label = srcM._label || (srcM._kind ? srcM._kind + ' 预测' : '模型预测因子');
      // 顶层并入报告三件套, 再置 composite 标记 (truthy) → analysis 命中透传分支; _compose 供终端判定。
      return { factor: Object.assign({}, rep, { composite: true, _model: srcM._kind || true, _label: label, _universe: ctx.universe, _compose: true, _warnings: srcM._warnings || rep.warnings || [] }) };
    }
    const exprs = [inputs.f1, inputs.f2, inputs.m1, inputs.m2].map(x => x && x.expr).filter(Boolean);
    let members = exprs;
    if (members.length < 2 && ctx.allExprs) members = ctx.allExprs;
    if (members.length < 2) throw new Error('多因子构建: 需要至少 2 个因子表达式 (上游公式输入不足)');
    const method = String(params.combine || 'equal');   // equal | ic | icir(样本内估权,防前视)
    const r = await ctx.post('/workflow/compose', { members, method, universe: ctx.universe });
    if (!r || r.ok === false) throw new Error('多因子构建: ' + ((r && r.reason) || '/factor/compose 失败'));
    if (r && r.status && r.status !== 'ok') throw new Error(r.status + (r.error ? ' · ' + r.error : ''));
    const comp = r.composite || {};   // 顶层带 ic/quantile/portfolio/weights → composite:true 让 analysis 透传为终端报告
    return { factor: Object.assign({}, comp, { composite: true, _model: 'compose_' + method, _label: members.join(' + '), _universe: ctx.universe, _compose: true, _warnings: comp.warnings || [] }) };
  },
  // —— 向量化回测 (TopN): 收 inputs.factor (上游三态: 公式/Python series 带 expr; 因子库 series 带
  //    expr/_factorName; pca/spearman/mf 报告因子顶层带 ic/portfolio/composite + 可复算 fe spec) +
  //    params.cash/topn + ctx.universe → 统一走 fe_spec 透传 (features = 上游 fe.features 或 [expr],
  //    后端 _materialize_xy「注册名优先, 否则编译表达式」) → POST /backtest/vector。后端复用
  //    _factor_eval 出 ic/quantile/characteristics/meta + _topn_portfolio 覆盖 portfolio 块
  //    (逐调仓期 nlargest(topn) 等权净值/年化/Sharpe/回撤), 返回 /factor/report 兼容顶层形 + composite。
  //    带 portfolio 且无 __pending → 终端判定命中 (workflow.jsx:448) → 进结果抽屉。
  //    诚实失败: 上游缺因子 / 后端 ok:false (含 reason) / status≠ok / HTTP 非 200 → 抛错, 原样显示。——
  backtest: async (inputs, params, ctx) => {
    const f = inputs.pf || inputs.factor;   // 双口: 优先「组合」(W5-1, dt=portfolio) 否则「因子」
    const pfIn = (inputs.pf && typeof inputs.pf === 'object') ? inputs.pf : null;
    if (!f) throw new Error('向量化回测: 上游缺因子 (需「因子」端口直连 公式/因子库/PCA/Spearman/多因子, 或「组合」端口接「组合构建」)');
    if (f.ok === false) throw new Error('向量化回测: 上游因子未物化 (' + (f.reason || '上游构建失败') + ')');
    // 因子来源 → fe_spec 透传。报告因子 (pca/spearman/mf) 携可复算 fe spec (features/label/fwd_days/
    // universe/start/end/freq/winsorize…) → 原样重建; 否则公式/因子库 series 用其 expr/注册名 作 features。
    const spec = (f.fe && typeof f.fe === 'object') ? f.fe : {};
    const expr = (f.expr || f._factorName || f._label) ? String(f.expr || f._factorName || f._label).trim() : '';
    const features = (spec.features && spec.features.length) ? spec.features : (expr ? [expr] : []);
    if (!features.length) throw new Error('向量化回测: 上游因子无表达式/注册名也无 fe spec, 无法物化截面因子');
    const topn = (params.topn != null && params.topn !== '' && !isNaN(+params.topn)) ? +params.topn : 30;
    const cash = (params.cash != null && params.cash !== '' && !isNaN(+params.cash)) ? +params.cash : 1000000;
    const rebalance = String(params.rebalance || 'month');
    const commission = (params.commission != null && params.commission !== '' && !isNaN(+params.commission)) ? +params.commission : 0.0003;
    const stamp_tax = (params.stamp_tax != null && params.stamp_tax !== '' && !isNaN(+params.stamp_tax)) ? +params.stamp_tax : 0.0005;
    const slippage = (params.slippage != null && params.slippage !== '' && !isNaN(+params.slippage)) ? +params.slippage : 5;
    const combine = String(params.combine || 'equal');   // 多因子(特征>1)合成法 equal|ic|icir
    // 持仓定权:接了「组合构建」节点 → 以其定权/行业中性为准(终于让 portfolio 设置真驱动回测);否则用本节点参数。
    const weighting = (pfIn && pfIn.weighting) ? String(pfIn.weighting) : String(params.weighting || 'equal');
    const industry_neutral = pfIn ? !!pfIn.industry_neutral : false;
    const vol_forecast = (pfIn && pfIn.vol_forecast) ? String(pfIn.vol_forecast) : String(params.vol_forecast || 'hist');   // (b) 波动口径
    const btStart = String(params.start || spec.start || '').trim();
    const btEnd = String(params.end || spec.end || '').trim();
    const payload = Object.assign({}, spec, {
      features,
      universe: (ctx.universe || spec.universe || 'csi_fast'),
      ...(btStart ? { start: btStart } : {}), ...(btEnd ? { end: btEnd } : {}),
      orthogonalization: f._orthogonalization || spec.orthogonalization || null,
      topn, cash, rebalance, commission, stamp_tax, slippage_bps: slippage, combine,
      weighting, industry_neutral, vol_forecast,
      params: { topn, cash, rebalance, commission, stamp_tax, slippage, combine, weighting, vol_forecast },
    });
    const r = await ctx.post('/backtest/vector', payload);
    if (!r || r.ok === false) throw new Error('向量化回测: ' + ((r && r.reason) || '/backtest/vector 失败'));
    if (r.status && r.status !== 'ok') throw new Error('向量化回测: ' + r.status + (r.error ? ' · ' + r.error : ''));
    // 顶层并入后端报告 (portfolio + ic/quantile/characteristics/meta, 已对齐 /factor/report 形), 置
    // __dt=result + composite/_compose → 终端判定命中送抽屉; 附 topn/cash 与元信息供抽屉文案。
    const label = (f._label || (features.length && features.join('+')) || ('TopN' + topn));
    // 若经「组合」端口喂入 → 目标持仓权重带进结果抽屉展示;其 weighting/行业中性已用于回测逐期定权(W5-1 真按权重序列)。
    const pfExtra = pfIn ? { _pf_weights: (pfIn.weights || pfIn.holdings || []), _pf_weighting: pfIn.weighting, _pf_asof: pfIn.asof, _pf_industry_neutral: pfIn.industry_neutral } : {};
    return { result: Object.assign({ __dt: 'result' }, r, pfExtra, { composite: true, topn, cash,
      // 后端会把运行时真正采用的依赖版本写进 canonical factor_spec；必须优先保留它，
      // 否则上游缓存的 raw spec 会遮住 dependency pins，保存后无法精确重放本次回测。
      factor_spec: r.factor_spec || f.factor_spec || null,
      factor_spec_digest: r.factor_spec_digest || f.factor_spec_digest || null,
      _label: label, _universe: payload.universe, _compose: true, _warnings: r.warnings || f._warnings || [] }) };
  },
  // 过拟合审计只消费回测的逐期净值和同一研究家族的完整试验矩阵。缺证据会返回 INCOMPLETE，不会伪装 PASS。
  overfit: async (inputs, params, ctx) => {
    const src = inputs.result;
    if (!src) throw new Error('过拟合审计: 上游缺回测结果');
    const pf = src.portfolio || {};
    const auditContext = src.audit_context || {};
    const bt = src.backtest || {};
    const rebalance = String(bt.rebalance || bt.rebalance_freq || 'month');
    const ppy = (src.meta && src.meta.periods_per_year) || ({ day: 252, week: 52, month: 12 }[rebalance] || 12);
    const r = await ctx.post('/workflow/overfit-audit', {
      return_series: Array.isArray(pf.return_series) ? pf.return_series : null,
      nav_series: Array.isArray(pf.nav_series) ? pf.nav_series : null,
      trial_returns: Array.isArray(auditContext.trial_returns) ? auditContext.trial_returns : null,
      trial_dates: Array.isArray(auditContext.trial_dates) ? auditContext.trial_dates : null,
      n_trials: auditContext.n_trials == null ? null : +auditContext.n_trials,
      family_id: auditContext.family_id || null,
      selected_trial_id: auditContext.selected_trial_id || null,
      audit_context_id: params.audit_context_id || auditContext.audit_context_id || null,
      periods_per_year: +ppy || 252,
      confidence: (params.confidence != null && !isNaN(+params.confidence)) ? +params.confidence : 0.95,
      pbo_max: (params.pbo_max != null && !isNaN(+params.pbo_max)) ? +params.pbo_max : 0.30,
      n_blocks: (params.n_blocks != null && !isNaN(+params.n_blocks)) ? +params.n_blocks : 8,
      overlapping_returns: !!bt.overlapping_returns,
    });
    if (!r || r.ok === false) throw new Error('过拟合审计: ' + ((r && (r.reason || r.note)) || '/workflow/overfit-audit 失败'));
    return { audit: Object.assign({ __dt: 'overfit_audit' }, r, {
      portfolio: src.portfolio, ic: src.ic, oos: src.oos, headline_ic: src.headline_ic,
      orthogonalization: src.orthogonalization || null,
      factor_spec: src.factor_spec || null,
      _label: src._label || '回测过拟合审计', _universe: src._universe || ctx.universe,
      _warnings: (src._warnings || []).concat(r.warnings || []),
    }) };
  },
  // —— 风险度量 (VaR/CVaR/EVT/Kupiec): 收 inputs.factor → 复用回测建 TopN 组合 → 损失分布尾部风险。
  //    后端 FactorRiskIn 默认周频 + _factor_risk 默认 3 年窗(VaR 需更密更长的组合收益样本)。
  //    终端 dt=risk(method='risk', risk 块带 levels/evt/kupiec/mc/hist)→ 进结果抽屉风险视图。
  //    诚实失败: 上游缺因子 / 后端 ok:false (含 reason) / status≠ok → 抛错原样显示;样本不足→ risk.enabled=false 诚实空。——
  risk: async (inputs, params, ctx) => {
    const f = inputs.factor;
    if (!f) throw new Error('风险度量: 上游缺因子 (需「因子」端口直连 公式/因子库/PCA/Spearman/多因子)');
    if (f.ok === false) throw new Error('风险度量: 上游因子未物化 (' + (f.reason || '上游构建失败') + ')');
    const spec = (f.fe && typeof f.fe === 'object') ? f.fe : {};
    const expr = (f.expr || f._factorName || f._label) ? String(f.expr || f._factorName || f._label).trim() : '';
    const features = (spec.features && spec.features.length) ? spec.features : (expr ? [expr] : []);
    if (!features.length) throw new Error('风险度量: 上游因子无表达式/注册名也无 fe spec, 无法物化截面因子');
    const topn = (params.topn != null && params.topn !== '' && !isNaN(+params.topn)) ? +params.topn : 30;
    const payload = Object.assign({}, spec, { features, universe: (ctx.universe || spec.universe || 'csi_fast'), topn });
    const r = await ctx.post('/workflow/risk', payload);
    if (!r || r.ok === false) throw new Error('风险度量: ' + ((r && r.reason) || '/workflow/risk 失败'));
    if (r.status && r.status !== 'ok') throw new Error('风险度量: ' + r.status);
    const label = (f._label || (features.length && features.join('+')) || ('TopN' + topn));
    return { risk: Object.assign({ __dt: 'risk' }, r, { _label: label, _universe: payload.universe, _warnings: r.warnings || f._warnings || [] }) };
  },
  // —— 条件波动预测 (GARCH): 收 inputs.factor → 建 TopN 组合 (复用回测) → 净值期收益 →
  //    POST /workflow/garch 拟合 EWMA + GARCH(1,1) → 条件波动路径 + 向前多步预测。dt=garch 终端。
  garch: async (inputs, params, ctx) => {
    const f = inputs.factor;
    if (!f) throw new Error('条件波动预测: 上游缺因子 (需「因子」端口直连 公式/因子库/PCA/Spearman/多因子)');
    if (f.ok === false) throw new Error('条件波动预测: 上游因子未物化 (' + (f.reason || '上游构建失败') + ')');
    const spec = (f.fe && typeof f.fe === 'object') ? f.fe : {};
    const expr = (f.expr || f._factorName || f._label) ? String(f.expr || f._factorName || f._label).trim() : '';
    const features = (spec.features && spec.features.length) ? spec.features : (expr ? [expr] : []);
    if (!features.length) throw new Error('条件波动预测: 上游因子无表达式/注册名也无 fe spec, 无法物化截面因子');
    const topn = (params.topn != null && params.topn !== '' && !isNaN(+params.topn)) ? +params.topn : 30;
    const horizon = (params.horizon != null && params.horizon !== '' && !isNaN(+params.horizon)) ? +params.horizon : 12;
    const payload = Object.assign({}, spec, { features, universe: (ctx.universe || spec.universe || 'csi_fast'), topn, horizon });
    const r = await ctx.post('/workflow/garch', payload);
    if (!r || r.ok === false) throw new Error('条件波动预测: ' + ((r && r.reason) || '/workflow/garch 失败'));
    if (r.status && r.status !== 'ok') throw new Error('条件波动预测: ' + r.status);
    const label = (f._label || (features.length && features.join('+')) || ('TopN' + topn));
    return { garch: Object.assign({ __dt: 'garch' }, r, { _label: label, _universe: payload.universe, _warnings: r.warnings || f._warnings || [] }) };
  },
  // —— 风格归因 (attrib): 收 inputs.factor → 建 TopN 组合 (复用回测) → 净值期收益 →
  //    POST /workflow/attrib 对四风格因子收益 OLS+Newey-West HAC → 因子暴露β/alpha/R²/收益贡献。dt=attrib 终端。
  attrib: async (inputs, params, ctx) => {
    const f = inputs.factor;
    if (!f) throw new Error('风格归因: 上游缺因子 (需「因子」端口直连 公式/因子库/PCA/Spearman/多因子)');
    if (f.ok === false) throw new Error('风格归因: 上游因子未物化 (' + (f.reason || '上游构建失败') + ')');
    const spec = (f.fe && typeof f.fe === 'object') ? f.fe : {};
    const expr = (f.expr || f._factorName || f._label) ? String(f.expr || f._factorName || f._label).trim() : '';
    const features = (spec.features && spec.features.length) ? spec.features : (expr ? [expr] : []);
    if (!features.length) throw new Error('风格归因: 上游因子无表达式/注册名也无 fe spec, 无法物化截面因子');
    const topn = (params.topn != null && params.topn !== '' && !isNaN(+params.topn)) ? +params.topn : 30;
    const payload = Object.assign({}, spec, { features, universe: (ctx.universe || spec.universe || 'csi_fast'), topn });
    const r = await ctx.post('/workflow/attrib', payload);
    if (!r || r.ok === false) throw new Error('风格归因: ' + ((r && r.reason) || '/workflow/attrib 失败'));
    if (r.status && r.status !== 'ok') throw new Error('风格归因: ' + r.status);
    const label = (f._label || (features.length && features.join('+')) || ('TopN' + topn));
    return { attrib: Object.assign({ __dt: 'attrib' }, r, { _label: label, _universe: payload.universe, _warnings: r.warnings || f._warnings || [] }) };
  },
  // —— 时变β (Kalman): 收 inputs.factor → 建 TopN 组合 (复用回测) → 净值期收益 →
  //    POST /workflow/tvbeta 对市场期收益(默认沪深300)做时变参数回归 r=α_t+β_t·m → Kalman 滤波+RTS 平滑 → β(t) 演化路径。dt=tvbeta 终端。
  tvbeta: async (inputs, params, ctx) => {
    const f = inputs.factor;
    if (!f) throw new Error('时变β: 上游缺因子 (需「因子」端口直连 公式/因子库/PCA/Spearman/多因子)');
    if (f.ok === false) throw new Error('时变β: 上游因子未物化 (' + (f.reason || '上游构建失败') + ')');
    const spec = (f.fe && typeof f.fe === 'object') ? f.fe : {};
    const expr = (f.expr || f._factorName || f._label) ? String(f.expr || f._factorName || f._label).trim() : '';
    const features = (spec.features && spec.features.length) ? spec.features : (expr ? [expr] : []);
    if (!features.length) throw new Error('时变β: 上游因子无表达式/注册名也无 fe spec, 无法物化截面因子');
    const topn = (params.topn != null && params.topn !== '' && !isNaN(+params.topn)) ? +params.topn : 30;
    const payload = Object.assign({}, spec, { features, universe: (ctx.universe || spec.universe || 'csi_fast'), topn });
    const r = await ctx.post('/workflow/tvbeta', payload);
    if (!r || r.ok === false) throw new Error('时变β: ' + ((r && r.reason) || '/workflow/tvbeta 失败'));
    if (r.status && r.status !== 'ok') throw new Error('时变β: ' + r.status);
    const label = (f._label || (features.length && features.join('+')) || ('TopN' + topn));
    return { tvbeta: Object.assign({ __dt: 'tvbeta' }, r, { _label: label, _universe: payload.universe, _warnings: r.warnings || f._warnings || [] }) };
  },
  // —— 组合构建 (W5-1): 收 inputs.factor (公式/因子库/PCA/Spearman/多因子) → fe_spec 透传 →
  //    POST /portfolio/build → 后端在最新截面 nlargest(topn) 按 weighting 定权 (+行业中性/单票上限)
  //    → 最新一期目标持仓权重。dt=portfolio 终端 (进抽屉看目标持仓), 亦可再连「向量化回测」的 pf 口。
  //    出最新期目标权重 (下单参考);接「向量化回测」pf 口时其定权法用于回测逐期加权(真按权重序列)。
  //    诚实失败: 上游缺因子 / 后端 ok:false (含 reason) / status≠ok / HTTP 非 200 → 抛错, 原样显示。——
  portfolio: async (inputs, params, ctx) => {
    const f = inputs.factor;
    if (!f) throw new Error('组合构建: 上游缺因子 (需「因子」端口直连 公式/因子库/PCA/Spearman/多因子)');
    if (f.ok === false) throw new Error('组合构建: 上游因子未物化 (' + (f.reason || '上游构建失败') + ')');
    const spec = (f.fe && typeof f.fe === 'object') ? f.fe : {};
    const expr = (f.expr || f._factorName || f._label) ? String(f.expr || f._factorName || f._label).trim() : '';
    const features = (spec.features && spec.features.length) ? spec.features : (expr ? [expr] : []);
    if (!features.length) throw new Error('组合构建: 上游因子无表达式/注册名也无 fe spec, 无法物化截面因子');
    const topn = (params.topn != null && params.topn !== '' && !isNaN(+params.topn)) ? +params.topn : 30;
    const weighting = String(params.weighting || 'equal');
    const max_weight = (params.max_weight != null && params.max_weight !== '' && !isNaN(+params.max_weight)) ? +params.max_weight : 0;
    const industry_neutral = (params.industry_neutral === '是' || params.industry_neutral === true);
    const vol_forecast = String(params.vol_forecast || 'hist');   // (b) 波动口径 hist/ewma/garch
    const payload = Object.assign({}, spec, {
      features,
      universe: (ctx.universe || spec.universe || 'csi_fast'),
      orthogonalization: f._orthogonalization || spec.orthogonalization || null,
      topn, weighting, max_weight, industry_neutral, vol_forecast,
      params: { topn, weighting, max_weight, industry_neutral, vol_forecast },
    });
    const r = await ctx.post('/portfolio/build', payload);
    if (!r || r.ok === false) throw new Error('组合构建: ' + ((r && r.reason) || '/portfolio/build 失败'));
    if (r.status && r.status !== 'ok') throw new Error('组合构建: ' + r.status + (r.error ? ' · ' + r.error : ''));
    const label = (f._label || (features.length && features.join('+')) || ('组合 Top' + topn));
    return { pf: Object.assign({ __dt: 'portfolio' }, r, { composite: true, weights: (r.holdings || []), weighting: r.weighting, vol_forecast: (r.vol_forecast || vol_forecast), asof: r.asof, industry_neutral: r.industry_neutral, fe: (r.fe || spec), factor_spec: f.factor_spec || r.factor_spec || null, factor_spec_digest: f.factor_spec_digest || r.factor_spec_digest || null, _label: label, _universe: payload.universe, _compose: true, _warnings: r.warnings || f._warnings || [] }) };
  },
};

// ───────── 拓扑排序 (Kahn; 并列项按 x 升序 → 保留左→右逐个点亮观感) ─────────
function topoOrder(nodes, edges) {
  const indeg = new Map(nodes.map(n => [n.id, 0]));
  const adj = new Map(nodes.map(n => [n.id, []]));
  edges.forEach(e => {
    if (indeg.has(e.to[0]) && indeg.has(e.from[0])) {
      indeg.set(e.to[0], indeg.get(e.to[0]) + 1);
      adj.get(e.from[0]).push(e.to[0]);
    }
  });
  const q = nodes.filter(n => indeg.get(n.id) === 0).sort((a, b) => a.x - b.x).map(n => n.id);
  const order = [];
  while (q.length) {
    const id = q.shift(); order.push(id);
    const next = [];
    adj.get(id).forEach(t => { indeg.set(t, indeg.get(t) - 1); if (indeg.get(t) === 0) next.push(t); });
    next.sort((a, b) => (nodes.find(n => n.id === a).x - nodes.find(n => n.id === b).x));
    next.forEach(t => q.push(t));
  }
  // 有环 → order 不全; 兜底把漏掉的按 x 追加 (不死循环)
  if (order.length < nodes.length) {
    nodes.filter(n => !order.includes(n.id)).sort((a, b) => a.x - b.x).forEach(n => order.push(n.id));
  }
  return order;
}

// ───────── 通用拓扑 DAG 执行器 (逐节点 await exec, 沿边喂数据, 经 hooks 写回) ─────────
// 需要 universe(股票池)的节点类型 —— 纯 IO 出 series 的(source/formula/factorlib)不吃 universe。
const _NEEDS_UNIVERSE = { history_screen: 1, data_screen: 1, feature: 1, xgb: 1, lgbm: 1, svm: 1, rf: 1, nn: 1, lstm: 1, pca: 1, spearman: 1, orthogonalize: 1, iccalc: 1, mf: 1, analysis: 1, backtest: 1, portfolio: 1 };
// 自选代码串 → 归一 list(逗号/空格/分号/中文逗号顿号/换行分隔)。
function _parseCodes(s) {
  const parts = Array.isArray(s) ? s : String(s == null ? '' : s).split(/[\s,;，、]+/);
  return parts.map(x => String(x).trim()).filter(Boolean);
}
// 沿入边反向回溯, 找本节点上游最近的「数据源」节点 → 用它的股票池 + 起止日 + 自选代码(连了才生效)。
// BFS 带 visited 防环;找不到上游 source → { wired:false }, 调用方回退全局默认。传递闭包: source→feature→model→mf 多跳也能回溯到。
function _universeForNode(nodeId, nodes, edges) {
  const byId = id => nodes.find(n => n.id === id);
  const seen = new Set([nodeId]);
  let frontier = [nodeId];
  while (frontier.length) {
    const next = [];
    const sources = new Map();
    for (const cur of frontier) {
      const current = byId(cur) || {};
      const contextId = (current.params || {}).context_source_id;
      if (contextId !== undefined) {
        const context = byId(contextId);
        if (!context || context.type !== 'source') throw new Error('数据上下文引用失效：' + cur);
        sources.set(context.id, context);
      } else if (current.type === 'source') sources.set(current.id, current);
      for (const e of edges) {
        if (e.to[0] === cur && !seen.has(e.from[0])) {
          seen.add(e.from[0]); next.push(e.from[0]);
        }
      }
    }
    if (sources.size > 1) throw new Error('多个等距数据源，请显式指定 context_source_id');
    if (sources.size === 1) {
      const p = sources.values().next().value.params || {};
      let codes = _parseCodes(p.codes);
      if (['single', '个股'].includes(String(p.scope || '').trim().toLowerCase())) {
        if (!codes.length) codes = [String(p.code || '').trim()];
        if (codes.length !== 1 || !/^(?:(?:SH|SZ|BJ)?\d{6}|\d{6}\.(?:SH|SZ|BJ))$/i.test(codes[0])) throw new Error('单股 source 必须明确一个合法股票代码；拒绝回落到默认股票池');
        const canonicalCode = value => {
          const text = String(value || '').trim().toUpperCase();
          if (text.includes('.')) { const [digits, exchange] = text.split('.'); return exchange + digits; }
          if (/^\d{6}$/.test(text)) return (/^(4|8|92)/.test(text) ? 'BJ' : text.startsWith('6') ? 'SH' : 'SZ') + text;
          return text;
        };
        if (String(p.code || '').trim() && canonicalCode(p.code) !== canonicalCode(codes[0])) throw new Error('单股 source 的 code 与 codes 冲突；拒绝猜测范围');
      }
      return { universe: _universeOf(p), start: (p.start || '').trim(), end: (p.end || '').trim(), codes, benchmark: (p.benchmark || '').trim(), leader: (p.leader || '').trim(), oos_frac: parseFloat(p.oos_frac) || 0, wf_refit: p.wf_refit === '是' || p.wf_refit === true, wired: true };
    }
    frontier = next;
  }
  if (nodes.filter(n => n.type === 'source').length > 1 && _NEEDS_UNIVERSE[(byId(nodeId) || {}).type]) throw new Error('未连接的数据节点不能猜测多个股票池，请连接数据源');
  return { universe: null, start: '', end: '', codes: [], benchmark: '', leader: '', oos_frac: 0, wf_refit: false, wired: false };
}

// E3「存入模型库」: 从一个 ML 模型节点(xgb/lgbm/rf)的上游静态导出 recipe = {features,label,fwd_days,universe,params}。
//   features: 上游每个「特征工程」节点的 feat 端口源(公式/因子库)表达式(zoo-DSL),保序去重;
//   label   : 任一上游特征节点的 label 端口源表达式;无 label 公式且 tag∈{IC,fwd_ret,空}→ 前向收益(label=null);
//   universe: 沿入边回溯到「数据源」(同 runGraph 口径);
//   params  : 本节点超参按 hpMap 映射成后端超参名(与 _trainModel 同表)。
//   后端 /model/promote 只读 recipe.features/label/fwd_days/universe/params;features 空 → 后端拒。
const _PROMOTE_HPMAP = {
  xgb: { n_estimators: 'trees', max_depth: 'depth', learning_rate: 'lr', subsample: 'sub' },
  lgbm: { num_leaves: 'leaves', learning_rate: 'lr' },
  rf: { n_estimators: 'trees' },
};
const _LABEL_FWD_TOKENS = { '': 1, ic: 1, fwd_ret: 1 };
function deriveRecipeForNode(node, nodes, edges) {
  const byId = id => nodes.find(n => n.id === id);
  // 本 ML 节点的直接上游「特征工程」节点(经 fe 端口连入)。
  const featNodes = edges.filter(e => e.to[0] === node.id && e.to[1] === 'fe').map(e => byId(e.from[0])).filter(n => n && n.type === 'feature');
  const features = []; const seen = new Set();
  let label = null;
  for (const fn of featNodes) {
    // 特征节点的 feat 端口源 → 表达式
    edges.filter(e => e.to[0] === fn.id && e.to[1] === 'feat').forEach(e => {
      const up = byId(e.from[0]);
      const expr = up ? String((up.params && (up.params.expr)) || '').trim() : '';
      if (expr && !seen.has(expr)) { seen.add(expr); features.push(expr); }
    });
    // label 端口源 → 公式标签(取首个非空);否则用 tag(IC/fwd_ret/空 → 前向收益 = label:null)
    if (label == null) {
      const le = edges.find(e => e.to[0] === fn.id && e.to[1] === 'label');
      const lup = le ? byId(le.from[0]) : null;
      const lexpr = lup ? String((lup.params && lup.params.expr) || '').trim() : '';
      if (lexpr) label = lexpr;
      else { const tag = String((fn.params && fn.params.tag) || '').trim().toLowerCase(); if (!_LABEL_FWD_TOKENS[tag]) label = (fn.params && fn.params.tag) || null; }
    }
  }
  const u = _universeForNode(node.id, nodes, edges);
  const universe = u.wired ? u.universe : 'csi_fast';
  const hpMap = _PROMOTE_HPMAP[node.type] || {};
  const params = {};
  for (const k in hpMap) { const v = node.params[hpMap[k]]; if (v != null && v !== '') params[k] = (typeof v === 'string' && v.trim() !== '' && !isNaN(+v)) ? +v : v; }
  return { features, label, fwd_days: 5, universe, params };
}

// hooks = { onState(id,'running'|'done'|'error'), onResult(report), onError(msg) }
async function runGraph(nodes, edges, hooks) {
  const order = topoOrder(nodes, edges);
  const byId = id => nodes.find(n => n.id === id);
  const outputs = {};            // nodeId -> exec 返回 (各 output 端口载荷)
  const src = nodes.find(n => n.type === 'source');
  const universe = src ? _universeOf(src.params) : 'csi_fast';
  const allExprs = (deriveCall(nodes) || {}).exprs || [];  // mf 兜底 (上游占位拿不到逐端口 expr 时退回全图表达式集合)
  const TERMINAL_DT = { data_screen: 1, report: 1, ic: 1, result: 1, audit: 1, portfolio: 1, tsic: 1, event: 1, relstat: 1, risk: 1, garch: 1, attrib: 1, tvbeta: 1, validation: 1 };
  let lastResult = null, firstErr = '', anyFellBack = false; const nodeErrs = []; const multiEdgeWarns = [];

  for (const id of order) {
    const node = byId(id); const spec = SPECS[node.type];
    // 收集上游输入: 对本节点每条入边, 取上游 outputs[源节点][源端口] → inputs[本节点入端口]
    // feature.feat 口多边聚合为数组 (多特征, 镜像后端 run_graph); 其余同口多边后到覆盖先到 + 记告警。
    const inputs = {}; const inEdgeN = {};
    edges.filter(e => e.to[0] === id).forEach(e => {
      const up = outputs[e.from[0]];
      if (!up) return;
      inEdgeN[e.to[1]] = (inEdgeN[e.to[1]] || 0) + 1;
      if (node.type === 'feature' && e.to[1] === 'feat') (inputs.feat = inputs.feat || []).push(up[e.from[1]]);
      else inputs[e.to[1]] = up[e.from[1]];
    });
    Object.keys(inEdgeN).forEach(port => {
      if (inEdgeN[port] > 1 && !(node.type === 'feature' && port === 'feat'))
        multiEdgeWarns.push('节点 ' + id + '(' + node.type + ') 输入口 ' + port + ' 收到 ' + inEdgeN[port] + ' 条边——仅最后一条生效');
    });
    const exec = NODE_EXEC[node.type];
    // 逐节点解析 universe: 沿入边回溯到上游「数据源」→ 用其池(连了才生效); 否则回退全局默认 + 记 fellBack。
    const _u = _universeForNode(id, nodes, edges);
    const nodeUniverse = _u.wired ? _u.universe : universe;
    if (!_u.wired && _NEEDS_UNIVERSE[node.type]) anyFellBack = true;
    // #2/#5: 源节点的「起止日 / 自选代码」沿图下传(连了才生效)→ 包进每次后端调用(覆盖 fe spec 里的旧窗口/池)。
    const _wc = {};
    if (_u.wired && _u.start) _wc.start = _u.start;
    if (_u.wired && _u.end) _wc.end = _u.end;
    if (_u.wired && _u.codes && _u.codes.length) _wc.codes = _u.codes;
    if (_u.wired && _u.benchmark) _wc.benchmark = _u.benchmark;   // B2 对标指数 → 注入 idx_ret 字段(大盘共振)
    if (_u.wired && _u.leader) _wc.leader = _u.leader;            // B2 龙头代码 → 注入 ref_ret 字段(龙头共振)
    if (_u.wired && _u.oos_frac > 0) _wc.oos_frac = _u.oos_frac;  // W7 样本外占比 → 因子分析/回测/模型做 IS/OOS 过拟合体检
    if (_u.wired && _u.wf_refit) _wc.wf_refit = true;             // W7 真滚动重训 → ML 节点逐折重训拼接 OOS
    const _postWC = (path, payload) => _post(path, Object.assign({}, payload || {}, _wc));
    hooks.onState(id, 'running');
    // 点亮节奏: 每节点最少 220ms 在屏 (保留原观感), 与真 await 取 max
    const minLit = new Promise(r => setTimeout(r, 220));
    try {
      const out = exec
        ? await exec(inputs, node.params, { universe: nodeUniverse, start: _u.start || '', end: _u.end || '', codes: (_u.codes || []), node, post: _postWC, list: _list, allExprs })
        : {};                              // 无注册 exec → 视作透传空 (不报错)
      await minLit;
      outputs[id] = out || {};
      hooks.onState(id, 'done');
      // 终端: 若本节点有任一 output 端口 dt ∈ TERMINAL_DT 且其载荷是真报告 → 记为结果
      spec.outputs.forEach(p => {
        const payload = outputs[id][p.id];
        if (TERMINAL_DT[p.dt] && payload && !payload.__pending && (payload.ic || payload.portfolio || payload.composite || payload._compose != null || payload.holdings || payload.weights || payload.codes_tsic || payload.car_curve || payload.codes_relstat || payload.method === 'risk' || payload.method === 'garch' || payload.method === 'attrib' || payload.method === 'tvbeta' || payload.__dt === 'validation' || payload.__dt === 'overfit_audit' || payload.__dt === 'data_screen')) {
          lastResult = payload;
        }
      });
    } catch (e) {
      await minLit;
      hooks.onState(id, 'error');
      const _em = (e && e.message) || String(e);
      if (!firstErr) firstErr = _em;
      nodeErrs.push(spec.title + ': ' + _em);
      // 不中断: 继续跑其余节点 (下游因缺 input 自然降级/占位)
    }
  }
  // 数据源未连线 → 诚实提示: 本次用默认池(把「数据源」连到下游才按所选池跑),写进结果抽屉 insight。
  if (anyFellBack && lastResult && typeof lastResult === 'object') {
    lastResult._warnings = ['未接数据源,本次用默认池 ' + universe + '(把「数据源」节点连到下游才会按所选股票池跑)', ...(lastResult._warnings || [])];
  }
  // 部分节点失败但下游仍产出结果 → 在结果里诚实标出哪些节点挂了(否则用户看到「成功」却不知有节点降级)。
  if (nodeErrs.length && lastResult && typeof lastResult === 'object') {
    lastResult._completed_with_errors = true;
    lastResult._run_status = 'completed_with_errors';
    lastResult._node_errors = nodeErrs.slice();
    lastResult._warnings = ['运行完成 · 含节点错误(completed_with_errors)：' + nodeErrs.length + ' 个节点执行失败，下游结果可能不完整。' + nodeErrs.join(' / '), ...(lastResult._warnings || [])];
  }
  // 非白名单口多边 last-wins → 诚实显形 (镜像后端 run_graph warnings; 画布拖线本不会造出这种图, 导入/粘贴图会)。
  if (multiEdgeWarns.length && lastResult && typeof lastResult === 'object') {
    lastResult._warnings = [...multiEdgeWarns, ...(lastResult._warnings || [])];
  }
  if (lastResult) hooks.onResult(lastResult);
  else if (firstErr) hooks.onError(firstErr);
  else hooks.onError('画布里没有可产出结果的终端节点 (因子分析 / IC / 回测), 或缺「公式输入」表达式');
}

// ───────── 主组件 ─────────
function WorkflowApp() {
  const init = useRef(seedGraph()).current;
  const [nodes, setNodes] = useState(init.nodes);
  const [edges, setEdges] = useState(init.edges);
  const [sel, setSel] = useState(null);
  const [view, setView] = useState({ x: 0, y: 0, z: 1 });
  const [draft, setDraft] = useState(null); // 连线草稿 {fromNode, fromPort, x, y}
  const [running, setRunning] = useState(false);
  const [runState, setRunState] = useState({}); // nodeId -> 'running'|'done'
  const [showRes, setShowRes] = useState(false);
  const [result, setResult] = useState(null);   // 运行后的真报告 (填充结果抽屉)
  const [repOpen, setRepOpen] = useState(false); // #9 报告库弹窗
  const [loop, setLoop] = useState(null);         // W8b AI 闭环编排状态 {running,goal,rounds,step}
  const [runErr, setRunErr] = useState('');
  const [durableWaiting, setDurableWaiting] = useState(false); // 仅表示本页正在跟踪服务端持久任务
  const [cards] = useState(EXP_CARDS);
  const [toast, setToast] = useState(null);
  const [past, setPast] = useState([]);
  const [future, setFuture] = useState([]);
  const [wfName, setWfName] = useState('因子示例 · XGBoost 因子');
  const [wfScope, setWfScope] = useState(WW_WORKSPACE_ID ? 'workspace' : 'global');
  const [assetNeedsPool, setAssetNeedsPool] = useState(false);
  const [saved, setSaved] = useState(wfLoadList);
  const [resCache, setResCache] = useState(wfLoadReports);  // scope + 工作流名 → 最近一次报告
  const [saveOpen, setSaveOpen] = useState(null); // {name}
  const [histOpen, setHistOpen] = useState(false);
  const [serverRuns, setServerRuns] = useState([]);
  const [serverRunsLoading, setServerRunsLoading] = useState(false);
  const [serverRunsError, setServerRunsError] = useState('');
  const [historyOpening, setHistoryOpening] = useState('');
  const [aiBusy, setAiBusy] = useState(false);
  const fileRef = useRef(null);
  const wrapRef = useRef(null);
  const runPollRef = useRef(null);
  const runBusyRef = useRef(false);        // 同步互斥:挡住 React 重绘前的双击并发启动
  const durableWaitIdRef = useRef('');     // 本地跟踪 token；清空即使在途 fetch 结果失效
  const mountedRef = useRef(true);
  const historyRequestRef = useRef(0);

  useEffect(() => () => {
    mountedRef.current = false;
    durableWaitIdRef.current = '';
    if (runPollRef.current) clearTimeout(runPollRef.current);
  }, []);

  const flash = (title, build, ms) => { setToast({ title, build }); setTimeout(() => setToast(null), ms || 3000); };
  // #9 报告库: 导出当前抽屉结果 → /report/save 真存盘;从报告库「重看」→ 载回抽屉。
  const exportReport = () => {
    const r = result;
    // 可导出 = 任一终端载荷(与 runGraph 终端判定同源):因子/回测/组合 + 个股时序IC(codes_tsic)/事件(car_curve)/关系稳定(codes_relstat)。
    const hasPayload = r && (r.ic || r.portfolio || r.composite || r._compose != null || r.holdings || r.weights || r.codes_tsic || r.car_curve || r.codes_relstat || r.method === 'risk' || r.method === 'garch' || r.method === 'attrib' || r.method === 'tvbeta' || r.__dt === 'overfit_audit' || r.__dt === 'data_screen');
    if (!hasPayload) { flash('导出报告', '当前无可导出的报告(先运行出结果)'); return; }
    const isTsic = !!r.codes_tsic;
    const isAudit = r.__dt === 'overfit_audit' || r.method === 'overfit_audit';
    const isDataScreen = r.__dt === 'data_screen';
    const method = r.method || (isDataScreen ? 'data_screen' : (isTsic ? 'tsic' : (r.car_curve ? 'event' : (r.codes_relstat ? 'relstat' : undefined))));
    const nm = window.prompt('报告命名', r._label || (isDataScreen ? '数据筛选 · ' + (r.as_of || '') : (isTsic ? '个股时序IC' : '')));
    if (nm == null) return;
    const ic = r.ic || {}, pf = r.portfolio || {}, sm = r.summary || {}, tp = (sm.timing_pool || {}), am = r.metrics || {}, ah = am.haircut || {}, ae = r.evidence || {};
    const kpi = isDataScreen
      ? { as_of: r.as_of, n_loaded: r.n_loaded, n_evaluated: r.n_evaluated, n_matched: r.n_matched, returned: (r.candidates || []).length }
      : isTsic
      ? { mean_pearson: sm.mean_pearson, mean_icir: sm.mean_icir, pool_sharpe: tp.pool_sharpe, mean_r2os: sm.mean_r2os }
      : (isAudit
          ? { audit_status: r.status, passes_gate: r.passes_gate === true, psr: am.psr, dsr: am.dsr, pbo: am.pbo, adjusted_psr: ah.adjusted_psr, n_trials: ae.n_trials }
          : { rank_ic: ic.rank_ic_mean, sharpe: pf.sharpe, ann_return: (pf.net_ann != null ? pf.net_ann : pf.ann_return), max_drawdown: pf.max_drawdown });
    // 快照产出该报告的工作流:名 + 全图 → 「重看」时铺回画布,真正回到对应工作流。
    if (WW_READONLY) { flash('归档空间只读', '历史工作流不能新增报告'); return; }
    _post('/report/save', _withWorkspace({ name: (nm || '').trim(), universe: r._universe, label: r._label, method, workflow_name: wfName, workflow_scope: wfScope, graph: { nodes, edges }, kpi, result: r }))
      .then(res => flash(res && res.ok ? '已存入报告库 · ' + (res.name || '') : '导出失败', (res && res.reason) || '顶栏「📁 报告库」点「重看」连图一起回到本工作流', 5000))
      .catch(e => flash('导出失败', String(e.message || e)));
  };
  // 报告库「重看」:先把快照图铺回画布(回到产出该报告的工作流),再把结果填进抽屉。
  const reopenReport = (rec) => {
    const r = (rec && rec.result) || rec;
    const g = rec && rec.graph;
    const nm = (rec && rec.workflow_name) || wfName;
    const scope = (rec && rec.workflow_scope) || wfScope;
    const graphBad = (g && Array.isArray(g.nodes) && g.nodes.length) ? validateGraph(g.nodes, g.edges || []) : '';
    if (g && Array.isArray(g.nodes) && g.nodes.length && !graphBad) {
      pushHist();
      const cg = cloneGraph(g.nodes, g.edges || []);
      setNodes(cg.nodes); setEdges(cg.edges); setAssetNeedsPool(graphNeedsPoolReview(cg.nodes)); setSel(null);
      if (rec && rec.workflow_name) setWfName(rec.workflow_name);
      setWfScope(scope);
      wfSaveLast({ name: nm, scope, nodes: cg.nodes, edges: cg.edges });   // 重看即记为「上次会话」→ 刷新恢复到此
    }
    setResult(r); setShowRes(true); setRepOpen(false);
    setResCache(c => { const n = { ...c, [wfReportKey(nm, scope)]: r }; wfSaveReports(n); return n; });
    flash(graphBad ? '已打开报告 · 附图未载入' : '已打开报告', graphBad || ((rec && rec.name) ? (rec.name + (g && g.nodes ? ' · 已回到对应工作流' : '')) : ''), graphBad ? 6500 : 3000);
  };
  const pushHist = () => { setPast(p => [...p.slice(-49), { nodes, edges, assetNeedsPool }]); setFuture([]); };
  const undo = () => setPast(p => { if (!p.length) return p; const prev = p[p.length - 1]; setFuture(f => [{ nodes, edges, assetNeedsPool }, ...f]); setNodes(prev.nodes); setEdges(prev.edges); setAssetNeedsPool(!!prev.assetNeedsPool); setSel(null); return p.slice(0, -1); });
  const redo = () => setFuture(f => { if (!f.length) return f; const nx = f[0]; setPast(p => [...p, { nodes, edges, assetNeedsPool }]); setNodes(nx.nodes); setEdges(nx.edges); setAssetNeedsPool(!!nx.assetNeedsPool); setSel(null); return f.slice(1); });
  const duplicate = () => { if (!sel) return; const n = nodes.find(x => x.id === sel); if (!n) return; pushHist(); const id = nid(); setNodes(ns => [...ns, { ...n, id, x: n.x + 28, y: n.y + 28, params: { ...n.params } }]); setSel(id); flash('已复制节点', SPECS[n.type].title); };
  const saveLocal = () => WW_READONLY ? flash('归档空间只读', '历史工作流不能保存或覆盖') : setSaveOpen({ name: wfName });
  const saveNamed = (name) => {
    if (WW_READONLY) { flash('归档空间只读', '历史工作流不能保存或覆盖'); return; }
    const bad = validateGraph(nodes, edges);
    if (bad) { flash('保存前校验未通过', bad, 6500); return; }
    const nm = (name || '').trim() || ('工作流 ' + new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }));
    const entry = { id: 'w' + Date.now(), name: nm, ts: Date.now(), nodes, edges,
      scope: WW_WORKSPACE_ID ? 'workspace' : 'global', workspace_id: WW_WORKSPACE_ID || null, read_only: false };
    // 同名仅覆盖当前空间副本；全局只读模板继续保留并明确展示。
    setSaved(list => { const next = [entry, ...list.filter(x => x.name !== nm || wfIsGlobalTemplate(x))]; wfSaveList(next); return next; }); // ① 本地先存 (降级保底)
    const nextScope = WW_WORKSPACE_ID ? 'workspace' : 'global';
    setWfName(nm); setWfScope(nextScope); setSaveOpen(null); flash('已保存到历史', nm);
    wfSaveLast({ name: nm, scope: nextScope, nodes, edges });   // 保存即记为「上次会话」→ 刷新恢复到此
    // ② 也写服务端: 成功用后端 id/ts 回填该条 (payload 形状 = {name, graph:{nodes,edges}})。_post 会抛, 失败仅留 localStorage 静默降级。
    _post('/workflow/save', _withWorkspace({ name: nm, graph: { nodes, edges } }))
      .then(r => {
        const rec = r && (r.workflow || r.item || r);
        if (r && r.ok && rec && rec.id) setSaved(list => list.map(x => x.id === entry.id ? { ...x, ...wfNormalizeRemote(rec), id: rec.id, ts: rec.ts || x.ts, read_only: false, is_template: false } : x));
        else flash('已存本地 · 服务端保存未成', (r && r.reason) || '后端恢复后再保存一次即可同步', 5000);
      })
      .catch(() => flash('已存本地 · 服务端不可达', '后端恢复后再保存一次即可同步', 5000));
  };
  const loadEntry = async (entry) => {
    const entryGraph = entry && entry.graph;
    let full = (entryGraph && Array.isArray(entryGraph.nodes) && entryGraph.nodes.length)
      ? { ...entry, nodes: entryGraph.nodes, edges: entryGraph.edges || [] }
      : entry;
    if (!Array.isArray(full.nodes) || !full.nodes.length) {          // 旧列表摘要 → 取全图
      const r = await _get(_workflowPath('/workflow/get/' + encodeURIComponent(entry.id)));
      const rec = r && (r.workflow || r.item || r);
      if (r && r.ok && rec && rec.graph) full = { ...entry, ...wfNormalizeRemote(rec), nodes: rec.graph.nodes, edges: rec.graph.edges };
    }
    if (!Array.isArray(full.nodes) || !full.nodes.length) { flash('载入失败', '该工作流无图数据'); return; }
    if (full.legacy_recovery && full.legacy_recovery.auto_repairable === false) {
      flash('历史图保留 · 不能自动恢复执行', wfReviewReason(full), 10000); return;
    }
    const bad = validateGraph(full.nodes, full.edges || []);
    if (bad) { flash('载入失败 · 图未通过校验', bad, 6500); return; }
    pushHist();
    const g = cloneGraph(full.nodes, full.edges || []);
    const scope = wfScopeOf(full);
    setNodes(g.nodes); setEdges(g.edges); setWfName(full.name); setWfScope(scope); setAssetNeedsPool(graphNeedsPoolReview(g.nodes)); setSel(null); setHistOpen(false);
    wfSaveLast({ name: full.name, scope, nodes: g.nodes, edges: g.edges });
    const cachedRep = resCache[wfReportKey(full.name, scope)] || resCache[full.name]; // 旧 name-only cache 只作迁移回退
    setResult(cachedRep || null); setShowRes(!!cachedRep); setRunErr('');
    flash(wfIsGlobalTemplate(full) ? '已载入全局模板 · 只读源' : '已载入', full.name + (wfIsGlobalTemplate(full) ? ' · 修改后保存将生成当前空间副本' : (cachedRep ? ' · 含上次报告' : '')));
  };
  const deleteEntry = (entry) => {
    if (WW_READONLY) { flash('归档空间只读', '历史工作流不能删除'); return; }
    if (wfIsGlobalTemplate(entry)) { flash('全局模板只读', '不能从研究空间删除；可载入修改后另存为空间副本'); return; }
    const id = typeof entry === 'string' ? entry : entry.id;
    setSaved(list => { const next = list.filter(x => x.id !== id); wfSaveList(next); return next; }); // 本地删 (降级保底)
    _post('/workflow/delete', _withWorkspace({ id })).catch(() => flash('本地已删 · 服务端不可达', '若该条曾同步到服务端,刷新后会回来;后端恢复后再删一次', 5000)); // ok:false(如纯本地条目服务端本就没有)不提示
  };
  const exportJSON = () => { const blob = new Blob([JSON.stringify({ nodes, edges }, null, 2)], { type: 'application/json' }); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'guanlan-workflow.json'; a.click(); flash('已导出', 'guanlan-workflow.json'); };
  const importJSON = (e) => { const f = e.target.files && e.target.files[0]; if (!f) return; if (WW_READONLY) { flash('归档空间只读', '历史工作流不能导入覆盖'); e.target.value = ''; return; } const rd = new FileReader(); rd.onload = () => { try { const g = JSON.parse(rd.result); const bad = validateGraph(g && g.nodes, g && g.edges); if (bad) throw new Error(bad); pushHist(); setNodes(g.nodes); setEdges(g.edges); setAssetNeedsPool(graphNeedsPoolReview(g.nodes)); setSel(null); flash('已导入', f.name); } catch (err) { flash('导入失败 · 图未通过校验', String(err.message), 6500); } }; rd.readAsText(f); e.target.value = ''; };

  // 屏幕 → 画布坐标
  const toCanvas = useCallback((cx, cy) => {
    const r = wrapRef.current.getBoundingClientRect();
    return { x: (cx - r.left - view.x) / view.z, y: (cy - r.top - view.y) / view.z };
  }, [view]);

  // 拖节点
  const dragNode = (id, e) => {
    e.stopPropagation();
    const start = toCanvas(e.clientX, e.clientY);
    const node = nodes.find(n => n.id === id);
    const o = { x: node.x, y: node.y };
    setSel(id); pushHist();
    const move = (ev) => {
      const p = toCanvas(ev.clientX, ev.clientY);
      const snap = (val) => Math.round(val / 11) * 11; // 网格吸附
      setNodes(ns => ns.map(n => n.id === id ? { ...n, x: snap(o.x + (p.x - start.x)), y: snap(o.y + (p.y - start.y)) } : n));
    };
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };

  // 平移画布
  const panStart = (e) => {
    if (e.button !== 0) return;
    setSel(null);
    const o = { x: view.x, y: view.y, cx: e.clientX, cy: e.clientY };
    const move = (ev) => setView(v => ({ ...v, x: o.x + (ev.clientX - o.cx), y: o.y + (o.cy ? ev.clientY - o.cy : 0) }));
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };

  // 连线: 从 output 拖出
  const startWire = (nodeId, portId, e) => {
    e.stopPropagation();
    const p = toCanvas(e.clientX, e.clientY);
    setDraft({ fromNode: nodeId, fromPort: portId, x: p.x, y: p.y });
    const move = (ev) => { const q = toCanvas(ev.clientX, ev.clientY); setDraft(d => d ? { ...d, x: q.x, y: q.y } : null); };
    const up = (ev) => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
      const el = document.elementFromPoint(ev.clientX, ev.clientY);
      const tgt = el && el.closest && el.closest('[data-inport]');
      if (tgt) {
        const [tn, tp] = tgt.getAttribute('data-inport').split('::');
        const sNode = nodes.find(n => n.id === nodeId), dNode = nodes.find(n => n.id === tn);
        const sP = sNode && SPECS[sNode.type].outputs.find(o => o.id === portId);
        const dP = dNode && SPECS[dNode.type].inputs.find(i => i.id === tp);
        if (tn === nodeId) { /* 不能自连 */ }
        else if ((tp === 'src' && !(sNode && sNode.type === 'source' && portId === 'data'))
            || (sNode && sNode.type === 'source' && portId === 'data' && tp !== 'src')) {
          setToast({ title: '上下文连线不合法', build: 'src 口只能连接「数据源」的 data 口，用于下传股票池、日期与样本外参数' }); setTimeout(() => setToast(null), 3600);
        } else if (sP && dP && sP.dt && dP.dt && !dtCompatible(sP.dt, dP.dt, dNode && dNode.type, sNode && sNode.type, tp)) {
          setToast({ title: '连线类型不匹配', build: `输出 ${sP.dt} ✗ 需要 ${dP.dt}` }); setTimeout(() => setToast(null), 2800);
        } else {
          pushHist();
          // feature.feat 口允许多边 (多特征聚合, 只剔完全重复的同一条); 其余口保持单入边 (新连替换旧连)。
          const multi = dNode && dNode.type === 'feature' && tp === 'feat';
          setEdges(es => [...es.filter(x => multi
            ? !(x.from[0] === nodeId && x.from[1] === portId && x.to[0] === tn && x.to[1] === tp)
            : !(x.to[0] === tn && x.to[1] === tp)), { from: [nodeId, portId], to: [tn, tp] }]);
        }
      }
      setDraft(null);
    };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };

  const buildFromCard = (card) => {
    const g = card.snapshot ? cloneGraph(card.snapshot.nodes, card.snapshot.edges)
      : CARD_GRAPH[card.id] ? CARD_GRAPH[card.id]() : seedGraph();
    const bad = validateGraph(g.nodes, g.edges || []);
    if (bad) { flash('经验模板未通过校验', bad, 6500); return; }
    pushHist();
    setNodes(g.nodes); setEdges(g.edges); setAssetNeedsPool(false); setSel(null); setShowRes(false); setRunState({});
    setToast({ title: '已据「' + card.title + '」搭建示例模板', build: '点 ▶ 运行出真结果 · ' + (card.build || g.nodes.map(n => SPECS[n.type].title).join(' → ')) });
    setTimeout(() => setToast(null), 6500);
  };
  const factorSpecOf = (r) => {
    if (r && r.factor_spec && r.factor_spec.schema === 'guanlan.factor_spec.v1') return r.factor_spec;
    const orth = r && (r.orthogonalization || r._orthogonalization || (r.fe && r.fe.orthogonalization));
    const artifact = orth && orth.artifact;
    const transform = (artifact && artifact.transform) || (orth && orth.version ? orth : null);
    let base = (artifact && artifact.base_features && artifact.base_features[0])
      || (r && r.expr) || (r && r.fe && r.fe.features && r.fe.features.length === 1 && r.fe.features[0]);
    if (!base) {
      const formulas = nodes.filter(n => n.type === 'formula').map(n => String((n.params || {}).expr || '').trim()).filter(Boolean);
      if (formulas.length === 1) base = formulas[0];
    }
    return (transform && base) ? { schema: 'guanlan.factor_spec.v1', kind: 'orthogonalized', base_features: [String(base)], transform } : null;
  };
  const auditAllowsPersistence = (r) => {
    const isAudit = !!(r && (r.__dt === 'overfit_audit' || r.method === 'overfit_audit'));
    return !isAudit || (String(r.status || '').toUpperCase() === 'PASS' && r.evidence_verified === true);
  };

  // 普通结果保存 expr；结构化结果保存完整 factor_spec，服务端注册可执行名称并在重放时保留变换顺序。
  const saveFactor = () => {
    if (WW_READONLY) { flash('归档空间只读', '历史结果不能发布到全局因子库'); return; }
    const r = result || {};
    if (!auditAllowsPersistence(r)) { flash('审计硬门未通过', '只有服务端 TrialLedger 验证的 PASS 结果可以存入因子库', 6500); return; }
    const factorSpec = factorSpecOf(r);
    if (r.factor_artifact) { flash('这是拟合信号，不能保存成原始因子公式', '完整信号随运行报告保留；树模型请在模型节点使用「存入模型库」保存重训配方。', 8000); return; }
    const expr = factorSpec ? factorSpec.base_features[0]
      : r.expr;
    if (!expr) { setToast({ title: '存入因子库', build: '该结果无可保存的因子表达式' }); setTimeout(() => setToast(null), 6500); return; }
    const name = window.prompt('为因子命名', r._label || '');
    if (name == null) return;                               // 取消
    const nm = (name || '').trim() || (r._label || '复合因子');
    _post('/factorlib/save', { name: nm, expr, factor_spec: factorSpec, family: 'library_mined',
      meta: { _label: r._label, universe: r._universe, ic: r.ic, portfolio: r.portfolio } })
      .then(res => {
        const saved = !!(res && res.ok);
        const replayable = saved && !!res.registered;
        const build = !saved ? ((res && res.reason) || '保存失败')
          : (factorSpec
              ? (replayable ? '结构化因子已保存并注册 · 可按名称完整重放' : '结构化因子已落盘，但运行时注册失败；重启服务并复验后才可称为可重放')
              : (replayable ? '已存入因子库并注册' : '已落盘，但运行时注册失败'));
        setToast({ title: '存入因子库 · ' + nm, build }); setTimeout(() => setToast(null), 6500);
      })
      .catch(e => { setToast({ title: '存入因子库', build: '保存失败 · ' + String(e.message || e) }); setTimeout(() => setToast(null), 6500); });
  };

  // P2-B:验证结果一键沉淀为经验卡(此前唯一通路是去卡片页手工重填 expr 再验一遍)。
  // 真指标快照进 insight(口径同 exportReport kpi);status=draft 留人审,卡片页批准后
  // 即可被对话/研报 wisdom_search 引用(P1⑩ 合流)。GL 用后端同一 id(EV-NNN)。
  const saveCard = () => {
    if (WW_READONLY) { flash('归档空间只读', '历史结果不能沉淀新经验卡'); return; }
    const r = result || {};
    if (!auditAllowsPersistence(r)) { flash('审计硬门未通过', '只有服务端 TrialLedger 验证的 PASS 结果可以沉淀经验卡', 6500); return; }
    const factorSpec = factorSpecOf(r);
    const ic = r.ic || {}, pf = r.portfolio || {}, sm = r.summary || {};
    if (r.factor_artifact) { flash('拟合信号请先导出报告', '不能用原始特征之和冒充本次模型结果；模型配方可从模型节点入库。', 8000); return; }
    const expr = (factorSpec && factorSpec.base_features[0]) || r.expr;
    if (!expr) { flash('沉淀为经验卡', '该结果无可沉淀的因子表达式', 6500); return; }
    const name = window.prompt('为经验卡命名', wfName || r._label || '');
    if (name == null) return;
    const nm = (name || '').trim() || (wfName || '工作流经验');
    const icv = (ic.rank_ic_mean != null ? ic.rank_ic_mean : sm.mean_pearson);
    const bits = [];
    if (icv != null) bits.push('RankIC=' + (+icv).toFixed(4));
    if (ic.icir != null) bits.push('ICIR=' + (+ic.icir).toFixed(2));
    if (pf.sharpe != null) bits.push('Sharpe=' + (+pf.sharpe).toFixed(2));
    if (pf.max_drawdown != null) bits.push('回撤=' + (pf.max_drawdown * 100).toFixed(1) + '%');
    const baseInsight = '工作流验证:' + (bits.join(' · ') || '指标见报告') + '(池 ' + (r._universe || '—') + ')。';
    const verdict = (icv != null && Math.abs(+icv) >= 0.02) ? '通过' : '存疑';   // 粗判,人审定夺
    const conf = (icv != null ? Math.max(40, Math.min(90, Math.round(Math.abs(+icv) * 1500))) : 50);
    let stagedStructuredName = '';
    (async () => {
      let replayExpr = expr, refs = [], structuredName = null;
      if (factorSpec) {
        structuredName = nm + '·正交·' + Date.now().toString(36).slice(-5);
        const saved = await _post('/factorlib/save', { name: structuredName, expr,
          factor_spec: factorSpec, family: 'library_mined', status: 'draft', source: 'workflow_card',
          description: '经验卡「' + nm + '」的可重放结构化因子',
          meta: { _label: r._label, universe: r._universe, ic: r.ic, portfolio: r.portfolio } });
        if (saved && saved.ok) stagedStructuredName = saved.name || structuredName;
        if (!saved || !saved.ok || !saved.registered) throw new Error((saved && saved.reason) || '结构化因子注册失败');
        replayExpr = saved.name || structuredName;
        refs = [replayExpr];
      }
      const insight = baseInsight + (factorSpec
        ? ('可重放结构化因子:' + replayExpr + '；原因子:' + expr.slice(0, 90))
        : ('表达式:' + expr.slice(0, 90)));
      const res = await _post('/cards', { title: nm, cat: '量化', tags: ['工作流', factorSpec ? '结构化因子' : '', r._universe || ''].filter(Boolean).slice(0, 3),
        verdict, conf, ic: (icv != null ? (+icv).toFixed(4) : ''), expr: replayExpr, insight, refs,
        src: '工作流 · ' + (wfName || '验证'), status: 'draft' });
      if (!res || !res.id) throw new Error('经验卡服务未返回卡片 id');
      return { res, insight, replayExpr, refs };
    })()
      .then(({ res, insight, replayExpr, refs }) => {
        const cid = res && res.id;
        if (window.GL && cid) {
          GL.put({ type: 'card', id: cid, title: nm, cat: '量化', tags: ['工作流'], verdict, conf,
            ic: (icv != null ? (+icv).toFixed(4) : ''), insight, expr: replayExpr, status: 'draft', real: true, refs });
        }
        flash('已沉淀为经验卡 · ' + nm, cid + (factorSpec ? ' · 已绑定可重放结构化因子' : '') + ' · 草稿;卡片页批准后可被对话/研报引用', 6500);
      })
      .catch(e => flash('沉淀为经验卡', '失败 · ' + String(e.message || e) + (stagedStructuredName ? ' · 结构化因子草稿已落盘:' + stagedStructuredName : ''), 6500));
  };
  const generate = (q) => {
    if (WW_READONLY) { flash('归档空间只读', '历史工作流不能生成或改写'); return; }
    if (!q || !q.trim()) return;
    const g = generateFromText(q);
    const bad = validateGraph(g.nodes, g.edges || []);
    if (bad) { flash('本地生成未通过校验', bad, 6500); return; }
    pushHist();
    setNodes(g.nodes); setEdges(g.edges); setAssetNeedsPool(false); setSel(null); setShowRes(false); setRunState({});
    setToast({ title: '已据「' + q.trim().slice(0, 22) + '」搭建工作流', build: g.nodes.map(n => SPECS[n.type].title).join(' → ') });
    setTimeout(() => setToast(null), 6500);
  };
  // AI 生成工作流 (真 LLM 联网): _post 后端 → 诚实失败判 resp.ok → SPECS 再校验 → 仅渲染待审阅, 绝不自动 run()。端点不可达 → 降级 generateFromText(关键词)。
  const aiGenerate = async (text) => {
    if (WW_READONLY) { flash('归档空间只读', '历史工作流不能生成或改写'); return; }
    if (!text || !text.trim() || aiBusy) return;
    setAiBusy(true);
    try {
      const resp = await _post('/workflow/generate', { goal: text.trim() });
      if (!resp || !resp.ok) { flash('AI 生成未通过', (resp && resp.reason) || '请换种说法重试'); return; }
      const G = (resp.graph || resp);
      if (!G || !Array.isArray(G.nodes)) { flash('AI 生成异常', '后端未返回合法图'); return; }
      const bad = validateGraph(G.nodes, G.edges || []);
      if (bad) { flash('AI 生成未通过校验', bad); return; }
      const g = cloneGraph(G.nodes, G.edges || []);          // 重发 id, 与当前画布零撞号
      pushHist();                                            // 入撤销栈, 用户可一键撤回 AI 生成
      setNodes(g.nodes); setEdges(g.edges); setAssetNeedsPool(false); setSel(null); setShowRes(false); setRunState({});  // 仅渲染, 不 run()
      flash('AI 已生成 · 请审阅后点 ▶ 运行', g.nodes.map(n => SPECS[n.type].title).join(' → '), 6500);
    } catch (err) {
      // LLM 端点不可达 → 降级到本地关键词解析 (现有 generateFromText), 永不白屏
      try {
        const g = generateFromText(text);
        const bad = g && validateGraph(g.nodes, g.edges || []);
        if (g && Array.isArray(g.nodes) && g.nodes.length && !bad) {
          pushHist();
          setNodes(g.nodes); setEdges(g.edges); setAssetNeedsPool(false); setSel(null); setShowRes(false); setRunState({});
          flash('AI 不可达 · 本地关键词规则生成(非 LLM · 仅供参考)', g.nodes.map(n => SPECS[n.type].title).join(' → '), 6500);
          return;
        }
        if (bad) throw new Error(bad);
      } catch (e2) { /* 降级也失败 → 落到下面统一提示 */ }
      flash('AI 生成失败', String(err && err.message || err));
    } finally { setAiBusy(false); }
  };
  // W8b AI 闭环(rd-agent 式):propose(生成)→ run(真跑)→ 读回真指标 → critique(自评+改进图)→ 复跑。
  const aiLoop = async (text) => {
    if (WW_READONLY) { flash('归档空间只读', '历史工作流不能执行 AI 闭环'); return; }
    const goal = (text || '').trim();
    if (!goal || aiBusy) return;
    setAiBusy(true);
    setLoop({ running: true, goal, rounds: [], step: '① AI 生成初始工作流…' });
    const metricsOf = r => ({
      rank_ic: (r && r.headline_ic && r.headline_ic.rank_ic != null) ? r.headline_ic.rank_ic : (r && r.ic && r.ic.rank_ic_mean),
      sharpe: r && r.portfolio && r.portfolio.sharpe,
      ann_return: r && r.portfolio && r.portfolio.ann_return,
      oos_verdict: r && r.oos && r.oos.verdict,
      n_dates: r && r.n_dates, factor: r && r._label,
    });
    const factorOf = g => { const f = (g.nodes || []).find(n => n.type === 'formula'); return (f && f.params && f.params.expr) || (g.nodes || []).map(n => SPECS[n.type] ? SPECS[n.type].title : n.type).join(' → '); };
    const runHeadless = g => new Promise(res => {
      const bad = validateGraph(g && g.nodes, (g && g.edges) || []);
      if (bad) { res({ result: null, error: bad }); return; }
      let last = null, errMsg = null;
      runGraph(g.nodes, g.edges, { onState() {}, onResult(r) { last = r; }, onError(m) { errMsg = m; } }).then(() => res({ result: last, error: last ? null : errMsg })).catch(e => res({ result: last, error: (e && e.message) || String(e) }));
    });
    try {
      const gen = await _post('/workflow/generate', { goal });
      if (!gen || !gen.ok || !gen.graph || !Array.isArray(gen.graph.nodes)) throw new Error('AI 初始提案失败：' + ((gen && gen.reason) || '未返回合法图') + '；未使用模板，未执行');
      const proposalError = validateGraph(gen.graph.nodes, gen.graph.edges || []);
      if (proposalError) throw new Error('AI 初始图未通过校验：' + proposalError + '；未使用模板，未执行');
      let graph = cloneGraph(gen.graph.nodes, gen.graph.edges || []);
      const rounds = [];
      let diag = '初始生成(AI propose)';
      for (let k = 0; k < 2; k++) {
        setLoop(L => ({ ...(L || {}), running: true, rounds: rounds.slice(), step: (k === 0 ? '② 运行初始工作流(真引擎)…' : '④ 运行改进工作流(真引擎)…') }));
        const rr = await runHeadless(graph);
        const res = rr.result;
        rounds.push({ k, diag, factor: factorOf(graph), metrics: metricsOf(res || {}), graph, failed: !!rr.error && !res, error: rr.error || '' });
        setLoop(L => ({ ...(L || {}), running: true, rounds: rounds.slice(), step: (k < 1 ? '③ AI 读回指标·自评改进…' : '闭环完成') }));
        if (k >= 1) break;
        let crit = null;
        try { crit = await _post('/workflow/critique', { goal, metrics: rounds[k].metrics, graph }); } catch (e) {}
        if (!crit || !crit.ok || !crit.graph || !Array.isArray(crit.graph.nodes)) { diag = (crit && crit.diagnosis) || 'AI 自评不可用,停止迭代'; break; }
        const critBad = validateGraph(crit.graph.nodes, crit.graph.edges || []);
        if (critBad) { diag = 'AI 改进图未通过校验：' + critBad; break; }
        diag = (crit.source === 'rule' ? '(规则兜底·非 LLM) ' : '') + (crit.diagnosis || '已据指标改进'); graph = cloneGraph(crit.graph.nodes, crit.graph.edges || []);
      }
      const nFail = rounds.filter(r => r.failed).length;
      setLoop(L => ({ ...(L || {}), running: false, rounds: rounds.slice(), step: '闭环完成 · 共 ' + rounds.length + ' 轮' + (nFail ? ' · ' + nFail + ' 轮运行失败' : '') }));
    } catch (err) {
      setLoop(L => (L ? { ...L, running: false, step: '失败: ' + (err && err.message || err) } : null));
    } finally { setAiBusy(false); }
  };
  const addNode = (type) => {
    const r = wrapRef.current.getBoundingClientRect();
    const c = toCanvas(r.left + r.width / 2, r.top + 140);
    const spec = SPECS[type];
    const params = {}; spec.params.forEach(p => params[p.id] = p.value);
    pushHist();
    setNodes(ns => [...ns, { id: nid(), type, x: c.x - W / 2, y: c.y, params }]);
  };
  const delNode = (id) => { pushHist(); setNodes(ns => ns.filter(n => n.id !== id)); setEdges(es => es.filter(e => e.from[0] !== id && e.to[0] !== id)); setSel(null); };
  const setParam = (id, pid, val) => { setNodes(ns => ns.map(n => n.id === id ? { ...n, params: { ...n.params, [pid]: val } } : n)); };

  const zoom = (d) => setView(v => ({ ...v, z: Math.max(0.4, Math.min(1.8, +(v.z + d).toFixed(2))) }));
  const fit = () => setView({ x: 0, y: 0, z: 1 });
  const onWheel = (e) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoom(e.deltaY > 0 ? -0.1 : 0.1); } };

  const applyDurableRun = (rec, ref, surfaceOnDone) => {
    const nodeStates = {};
    const rawNodeStates = (rec && (rec.node_state || rec.node_states)) || {};
    Object.keys(rawNodeStates).forEach(id => {
      const row = rawNodeStates[id];
      nodeStates[id] = (row && typeof row === 'object') ? row.state : row;
    });
    setRunState(nodeStates);
    const status = String((rec && rec.status) || 'failed');
    const active = status === 'queued' || status === 'running';
    const nextRef = { ...(ref || {}), id: rec.id || (ref && ref.id), status, updatedAt: rec.updated_at || Date.now() };
    if (active) {
      wfSaveRunRef(nextRef);
      runBusyRef.current = true;
      setDurableWaiting(true);
      // 页面重挂载时 resCache 可能先恢复上一份报告；活动任务必须清掉它，
      // 否则抽屉会把旧报告伪装成本次运行结果。
      setResult(null); setRunning(true); setShowRes(true); setRunErr('');
      return true;
    }

    durableWaitIdRef.current = '';
    setDurableWaiting(false);
    runBusyRef.current = false;
    setRunning(false);
    const outcome = rec && rec.result;
    let rep = outcome && outcome.terminal && outcome.terminal.payload;
    const durableNodeErrors = Array.isArray(outcome && outcome.node_errors) ? outcome.node_errors : [];
    const completedWithErrors = status === 'completed_with_errors' || (outcome && outcome.completed_with_errors === true) || durableNodeErrors.length > 0;
    const allTerminals = Array.isArray(outcome && outcome.all_terminals) ? outcome.all_terminals : [];
    const mainNodeId = outcome && outcome.terminal && outcome.terminal.node_id;
    const secondaryTerminals = allTerminals.filter(t => t && t.node_id !== mainNodeId && t.payload).map(t => ({ kind: t.kind, node_id: t.node_id, run_id: t.payload.screen_run_id || t.payload.run_id || null, as_of: t.payload.as_of || null }));
    const secondaryScreens = allTerminals.filter(t => t && t.kind === 'data_screen' && t.node_id !== mainNodeId && t.payload).map(t => ({ node_id: t.node_id, run_id: t.payload.screen_run_id || t.payload.run_id, as_of: t.payload.as_of, n_matched: t.payload.n_matched, returned: (t.payload.candidates || []).length }));
    const auditGate = outcome && outcome.audit;
    if (auditGate && auditGate.declared && (!rep || rep.__dt !== 'overfit_audit')) {
      const sourceRep = (rep && typeof rep === 'object') ? rep : {};
      const auditReasons = (auditGate.nodes || []).map(n => n && (n.reason || (n.node_id + ': ' + n.status))).filter(Boolean);
      rep = {
        __dt: 'overfit_audit', method: 'overfit_audit', ok: true,
        status: auditGate.status || 'INCOMPLETE', passes_gate: auditGate.passes_gate === true,
        metrics: {}, evidence: {}, thresholds: {}, reasons: auditReasons.length ? auditReasons : ['审计未产出完整证据'],
        audit_aggregate: auditGate, portfolio: sourceRep.portfolio, ic: sourceRep.ic,
        oos: sourceRep.oos, headline_ic: sourceRep.headline_ic,
        orthogonalization: sourceRep.orthogonalization || null, factor_spec: sourceRep.factor_spec || null,
        _label: sourceRep._label || '回测过拟合审计', _universe: sourceRep._universe,
      };
    }
    if (rep && typeof rep === 'object') {
      const extraWarnings = [];
      ((outcome && outcome.warnings) || []).forEach(w => extraWarnings.push(String(w)));
      durableNodeErrors.forEach(e => extraWarnings.push('节点 ' + (e.nid || '') + '(' + (e.type || '') + '):' + (e.error || '执行失败')));
      if (completedWithErrors) extraWarnings.unshift('运行完成 · 含节点错误(completed_with_errors)：' + durableNodeErrors.length + ' 个节点失败，主终端结果仍已保留。');
      if (secondaryTerminals.length) extraWarnings.push('并行终端结果已独立保留（当前抽屉仅展示主终端）: ' + secondaryTerminals.map(x => (x.kind || '未知') + '@' + (x.node_id || '未标注')).join(' · '));
      if (secondaryScreens.length) extraWarnings.push('并行数据筛选结果已独立保留（当前抽屉展示主终端）: ' + secondaryScreens.map(x => (x.run_id || x.node_id) + (x.as_of ? '@' + x.as_of : '')).join(' · '));
      if (secondaryTerminals.length) rep = { ...rep, _parallel_terminal_refs: secondaryTerminals };
      if (secondaryScreens.length) rep = { ...rep, _data_screen_runs: secondaryScreens };
      if (extraWarnings.length) rep = { ...rep, _warnings: [...extraWarnings, ...(rep._warnings || [])] };
      rep = { ...rep, _run_status: completedWithErrors ? 'completed_with_errors' : status,
        _completed_with_errors: completedWithErrors, _node_errors: durableNodeErrors };
      const name = nextRef.name || wfName;
      const scope = nextRef.scope || wfScope;
      setResult(rep); setRunErr('');
      setResCache(c => { const n = { ...c, [wfReportKey(name, scope)]: rep }; wfSaveReports(n); return n; });
      if (nextRef.nodes && nextRef.nodes.length) wfSaveLast({ name, scope, nodes: nextRef.nodes, edges: nextRef.edges || [] });
      if (surfaceOnDone) setShowRes(true);
    } else {
      setResult(null);
      setRunErr(String((rec && rec.reason) || (outcome && outcome.reason) || '运行未产出可用结果'));
      if (surfaceOnDone) setShowRes(true);
    }
    wfSaveRunRef({ ...nextRef, status: completedWithErrors ? 'completed_with_errors' : status, acknowledged: true });
    return false;
  };

  const pollDurableRun = (runId, ref, surfaceOnDone, misses) => {
    // 放弃重连或启动了新 run_id 后，旧轮询不得清掉新 timer、
    // 重新上锁或把旧结果投影回页面。
    if (durableWaitIdRef.current !== runId) return;
    if (runPollRef.current) clearTimeout(runPollRef.current);
    const missN = misses || 0;
    const tick = async () => {
      const rec = await _get(_scopedPath('/workflow/run/status/' + encodeURIComponent(runId), false));
      if (!mountedRef.current || durableWaitIdRef.current !== runId) return;
      if (!rec) {
        // A board switch can race the service for a moment. Keep the durable
        // reference and retry instead of declaring that the computation died.
        // 连续失联后降低轮询频率，但不解锁第二次运行：只有服务端
        // 明确 ok:false 或终态才能释放同一个 run_id，避免网络抖动制造重复重任务。
        runBusyRef.current = true; setRunning(true); setShowRes(true);
        if (missN === 20) setRunErr('暂时无法连接运行记录；任务编号已保留，正在低频重连');
        const retryMs = missN < 20 ? 1500 : 5000;
        runPollRef.current = setTimeout(() => pollDurableRun(runId, ref, surfaceOnDone, missN + 1), retryMs);
        return;
      }
      if (rec.ok === false) {
        durableWaitIdRef.current = ''; setDurableWaiting(false);
        runBusyRef.current = false; setRunning(false); setShowRes(true); setRunErr(rec.reason || '运行记录不存在'); wfClearRunRef(); return;
      }
      const active = applyDurableRun(rec, ref, surfaceOnDone);
      if (active) runPollRef.current = setTimeout(() => pollDurableRun(runId, { ...ref, status: rec.status }, surfaceOnDone, 0), 900);
    };
    tick();
  };

  const stopWaitingForDurableRun = () => {
    const runId = durableWaitIdRef.current;
    if (!durableWaiting || !runId) { flash('当前没有可放弃的重连', '本页未在跟踪持久任务'); return; }
    const warning = '这只会停止本页轮询并清除本地任务引用；不会取消服务端任务。服务端任务可能仍在运行，重新运行可能重复计算。';
    if (!window.confirm('停止等待 / 放弃重连？\n\n' + warning + '\n\n任务编号：' + runId)) return;
    // 纯本地恢复口：不调服务端取消接口。token 先失效，
    // 即使此时有 status/start fetch 在途，返回后也不会重新上锁。
    durableWaitIdRef.current = '';
    if (runPollRef.current) clearTimeout(runPollRef.current);
    runPollRef.current = null;
    wfClearRunRef();
    runBusyRef.current = false;
    setDurableWaiting(false);
    setRunning(false);
    setRunErr('');
    setShowRes(false);
    flash('已停止本地等待', '未取消服务端任务 · 服务端可能仍在运行 · 重新运行可能重复计算', 10000);
  };

  const loadServerRuns = async () => {
    setServerRunsLoading(true); setServerRunsError('');
    try {
      const response = await _get(_scopedPath('/workflow/run/list?limit=30', false));
      if (!mountedRef.current) return;
      if (!response || response.ok !== true || !Array.isArray(response.items)) throw new Error((response && response.reason) || '无法读取服务端运行历史');
      setServerRuns(response.items.filter(rec => String(rec.workspace_id || '') === String(WW_WORKSPACE_ID || '')));
    } catch (error) { if (mountedRef.current) setServerRunsError(String(error.message || error)); }
    finally { if (mountedRef.current) setServerRunsLoading(false); }
  };
  const openHistoricalRun = async (entry, fromLink) => {
    const runId = String((entry && entry.id) || '');
    if (!/^wr_[0-9a-f]{12,40}$/.test(runId)) { flash('任务载入失败', '无效任务编号'); return; }
    if (!fromLink && (runBusyRef.current || running) && durableWaitIdRef.current !== runId
      && !window.confirm('切换查看这项历史任务？只停止本页对旧任务的跟踪，不取消服务端任务，也不会重新执行。')) return;
    if (!fromLink && !runBusyRef.current && !window.confirm('载入这项任务的原图与结果？当前画布会进入撤销历史，不会重新执行。')) return;
    const request = ++historyRequestRef.current;
    setHistoryOpening(runId);
    try {
      const rec = await _get(_scopedPath('/workflow/run/status/' + encodeURIComponent(runId), false));
      if (!mountedRef.current || historyRequestRef.current !== request) return;
      const error = wfRunRecordError(rec, WW_WORKSPACE_ID);
      if (error) throw new Error(error);
      if (runPollRef.current) clearTimeout(runPollRef.current);
      durableWaitIdRef.current = '';
      pushHist();
      // 恢复服务端原节点 id，状态和终端引用才能准确对应；绝不 cloneGraph 重编号。
      const snap = wfGraphSnapshot(rec.graph.nodes, rec.graph.edges);
      wfPinRunLocation(rec.id);
      const ref = { id: rec.id, name: rec.name || '历史工作流', scope: WW_WORKSPACE_ID ? 'workspace' : 'global',
        nodes: snap.nodes, edges: snap.edges, status: rec.status, pageQuery: location.search };
      setNodes(snap.nodes); setEdges(snap.edges); setWfName(ref.name); setWfScope(ref.scope);
      setAssetNeedsPool(graphNeedsPoolReview(snap.nodes)); setSel(null); setHistOpen(false);
      wfSaveLast({ name: ref.name, scope: ref.scope, nodes: snap.nodes, edges: snap.edges });
      durableWaitIdRef.current = rec.id;
      const active = applyDurableRun(rec, ref, true);
      if (active) pollDurableRun(rec.id, ref, true, 0);
      flash('已载入 · ' + wfRunStatusLabel(rec.status, rec), active ? '仅继续查看服务端运行，没有重跑' : '已恢复原图与历史结果；中断任务不支持断点续算，没有重跑', 6500);
    } catch (error) { if (mountedRef.current && historyRequestRef.current === request) flash('任务载入失败', String(error.message || error), 6500); }
    finally { if (mountedRef.current && historyRequestRef.current === request) setHistoryOpening(''); }
  };
  useEffect(() => {
    loadServerRuns();
    if (!histOpen) return;
    const timer = setInterval(loadServerRuns, 5000);
    return () => clearInterval(timer);
  }, [histOpen]);

  const run = async () => {
    if (WW_READONLY) { flash('归档空间只读', '可查看历史图，但不能运行或生成新结果'); return; }
    const bad = validateGraph(nodes, edges);
    if (bad) { flash('运行前校验未通过', bad, 6500); return; }
    if (graphNeedsPoolReview(nodes)) {
      flash('请先选择真实股票池', '该因子的成分/自定义池尚未结构化，不能用默认池冒充'); return;
    }
    if (runBusyRef.current || running) { flash('已有工作流正在运行', '请等待当前任务完成；切换板块后回来会按任务编号自动重连'); return; }
    historyRequestRef.current += 1; setHistoryOpening(''); // 新执行意图使在途历史载入失效
    runBusyRef.current = true;
    setRunning(true); setShowRes(true); setRunState({}); setResult(null); setRunErr('');
    const snap = wfGraphSnapshot(nodes, edges);
    wfSaveLast({ name: wfName, scope: wfScope, nodes: snap.nodes, edges: snap.edges });

    // Backtests whose node vocabulary is mirrored by the Python executor run
    // as durable server jobs. Special browser-only nodes keep the original
    // path below, preserving existing behavior rather than partially running.
    if (wfCanRunDurably(snap.nodes)) {
      const ref = { id: wfNewRunId(), name: wfName, scope: wfScope, nodes: snap.nodes, edges: snap.edges,
        pageQuery: location.search, status: 'starting', startedAt: Date.now(), acknowledged: false };
      wfPinRunLocation(ref.id); ref.pageQuery = location.search;
      durableWaitIdRef.current = ref.id;
      setDurableWaiting(true);
      wfSaveRunRef(ref);
      try {
        const started = await _post('/workflow/run/start', _withWorkspace({ run_id: ref.id, name: wfName, graph: snap }));
        if (!mountedRef.current || durableWaitIdRef.current !== ref.id) return;
        if (!started || !started.ok) {
          durableWaitIdRef.current = ''; setDurableWaiting(false); runBusyRef.current = false; wfClearRunRef();
          setRunning(false); setRunErr((started && (started.reason || started.detail)) || '未能启动持久回测');
          return;
        }
        const nextRef = { ...ref, status: started.status || 'queued' };
        wfSaveRunRef(nextRef);
        pollDurableRun(ref.id, nextRef, true, 0);
      } catch (err) {
        if (mountedRef.current && durableWaitIdRef.current === ref.id) {
          if (err && err.httpStatus) {
            durableWaitIdRef.current = ''; setDurableWaiting(false); runBusyRef.current = false; wfClearRunRef(); setRunning(false); setRunErr(String(err.message || err));
          } else {
            // 请求响应中断不等于服务端没接单；保留同一个 run_id 并转状态轮询，
            // 防止用户重试制造第二个重任务。
            setRunning(true); setRunErr('');
            flash('启动响应中断 · 正在重连', '任务编号 ' + ref.id + ' 已保留，不会重复启动', 5000);
            pollDurableRun(ref.id, ref, true, 0);
          }
        }
      }
      return;
    }

    durableWaitIdRef.current = '';
    setDurableWaiting(false);
    wfClearRunRef();
    // 通用拓扑 DAG 执行器: 按 edges 拓扑序逐节点 await NODE_EXEC, 沿边喂数据, 经 hooks 写回 runState / 结果。
    try {
      await runGraph(nodes, edges, {
        onState: (id, st) => setRunState(s => ({ ...s, [id]: st })),
        onResult: (rep) => { setResult(rep); setResCache(c => { const n = { ...c, [wfReportKey(wfName, wfScope)]: rep }; wfSaveReports(n); return n; }); wfSaveLast({ name: wfName, scope: wfScope, nodes, edges }); },
        onError: (msg) => setRunErr(msg),
      });
    } finally {
      runBusyRef.current = false; setRunning(false);
    }
  };

  const nodeById = (id) => nodes.find(n => n.id === id);

  // 带入经验验证区 / 研究图谱传来的经验·因子 → 预填工作流
  const prefilledRef = useRef(!!(WW_QUERY.get('run') || WW_QUERY.get('import')));   // 显式整图/运行链接先占位，不被旧任务恢复覆盖
  useEffect(() => {
    const runId = WW_QUERY.get('run');
    if (runId) openHistoricalRun({ id: runId }, true);
  }, []);
  useEffect(() => {
    const token = WW_QUERY.get('import');
    if (!token) return;
    let consumed = false;
    const receive = (event) => {
      if (consumed) return;
      const payload = wfAcceptGraphMessage(event, token, WW_WORKSPACE_ID, WW_SESSION_ID);
      if (!payload) return;
      consumed = true;
      const graph = payload.graph;
      const bad = validateGraph(graph && graph.nodes, graph && graph.edges);
      if (bad) { flash('工作流图未通过校验', bad, 6500); return; }
      pushHist();
      const snap = wfGraphSnapshot(graph.nodes, graph.edges);
      setNodes(snap.nodes); setEdges(snap.edges); setWfName(String(payload.name || 'Agent 工作流'));
      setWfScope(WW_WORKSPACE_ID ? 'workspace' : 'global'); setAssetNeedsPool(graphNeedsPoolReview(snap.nodes));
      setSel(null); setResult(null); setShowRes(false); setRunState({});
      flash('已打开 Agent 流程图', '尚未执行，也未发布到全局；审阅后再决定是否运行或保存', 6500);
    };
    window.addEventListener('message', receive);
    if (window.parent !== window) window.parent.postMessage({ type: 'guanlan.workflow.ready', token }, location.origin);
    return () => window.removeEventListener('message', receive);
  }, []);
  useEffect(() => {
    if (WW_QUERY.get('run') || WW_QUERY.get('import') || WW_QUERY.get('load')) return;
    if (new URLSearchParams(location.search).get('asset')) return;
    const h = window.GL && GL.take('workflow', WW_WS);   // WW_WS=按帷幄会话取信箱,防跨会话串扰
    if (h && (h.name || h.factor || h.expr)) {
      prefilledRef.current = true;
      // 有精确表达式 → 确定性直建图(source→formula→feature→analysis),expr 逐字进 formula 节点;
      // 旧版把 expr 拼进 generateFromText 关键词正则,验证的是模板因子不是原因子(互通审计 P0④)。
      const px = String(h.expr || h.factor || '').trim();
      const g = px
        ? tplG({ universe: 'csi300', oos_frac: '0.3' }, [
            { type: 'formula', params: { expr: px } },
            { type: 'feature', params: { tag: 'IC' } },
            { type: 'spearman', params: {} },
            { type: 'analysis', params: {} },
          ])
        : generateFromText((h.name || '') + ' 回测');   // 只有名字没表达式才退回关键词建图
      const bad = validateGraph(g.nodes, g.edges || []);
      if (bad) { setToast({ title: '交棒图未通过校验', build: bad }); setTimeout(() => setToast(null), 6500); return; }
      setNodes(g.nodes); setEdges(g.edges); setSel(null); setShowRes(false); setRunState({});
      setWfScope(WW_WORKSPACE_ID ? 'workspace' : 'global'); setAssetNeedsPool(false);
      if (h.name) setWfName(h.name + ' · 验证回测');
      setToast({ title: '已带入「' + (h.name || '因子') + '」',
        build: (px ? '原表达式直建因子链:' + px.slice(0, 46) + (px.length > 46 ? '…' : '') : '因子链已预填 · ' + g.nodes.map(n => SPECS[n.type].title).join(' → '))
          + (h.conds && h.conds.length ? ' · 附条件 ' + h.conds.length + ' 条(见经验卡)' : '') });
      setTimeout(() => setToast(null), 6500);
    }
  }, []);

  useEffect(() => {
    if (WW_QUERY.get('run') || WW_QUERY.get('import') || WW_QUERY.get('load')) return;
    if (new URLSearchParams(location.search).get('asset')) return;
    const q = new URLSearchParams(location.search).get('q');
    if (q && q.trim()) {
      prefilledRef.current = true;
      const g = generateFromText(q), bad = validateGraph(g.nodes, g.edges || []);
      if (bad) { setToast({ title: '深链生成未通过校验', build: bad }); setTimeout(() => setToast(null), 6500); return; }
      setNodes(g.nodes); setEdges(g.edges); setAssetNeedsPool(false); setToast({ title: '已据「' + q.trim().slice(0, 24) + '」搭建工作流', build: g.nodes.map(n => SPECS[n.type].title).join(' → ') }); setTimeout(() => setToast(null), 7000);
    }
  }, []);

  // ?asset=<wa_id> 是右侧资产卡与 Agent 新产物共用的持久深链。
  // 表达式只从 scope-checked exact endpoint 获取；打开即审阅，不自动运行。
  useEffect(() => {
    const assetId = new URLSearchParams(location.search).get('asset');
    if (!assetId) return;
    prefilledRef.current = true;
    if (!WW_WORKSPACE_ID || !/^wa_[0-9a-f]{16}$/.test(assetId)) {
      setToast({ title: '空间因子载入失败', build: '缺少有效研究空间或因子资产编号' });
      setTimeout(() => setToast(null), 6000); return;
    }
    _get('/console/workspaces/' + encodeURIComponent(WW_WORKSPACE_ID) + '/assets/' + encodeURIComponent(assetId)).then(r => {
      const a = r && r.asset;
      if (!r || !r.ok || !a || a.kind !== 'factor' || a.workspace_id !== WW_WORKSPACE_ID || !String(a.expr || '').trim()) {
        setToast({ title: '空间因子载入失败', build: '资产不存在、已损坏或不属于当前研究空间' });
        setTimeout(() => setToast(null), 6000); return;
      }
      const g = workspaceFactorGraph(a);
      const bad = validateGraph(g.nodes, g.edges || []);
      if (bad) { setToast({ title: '空间因子图未通过校验', build: bad }); setTimeout(() => setToast(null), 6500); return; }
      const nm = String(a.name || '空间因子') + ' · v' + String(a.version || 1) + ' 验证';
      const needsPool = graphNeedsPoolReview(g.nodes);
      setNodes(g.nodes); setEdges(g.edges); setWfName(nm); setWfScope('workspace'); setAssetNeedsPool(needsPool); setSel(null); setShowRes(false); setRunState({}); setRunErr('');
      wfSaveLast({ name: nm, scope: 'workspace', nodes: g.nodes, edges: g.edges });
      setToast({ title: '已载入空间因子「' + String(a.name || assetId) + '」', build: '原表达式已精确铺入画布 · ' + (needsPool ? '成分/自定义池尚未结构化，请先选择真实股票池 · ' : '请核对数据口径后手动运行 · ') + '不会自动发布或进入落子' });
      setTimeout(() => setToast(null), 7000);
    });
  }, []);

  // P3:?load=<wid> 深链 —— 落子页「研究回路」卡「上画布」跳转落点:从工作流库取全图铺画布。
  // 同步立旗 prefilledRef(否则「恢复上次会话」effect 抢画布,P0④ 坑);绝不自动运行。
  useEffect(() => {
    const wid = new URLSearchParams(location.search).get('load');
    if (WW_QUERY.get('run') || WW_QUERY.get('import')) return;
    if (!wid || new URLSearchParams(location.search).get('asset')) return;
    prefilledRef.current = true;
    _get(_workflowPath('/workflow/get/' + encodeURIComponent(wid))).then(r => {
      const rec = r && (r.workflow || r.item || r);
      if (!r || !r.ok || !rec || !rec.graph || !Array.isArray(rec.graph.nodes) || !rec.graph.nodes.length) {
        setToast({ title: '深链载入失败', build: '工作流 ' + wid + ' 不存在或无图数据' }); setTimeout(() => setToast(null), 6000); return;
      }
      const full = wfNormalizeRemote(rec);
      if (full.legacy_recovery && full.legacy_recovery.auto_repairable === false) {
        setToast({ title: '历史图保留 · 不能自动恢复执行', build: wfReviewReason(full) }); setTimeout(() => setToast(null), 10000); return;
      }
      const bad = validateGraph(full.graph.nodes, full.graph.edges || []);
      if (bad) { setToast({ title: '深链载入失败 · 图未通过校验', build: bad }); setTimeout(() => setToast(null), 6500); return; }
      const g = cloneGraph(full.graph.nodes, full.graph.edges || []);
      const scope = wfScopeOf(full);
      setNodes(g.nodes); setEdges(g.edges); if (full.name) setWfName(full.name); setWfScope(scope); setAssetNeedsPool(graphNeedsPoolReview(g.nodes)); setSel(null); setShowRes(false); setRunState({});
      wfSaveLast({ name: full.name || '深链载入', scope, nodes: g.nodes, edges: g.edges });
      setToast({ title: '已载入「' + (full.name || wid) + '」', build: wfIsGlobalTemplate(full) ? '全局只读模板 · 修改后保存将生成当前空间副本 · 不自动运行' : '研究空间工作流深链 · 待审阅,不自动运行' }); setTimeout(() => setToast(null), 6000);
    });
  }, []);

  // 进页 hydrate: 用服务端 /workflow/list 覆盖/并入 localStorage 列表 (服务端为准, 本地离线存的按 name 去重并到尾部); 不可达 → 保持 localStorage 初值。
  useEffect(() => {
    _get(_workflowPath('/workflow/list')).then(r => {
      if (!r || !r.ok || !Array.isArray(r.items)) return;
      const remote = r.items.map(wfNormalizeRemote);
      setSaved(prev => {
        const next = [...remote, ...prev.filter(p => !remote.some(it => it.id === p.id || (it.name === p.name && !wfIsGlobalTemplate(it))))];
        wfSaveList(next); return next;
      });
    });
  }, []);

  // 进页恢复上次会话(不弹抽屉):把上次运行/载入的工作流(名+图)铺回画布,并把它那次结果静默置入 result
  // → 顶栏「↻ 重看结果」亮起,点了才看。刷新/关页再回来 = 接着上次,不必重跑(用户红线诉求)。
  useEffect(() => {
    if (prefilledRef.current) return;   // 交棒/?q 已预填 → 不抢画布(否则带因子来验证被上次会话图覆盖,P0④ 坑)
    const last = wfLoadLast();
    const name = (last && last.name) || wfName;
    const scope = (last && last.scope) || (WW_WORKSPACE_ID ? 'workspace' : 'global');
    if (last && Array.isArray(last.nodes) && last.nodes.length) {
      const bad = validateGraph(last.nodes, last.edges || []);
      if (!bad) {
        const g = cloneGraph(last.nodes, last.edges || []);
        setNodes(g.nodes); setEdges(g.edges); setWfName(name); setWfScope(scope); setAssetNeedsPool(graphNeedsPoolReview(g.nodes)); setSel(null);
      } else {
        setToast({ title: '上次会话图未通过校验', build: bad + ' · 已保留当前合法示例图' });
        setTimeout(() => setToast(null), 6500);
      }
    }
    const rep = resCache[wfReportKey(name, scope)] || resCache[name];
    if (rep) setResult(rep);   // showRes 保持 false:只亮重看入口,不抢画布
  }, []);   // 仅 mount 一次

  // 长回测重连优先使用启动时的原节点 id（不能 cloneGraph 重发 id，否则
  // 服务端 node_state 无法点亮原节点）。若离开时仍在运行，回来后自动打开
  // 结果抽屉；已看过的完成任务只静默恢复“重看结果”。
  useEffect(() => {
    // 显式点击的 run/import/load 属于另一张图；不能让后台旧任务抢占。
    if (WW_QUERY.get('run') || WW_QUERY.get('import')) return;
    const ref = wfLoadRunRef();
    if (!ref || !/^wr_[0-9a-f]{12,40}$/.test(String(ref.id || ''))) return;
    const wasActive = ref.status === 'starting' || ref.status === 'queued' || ref.status === 'running';
    const shouldWait = wasActive || !ref.acknowledged;
    const sameLaunchQuery = typeof ref.pageQuery === 'string' && ref.pageQuery === location.search;
    if (WW_QUERY.get('load') && !sameLaunchQuery) return;
    // asset/q/load 预填通常应压过“上次会话”，但不能压过一个仍在运行或
    // 尚未被本页确认终态的服务端任务。否则 URL 中长期保留的 q 参数会令
    // 刷新/切页回来跳过 run_ref，用户看到默认/预填图并误以为回测已停止。
    // 同一深链启动的已完成任务也要静默恢复“重看结果”；只有换了另一条
    // q/asset/load 深链时，新的预填才压过旧任务。
    if (prefilledRef.current && !shouldWait && !sameLaunchQuery) return;
    if (Array.isArray(ref.nodes) && ref.nodes.length) {
      const snap = wfGraphSnapshot(ref.nodes, ref.edges || []);
      setNodes(snap.nodes); setEdges(snap.edges); setWfName(ref.name || wfName); setWfScope(ref.scope || wfScope);
      setAssetNeedsPool(graphNeedsPoolReview(snap.nodes)); setSel(null);
    }
    durableWaitIdRef.current = ref.id;
    setDurableWaiting(shouldWait);
    if (shouldWait) { runBusyRef.current = true; setResult(null); setRunning(true); setShowRes(true); setRunErr(''); }
    pollDurableRun(ref.id, ref, shouldWait, 0);
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.target && /input|textarea|select/i.test(e.target.tagName)) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === 'z' && !e.shiftKey) { e.preventDefault(); undo(); }
      else if (mod && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) { e.preventDefault(); redo(); }
      else if (mod && e.key.toLowerCase() === 'd') { e.preventDefault(); duplicate(); }
      else if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); saveLocal(); }
      else if ((e.key === 'Delete' || e.key === 'Backspace') && sel) { e.preventDefault(); delNode(sel); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  return (
    <div style={{ height: '100vh', display: 'grid', gridTemplateRows: '50px 1fr' }}>
      <TopBar onRun={run} running={running} onStopWaiting={stopWaitingForDurableRun} canStopWaiting={durableWaiting} onGenerate={generate} wfName={wfName} onOpenHist={() => setHistOpen(true)} onAIGenerate={aiGenerate} onAILoop={aiLoop} aiBusy={aiBusy} onOpenReports={() => setRepOpen(true)} hasResult={!!result} resOpen={showRes} onReopenRes={() => setShowRes(true)} />
      {loop && <AILoopModal loop={loop} onClose={() => setLoop(null)} onApply={g => {
        const ns = (g.nodes || []).map((n, i) => ({ ...n, x: (n.x != null ? n.x : 60 + i * 240), y: (n.y != null ? n.y : 210), params: { ...(n.params || {}) } }));
        const bad = validateGraph(ns, g.edges || []);
        if (bad) { flash('AI 闭环图未通过校验', bad, 6500); return; }
        pushHist(); setNodes(ns); setEdges((g.edges || []).map(e => ({ ...e }))); setAssetNeedsPool(false); setLoop(null); setSel(null); setShowRes(false); setRunState({});
        flash('已应用 AI 闭环工作流', ns.map(n => (SPECS[n.type] ? SPECS[n.type].title : n.type)).join(' → '), 6000);
      }} />}
      <div style={{ display: 'grid', gridTemplateColumns: '232px 1fr', minHeight: 0 }}>
        <Catalog onAdd={addNode} onBuild={buildFromCard} cards={cards} />
        <div ref={wrapRef} onPointerDown={panStart} onWheel={onWheel}
          style={{ position: 'relative', overflow: 'hidden', cursor: 'grab',
            background: 'linear-gradient(rgba(28,24,20,0.045) 1px, transparent 1px) 0 0/22px 22px, linear-gradient(90deg, rgba(28,24,20,0.045) 1px, transparent 1px) 0 0/22px 22px, var(--paper)' }}>
          <div style={{ position: 'absolute', inset: 0, transformOrigin: '0 0', transform: `translate(${view.x}px,${view.y}px) scale(${view.z})` }}>
            <svg style={{ position: 'absolute', overflow: 'visible', pointerEvents: 'none', width: 1, height: 1 }}>
              {edges.map((e, i) => {
                const fn = nodeById(e.from[0]), tn = nodeById(e.to[0]); if (!fn || !tn) return null;
                const a = portXY(fn, e.from[1], 'out'), b = portXY(tn, e.to[1], 'in');
                const d = wirePath(a, b); const done = runState[e.from[0]] === 'done';
                return <g key={i}>
                  <path d={d} fill="none" stroke={done ? 'var(--dai)' : 'var(--ink-2)'} strokeWidth={done ? 2 : 1.6} opacity={done ? 0.75 : 0.5} />
                  <path d={d} fill="none" stroke="transparent" strokeWidth="13" style={{ pointerEvents: 'stroke', cursor: 'pointer' }}
                    onPointerDown={(ev) => { ev.stopPropagation(); setEdges(es => es.filter((_, k) => k !== i)); }}><title>点击删除连线</title></path>
                </g>;
              })}
              {draft && (() => { const fn = nodeById(draft.fromNode); const a = portXY(fn, draft.fromPort, 'out'); return <path d={wirePath(a, { x: draft.x, y: draft.y })} fill="none" stroke="var(--zhu)" strokeWidth="1.8" strokeDasharray="5 4" />; })()}
            </svg>
            {nodes.map(n => (
              <Node key={n.id} node={n} sel={sel === n.id} status={runState[n.id]} nodes={nodes} edges={edges} onNotify={flash}
                onDrag={(e) => dragNode(n.id, e)} onStartWire={(pid, e) => startWire(n.id, pid, e)}
                onParam={(pid, v) => setParam(n.id, pid, v)} onDel={() => delNode(n.id)} onSel={() => setSel(n.id)} />
            ))}
          </div>
          <Rail zoom={zoom} fit={fit} onRun={run} running={running} onDel={() => sel && delNode(sel)}
            onDup={duplicate} onUndo={undo} onRedo={redo} canUndo={past.length > 0} canRedo={future.length > 0}
            onSave={saveLocal} onExport={exportJSON} onImport={() => fileRef.current && fileRef.current.click()} hasSel={!!sel} />
          <input ref={fileRef} type="file" accept="application/json,.json" onChange={importJSON} style={{ display: 'none' }} />
          {toast && (
            <div style={{ position: 'absolute', top: 16, left: '50%', transform: 'translateX(-50%)', zIndex: 9, display: 'flex', alignItems: 'center', gap: 10, background: 'var(--paper)', border: '1px solid var(--zhu-soft)', borderRadius: 11, padding: '10px 16px', boxShadow: '0 4px 20px rgba(28,24,20,0.14)', animation: 'fadeIn .3s ease', maxWidth: 580 }}>
              <span style={{ width: 22, height: 22, borderRadius: 6, background: 'var(--yin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>瀾</span>
              <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink-1)', lineHeight: 1.5 }}><b style={{ color: 'var(--yin)' }}>{toast.title}</b>{toast.build ? <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)' }}> · {toast.build}</span> : null}</span>
            </div>
          )}
          <Minimap nodes={nodes} />
          {showRes && result && (result._completed_with_errors || result._run_status === 'completed_with_errors') && (
            <div className="mono" style={{ position: 'absolute', top: 14, right: 14, zIndex: 11, padding: '7px 10px', border: '1px solid #9a6b16', borderRadius: 7, background: 'var(--paper)', color: '#9a6b16', boxShadow: '0 3px 12px rgba(28,24,20,.12)', fontSize: 9.5 }}>
              运行完成 · 含节点错误 · completed_with_errors
            </div>
          )}
          {showRes && <ResultsDrawer result={result} loading={running && !result && !runErr} error={runErr} durableWaiting={durableWaiting} onStopWaiting={stopWaitingForDurableRun} onClose={() => setShowRes(false)} onSaveFactor={saveFactor} onSaveCard={saveCard} onExport={exportReport} />}
          {saveOpen && <SaveModal initial={saveOpen.name} onSave={saveNamed} onClose={() => setSaveOpen(null)} />}
          {histOpen && <HistoryModal list={saved} runs={serverRuns} runsLoading={serverRunsLoading} runsError={serverRunsError} openingRun={historyOpening} onRefreshRuns={loadServerRuns} onOpenRun={openHistoricalRun} onLoad={loadEntry} onDelete={deleteEntry} onClose={() => setHistOpen(false)} />}
          {repOpen && <ReportLibModal onReopen={reopenReport} onClose={() => setRepOpen(false)} />}
          <div className="mono" style={{ position: 'absolute', bottom: 14, left: 14, fontSize: 9, color: 'var(--ink-3)', background: 'rgba(241,234,217,0.8)', border: '1px solid var(--line)', borderRadius: 6, padding: '4px 8px' }}>
            {nodes.length} 节点 · {edges.length} 连线 · 缩放 {Math.round(view.z * 100)}% · 拖空白平移 · ⌘滚轮缩放
          </div>
        </div>
      </div>
    </div>
  );
}

function TopBar({ onRun, running, onStopWaiting, canStopWaiting, onGenerate, wfName, onOpenHist, onAIGenerate, onAILoop, aiBusy, onOpenReports, hasResult, resOpen, onReopenRes }) {
  const [q, setQ] = useState('');
  const [aiQ, setAiQ] = useState('');
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '0 18px', borderBottom: '1px solid var(--line)', background: 'rgba(241,234,217,0.7)' }}>
      {!WW_EMBED && (<React.Fragment>
      <div style={{ width: 26, height: 26, background: 'var(--yin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>觀</div>
      <span className="serif" style={{ fontSize: 14, fontWeight: 600, letterSpacing: '0.04em' }}>觀瀾 · AI 工作流</span>
      <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 5, padding: '2px 6px' }}>V1.0</span>
      </React.Fragment>)}
      <span style={{ color: 'var(--line)' }}>|</span>
      <span onClick={onOpenHist} className="mono hover-pill" title="点击查看 / 切换历史工作流" style={{ fontSize: 11, color: 'var(--ink-2)', cursor: 'pointer', border: '1px solid var(--line)', borderRadius: 7, padding: '4px 10px' }}>当前工作流 · <b style={{ fontFamily: 'var(--sans)', color: 'var(--ink)' }}>{wfName}</b> <span style={{ color: 'var(--ink-3)' }}>⌄</span></span>
      <span onClick={onOpenReports} className="mono hover-pill" title="报告库 · 浏览 / 重看 / 删除已存报告" style={{ fontSize: 11, color: 'var(--ink-2)', cursor: 'pointer', border: '1px solid var(--line)', borderRadius: 7, padding: '4px 10px' }}>📁 报告库</span>
      {hasResult && !resOpen && <span onClick={onReopenRes} className="mono" title="重看上次运行结果(无需重跑)" style={{ fontSize: 11, color: 'var(--paper)', cursor: 'pointer', background: 'var(--yin)', borderRadius: 7, padding: '4px 10px', animation: 'fadeIn .3s ease' }}>↻ 重看结果</span>}
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'rgba(168,57,45,0.06)', border: '1px solid var(--zhu-soft)', borderRadius: 20, padding: '5px 8px 5px 11px' }}>
          <span style={{ width: 18, height: 18, borderRadius: 5, background: 'var(--yin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 10, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>瀾</span>
          <input value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') onGenerate(q); }} placeholder="一句话生成 / 改写工作流…"
            style={{ width: 250, border: 'none', outline: 'none', background: 'transparent', fontFamily: 'var(--serif)', fontSize: 12, color: 'var(--ink)' }} />
          <span onClick={() => onGenerate(q)} style={{ background: 'var(--yin)', color: 'var(--paper)', borderRadius: 14, padding: '4px 11px', fontFamily: 'var(--serif)', fontSize: 11.5, cursor: 'pointer' }}>生成 ↵</span>
        </div>
        {WW_LEGACY && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'rgba(168,57,45,0.06)', border: '1px solid var(--zhu-soft)', borderRadius: 20, padding: '5px 8px 5px 11px' }}>
          <span style={{ width: 18, height: 18, borderRadius: 5, background: 'var(--zhu)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 10, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>✦</span>
          <input value={aiQ} onChange={e => setAiQ(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') onAIGenerate(aiQ); }} placeholder="AI 生成工作流 · 描述目标…"
            style={{ width: 250, border: 'none', outline: 'none', background: 'transparent', fontFamily: 'var(--serif)', fontSize: 12, color: 'var(--ink)' }} />
          <span onClick={() => onAIGenerate(aiQ)} style={{ background: 'var(--zhu)', color: 'var(--paper)', borderRadius: 14, padding: '4px 11px', fontFamily: 'var(--serif)', fontSize: 11.5, cursor: aiBusy ? 'default' : 'pointer', opacity: aiBusy ? 0.6 : 1 }}>{aiBusy ? '生成中…' : 'AI 生成 ✦'}</span>
          <span onClick={() => onAILoop(aiQ)} title="AI 闭环:生成→运行→读回真指标→自评→改进(rd-agent 式)" style={{ background: 'var(--yin)', color: 'var(--paper)', borderRadius: 14, padding: '4px 11px', fontFamily: 'var(--serif)', fontSize: 11.5, cursor: aiBusy ? 'default' : 'pointer', opacity: aiBusy ? 0.6 : 1, whiteSpace: 'nowrap' }}>{aiBusy ? '闭环中…' : 'AI 闭环 ✦'}</span>
        </div>
        )}
        {canStopWaiting && <span onClick={onStopWaiting} className="serif" title="仅停止本地轮询；不取消服务端任务，重新运行可能重复计算" style={{ color: '#9a6b16', border: '1px solid #9a6b16', borderRadius: 8, padding: '6px 10px', fontSize: 11.5, cursor: 'pointer', whiteSpace: 'nowrap' }}>停止等待 / 放弃重连</span>}
        <span onClick={running ? undefined : onRun} aria-disabled={running ? 'true' : 'false'} style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'var(--ink)', color: 'var(--paper)', borderRadius: 8, padding: '7px 14px', fontFamily: 'var(--serif)', fontSize: 12, cursor: running ? 'default' : 'pointer', opacity: running ? 0.62 : 1 }}>
          {running ? '运行中…' : '▶ 运行工作流'}
        </span>
        <button type="button" onClick={wwBackToConversation} className="mono" title="返回当前帷幄对话"
          style={{ border: 0, padding: 0, background: 'transparent', fontSize: 11, color: 'var(--ink-3)', cursor: 'pointer' }}>← 对话</button>
      </div>
    </div>
  );
}

// ───────── 经验卡片 (个人知识库) ─────────
// W8a 一键策略模板(经验卡 → 可跑工作流)。点卡即载入完整图,可直接「运行工作流」。
const EXP_CARDS = [
  { id: 'e1', title: '动量延续 · 样本外体检', insight: '过去20日相对强势短期倾向延续;含「样本外占比30%」自动过拟合体检 —— 样本内→样本外 RankIC 衰减一眼看出。', tags: ['动量', '沪深300', '样本外'], build: '数据源(沪深300·样本外30%) → 20日动量 → 特征 → Spearman 因子 → 因子分析' },
  { id: 'e2', title: '短期反转', insight: '近5日超跌的短线倾向反弹(A股反转异象);用 Spearman 将特征物化为因子后计算 5 日 IC。', tags: ['反转', '中证500', 'IC'], build: '数据源(中证500) → 5日反转 → 特征 → Spearman 因子 → IC计算' },
  { id: 'e3', title: '低波防御 · 全绩效回测', insight: '低波动股长期风险调整后收益更优(低波异象);走真回测出 Sharpe/Sortino/信息比率/回撤水下图/月度热力图 + 样本外体检。', tags: ['低波', '回测', '绩效'], build: '数据源(中证800·样本外30%) → 20日低波 → 特征 → 向量化回测(TopN50·月频)' },
  { id: 'e4', title: '低估值 PB', insight: '市净率越低越高分(价值/低估异象);月频、大盘股更稳。', tags: ['价值', '估值', '沪深300'], build: '数据源(沪深300) → 低PB → 特征 → Spearman 因子 → 因子分析' },
  { id: 'e5', title: '高股息', insight: '股息率高的股票偏防御、长期超额稳健;适合震荡/弱市底仓。', tags: ['股息', '防御'], build: '数据源(沪深300) → 高股息(dv_ttm) → 特征 → Spearman 因子 → 因子分析' },
  { id: 'e6', title: '低换手', insight: '换手率低(关注度低)的股票常有超额(流动性/博弈异象);取负为高分。', tags: ['流动性', '换手', '中证500'], build: '数据源(中证500) → 低换手 → 特征 → Spearman 因子 → 因子分析' },
  { id: 'e7', title: '大盘共振 · 对标沪深300', insight: '个股与真沪深300的20日滚动相关(共振系数):高=跟大盘、低/负=独立行情。数据源已挂「对标指数=沪深300」注入 idx_ret。', tags: ['共振', '大盘', 'B2'], build: '数据源(沪深300·对标沪深300) → correlation(returns,idx_ret,20) → 特征 → Spearman 因子 → 因子分析' },
  { id: 'e8', title: '行业共振', insight: '个股与所在行业当日均值的20日滚动相关:高=随板块、低=个股独立。用 indmean 算子,无需额外数据。', tags: ['共振', '行业', 'B2'], build: '数据源(沪深300) → correlation(returns,indmean(returns,industry),20) → 特征 → Spearman 因子 → 因子分析' },
];

function ExpPanel({ onBuild, cards }) {
  return (
    <div>
      <div className="mono" style={{ fontSize: 9, letterSpacing: '.14em', color: 'var(--ink-3)', margin: '2px 2px 11px', display: 'flex', justifyContent: 'space-between' }}><span>个人知识库 · 经验卡 → 数学模型</span><span>{cards.length}</span></div>
      {cards.map(c => (
        <div key={c.id} style={{ border: '1px solid ' + (c.agent ? 'var(--zhu-soft)' : 'var(--line)'), borderRadius: 10, background: c.agent ? 'rgba(168,57,45,0.04)' : 'var(--paper)', padding: '11px 12px', marginBottom: 9, boxShadow: '0 1px 4px rgba(28,24,20,0.05)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <span className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{c.title}</span>
            {c.agent && <span className="mono" style={{ marginLeft: 'auto', fontSize: 8, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 4, padding: '1px 5px' }}>agent 提炼</span>}
          </div>
          <div className="serif" style={{ fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.55, margin: '6px 0 8px' }}>{c.insight}</div>
          {c.metrics && (
            <div style={{ display: 'flex', gap: 12, marginBottom: 9, padding: '7px 9px', background: 'rgba(28,24,20,0.03)', borderRadius: 7 }}>
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>RankIC <b className="up" style={{ fontWeight: 600 }}>{c.metrics.ic}</b></span>
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>Sharpe <b style={{ color: 'var(--ink)', fontWeight: 600 }}>{c.metrics.sharpe}</b></span>
            </div>
          )}
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 9 }}>
            {c.tags.map(t => <span key={t} className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 5, padding: '1px 6px' }}>{t}</span>)}
          </div>
          {c.build && <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginBottom: 9, lineHeight: 1.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>路线 · {c.build}</div>}
          <span onClick={() => onBuild(c)} style={{ display: 'block', textAlign: 'center', fontFamily: 'var(--serif)', fontSize: 12, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 7, padding: '7px', cursor: 'pointer' }}>瀾 模板 · 据此搭建后点 ▶ 运行出真结果</span>
        </div>
      ))}
    </div>
  );
}

// ───────── 保存工作流 弹窗 ─────────
function SaveModal({ initial, onSave, onClose }) {
  const [name, setName] = useState(initial || '');
  return (
    <div onPointerDown={onClose} style={{ position: 'absolute', inset: 0, zIndex: 20, background: 'rgba(28,24,20,0.28)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div onPointerDown={e => e.stopPropagation()} style={{ width: 380, background: 'var(--paper)', border: '1px solid var(--ink)', borderRadius: 14, padding: '18px 20px', boxShadow: '0 12px 40px rgba(28,24,20,0.22)' }}>
        <div className="serif" style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>保存工作流</div>
        <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginBottom: 12 }}>存入历史，可随时从顶栏「当前工作流 ⌄」载入。同名将覆盖。</div>
        <input autoFocus value={name} onChange={e => setName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') onSave(name); }} placeholder="工作流名称"
          style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--line)', borderRadius: 8, padding: '9px 11px', fontFamily: 'var(--sans)', fontSize: 13, color: 'var(--ink)', background: 'var(--paper)', outline: 'none' }} />
        <div style={{ display: 'flex', gap: 9, marginTop: 16, justifyContent: 'flex-end' }}>
          <span onClick={onClose} className="serif" style={{ fontSize: 12.5, color: 'var(--ink-2)', border: '1px solid var(--line)', borderRadius: 8, padding: '8px 15px', cursor: 'pointer' }}>取消</span>
          <span onClick={() => onSave(name)} className="serif" style={{ fontSize: 12.5, color: 'var(--paper)', background: 'var(--ink)', borderRadius: 8, padding: '8px 17px', cursor: 'pointer' }}>保存</span>
        </div>
      </div>
    </div>
  );
}

// ───────── 历史工作流 弹窗 ─────────
function HistoryModal({ list, runs, runsLoading, runsError, openingRun, onRefreshRuns, onOpenRun, onLoad, onDelete, onClose }) {
  return (
    <div onPointerDown={onClose} style={{ position: 'absolute', inset: 0, zIndex: 20, background: 'rgba(28,24,20,0.28)', display: 'flex', alignItems: 'flex-start', justifyContent: 'center', paddingTop: 70 }}>
      <div onPointerDown={e => e.stopPropagation()} style={{ width: 520, maxHeight: '76%', background: 'var(--paper)', border: '1px solid var(--ink)', borderRadius: 14, boxShadow: '0 12px 40px rgba(28,24,20,0.22)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '15px 20px 13px', borderBottom: '1px solid var(--line-soft)' }}>
          <span className="serif" style={{ fontSize: 16, fontWeight: 600 }}>历史工作流</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{list.length} 份</span>
          <span onClick={onClose} style={{ marginLeft: 'auto', fontSize: 17, color: 'var(--ink-3)', cursor: 'pointer' }}>✕</span>
        </div>
        <div style={{ overflowY: 'auto', padding: '10px 14px 14px' }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', margin: '4px 8px 10px' }}><span className="serif" style={{ fontSize: 13 }}>服务端运行历史</span><span className="mono" style={{ color: 'var(--ink-3)', fontSize: 9 }}>不依赖浏览器缓存</span><button onClick={onRefreshRuns} disabled={runsLoading} style={{ marginLeft: 'auto', background: 'transparent', border: '1px solid var(--line)', borderRadius: 5, color: 'var(--ink-2)', cursor: 'pointer' }}>{runsLoading ? '读取中…' : '刷新'}</button></div>
          {runsError && <div role="alert" style={{ color: 'var(--zhu)', padding: '8px', fontSize: 11 }}>{runsError} · 未用本地记录冒充服务端历史</div>}
          {!runsError && !runsLoading && !(runs || []).length && <div className="mono" style={{ color: 'var(--ink-3)', fontSize: 10, padding: '8px' }}>当前空间尚无服务端运行记录</div>}
          {(runs || []).map(rec => <div key={rec.id} style={{ border: '1px solid var(--line-soft)', borderRadius: 8, padding: '10px 12px', marginBottom: 7, background: 'rgba(255,255,255,.35)' }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><span className="serif" style={{ flex: 1, fontSize: 13 }}>{rec.name || rec.id}</span><span className="mono" style={{ color: rec.status === 'failed' || rec.status === 'interrupted' ? 'var(--zhu)' : 'var(--yin)', fontSize: 9 }}>{wfRunStatusLabel(rec.status, rec)}</span></div>
            <div className="mono" style={{ color: 'var(--ink-3)', fontSize: 9, marginTop: 5, overflowWrap: 'anywhere' }}>{rec.id} · {wfAgo(rec.updated_at || rec.created_at)}{rec.node_error_count ? ' · ' + rec.node_error_count + ' 个节点错误' : ''}</div>
            {wfRunIsInterrupted(rec) && <div style={{ color: 'var(--ink-2)', fontSize: 10, marginTop: 6 }}>可恢复原图和已有记录；不支持断点续算，不会自动重跑。</div>}
            <button disabled={!!openingRun} onClick={() => onOpenRun(rec, false)} style={{ marginTop: 8, border: '1px solid var(--line)', borderRadius: 5, padding: '5px 9px', color: 'var(--ink)', background: 'var(--paper)', cursor: 'pointer' }}>{openingRun === rec.id ? '读取状态…' : rec.status === 'running' || rec.status === 'queued' ? '继续查看运行' : '恢复原图 / 查看结果'}</button>
          </div>)}
          <div className="serif" style={{ borderTop: '1px solid var(--line)', margin: '17px 0 10px', padding: '12px 8px 0', fontSize: 13 }}>保存的工作流</div>
          {list.length === 0 && <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', padding: '30px 0' }}>暂无保存的工作流 · 用右栏「存」保存当前画布</div>}
          {list.map(w => (
            <div key={(w.scope || '') + ':' + w.id} className="hover-row" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 12px', borderRadius: 9, border: wfNeedsReview(w) ? '1px solid rgba(166,61,48,.45)' : '1px solid var(--line-soft)', marginBottom: 7, cursor: 'pointer', background: wfNeedsReview(w) ? 'rgba(166,61,48,.055)' : (wfIsGlobalTemplate(w) ? 'rgba(138,111,63,.06)' : 'rgba(255,255,255,0.5)') }}
              onClick={() => onLoad(w)}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="serif" style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13.5, fontWeight: 500, color: 'var(--ink)' }}><span>{w.name}</span>{wfIsGlobalTemplate(w) && <span className="mono" style={{ fontSize: 8.5, color: 'var(--jin)', border: '1px solid var(--jin)', padding: '1px 5px', borderRadius: 4 }}>全局只读模板</span>}{wfNeedsReview(w) && <span className="mono" title={wfReviewReason(w)} style={{ fontSize: 8.5, color: 'var(--zhu)', border: '1px solid var(--zhu)', padding: '1px 5px', borderRadius: 4 }}>旧版只读 · 需重建</span>}</div>
                <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 3 }}>{wfAgo(w.ts)} · {((w.graph && w.graph.nodes) || w.nodes || []).length} 节点 · {((w.graph && w.graph.edges) || w.edges || []).length} 连线</div>
                {wfNeedsReview(w) && <div className="mono" style={{ fontSize: 9, color: 'var(--zhu)', marginTop: 3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{wfReviewReason(w)}</div>}
                {w.legacy_recovery && <details onClick={e => e.stopPropagation()} style={{ color: 'var(--ink-2)', fontSize: 10, marginTop: 7 }}><summary style={{ cursor: 'pointer' }}>查看保留说明与重建建议</summary><div style={{ lineHeight: 1.6, marginTop: 5 }}>{wfReviewReason(w)}<br />{(w.legacy_recovery.suggested_actions || []).join('；')}{(w.legacy_recovery.model_references || []).length > 0 && <div className="mono" style={{ overflowWrap: 'anywhere' }}>历史引用（尚未核验）：{w.legacy_recovery.model_references.map(x => x.model_id || x.model_name || x.node_id).join(' / ')}</div>}</div></details>}
                <div className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{(((w.graph && w.graph.nodes) || w.nodes || [])).slice().sort((a, b) => a.x - b.x).map(n => SPECS[n.type] ? SPECS[n.type].title : n.type).join(' → ')}</div>
              </div>
              <span onClick={() => onLoad(w)} className="serif" style={{ flexShrink: 0, fontSize: 12, color: 'var(--paper)', background: 'var(--ink)', borderRadius: 7, padding: '6px 13px', cursor: 'pointer' }}>载入</span>
              {!wfIsGlobalTemplate(w) && <span onClick={(e) => { e.stopPropagation(); onDelete(w); }} title="删除" style={{ flexShrink: 0, fontSize: 13, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 2px' }}>✕</span>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Catalog({ onAdd, onBuild, cards }) {
  const [tab, setTab] = useState('nodes');
  const tabBtn = (k, l) => <span onClick={() => setTab(k)} style={{ flex: 1, textAlign: 'center', padding: '8px 0', fontFamily: 'var(--serif)', fontSize: 12.5, cursor: 'pointer', color: tab === k ? 'var(--ink)' : 'var(--ink-3)', borderBottom: tab === k ? '2px solid var(--yin)' : '2px solid transparent' }}>{l}</span>;
  return (
    <aside style={{ borderRight: '1px solid var(--line)', background: 'rgba(241,234,217,0.5)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ display: 'flex', borderBottom: '1px solid var(--line)', flexShrink: 0 }}>{tabBtn('nodes', '节点目录')}{tabBtn('exp', '经验卡')}</div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 12, minHeight: 0 }}>
        {tab === 'nodes' ? (
          <div>
            <input placeholder="搜索节点…" style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--line)', borderRadius: 8, padding: '8px 11px', fontFamily: 'var(--sans)', fontSize: 12, background: 'var(--paper)', color: 'var(--ink-2)', marginBottom: 12 }} />
            {CATALOG.map(grp => (
              <div key={grp.g} style={{ marginBottom: 6 }}>
                <div className="serif" style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '7px 6px', fontSize: 12.5, fontWeight: 600, color: 'var(--ink-1)' }}>
                  <span style={{ color: 'var(--ink-3)', fontSize: 11 }}>▾</span>{grp.g}
                  <span className="mono" style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--ink-3)' }}>{grp.items.length}</span>
                </div>
                {grp.items.map(t => (
                  <div key={t} onClick={() => onAdd(t)} title="点击加入画布"
                    style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 6px 6px 22px', fontSize: 12, color: 'var(--ink-2)', cursor: 'pointer', borderRadius: 6 }}
                    onMouseEnter={e => e.currentTarget.style.background = 'rgba(28,24,20,0.05)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                    <span style={{ width: 6, height: 6, borderRadius: 2, background: CAT[SPECS[t].cat].c, flexShrink: 0 }} />{SPECS[t].title}
                  </div>
                ))}
              </div>
            ))}
          </div>
        ) : <ExpPanel onBuild={onBuild} cards={cards} />}
      </div>
    </aside>
  );
}

// ───────── 公式输入辅助: 字段/算子速查 + 真实例子 + 校验·预览(W2)─────────
// 真值表对齐引擎 expr.py 的 FACTOR_VOCAB(16 字段)与算子白名单;例子全部用真字段、可直接跑。
const DSL_FIELD_GROUPS = [
  { g: '量价', items: [['close', '收盘价'], ['open', '开盘价'], ['high', '最高价'], ['low', '最低价'], ['volume', '成交量'], ['vwap', '成交均价'], ['amount', '成交额'], ['returns', '日收益率']] },
  { g: '估值', items: [['pe_ttm', '市盈率TTM'], ['pb', '市净率'], ['ps_ttm', '市销率TTM'], ['dv_ttm', '股息率%'], ['total_mv', '总市值'], ['circ_mv', '流通市值'], ['turnover_rate', '换手率%']] },
  { g: '财务(季频·公告日PIT)', items: [['roe', '净资产收益率'], ['roa', '总资产收益率'], ['net_margin', '净利率'], ['rev_yoy', '营收同比%'], ['np_yoy', '净利同比%'], ['debt_ratio', '资产负债率'], ['eps', '每股收益'], ['net_income', '净利润(元)'], ['revenue', '营收(元)'], ['total_equity', '净资产(元)'], ['cfo', '经营现金流(元)']] },
  { g: '行业', items: [['industry', '行业(中性化用)']] },
  { g: '参照(数据源填了才有)', items: [['idx_ret', '对标指数日收益(大盘共振)'], ['ref_ret', '龙头股日收益(龙头共振)']] },
];
const DSL_OP_LIST = [
  ['rank(x)', '截面排名 0~1'], ['ts_rank(x,n)', 'n日时序排名'], ['delta(x,n)', 'x − n日前'], ['delay(x,n)', 'n日前的 x'],
  ['ts_mean(x,n)', 'n日均值'], ['ts_sum(x,n)', 'n日累计'], ['ts_max(x,n)', 'n日最大'], ['ts_min(x,n)', 'n日最小'],
  ['stddev(x,n)', 'n日标准差'], ['correlation(x,y,n)', 'n日相关'], ['covariance(x,y,n)', 'n日协方差(→beta)'], ['scale(x)', '归一(|和|=1)'], ['indneutralize(x,industry)', '行业中性化(需 2 参)'],
  ['csmean(x)', '截面/篮子均值(→共振)'], ['indmean(x,industry)', '所在行业均值(→行业共振)'], ['abs(x)', '绝对值'], ['log(x)', '对数'], ['sign(x)', '符号'], ['power(x,a)', 'x 的 a 次幂'], ['decay_linear(x,n)', 'n日线性加权'],
];
const FORMULA_EXAMPLES = [
  ['rank(-delta(close,5))', '5日反转 · 近5日跌得多→高分'],
  ['-rank(ts_sum(returns,5))', '5日短期反转(A股常见)'],
  ['rank(ts_sum(returns,20))', '20日动量 · 涨得多→高分'],
  ['rank(-pb)', '低估值 · 市净率越低越高分'],
  ['rank(-total_mv)', '小市值 · 市值越小越高分'],
  ['rank(-stddev(returns,20))', '低波动 · 20日波动越小越高分'],
  ['rank(-turnover_rate)', '低换手 · 换手越低越高分'],
  ['rank(dv_ttm)', '高股息 · 股息率越高越高分'],
  ['rank(roe)', '高质量 · ROE 越高越高分(质量因子)'],
  ['rank(np_yoy)', '高成长 · 净利润同比越高越高分(成长)'],
  ['rank(net_income/total_mv)', '盈利收益率 · 净利/市值(价值,PIT真财报)'],
  ['rank(-debt_ratio)', '低杠杆 · 资产负债率越低越高分(质量)'],
  ['rank(cfo/net_income)', '盈利质量 · 经营现金流/净利(现金含量)'],
  ['rank(correlation(close,volume,10))', '量价相关 · 10日价量相关'],
  ['indneutralize(rank(-pb), industry)', '低估值(行业中性化)'],
  ['correlation(returns, csmean(returns), 20)', '共振 · 个股与篮子20日相关(配「自选代码」选一篮子票)'],
  ['covariance(returns, csmean(returns), 20) / covariance(csmean(returns), csmean(returns), 20)', '篮子 beta · 个股对篮子的敏感度'],
  ['correlation(returns, idx_ret, 20)', '大盘共振 · 个股与沪深300的20日相关(数据源选「对标指数·沪深300」)'],
  ['correlation(returns, indmean(returns, industry), 20)', '行业共振 · 个股与所在行业的20日相关'],
  ['correlation(returns, ref_ret, 20)', '龙头共振 · 个股跟随龙头的20日相关(数据源填「龙头代码」)'],
];

// 公式节点辅助面板: 仅 formula 节点渲染。点字段/算子插入表达式; 点例子整条载入;
// 「校验·预览」真打 /factor/preview(诚实失败显红, 真值样本显绿)。不改既有 expr 输入框。
function FormulaPanel({ node, onParam }) {
  const [tab, setTab] = useState(null);   // 'field' | 'op' | 'eg' | null
  const [r, setR] = useState(null);
  const [busy, setBusy] = useState(false);
  const insert = (tok) => { const cur = String(node.params.expr || '').trim(); onParam('expr', (cur && cur !== 'close') ? cur + tok : tok); };
  const preview = async (ev) => {
    ev.stopPropagation();
    if (busy) return;
    setBusy(true); setR(null);
    try {
      const expr = String(node.params.expr || '').trim();
      const res = await _post('/factor/preview', { expr });
      setR(res || { ok: false, reason: '空响应' });
    } catch (err) { setR({ ok: false, reason: (err && err.message) || String(err) }); }
    setBusy(false);
  };
  const chip = { fontSize: 9.5, fontFamily: 'var(--mono)', border: '1px solid var(--line)', borderRadius: 4, padding: '1px 5px', cursor: 'pointer', color: 'var(--ink-2)', background: 'var(--paper)' };
  const tabBtn = (k, l) => <span onClick={e => { e.stopPropagation(); setTab(tab === k ? null : k); }} style={{ fontSize: 10, padding: '2px 7px', borderRadius: 5, cursor: 'pointer', color: tab === k ? 'var(--paper)' : 'var(--ink-2)', background: tab === k ? 'var(--yin)' : 'rgba(28,24,20,0.04)' }}>{l}</span>;
  return (
    <div onPointerDown={e => e.stopPropagation()} style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)' }}>
      <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
        {tabBtn('field', '字段')}{tabBtn('op', '算子')}{tabBtn('eg', '例子')}
        <span onClick={e => { e.stopPropagation(); onParam('expr', ''); setR(null); }} style={{ marginLeft: 'auto', fontSize: 10, padding: '2px 7px', borderRadius: 5, cursor: 'pointer', color: 'var(--ink-3)', border: '1px solid var(--line)' }}>清空</span>
        <span onClick={preview} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 500, padding: '2px 8px', borderRadius: 5, cursor: busy ? 'default' : 'pointer', color: busy ? 'var(--ink-3)' : 'var(--yin)', border: '1px solid ' + (busy ? 'var(--line)' : 'var(--yin)') }}>{busy && <span style={{ width: 9, height: 9, border: '1.5px solid var(--line)', borderTopColor: 'var(--yin)', borderRadius: '50%', display: 'inline-block', animation: 'spin .7s linear infinite' }} />}{busy ? '校验中…' : '校验·预览'}</span>
      </div>
      {tab === 'field' && (
        <div style={{ marginTop: 6 }}>
          {DSL_FIELD_GROUPS.map(grp => (
            <div key={grp.g} style={{ marginBottom: 5 }}>
              <div style={{ fontSize: 9, color: 'var(--ink-3)', marginBottom: 3 }}>{grp.g}</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {grp.items.map(([f, d]) => <span key={f} title={d} onClick={() => insert(f)} style={chip}>{f}</span>)}
              </div>
            </div>
          ))}
        </div>
      )}
      {tab === 'op' && (
        <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {DSL_OP_LIST.map(([o, d]) => <span key={o} title={d} onClick={() => insert(o.replace(/\(.*\)/, '('))} style={chip}>{o}</span>)}
        </div>
      )}
      {tab === 'eg' && (
        <div style={{ marginTop: 6 }}>
          {FORMULA_EXAMPLES.map(([e, d]) => (
            <div key={e} onClick={() => onParam('expr', e)} title={'点此载入: ' + e} style={{ padding: '4px 6px', borderRadius: 5, cursor: 'pointer', marginBottom: 3, border: '1px solid var(--line)' }}>
              <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink)' }}>{e}</div>
              <div style={{ fontSize: 9, color: 'var(--ink-3)' }}>{d}</div>
            </div>
          ))}
        </div>
      )}
      {r && (r.ok ? (
        <div style={{ marginTop: 6, fontSize: 10, color: 'var(--ink-2)', lineHeight: 1.5 }}>
          <div style={{ color: 'var(--dai)' }}>✓ 截面 {r.n_cross} 只 · 覆盖 {r.coverage != null ? Math.round(r.coverage * 100) + '%' : '—'} · {r.latest_date}</div>
          <div className="mono" style={{ color: 'var(--ink-3)' }}>均值 {_n2(r.stats && r.stats.mean)} · σ {_n2(r.stats && r.stats.std)} · [{_n2(r.stats && r.stats.min)}, {_n2(r.stats && r.stats.max)}]</div>
          <div className="mono" style={{ color: 'var(--ink-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>样本 {(r.sample || []).map(s => s.code + ':' + _n2(s.value)).join('  ')}</div>
        </div>
      ) : (
        <div title={r.reason} style={{ marginTop: 6, fontSize: 10, color: 'var(--zhu)', lineHeight: 1.4 }}>✗ {(() => { const s = String(r.reason || '').toLowerCase(); if (s.includes('无后端') || s.includes('failed to fetch') || s.includes('networkerror')) return '没连上后端 —— 请用 http://127.0.0.1:9999/ 打开本页(file:// 直开无后端)'; if (s.includes('engine_import') || s.includes('no module')) return '数据引擎未就绪 —— 需启动本地后端引擎'; if (s.includes('never closed') || s.includes('syntax') || s.includes('invalid')) return '括号没闭合 / 公式不完整 —— 点「例子」选现成的最省事'; if (s.includes('not defined') || s.includes('nameerror')) return '用了不存在的字段或名字 —— 只能用「字段」里列的那些'; if (s.includes('load_error') || s.includes('universe')) return '数据加载失败 —— 检查股票池 / 时间窗设置'; if (s.includes('全为空') || s.includes('empty') || s.includes('series')) return '这条公式在样本里算不出值'; return String(r.reason || '').replace(/^\s*[A-Za-z][A-Za-z.]*Error:\s*/, ''); })()}</div>
      ))}
    </div>
  );
}

// ───────── 因子库浏览(W3)─────────
// 全屏因子目录(portal 到 body, 避开画布缩放/裁剪)。两页: 研报精选(/factor/catalog, 中文名/
// 大类/方向/说明)+ 全部因子(/factor/list 的 442 内置 + 39 仓内)。「用此因子」→ 写节点 name+expr。
// 研究库弹窗:因子 tab(研报精选 / 全部因子)+ 模型 tab(registry 变体)。
//   onPick(f)        → 因子节点回写 name/expr(factorlib 节点用)。
//   onPickModel(m)   → 给定时显示「模型」tab(model 节点用),选中回写 model_id/model_name;
//                      不给时仅因子 tab(行为如旧,factorlib 节点)。
function FactorLibModal({ onPick, onClose, onPickModel, initialLibTab }) {
  const [cur, setCur] = useState(null);     // 研报精选目录
  const [full, setFull] = useState(null);   // 全部因子(registered+user)
  const [models, setModels] = useState(null); // 研究库模型变体(/screen/models)
  const [err, setErr] = useState('');       // 加载失败显形(后端挂时不再伪装成「没有匹配的因子」空库)
  const [libTab, setLibTab] = useState(initialLibTab === 'model' && onPickModel ? 'model' : 'factor'); // 顶层:因子 / 模型
  const [tab, setTab] = useState('curated');
  const [cat, setCat] = useState('全部');
  const [q, setQ] = useState('');
  useEffect(() => {
    (async () => {
      const c = await _get('/factor/catalog');
      if (!c) setErr('因子目录加载失败 — 后端不可达或出错(检查 9999 是否在跑)');
      setCur(c && c.ok ? c : { factors: [], cats: [] });
      try {
        const lib = await _get('/factorlib/list?validate=false');
        const savedByName = {}; ((lib && lib.factors) || []).forEach(x => { savedByName[x.name] = x; });
        // 用引擎 /factor/list(含全部 442 内置 + 仓内已注册 = registered 481);兼容 /factorlib/list 的 factors 形。
        const l = (await _get('/factor/list')) || (await _list());
        const reg = (l && l.registered) || [], usr = (l && l.user) || [], fac = (l && l.factors) || [];
        setFull([
          ...reg.map(s => { const z = savedByName[s.name] || {}; const fs = z.factor_spec || s.factor_spec; return { name: s.name, expr: s.formula || z.expr || '', cat: s.family || 'zoo', status: z.status, registered: true, structured: !!(z.structured || s.structured || fs), factor_spec: fs, factor_spec_digest: z.factor_spec_digest || s.factor_spec_digest }; }),
          ...usr.map(u => { const z = savedByName[u.name] || {}; const fs = z.factor_spec || u.factor_spec; return { name: u.name, expr: u.expr || u.formula || z.expr || '', cat: u.family || 'user', status: z.status, registered: true, structured: !!(z.structured || u.structured || fs), factor_spec: fs, factor_spec_digest: z.factor_spec_digest || u.factor_spec_digest }; }),
          // /factorlib/list 只能证明已落盘；在 /factor/list 不可用的回退路径上，
          // 宁可显示“未证实注册”，也不把 structured 冒充为可重放。
          ...fac.map(u => { const z = savedByName[u.name] || u; return { name: u.name, expr: u.expr || u.formula || z.expr || '', cat: u.family || 'library', status: z.status || u.status, registered: false, structured: !!z.structured, factor_spec: z.factor_spec, factor_spec_digest: z.factor_spec_digest }; }),
        ]);
      } catch (e) { setFull([]); setErr('因子清单加载失败 — 后端不可达或出错(检查 9999 是否在跑)'); }
    })();
  }, []);
  // 模型 tab 首次切到时拉一次研究库变体(/screen/models)。仅 onPickModel 给定(model 节点)才会用到。
  useEffect(() => {
    if (libTab !== 'model' || models !== null) return;
    _get('/screen/models').then(j => { setModels((j && j.ok) ? (j.variants || []) : []); }).catch(() => setModels([]));
  }, [libTab]);
  const ql = q.trim().toLowerCase();
  let list = tab === 'curated' ? ((cur && cur.factors) || []) : (full || []);
  if (tab === 'curated' && cat !== '全部') list = list.filter(f => f.cat === cat);
  if (ql) list = list.filter(f => (f.name + ' ' + (f.expr || '') + ' ' + (f.desc || '') + ' ' + (f.cat || '')).toLowerCase().includes(ql));
  let mlist = models || [];
  if (ql) mlist = mlist.filter(m => ((m.name || '') + ' ' + (m.id || '') + ' ' + (m.kind || '') + ' ' + (m.source || '')).toLowerCase().includes(ql));
  const cats = ['全部', ...((cur && cur.cats) || [])];
  const tabSt = (on) => ({ fontSize: 12, padding: '5px 12px', borderRadius: 7, cursor: 'pointer', fontFamily: 'var(--serif)', color: on ? 'var(--paper)' : 'var(--ink-2)', background: on ? 'var(--yin)' : 'rgba(28,24,20,0.05)' });
  const badge = (txt, c) => <span className="mono" style={{ fontSize: 8.5, color: c || 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 4, padding: '0 5px' }}>{txt}</span>;
  return ReactDOM.createPortal(
    <div onPointerDown={onClose} style={{ position: 'fixed', inset: 0, zIndex: 50, background: 'rgba(28,24,20,0.32)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div onPointerDown={e => e.stopPropagation()} style={{ width: 580, maxHeight: '82%', background: 'var(--paper)', border: '1px solid var(--ink)', borderRadius: 14, display: 'flex', flexDirection: 'column', overflow: 'hidden', boxShadow: '0 12px 40px rgba(28,24,20,0.25)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '14px 18px 11px', borderBottom: '1px solid var(--line-soft)' }}>
          <span className="serif" style={{ fontSize: 16, fontWeight: 600 }}>研究库</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{libTab === 'model' ? (models === null ? '加载中' : (models.length + ' 模型')) : (tab === 'curated' ? (((cur && cur.factors) || []).length + ' 预置') : (full === null ? '加载中' : (full.length + ' 全部')))}</span>
          <span onClick={onClose} style={{ marginLeft: 'auto', fontSize: 17, color: 'var(--ink-3)', cursor: 'pointer' }}>✕</span>
        </div>
        {onPickModel && (
          <div style={{ display: 'flex', gap: 7, padding: '11px 18px 0' }}>
            <span onClick={() => setLibTab('factor')} style={tabSt(libTab === 'factor')}>因子</span>
            <span onClick={() => setLibTab('model')} style={tabSt(libTab === 'model')}>模型</span>
          </div>
        )}
        {libTab !== 'model' && (
          <div style={{ display: 'flex', gap: 7, padding: '11px 18px 0' }}>
            <span onClick={() => setTab('curated')} style={tabSt(tab === 'curated')}>研报精选</span>
            <span onClick={() => setTab('all')} style={tabSt(tab === 'all')}>全部因子</span>
          </div>
        )}
        <div style={{ padding: '10px 18px' }}>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder={libTab === 'model' ? '搜索模型名 / id / 类型…' : '搜索因子名 / 公式 / 说明…'} style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--line)', borderRadius: 8, padding: '7px 11px', fontFamily: 'var(--sans)', fontSize: 12, background: 'var(--paper)', color: 'var(--ink)' }} />
          {libTab !== 'model' && tab === 'curated' && <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 8 }}>{cats.map(c => <span key={c} onClick={() => setCat(c)} style={{ fontSize: 10.5, padding: '2px 9px', borderRadius: 5, cursor: 'pointer', color: cat === c ? 'var(--paper)' : 'var(--ink-2)', background: cat === c ? 'var(--yin)' : 'rgba(28,24,20,0.04)' }}>{c}</span>)}</div>}
        </div>
        <div style={{ overflowY: 'auto', padding: '0 14px 14px' }}>
          {err && libTab !== 'model' && <div className="mono" style={{ fontSize: 11, color: 'var(--zhu)', border: '1px solid var(--zhu)', borderRadius: 8, background: 'rgba(185,74,61,0.05)', padding: '10px 12px', margin: '4px 4px 10px' }}>⚠ {err}</div>}
          {libTab === 'model' ? (
            <React.Fragment>
              {models === null && <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', padding: '26px' }}>加载中…</div>}
              {mlist.map((m, i) => (
                <div key={(m.id || '') + i} style={{ border: '1px solid var(--line-soft)', borderRadius: 9, padding: '9px 11px', marginBottom: 7, background: 'rgba(255,255,255,0.5)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <span className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{m.name || m.id}</span>
                    {badge(m.source === 'workflow' ? '来自工作流' : '本工坊', m.source === 'workflow' ? 'var(--dai)' : 'var(--jin)')}
                    {m.kind && badge(m.kind)}
                    {m.oos_ic != null && badge('OOS IC ' + (+m.oos_ic).toFixed(4), (+m.oos_ic) >= 0 ? 'var(--dai)' : 'var(--zhu)')}
                    <span onClick={() => onPickModel(m)} className="serif" style={{ marginLeft: 'auto', fontSize: 11.5, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 6, padding: '4px 12px', cursor: 'pointer' }}>用此模型</span>
                  </div>
                  {m.id && <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', marginTop: 4 }}>{m.id}</div>}
                </div>
              ))}
              {models !== null && mlist.length === 0 && <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', padding: '26px' }}>研究库暂无模型(在工坊训练或工作流「存入模型库」后出现)</div>}
            </React.Fragment>
          ) : (
            <React.Fragment>
              {(tab === 'curated' ? cur === null : full === null) && <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', padding: '26px' }}>加载中…</div>}
              {list.map((f, i) => (
                <div key={(f.name || '') + i} style={{ border: '1px solid var(--line-soft)', borderRadius: 9, padding: '9px 11px', marginBottom: 7, background: 'rgba(255,255,255,0.5)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <span className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{f.name}</span>
                    {f.cat && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 4, padding: '0 5px' }}>{f.cat}</span>}
                    {f.status === 'draft' && <span className="mono" style={{ fontSize: 8.5, color: 'var(--zhu)', border: '1px solid var(--zhu-soft)', borderRadius: 4, padding: '0 5px' }}>draft·待审</span>}
                    {f.structured && f.registered && <span className="mono" style={{ fontSize: 8.5, color: 'var(--dai)', border: '1px solid var(--dai)', borderRadius: 4, padding: '0 5px' }}>factor_spec·可重放</span>}
                    {f.structured && !f.registered && <span className="mono" style={{ fontSize: 8.5, color: '#9a6b16', border: '1px dashed #9a6b16', borderRadius: 4, padding: '0 5px' }}>结构化·未证实注册</span>}
                    {f.dir && <span className="mono" style={{ fontSize: 8.5, color: f.dir === '正向' ? 'var(--dai)' : 'var(--zhu)' }}>{f.dir}</span>}
                    <span onClick={() => onPick(f)} className="serif" style={{ marginLeft: 'auto', fontSize: 11.5, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 6, padding: '4px 12px', cursor: 'pointer' }}>用此因子</span>
                  </div>
                  {f.desc && <div className="serif" style={{ fontSize: 11, color: 'var(--ink-2)', margin: '5px 0 4px', lineHeight: 1.5 }}>{f.desc}</div>}
                  {f.expr && <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.expr}</div>}
                </div>
              ))}
              {list.length === 0 && !err && (tab === 'curated' ? cur !== null : full !== null) && <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', padding: '26px' }}>没有匹配的因子</div>}
            </React.Fragment>
          )}
        </div>
      </div>
    </div>, document.body);
}

// 因子库节点辅助面板: 「浏览因子库」按钮 → 弹全屏目录; 选中显示已选因子。仅 factorlib 节点渲染。
function FactorLibPanel({ node, onParam }) {
  const [open, setOpen] = useState(false);
  const picked = String(node.params.name || '').trim();
  return (
    <div onPointerDown={e => e.stopPropagation()} style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)' }}>
      <div onClick={e => { e.stopPropagation(); setOpen(true); }} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5, height: 22, borderRadius: 6, cursor: 'pointer', fontSize: 11, fontWeight: 500, color: 'var(--yin)', border: '1px solid var(--yin)', background: 'rgba(168,57,45,0.05)' }}>浏览因子库 ✦</div>
      {picked
        ? <div style={{ marginTop: 5, fontSize: 10, color: 'var(--ink-2)' }}>已选:<b style={{ color: 'var(--ink)' }}>{picked}</b></div>
        : <div style={{ marginTop: 5, fontSize: 9.5, color: 'var(--ink-3)' }}>未选 —— 点上方按钮挑一个因子</div>}
      {open && <FactorLibModal onClose={() => setOpen(false)} onPick={f => {
        onParam('name', f.name); onParam('expr', f.expr);
        // 结构化因子把完整定义和摘要写进工作流快照。执行时仍按注册名
        // 取数，但会核对这两项；库项缺失/变更绝不退回 base expr。
        onParam('factor_spec', f.factor_spec || null);
        onParam('factor_spec_digest', f.factor_spec_digest || '');
        setOpen(false);
      }} />}
    </div>
  );
}

// 模型节点辅助面板:「研究库」按钮 → 弹研究库(默认「模型」tab); 选中显示已选模型。仅 model 节点渲染。
// 写回机制与 FactorLibPanel 同源(onParam),区别仅写 model_id/model_name。
function ModelLibPanel({ node, onParam }) {
  const [open, setOpen] = useState(false);
  const picked = String(node.params.model_name || '').trim();
  const pid = String(node.params.model_id || '').trim();
  return (
    <div onPointerDown={e => e.stopPropagation()} style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)' }}>
      <div onClick={e => { e.stopPropagation(); setOpen(true); }} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5, height: 22, borderRadius: 6, cursor: 'pointer', fontSize: 11, fontWeight: 500, color: 'var(--yin)', border: '1px solid var(--yin)', background: 'rgba(168,57,45,0.05)' }}>研究库 ✦</div>
      {pid
        ? <div style={{ marginTop: 5, fontSize: 10, color: 'var(--ink-2)' }}>已选:<b style={{ color: 'var(--ink)' }}>{picked || pid}</b></div>
        : <div style={{ marginTop: 5, fontSize: 9.5, color: 'var(--ink-3)' }}>未选 —— 点上方按钮挑一个模型</div>}
      {open && <FactorLibModal onClose={() => setOpen(false)} initialLibTab="model"
        onPick={() => {}}
        onPickModel={m => { onParam('model_id', m.id || ''); onParam('model_name', m.name || m.id || ''); setOpen(false); }} />}
    </div>
  );
}

// E3 ML 树模型节点(xgb/lgbm/rf)的「存入模型库」: 据上游静态导出 recipe → POST /model/promote
// (后端起子进程生产重训, 异步) → 轮询 /model/promote/status 至 done。仅树模型节点渲染。
const _PROMOTE_KIND = { lgbm: 'lightgbm', xgb: 'xgboost', rf: 'rf' };
function PromoteModelPanel({ node, nodes, edges, onNotify }) {
  const [busy, setBusy] = useState(false);
  const timerRef = useRef(null);
  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current); }, []);
  const notify = (t, b) => { if (onNotify) onNotify(t, b, 6500); };
  const promote = async (e) => {
    e.stopPropagation();
    if (busy) return;
    const kind = _PROMOTE_KIND[node.type];
    if (!kind) { notify('存入模型库', '该模型类型暂不支持入库(首期树模型 lgbm/xgb/rf)'); return; }
    const recipe = deriveRecipeForNode(node, nodes, edges);
    if (!recipe.features || !recipe.features.length) { notify('存入模型库', '上游无特征表达式 —— 需「特征工程构建(接公式/因子库)」直连 fe 端口'); return; }
    setBusy(true);
    try {
      const name = String((node.params && node.params.name) || '').trim() || (SPECS[node.type].title + '·入库');
      const r = await _post('/model/promote', { name, kind, recipe });
      if (!r || !r.ok) { setBusy(false); notify('存入失败', (r && r.reason) || '后端拒绝'); return; }
      notify('已起生产重训(分钟级)', '完成后在研究库 / 工坊可见');
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = setInterval(async () => {
        const s = (await _get('/model/promote/status')) || {};
        const st = s.state || {};
        if (!st.running && st.phase === 'done') {
          clearInterval(timerRef.current); timerRef.current = null; setBusy(false);
          notify(st.ok ? '入库完成 ✓' : '入库失败', st.ok ? ('变体 ' + (st.variant_id || '')) : (st.error || ''));
        }
      }, 3000);
    } catch (err) { setBusy(false); notify('存入失败', String((err && err.message) || err)); }
  };
  return (
    <div onPointerDown={e => e.stopPropagation()} style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)' }}>
      <div onClick={promote} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5, height: 22, borderRadius: 6, cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.55 : 1, fontSize: 11, fontWeight: 500, color: 'var(--jin)', border: '1px solid var(--jin)', background: 'rgba(138,111,63,0.06)' }}>{busy ? '生产重训中…' : '存入模型库 ⤓'}</div>
      <div style={{ marginTop: 5, fontSize: 9.5, color: 'var(--ink-3)', lineHeight: 1.4 }}>据上游特征 + 本节点超参,起全市场全窗口生产重训,落研究库(异步)。</div>
    </div>
  );
}

// LSTM 节点「发布为 DL 源」: 据上游 recipe(universe/params)→ POST /model/publish_dl
// (后端起子进程:训练 → 写 var/dl_pred_lstm.parquet → regen 折进 v4,异步)→ 轮询 status。仅 lstm 节点渲染。
function PublishDlPanel({ node, nodes, edges, onNotify }) {
  const [busy, setBusy] = useState(false);
  const timerRef = useRef(null);
  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current); }, []);
  const notify = (t, b) => { if (onNotify) onNotify(t, b, 6500); };
  const publish = async (e) => {
    e.stopPropagation();
    if (busy) return;
    const recipe = deriveRecipeForNode(node, nodes, edges);
    setBusy(true);
    try {
      const name = String((node.params && node.params.name) || '').trim() || 'LSTM·DL源';
      const r = await _post('/model/publish_dl', { kind: 'lstm', name, recipe });
      if (!r || !r.ok) { setBusy(false); notify('发布失败', (r && r.reason) || '后端拒绝'); return; }
      notify('已起 LSTM 发布(分钟级)', '训练 → 折进 v4(regen);完成后选股页徽章多一源 lstm');
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = setInterval(async () => {
        const s = (await _get('/model/publish_dl/status')) || {};
        const st = s.state || {};
        if (!st.running && st.phase === 'done') {
          clearInterval(timerRef.current); timerRef.current = null; setBusy(false);
          notify(st.ok ? '已发布 DL 源 ✓' : '发布失败',
                 st.ok ? '已折进 v4 · 重启 9999 后选股页徽章显 lstm 源' : (st.error || ''));
        }
      }, 3000);
    } catch (err) { setBusy(false); notify('发布失败', String((err && err.message) || err)); }
  };
  return (
    <div onPointerDown={e => e.stopPropagation()} style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)' }}>
      <div onClick={publish} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5, height: 22, borderRadius: 6, cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.55 : 1, fontSize: 11, fontWeight: 500, color: 'var(--jin)', border: '1px solid var(--jin)', background: 'rgba(138,111,63,0.06)' }}>{busy ? '发布中(训练+regen)…' : '发布为 DL 源 ⤓'}</div>
      <div style={{ marginTop: 5, fontSize: 9.5, color: 'var(--ink-3)', lineHeight: 1.4 }}>全市场 38 因子 PIT 训练 → 混进 v4(LGB 仍主导)→ 选股页徽章多一源(异步·分钟级)。</div>
    </div>
  );
}

// 数据源「数据体检」: 点一下真打后端 /data/probe, 显示该池真实覆盖(票数/交易日/字段/覆盖率)。
// 只读真查询(不写盘); 无后端 / 失败 → 诚实显红, 不谎报。仅 source 节点渲染(见 Node 体内注入)。
function SourceProbe({ node }) {
  const [r, setR] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async (e) => {
    e.stopPropagation();
    if (busy) return;
    setBusy(true); setR(null);
    try {
      const p = node.params || {};
      const body = { universe: _universeOf(p) };
      const s = (p.start || '').toString().trim(); if (s) body.start = s;
      const en = (p.end || '').toString().trim(); if (en) body.end = en;
      const fq = (p.freq || '').toString().trim(); if (fq) body.freq = fq;
      const res = await _post('/data/probe', body);
      setR(res || { ok: false, reason: '空响应' });
    } catch (err) { setR({ ok: false, reason: (err && err.message) || String(err) }); }
    setBusy(false);
  };
  return (
    <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)' }}>
      <div style={{ fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.4, marginBottom: 5 }} title="股票池可作全局默认；日期、基准和滚动重训必须通过数据连接或 context_source_id 明确传递。多个数据源不会自动猜选。">ℹ 日期 / 基准 / 重训需数据连接或明确引用</div>
      <div onPointerDown={e => e.stopPropagation()} onClick={run}
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5, height: 22, borderRadius: 6, cursor: busy ? 'default' : 'pointer', fontSize: 11, fontWeight: 500, color: busy ? 'var(--ink-3)' : 'var(--yin)', border: '1px solid ' + (busy ? 'var(--line)' : 'var(--yin)'), background: busy ? 'rgba(28,24,20,0.03)' : 'rgba(168,57,45,0.05)' }}>
        {busy ? '体检中…' : '数据体检 ✦'}
      </div>
      {r && (r.ok ? (
        <div style={{ marginTop: 6, fontSize: 10.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>
          <div className="mono"><b style={{ color: 'var(--ink)' }}>{r.n_codes_data}</b>/{r.n_codes} 只 · <b style={{ color: 'var(--ink)' }}>{r.n_dates}</b> 交易日 · 覆盖 {r.coverage != null ? Math.round(r.coverage * 100) + '%' : '—'}</div>
          <div className="mono" style={{ color: 'var(--ink-3)' }}>{r.date_min} → {r.date_max} · {r.freq}</div>
          <div title={(r.fields || []).join(', ')} style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>字段 {r.n_fields}: {(r.fields || []).join(' ')}</div>
        </div>
      ) : (
        <div style={{ marginTop: 6, fontSize: 10.5, color: 'var(--zhu)', lineHeight: 1.4 }}>体检失败: {r.reason}</div>
      ))}
    </div>
  );
}

// Bounded JSON authoring only; the server remains authoritative for each rule's grammar.
function wfParseHistoryBranches(text) {
  if (typeof text !== 'string' || text.length > 131072) throw new Error('分支 JSON 必须是文本且不超过128 KiB');
  let value;
  try { value = JSON.parse(text); } catch (_) { throw new Error('分支 JSON 语法无效，原配置未修改'); }
  if (!Array.isArray(value) || value.length < 1 || value.length > 8) throw new Error('需要1至8个筛选分支');
  value.forEach((branch, i) => {
    const selector = branch && branch.selector;
    if (!branch || typeof branch !== 'object' || Array.isArray(branch) || !selector || typeof selector !== 'object' || Array.isArray(selector)) throw new Error('分支' + (i + 1) + '缺少合法selector');
    if (selector.preset === 'ma_trend' && !Object.prototype.hasOwnProperty.call(selector, 'conditions')) return;
    if (Object.prototype.hasOwnProperty.call(selector, 'preset') || (selector.combine != null && selector.combine !== 'all') || !Array.isArray(selector.conditions) || selector.conditions.length < 1 || selector.conditions.length > 16) throw new Error('分支' + (i + 1) + '需包含combine=all的1至16条硬规则，或显式ma_trend模板');
    if (selector.conditions.some(rule => !rule || !['compare', 'range', 'strict_rise', 'max_consecutive'].includes(rule.type))) throw new Error('分支' + (i + 1) + '含不支持的规则类型');
  });
  return value;
}

function HistoryBranchesEditor({ value, onChange }) {
  const [editing, setEditing] = useState(false), [draft, setDraft] = useState(''), [error, setError] = useState('');
  const branches = Array.isArray(value) ? value : [];
  const summaries = branches.map(params => wfCustomScreenSummary({ type: 'data_screen', params }, [], []));
  const open = () => { setDraft(JSON.stringify(value || [], null, 2)); setError(''); setEditing(true); };
  const apply = () => { try { const parsed = wfParseHistoryBranches(draft); onChange(parsed); setEditing(false); } catch (e) { setError(e.message); } };
  return <div data-history-branches="true" onPointerDown={e => e.stopPropagation()} style={{ height: ROW * 8, display: 'flex', flexDirection: 'column', gap: 5, overflow: 'hidden', fontSize: 10, paddingTop: 4, boxSizing: 'border-box' }}>
    <div style={{ color: 'var(--dai)', fontWeight: 600 }}>{branches.length} 个分支 · 分支间取并集（OR）</div>
    <div style={{ color: 'var(--ink-3)', lineHeight: 1.4 }}>逐日执行全部规则；先合并去重再排名，不使用单日快照回填历史。</div>
    <div aria-label="历史筛选真实规则" onWheel={e => e.stopPropagation()} style={{ flex: 1, minHeight: 0, overflowY: 'auto', overscrollBehavior: 'contain', borderBlock: '1px solid var(--line)', padding: '3px 0' }}>
      {!branches.length && <div style={{ color: 'var(--zhu)' }}>尚未配置规则，不能运行。</div>}
      {summaries.map((summary, i) => <div key={i} style={{ marginBottom: 7 }}>
        <b>分支 {i + 1} · 全部满足（AND）</b>
        {summary ? <React.Fragment><div style={{ color: 'var(--ink-3)' }}>{summary.liquidity} · ST剔除 {summary.excludeST}</div>{summary.rules.map((rule, j) => <div key={j} title={rule.detail} style={{ paddingTop: 3, overflowWrap: 'anywhere' }}>{j + 1}. {rule.label}</div>)}</React.Fragment> : <React.Fragment><div style={{ color: 'var(--ink-3)' }}>模板/待校验配置 · 原样JSON，未补默认条件</div><pre style={{ fontSize: 9, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(branches[i], null, 2)}</pre></React.Fragment>}
      </div>)}
    </div>
    <button type="button" disabled={WW_READONLY} onClick={open} style={{ fontSize: 10, padding: '4px 7px', border: '1px solid var(--line)', borderRadius: 5, color: 'var(--ink-2)', background: 'transparent', cursor: 'pointer' }}>编辑规则 JSON</button>
    {editing && ReactDOM.createPortal(<div onPointerDown={e => e.stopPropagation()} style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(28,24,20,.35)', display: 'grid', placeItems: 'center' }}>
      <div role="dialog" aria-modal="true" aria-label="编辑历史筛选规则" style={{ width: 'min(760px,90vw)', maxHeight: '85vh', display: 'flex', flexDirection: 'column', gap: 10, padding: 18, background: 'var(--paper)', borderRadius: 12, boxShadow: '0 16px 48px rgba(0,0,0,.2)' }}>
        <b>历史逐日筛选 · 完整分支 JSON</b>
        <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>最多8分支、每支16条有界规则。仅解析JSON；服务端运行前再次校验。取消或解析失败保留原配置。</div>
        <textarea aria-label="分支JSON" spellCheck={false} value={draft} maxLength={131072} onChange={e => setDraft(e.target.value)} style={{ width: '100%', height: '48vh', minHeight: 160, boxSizing: 'border-box', resize: 'vertical', border: '1px solid var(--line)', borderRadius: 6, padding: 10, fontFamily: 'var(--mono)', fontSize: 11, background: 'var(--paper-2)', color: 'var(--ink)' }} />
        {error && <div role="alert" style={{ color: 'var(--zhu)', fontSize: 12 }}>{error}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}><button type="button" onClick={() => setEditing(false)}>取消</button><button type="button" disabled={WW_READONLY} onClick={apply}>应用 JSON</button></div>
      </div>
    </div>, document.body)}
  </div>;
}

function wfCustomScreenSummary(node, nodes, edges) {
  const p = (node && node.params) || {};
  const has = (o, k) => Object.prototype.hasOwnProperty.call(o || {}, k);
  const selector = p.selector && typeof p.selector === 'object' && !Array.isArray(p.selector) ? p.selector : null;
  if (!node || node.type !== 'data_screen' || !(has(selector, 'conditions') || has(p, 'conditions'))) return null;
  const raw = has(selector, 'conditions') ? selector.conditions : p.conditions;
  const conditions = Array.isArray(raw) ? raw : [];
  const customFilters = p.filters && typeof p.filters === 'object' && !Array.isArray(p.filters) ? p.filters : null;
  const f = customFilters || {};
  const amountWindow = customFilters ? (has(f, 'amount_window') ? f.amount_window : 20) : (has(p, 'amount_window') ? p.amount_window : 20);
  const amount = customFilters ? (has(f, 'min_avg_amount_yuan') ? f.min_avg_amount_yuan : 100000000) : (has(p, 'min_avg_amount_yi') ? p.min_avg_amount_yi * 100000000 : 100000000);
  const amountMin = has(f, 'amount_min_observations') ? f.amount_min_observations : amountWindow;
  const filterFlag = (key, fallback) => has(customFilters || p, key) ? (customFilters || p)[key] : fallback;
  const flagText = v => v === true ? '是' : v === false ? '否' : '无效配置';
  const sources = (edges || []).filter(e => e && e.to && e.from && e.to[0] === node.id && e.to[1] === 'src' && e.from[1] === 'data')
    .map(e => (nodes || []).find(n => n.id === e.from[0] && n.type === 'source')).filter(Boolean);
  const sourceEnd = sources.length === 1 ? String((sources[0].params || {}).end || '').trim() : '';
  const explicitAsOf = typeof p.as_of === 'string' ? p.as_of.trim() : '';
  const amountText = typeof amount === 'number' && Number.isFinite(amount) ? String(amount / 100000000) + ' 亿元' : '无效配置';
  return {
    rules: conditions.map((rule, i) => ({ id: String((rule && rule.id) || ('rule_' + (i + 1))),
      label: rule && typeof rule.label === 'string' && rule.label.trim() ? rule.label.trim() : String((rule && (rule.id || rule.type)) || ('规则 ' + (i + 1))),
      detail: JSON.stringify(rule) })),
    valid: Array.isArray(raw) && conditions.length > 0 && (!selector || !has(selector, 'combine') || selector.combine === 'all'),
    asOf: explicitAsOf || sourceEnd || (sources.length > 1 ? '数据源不明确，须校验' : '最新有效交易日（运行时确定）'),
    asOfSource: explicitAsOf ? '节点截止日' : sourceEnd ? '来自所连数据源' : '未固定截止日',
    topN: has(p, 'top_n') ? p.top_n : has(p, 'topN') ? p.topN : 5,
    liquidity: String(amountWindow) + ' 日均额 ≥ ' + amountText,
    observations: String(amountMin) + '/' + String(amountWindow) + ' 日有效成交额',
    excludeST: flagText(filterFlag('exclude_st', true)),
    requireTrade: flagText(filterFlag('require_asof_trade', true)),
    strictPIT: flagText(filterFlag('require_strict_metadata_pit', false)),
  };
}

function CustomScreenSummary({ summary }) {
  // Replace exactly the old template-parameter area: fixed node bounds and
  // unchanged port coordinates, with scrolling confined to the rule list.
  return <div data-screen-custom-summary="true" onPointerDown={e => e.stopPropagation()}
    style={{ height: SPECS.data_screen.params.length * ROW, boxSizing: 'border-box', display: 'flex', flexDirection: 'column', gap: 6, paddingTop: 5, overflow: 'hidden', fontSize: 10.5, lineHeight: 1.45 }}>
    <div style={{ flexShrink: 0, color: summary.valid ? 'var(--yin)' : 'var(--zhu)', fontWeight: 600 }}>
      {summary.valid ? '自定义硬条件 · 全部满足（AND）' : '自定义规则配置无效，请先校验'} · {summary.rules.length} 条
    </div>
    <div style={{ flexShrink: 0, color: 'var(--ink-2)', overflowWrap: 'anywhere' }}>
      <div title={summary.asOfSource}>截止：{summary.asOf} · 日线</div>
      <div>最多返回 {String(summary.topN)} 只 · {summary.liquidity}</div>
      <div style={{ color: 'var(--ink-3)', fontSize: 9.5 }}>{summary.observations} · 剔 ST：{summary.excludeST}</div>
      <div style={{ color: 'var(--ink-3)', fontSize: 9.5 }}>截止日须成交：{summary.requireTrade} · 严格 PIT：{summary.strictPIT}</div>
    </div>
    <div role="list" aria-label="全部筛选规则" tabIndex={0} onWheel={e => e.stopPropagation()}
      style={{ flex: '1 1 0', minHeight: 0, overflowY: 'auto', overscrollBehavior: 'contain', borderTop: '1px solid var(--line)', borderBottom: '1px solid var(--line)', padding: '5px 2px' }}>
      {summary.rules.map((rule, index) => <div role="listitem" key={rule.id + ':' + index} title={rule.detail}
        style={{ display: 'flex', gap: 5, padding: '3px 0', overflowWrap: 'anywhere', color: 'var(--ink-2)' }}>
        <span className="mono" style={{ color: 'var(--ink-3)', flexShrink: 0 }}>{index + 1}.</span><span>{rule.label}</span>
      </div>)}
    </div>
    <div style={{ flexShrink: 0, fontSize: 9.5, color: 'var(--ink-3)' }}>只读摘要 · 自定义规则由帷幄／完整图配置，旧模板控件不适用。</div>
  </div>;
}

function wfParamSelectValue(nodeType, paramId, value) {
  // Agent graphs may use JSON booleans; keep the persisted graph untouched.
  // Explicit user edits still use the existing 是/否 option values understood by both executors.
  if (nodeType === 'source' && paramId === 'wf_refit' && typeof value === 'boolean') return value ? '是' : '否';
  return value;
}

function Node({ node, sel, onDrag, onStartWire, onParam, onDel, onSel, status, nodes, edges, onNotify }) {
  const spec = SPECS[node.type]; const cat = CAT[spec.cat];
  const customScreen = wfCustomScreenSummary(node, nodes, edges);
  const rows = rowsOf(spec).filter(r => !customScreen || r.kind !== 'param');
  const orthManaged = node.type === 'analysis' && (edges || []).some(e => {
    if (!e || !e.to || !e.from || e.to[0] !== node.id || e.to[1] !== 'factor') return false;
    const upstream = (nodes || []).find(n => n.id === e.from[0]);
    return upstream && upstream.type === 'orthogonalize';
  });
  const governedAudit = node.type === 'overfit' && !!String((node.params || {}).audit_context_id || '').trim();
  return (
    <div className="nodrag" onPointerDown={(e) => { e.stopPropagation(); onSel(); }}
      style={{ position: 'absolute', left: node.x, top: node.y, width: W, background: 'var(--paper)', border: '1px solid ' + (sel ? cat.c : 'var(--line)'), borderRadius: 9, boxShadow: sel ? '0 3px 16px rgba(28,24,20,0.16)' : '0 2px 10px rgba(28,24,20,0.1)' }}>
      <div onPointerDown={onDrag}
        style={{ display: 'flex', alignItems: 'center', gap: 7, height: HEADER, padding: '0 11px', background: cat.hd, borderRadius: '8px 8px 0 0', cursor: 'grab' }}>
        <span title={status === 'error' ? '此节点执行失败' : ''} style={{ width: 8, height: 8, borderRadius: '50%', background: status === 'running' ? 'var(--jin)' : status === 'done' ? 'var(--dai)' : status === 'error' ? 'var(--zhu)' : cat.c, animation: status === 'running' ? 'pulse 1s infinite' : 'none' }} />
        <span className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{spec.title}</span>
        <span onPointerDown={e => { e.stopPropagation(); onDel(); }} style={{ marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 12, cursor: 'pointer' }}>✕</span>
      </div>
      <div style={{ padding: PAD + 'px 11px' }}>
        {rows.map((r, i) => {
          if (r.kind === 'in') return (
            <div key={i} data-inport={node.id + '::' + r.port.id} style={{ position: 'relative', display: 'flex', alignItems: 'center', height: ROW, fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--ink-2)' }}>
              <span style={{ position: 'absolute', left: -17, width: 10, height: 10, borderRadius: '50%', background: 'var(--paper)', border: '2px solid var(--ink-2)' }} />{r.port.label}
            </div>
          );
          if (r.kind === 'out') return (
            <div key={i} style={{ position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'flex-end', height: ROW, fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--ink-2)' }}>
              {r.port.label}
              <span onPointerDown={e => onStartWire(r.port.id, e)} title="拖出连线"
                style={{ position: 'absolute', right: -17, width: 11, height: 11, borderRadius: '50%', background: 'var(--paper)', border: '2px solid ' + cat.c, cursor: 'crosshair' }} />
            </div>
          );
          const p = r.param; const v = node.params[p.id] !== undefined ? node.params[p.id] : p.value;
          if (node.type === 'history_screen' && p.id === 'branches') return <HistoryBranchesEditor key={i} value={v} onChange={branches => onParam('branches', branches)} />;
          const paramDisabled = (orthManaged && p.id === 'neutral')
            || (governedAudit && ['confidence', 'pbo_max', 'n_blocks'].includes(p.id));
          // 公式/因子库节点的「表达式」特例: 整宽、随内容长高的文本域(普通小输入框放不下长公式)。
          if ((node.type === 'formula' || node.type === 'factorlib') && p.id === 'expr') {
            return (
              <div key={i} style={{ paddingTop: 2, paddingBottom: 2 }}>
                <div style={{ fontSize: 11, color: 'var(--ink-2)', marginBottom: 3 }}>{p.label}</div>
                <textarea value={v} onChange={e => onParam(p.id, e.target.value)} onPointerDown={e => e.stopPropagation()}
                  spellCheck={false} rows={Math.min(6, Math.max(2, Math.ceil((String(v || '').length || 1) / 24)))}
                  style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--line)', borderRadius: 5, padding: '4px 7px', fontFamily: 'var(--mono)', fontSize: 10.5, color: 'var(--ink)', background: 'rgba(28,24,20,0.03)', resize: 'vertical', lineHeight: 1.45 }} />
              </div>
            );
          }
          return (
            <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, height: ROW }}>
              <span title={p.hint || p.label} style={{ fontSize: 11, color: 'var(--ink-2)' }}>{p.label}</span>
              {p.type === 'step' ? (
                <span style={{ display: 'inline-flex', alignItems: 'center', border: '1px solid var(--line)', borderRadius: 6, overflow: 'hidden', pointerEvents: paramDisabled ? 'none' : 'auto', opacity: paramDisabled ? 0.45 : 1 }}>
                  <span onPointerDown={e => { e.stopPropagation(); onParam(p.id, +(v - p.step).toFixed(p.dec || 0)); }} style={{ padding: '2px 6px', color: 'var(--ink-3)', cursor: 'pointer', fontSize: 10 }}>◀</span>
                  <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink)', padding: '2px 8px', borderLeft: '1px solid var(--line)', borderRight: '1px solid var(--line)', minWidth: 36, textAlign: 'right' }}>{p.dec ? Number(v).toFixed(p.dec) : v}</span>
                  <span onPointerDown={e => { e.stopPropagation(); onParam(p.id, +(v + p.step).toFixed(p.dec || 0)); }} style={{ padding: '2px 6px', color: 'var(--ink-3)', cursor: 'pointer', fontSize: 10 }}>▶</span>
                </span>
              ) : p.type === 'select' ? (
                <select value={wfParamSelectValue(node.type, p.id, v)} disabled={paramDisabled} onChange={e => onParam(p.id, e.target.value)} onPointerDown={e => e.stopPropagation()}
                  style={{ width: 96, border: '1px solid var(--line)', borderRadius: 5, padding: '3px 6px', fontFamily: 'var(--sans)', fontSize: 10.5, color: 'var(--ink)', background: 'rgba(28,24,20,0.03)', appearance: 'none', WebkitAppearance: 'none', cursor: paramDisabled ? 'not-allowed' : 'pointer', opacity: paramDisabled ? 0.45 : 1 }}>
                  {p.options.map(o => { const ov = (o && typeof o === 'object') ? o.value : o; const ol = (o && typeof o === 'object') ? o.label : o; return <option key={ov} value={ov}>{ol}</option>; })}
                </select>
              ) : (
                <input value={v} disabled={paramDisabled} onChange={e => onParam(p.id, e.target.value)} onPointerDown={e => e.stopPropagation()}
                  style={{ width: 92, border: '1px solid var(--line)', borderRadius: 5, padding: '2px 7px', fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--ink)', background: 'rgba(28,24,20,0.03)', textAlign: 'right', opacity: paramDisabled ? 0.45 : 1 }} />
              )}
            </div>
          );
        })}
        {customScreen ? <CustomScreenSummary summary={customScreen} /> : null}
        {node.type === 'source' ? <SourceProbe node={node} /> : null}
        {node.type === 'formula' ? <FormulaPanel node={node} onParam={onParam} /> : null}
        {node.type === 'factorlib' ? <FactorLibPanel node={node} onParam={onParam} /> : null}
        {node.type === 'model' ? <ModelLibPanel node={node} onParam={onParam} /> : null}
        {node.type === 'validate' ? <ModelLibPanel node={node} onParam={onParam} /> : null}
        {(node.type === 'xgb' || node.type === 'lgbm' || node.type === 'rf') ? <PromoteModelPanel node={node} nodes={nodes} edges={edges} onNotify={onNotify} /> : null}
        {node.type === 'lstm' ? <PublishDlPanel node={node} nodes={nodes} edges={edges} onNotify={onNotify} /> : null}
        {governedAudit ? <div title={String(node.params.audit_context_id)} style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)', fontSize: 9, color: 'var(--dai)', lineHeight: 1.45 }}>ⓘ 已绑定服务端 TrialLedger；置信度、PBO 与分块由 Governor 固定策略接管</div> : null}
        {node.type === 'analysis' ? <div title="单因子(直连公式 / 单特征 Spearman·PCA)→ 分组/调仓/方向 经壳内 /factor/report2 真生效;经多模型或多因子合成的复合因子,报告已在上游按其设置算好,此处透传。" style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)', fontSize: 9, color: orthManaged ? '#9a6b16' : 'var(--ink-3)', lineHeight: 1.4 }}>{orthManaged ? 'ⓘ 上游正交化已接管暴露控制；旧“中性化”已置灰，不二次残差化' : 'ⓘ 分组/调仓/方向:单因子时生效;多因子复合为透传'}</div> : null}
      </div>
    </div>
  );
}

function Rail({ zoom, fit, onRun, running, onDel, onDup, onUndo, onRedo, canUndo, canRedo, onSave, onExport, onImport, hasSel }) {
  const btn = { width: 32, height: 32, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--ink-2)', cursor: 'pointer', fontSize: 14 };
  const cbtn = { ...btn, fontFamily: 'var(--serif)', fontSize: 14.5, fontWeight: 500 };
  const dim = { opacity: 0.32, pointerEvents: 'none' };
  const hov = e => e.currentTarget.style.background = 'rgba(28,24,20,0.06)';
  const out = e => e.currentTarget.style.background = 'transparent';
  return (
    <div style={{ position: 'absolute', top: 16, right: 16, display: 'flex', flexDirection: 'column', gap: 6, background: 'rgba(241,234,217,0.85)', border: '1px solid var(--line)', borderRadius: 12, padding: 8, boxShadow: '0 2px 12px rgba(28,24,20,0.1)' }}>
      <div style={{ ...btn, ...(canUndo ? {} : dim) }} onMouseEnter={hov} onMouseLeave={out} onClick={onUndo} title="撤销 (⌘Z)">↶</div>
      <div style={{ ...btn, ...(canRedo ? {} : dim) }} onMouseEnter={hov} onMouseLeave={out} onClick={onRedo} title="重做 (⌘⇧Z)">↷</div>
      <div style={{ ...cbtn, ...(hasSel ? {} : dim) }} onMouseEnter={hov} onMouseLeave={out} onClick={onDup} title="复制选中 (⌘D)">复</div>
      <div style={{ height: 1, background: 'var(--line)', margin: '2px 4px' }} />
      <div style={btn} onMouseEnter={hov} onMouseLeave={out} onClick={() => zoom(0.1)} title="放大">＋</div>
      <div style={btn} onMouseEnter={hov} onMouseLeave={out} onClick={() => zoom(-0.1)} title="缩小">－</div>
      <div style={btn} onMouseEnter={hov} onMouseLeave={out} onClick={fit} title="复位">⤢</div>
      <div style={{ height: 1, background: 'var(--line)', margin: '2px 4px' }} />
      <div style={cbtn} onMouseEnter={hov} onMouseLeave={out} onClick={onSave} title="保存到本地 (⌘S)">存</div>
      <div style={cbtn} onMouseEnter={hov} onMouseLeave={out} onClick={onExport} title="导出 JSON 文件">出</div>
      <div style={cbtn} onMouseEnter={hov} onMouseLeave={out} onClick={onImport} title="导入 JSON 文件">入</div>
      <div style={{ height: 1, background: 'var(--line)', margin: '2px 4px' }} />
      <div style={{ ...cbtn, ...(hasSel ? {} : dim) }} onMouseEnter={hov} onMouseLeave={out} onClick={onDel} title="删除选中 (Del)">删</div>
      <div aria-disabled={running ? 'true' : 'false'}
        style={{ ...btn, background: 'var(--yin)', color: 'var(--paper)', ...(running ? dim : {}) }}
        onClick={running ? undefined : onRun} title={running ? '工作流运行中' : '运行'}>▶</div>
    </div>
  );
}

// ───────── 小地图 ─────────
function Minimap({ nodes }) {
  if (!nodes.length) return null;
  const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
  const minX = Math.min(...xs) - 50, minY = Math.min(...ys) - 50;
  const maxX = Math.max(...xs) + W + 50, maxY = Math.max(...ys) + 220;
  const bw = 158, bh = 104; const s = Math.min(bw / (maxX - minX), bh / (maxY - minY));
  return (
    <div style={{ position: 'absolute', bottom: 14, right: 14, width: bw, height: bh, background: 'rgba(241,234,217,0.92)', border: '1px solid var(--line)', borderRadius: 8, overflow: 'hidden', boxShadow: '0 2px 10px rgba(28,24,20,0.12)' }}>
      <span className="mono" style={{ position: 'absolute', top: 3, left: 6, fontSize: 8, color: 'var(--ink-3)', letterSpacing: '.1em' }}>MAP</span>
      {nodes.map(n => (
        <div key={n.id} style={{ position: 'absolute', left: (n.x - minX) * s, top: (n.y - minY) * s, width: Math.max(6, W * s), height: Math.max(4, nodeHeight(SPECS[n.type]) * s), background: CAT[SPECS[n.type].cat].c, opacity: 0.6, borderRadius: 2 }} />
      ))}
    </div>
  );
}

// ───────── 结果抽屉 (运行后) ─────────
function RCard({ title, children }) {
  return <div style={{ border: '1px solid var(--line-soft)', borderRadius: 10, background: 'rgba(255,255,255,0.5)', padding: '11px 13px', minWidth: 0 }}>
    <div className="serif" style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink-1)', marginBottom: 9 }}>{title}</div>{children}</div>;
}
// #9 报告库弹窗(portal):列出 /report/list,可「重看」(载回抽屉)/「删除」。落盘在 guanlan_v2/reports/store。
// W8b AI 闭环弹窗:展示 propose→run→critique→improve 各轮(因子+真指标+AI诊断),可应用任一轮。
function AILoopModal({ loop, onApply, onClose }) {
  const rounds = (loop && loop.rounds) || [];
  const fIC = v => (v == null || v !== v) ? '—' : (v >= 0 ? '+' : '') + (+v).toFixed(4);
  const fSh = v => (v == null || v !== v) ? '—' : (+v).toFixed(2);
  const VL = { robust: '稳健', degraded: '衰减', overfit: '疑似过拟合', insufficient: '期数不足', na: '不适用' };
  const VC = { robust: 'rgb(74,107,92)', degraded: '#b8860b', overfit: 'var(--zhu)' };
  let best = -1, bestIC = -Infinity;
  rounds.forEach((r, i) => { const v = r.metrics && r.metrics.rank_ic; if (v != null && v === v && v > bestIC) { bestIC = v; best = i; } });
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(28,24,20,0.45)', zIndex: 60, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div onClick={e => e.stopPropagation()} style={{ width: 780, maxWidth: '92vw', maxHeight: '86vh', overflowY: 'auto', background: 'var(--paper)', borderRadius: 14, boxShadow: '0 20px 60px rgba(0,0,0,0.3)', padding: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
          <span className="serif" style={{ fontSize: 16, fontWeight: 700 }}>AI 闭环 ✦ · 生成 → 运行 → 自评 → 改进</span>
          <span style={{ marginLeft: 'auto', cursor: 'pointer', fontSize: 18, color: 'var(--ink-3)' }} onClick={onClose}>✕</span>
        </div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', marginBottom: 14 }}>目标:{loop.goal} · {loop.running ? <span style={{ color: 'var(--zhu)' }}>{loop.step} ⟳</span> : <span>{loop.step}</span>}</div>
        {rounds.map((r, i) => {
          const m = r.metrics || {};
          return (
            <div key={i} style={{ border: '1px solid ' + (i === best && rounds.length > 1 ? 'var(--zhu-soft)' : 'var(--line)'), borderRadius: 10, padding: '12px 14px', marginBottom: 11, background: (i === best && rounds.length > 1) ? 'rgba(168,57,45,0.04)' : 'var(--paper)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="serif" style={{ fontSize: 13, fontWeight: 600 }}>{'第 ' + (i + 1) + ' 轮' + (i === 0 ? ' · 初始' : ' · 改进')}</span>
                {i === best && rounds.length > 1 && <span className="mono" style={{ fontSize: 8, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 4, padding: '1px 6px' }}>RankIC 最佳</span>}
                {r.failed && <span className="mono" title={r.error} style={{ fontSize: 8, color: 'var(--paper)', background: 'var(--zhu)', borderRadius: 4, padding: '1px 6px' }}>运行失败</span>}
                <span style={{ marginLeft: 'auto' }}><span onClick={() => onApply(r.graph)} className="serif" style={{ fontSize: 11.5, color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 7, padding: '4px 10px', cursor: 'pointer' }}>应用此工作流</span></span>
              </div>
              <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)', margin: '7px 0' }}>因子:{r.factor}</div>
              <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
                <span className="mono" style={{ fontSize: 11 }}>RankIC <b style={{ color: (m.rank_ic >= 0 ? 'rgb(74,107,92)' : 'var(--zhu)') }}>{fIC(m.rank_ic)}</b></span>
                <span className="mono" style={{ fontSize: 11 }}>Sharpe <b>{fSh(m.sharpe)}</b></span>
                {m.oos_verdict && <span className="mono" style={{ fontSize: 11 }}>样本外 <b style={{ color: VC[m.oos_verdict] || 'var(--ink-3)' }}>{VL[m.oos_verdict] || m.oos_verdict}</b></span>}
              </div>
              {r.failed && <div className="mono" style={{ fontSize: 10.5, color: 'var(--zhu)', marginTop: 7, paddingTop: 7, borderTop: '1px dashed var(--zhu-soft)' }}>⚠ 本轮未产出结果:{r.error || '运行出错'}</div>}
              {i > 0 && <div className="serif" style={{ fontSize: 11.5, color: 'var(--ink-1)', marginTop: 8, paddingTop: 8, borderTop: '1px dashed var(--line)' }}><b style={{ color: 'var(--yin)' }}>AI 诊断 →</b> {r.diag}</div>}
            </div>
          );
        })}
        {loop.running && <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', padding: 10 }}>⟳ {loop.step}</div>}
        {!loop.running && rounds.length === 0 && <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', textAlign: 'center', padding: 16 }}>无结果</div>}
      </div>
    </div>
  );
}

function ReportLibModal({ onReopen, onClose }) {
  const [list, setList] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => { _get(_reportPath('/report/list')).then(r => setList((r && r.reports) || [])).catch(() => setList([])); };
  useEffect(() => { load(); }, []);
  const del = (id) => { if (busy || WW_READONLY) return; setBusy(true); _post('/report/delete', _withWorkspace({ id })).then(() => { load(); setBusy(false); }).catch(() => setBusy(false)); };
  const open = (rec) => { _get(_reportPath('/report/get/' + encodeURIComponent(rec.id))).then(full => { if (full && full.ok) onReopen(full); }).catch(() => {}); };
  const METH = { daily_screen_backtest: '日频模拟调仓', report2: '因子分析', backtest_vector: '向量化回测', portfolio_build: '组合构建', tsic: '个股时序IC', event: '事件研究', relstat: '关系稳定度', overfit_audit: '过拟合审计' };
  return ReactDOM.createPortal(
    <div onPointerDown={onClose} style={{ position: 'fixed', inset: 0, zIndex: 50, background: 'rgba(28,24,20,0.32)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div onPointerDown={e => e.stopPropagation()} style={{ width: 620, maxHeight: '82%', background: 'var(--paper)', border: '1px solid var(--ink)', borderRadius: 14, display: 'flex', flexDirection: 'column', overflow: 'hidden', boxShadow: '0 12px 40px rgba(28,24,20,0.25)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '14px 18px 11px', borderBottom: '1px solid var(--line-soft)' }}>
          <span className="serif" style={{ fontSize: 16, fontWeight: 600 }}>📁 报告库</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{list === null ? '加载中' : (list.length + ' 份 · ' + (WW_WORKSPACE_ID ? '当前研究空间' : '全局报告库'))}</span>
          <span onClick={onClose} style={{ marginLeft: 'auto', fontSize: 17, color: 'var(--ink-3)', cursor: 'pointer' }}>✕</span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '10px 14px' }}>
          {list === null ? <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', padding: 20 }}>加载中…</div>
            : !list.length ? <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', padding: 20, textAlign: 'center' }}>暂无报告 —— 运行出结果后,在结果抽屉点「↧ 导出报告」存入。</div>
            : list.map(r => (
              <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 11px', borderBottom: '1px solid var(--line-soft)' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="serif" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', display: 'flex', alignItems: 'center', gap: 7 }}>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.name}</span>
                    {r.has_graph && <span className="mono" title={'重看会铺回工作流「' + (r.workflow_name || '') + '」的图'} style={{ flexShrink: 0, fontSize: 9, fontWeight: 400, color: 'var(--yin)', background: 'rgba(168,57,45,0.08)', border: '1px solid var(--zhu-soft)', borderRadius: 5, padding: '1px 6px' }}>↗ {r.workflow_name || '工作流'}</span>}
                  </div>
                  <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 2 }}>{(METH[r.method] || r.method || '—')} · {r.universe || '—'} · {r.ts} · {r.method === 'tsic'
                    ? <span>Pearson-IC {_n2((r.kpi || {}).mean_pearson, 3)} · ICIR {_n2((r.kpi || {}).mean_icir, 2)} · 择时Sh {_n2((r.kpi || {}).pool_sharpe, 2)}</span>
                    : (r.method === 'overfit_audit'
                        ? <span>{(r.kpi || {}).audit_status || 'INCOMPLETE'} · PSR {_pct((r.kpi || {}).psr, 1)} · DSR {_pct((r.kpi || {}).dsr, 1)} · PBO {_pct((r.kpi || {}).pbo, 1)}</span>
                        : <span>RankIC {_n2((r.kpi || {}).rank_ic, 3)} · 年化 {_pct((r.kpi || {}).ann_return, 1)}</span>)}</div>
                </div>
                <span onClick={() => open(r)} className="serif" style={{ fontSize: 11.5, color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 7, padding: '5px 10px', cursor: 'pointer', whiteSpace: 'nowrap' }}>重看</span>
                {!WW_READONLY && <span onClick={() => del(r.id)} style={{ fontSize: 11.5, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 9px', cursor: busy ? 'default' : 'pointer', whiteSpace: 'nowrap' }}>删除</span>}
              </div>
            ))}
        </div>
      </div>
    </div>,
    document.body
  );
}

// 指标文案按当前终端报告区分；分析收益不冒充交易成本后的回测净收益。
const _KPI_TIP = {
  'RankIC': '因子值与未来收益的截面秩相关均值;>0 正向有效,绝对值越大越强',
  'ICIR': 'RankIC 均值 ÷ RankIC 标准差;IC 的稳定性,越大越稳',
  'IC胜率': 'RankIC 为正的期数占比;>50% 方向偏稳',
  'IC-t值': 'RankIC 的 t 统计量;|t|>2 视为显著',
  'Sharpe': '年化收益 ÷ 年化波动;风险调整后收益,>1 优秀',
  'Sortino': '年化收益 ÷ 年化下行波动(只罚亏损);比 Sharpe 更看亏损风险',
  '最大回撤': '净值从历史最高点的最大跌幅',
  'Calmar': '年化收益 ÷ |最大回撤|;越大越好',
  '信息比率': '年化超额(对标沪深300)÷ 跟踪误差;主动管理能力',
  '组合胜率': '盈利调仓期占比',
  '换手率': '每期平均双边换手率',
  '总成本': '区间累计交易成本拖累(佣金+印花税+滑点)',
  '单调性': '十分位组序与组收益的秩相关;越接近 ±1 因子越单调可信',
  '年化(毛)': '未扣交易成本的年化收益',
};
const _kpiTip = (l, isBacktest = false) => {
  if (String(l).indexOf('年化') === 0) {
    if (!isBacktest) return '因子分析的未扣交易成本年化指示值，不是实际净回测收益';
    if (l === '年化(净)') return '扣除已配置交易成本后的回测年化收益(净)，不代表实盘收益';
    return _KPI_TIP[l] || '回测报告的年化收益；扣费口径以报告字段为准';
  }
  return _KPI_TIP[l] || '';
};
function wfResultPresentation(result) {
  const isBacktest = !!(result && result.ok !== false && result.method === 'backtest_vector');
  const pf = (result && result.portfolio) || {};
  return {
    isBacktest,
    metricTitle: isBacktest ? '回测指标' : '分析指标 · 非净回测',
    annualLabel: isBacktest ? '年化' + (pf.net_ann != null ? '(净)' : '') : '年化(指示)',
    diagnostic: isBacktest ? '引擎回测 · /backtest/vector' : '因子分析 · 未扣成本的多空指示值（非净回测）',
    resultTitle: isBacktest ? '运行完成 · 回测结果' : '运行完成 · 因子分析结果',
    note: isBacktest
      ? '注:当前展示向量化回测结果；净指标计入已配置的交易成本，毛指标未扣成本。是否可交易仍受数据覆盖及执行约束影响，不代表实盘收益。'
      : '注:年化 / Sharpe / 回撤为多空分析组合未扣交易成本的指示值；短窗 / 高频会放大失真，不是实际净回测收益，也不证明下游回测节点执行成功。',
  };
}

function _screenSeriesUnit(expr) {
  if (!expr || typeof expr !== 'object') return 'number';
  if (expr.type === 'bias') return 'ratio';
  if (expr.field === 'returns') return 'ratio';
  if (expr.field === 'amount') return 'yuan';
  if (expr.field === 'volume') return 'shares';
  return 'number';
}
function _screenFormatValue(value, expr) {
  if (value == null || !Number.isFinite(+value)) return '—';
  const n = +value, unit = _screenSeriesUnit(expr);
  if (unit === 'ratio') return (n * 100).toFixed(3) + '%';
  if (unit === 'yuan') return Math.abs(n) >= 1e8 ? (n / 1e8).toFixed(3) + '亿' : (Math.abs(n) >= 1e4 ? (n / 1e4).toFixed(3) + '万' : n.toFixed(2) + '元');
  if (unit === 'shares') return Math.abs(n) >= 1e4 ? (n / 1e4).toFixed(3) + '万股' : n.toFixed(0) + '股';
  return n.toFixed(4);
}
function _screenFormatExactShares(value) {
  if (value == null || !Number.isFinite(+value)) return '—';
  const n = +value, negative = n < 0, abs = Math.abs(n);
  let raw = Number.isInteger(abs) ? abs.toFixed(0) : abs.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
  const parts = raw.split('.');
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return (negative ? '-' : '') + parts.join('.') + '股';
}
function _screenFormatEvidenceValues(values, exprs) {
  const formatted = values.map((value, i) => _screenFormatValue(value, exprs[i]));
  if (!exprs.length || !exprs.every(expr => _screenSeriesUnit(expr) === 'shares')) return formatted;
  const numbers = values.map(value => value == null || !Number.isFinite(+value) ? null : +value);
  let collision = false;
  for (let i = 0; i < numbers.length && !collision; i++) {
    for (let j = i + 1; j < numbers.length; j++) {
      if (numbers[i] != null && numbers[j] != null && numbers[i] !== numbers[j] && formatted[i] === formatted[j]) {
        collision = true;
        break;
      }
    }
  }
  return collision ? values.map(_screenFormatExactShares) : formatted;
}
function _screenEvidenceValue(rule, ev) {
  const r = rule || {}, e = ev || {};
  if (r.type === 'range') {
    const values = _screenFormatEvidenceValues([e.value, e.min, e.max], [r.value, r.value, r.value]);
    return values[0] + ' ∈ [' + values[1] + ', ' + values[2] + ']';
  }
  if (r.type === 'max_consecutive') return '最大连续 ' + (e.max_streak == null ? '—' : e.max_streak) + ' 日 / 允许 ' + (e.allowed == null ? '—' : e.allowed) + ' 日';
  if (r.type === 'strict_rise') return '变化 ' + (Array.isArray(e.deltas) ? e.deltas.map(x => _screenFormatValue(x, r.series)).join(' / ') : '—');
  if (r.type === 'compare') {
    const values = _screenFormatEvidenceValues([e.left, e.right], [r.left, r.right]);
    return values[0] + ' ' + (e.op || r.op || '') + ' ' + values[1];
  }
  return '规则证据已记录';
}

function wfDailyScreenView(result) {
  const r = result || {}, summary = r.summary || {}, diagnostics = r.diagnostics || {};
  const finite = value => value != null && value !== '' && Number.isFinite(+value) ? +value : null;
  const nav = (((r.portfolio || {}).nav_series) || []).filter(row => Array.isArray(row) && row.length > 1 && finite(row[1]) != null).map(row => [String(row[0]), +row[1]]);
  const strings = value => Array.isArray(value) ? value.map(x => typeof x === 'string' ? x : JSON.stringify(x)).filter(Boolean) : [];
  return {
    summary, diagnostics, nav,
    metrics: [
      ['净累计收益', finite(summary.total_return), 'percent'], ['净年化收益', finite(summary.ann_return), 'percent'],
      ['最大回撤', finite(summary.max_drawdown), 'percent'], ['日净值 Sharpe', finite(summary.sharpe), 'number'],
      ['平均现金仓位', finite(summary.average_cash_weight), 'percent'], ['日均换手', finite(summary.turnover), 'percent'],
      ['总交易成本(元)', finite(summary.costs_paid), 'money'], ['实际成交笔数', finite(summary.trade_count), 'integer'],
    ],
    warnings: Array.from(new Set([...strings(r.warnings), ...strings(r._warnings), ...strings(r._node_errors)])),
    assumptions: strings(r.assumptions),
    staleDays: finite(diagnostics.stale_position_days),
    unresolved: Array.isArray(diagnostics.final_unresolved_positions) ? diagnostics.final_unresolved_positions : [],
    daily: (Array.isArray(r.daily) ? r.daily : []).slice(-20),
    candidates: (Array.isArray(r.candidates) ? r.candidates : []).slice(-20),
    trades: (Array.isArray(r.trades) ? r.trades : []).slice(-30),
  };
}

function DailyScreenResults({ result, expanded, setExpanded, onClose, onExport }) {
  // Preserve old receipts as evidence, without presenting unvalidated returns.
  if (!result.price_quality || result.price_quality.performance_ready !== true) return <div data-daily-screen-blocked="true" role="alert" style={{ position: 'absolute', left: 0, right: 0, bottom: 0, minHeight: 190, padding: 24, background: 'var(--paper)', borderTop: '2px solid var(--zhu)', zIndex: 8 }}>
    <button type="button" aria-label="关闭结果" onClick={onClose} style={{ float: 'right' }}>✕</button>
    <h3>历史数据尚未通过价格质量核验</h3>
    <p>该记录缺少通过验证的复权来源与价格质量凭证，仅保留为诊断证据；收益曲线、绩效指标与报告导出已隐藏，不能用于策略排名。</p>
    <p style={{ color: 'var(--ink-3)' }}>本轮发现历史 OHLC 矛盾及复权因子缺口。请修复并验证研究行情快照后，以相同规则重新运行；不会删除旧记录，也不会自动下单。</p>
  </div>;
  const view = wfDailyScreenView(result), sm = view.summary, dg = view.diagnostics;
  const fmt = (value, kind = 'number') => value == null || value === '' || !Number.isFinite(+value) ? '—' : kind === 'percent' ? (+value * 100).toFixed(2) + '%' : kind === 'integer' ? String(value) : (+value).toLocaleString('zh-CN', { maximumFractionDigits: kind === 'money' ? 2 : 3 });
  const cell = { padding: '6px 8px', borderBottom: '1px solid var(--line-soft)', textAlign: 'left', whiteSpace: 'nowrap', fontSize: 10 };
  const nav = view.nav, min = Math.min(1, ...nav.map(p => p[1])), max = Math.max(1, ...nav.map(p => p[1])), span = max - min || .01;
  const y = v => 122 - ((v - min) / span) * 102;
  return <div data-daily-screen-result="true" style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: expanded ? 'calc(100vh - 90px)' : 390, background: 'var(--paper)', borderTop: '1px solid var(--ink)', boxShadow: '0 -12px 32px rgba(28,24,20,.12)', zIndex: 8, display: 'flex', flexDirection: 'column' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '10px 18px', borderBottom: '1px solid var(--line-soft)' }}>
      <b className="serif" style={{ fontSize: 14 }}>日频模拟调仓 · {result._label || result.label || '历史规则验证'}</b>
      <span style={{ color: 'var(--ink-3)', fontSize: 10 }}>{nav.length ? nav[0][0] + ' → ' + nav[nav.length - 1][0] : '尚无有效净值'}</span>
      <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}><button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? '收起' : '展开'}</button><button type="button" onClick={onExport}>导出报告</button><button type="button" aria-label="关闭结果" onClick={onClose}>✕</button></span>
    </div>
    <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '12px 18px' }}>
      <div style={{ color: '#2f6072', background: 'rgba(47,96,114,.06)', border: '1px solid rgba(47,96,114,.2)', borderRadius: 6, padding: '7px 9px', fontSize: 11, lineHeight: 1.55, marginBottom: 10 }}>研究模拟，不会下单。收盘筛选 → 次日开盘尝试成交；固定槽位未用部分保留现金。净指标计入已配置费税与滑点，不按未来收益是否有值换票。没有手数、最低佣金、冲击成本或涨跌停排队成交保证。</div>
      {(view.staleDays > 0 || view.unresolved.length > 0) && <div role="alert" style={{ color: '#9a6b16', border: '1px dashed #9a6b16', padding: '7px 9px', borderRadius: 6, marginBottom: 10, fontSize: 11 }}>存在陈旧估值：{fmt(view.staleDays, 'integer')} 个持仓日；期末 {view.unresolved.length} 个缺价／待退出持仓。缺退出行情时保留持仓，不虚构卖出；净值可能受旧标记价影响，期末不是强制清仓收益。</div>}
      {view.warnings.length > 0 && <div role="alert" style={{ color: '#9a6b16', fontSize: 11, lineHeight: 1.5, marginBottom: 10 }}>{view.warnings.join(' · ')}</div>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(115px,1fr))', gap: 8, marginBottom: 12 }}>{view.metrics.map(([label, value, kind]) => <div key={label} style={{ border: '1px solid var(--line-soft)', borderRadius: 6, padding: '7px 9px' }}><div style={{ color: 'var(--ink-3)', fontSize: 10 }}>{label}</div><div className="mono" style={{ marginTop: 4, fontSize: 16 }}>{fmt(value, kind)}</div></div>)}</div>
      <div style={{ color: 'var(--ink-3)', fontSize: 10, marginBottom: 10 }}>账户盈利日占比 {fmt(sm.daily_win_rate, 'percent')}（不是选股命中率） · 完整平仓回合 {fmt(sm.closed_position_count, 'integer')} / 胜率 {fmt(sm.closed_position_win_rate, 'percent')} · 入场受阻 {fmt(dg.missing_or_blocked_entry_attempts, 'integer')} · 卖出受阻 {fmt(dg.blocked_sale_attempts, 'integer')}</div>
      {nav.length > 1 && <div style={{ border: '1px solid var(--line-soft)', borderRadius: 8, padding: 9, marginBottom: 12 }}><b style={{ fontSize: 11 }}>含现金与交易成本的账户净值（初始=1）</b><svg role="img" aria-label="日频模拟净值曲线" viewBox="0 0 760 150" style={{ width: '100%', height: 150 }}><line x1="48" x2="742" y1={y(1)} y2={y(1)} stroke="var(--line)" strokeDasharray="4 4" /><text x="0" y="22" fontSize="10" fill="var(--ink-3)">{fmt(max)}</text><text x="0" y="124" fontSize="10" fill="var(--ink-3)">{fmt(min)}</text><polyline fill="none" stroke="var(--dai)" strokeWidth="2" points={nav.map((p, i) => (48 + 694 * i / (nav.length - 1)).toFixed(2) + ',' + y(p[1]).toFixed(2)).join(' ')} /><text x="48" y="145" fontSize="10" fill="var(--ink-3)">{nav[0][0]}</text><text x="742" y="145" textAnchor="end" fontSize="10" fill="var(--ink-3)">{nav[nav.length - 1][0]}</text></svg></div>}
      <details><summary style={{ cursor: 'pointer', fontSize: 12 }}>逐日账户与现金 · 最近20日（含空仓日）</summary><div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse' }}><thead><tr>{['日期', '信号日', '净值', '现金比例', '持股数', '日换手', '受阻订单'].map(t => <th key={t} style={cell}>{t}</th>)}</tr></thead><tbody>{view.daily.map((row, i) => <tr key={i}>{[row.date, row.signal_date || '初始现金', fmt(row.normalized_nav), fmt(row.cash_weight, 'percent'), fmt(row.holdings_count, 'integer'), fmt(row.turnover, 'percent'), (row.blocked_orders || []).length].map((v, j) => <td key={j} style={cell}>{v}</td>)}</tr>)}</tbody></table></div></details>
      <details style={{ marginTop: 9 }}><summary style={{ cursor: 'pointer', fontSize: 12 }}>逐日筛选候选 · 最近20日</summary><div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse' }}><thead><tr>{['信号日', '执行日', '合格数', '实际入选代码'].map(t => <th key={t} style={cell}>{t}</th>)}</tr></thead><tbody>{view.candidates.map((row, i) => <tr key={i}><td style={cell}>{row.date}</td><td style={cell}>{row.execution_date || '区间外未执行'}</td><td style={cell}>{fmt(row.eligible_count, 'integer')}</td><td style={cell}>{(row.selected || []).map(x => x.instrument || x.code).join(' · ') || '空仓信号'}</td></tr>)}</tbody></table></div></details>
      <details style={{ marginTop: 9 }}><summary style={{ cursor: 'pointer', fontSize: 12 }}>实际模拟成交 · 最近30笔</summary><div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse' }}><thead><tr>{['日期', '信号日', '股票', '方向', '参考开盘', '成交价', '成本(元)'].map(t => <th key={t} style={cell}>{t}</th>)}</tr></thead><tbody>{view.trades.map((row, i) => <tr key={i}>{[row.date, row.signal_date, row.instrument, row.side === 'buy' ? '买入' : row.side === 'sell' ? '卖出' : row.side, fmt(row.reference_open), fmt(row.execution_price), fmt(row.costs_paid, 'money')].map((v, j) => <td key={j} style={cell}>{v}</td>)}</tr>)}</tbody></table></div></details>
      <details style={{ marginTop: 9 }}><summary style={{ cursor: 'pointer', fontSize: 12 }}>完整模拟假设与数据限制</summary><ul style={{ paddingLeft: 18, color: 'var(--ink-3)', fontSize: 10.5, lineHeight: 1.6 }}>{view.assumptions.map((text, i) => <li key={i}>{text}</li>)}</ul></details>
    </div>
  </div>;
}

function ResultsDrawer({ result, loading, error, durableWaiting, onStopWaiting, onClose, onSaveFactor, onSaveCard, onExport }) {
  const [expanded, setExpanded] = useState(false);   // 抽屉可一键展开充满屏幕(图表更舒展)
  const [tip, setTip] = useState(null);              // 数据悬浮提示 {x,y,t}(鼠标在数据上显示详情)
  const [openRow, setOpenRow] = useState(null);      // 时序IC 逐股展开行(点击看完整单票体检)
  const completedWithErrors = !!(result && (result._completed_with_errors || result._run_status === 'completed_with_errors'));
  // 悬停 helper:贴到 svg 柱/点上,鼠标移入即时显示详情、跟随光标,移出消失。
  const _hv = t => ({ onMouseMove: e => setTip({ x: e.clientX, y: e.clientY, t }), onMouseLeave: () => setTip(null) });
  if (result && result.method === 'daily_screen_backtest' && !loading && !error) return <DailyScreenResults result={result} expanded={expanded} setExpanded={setExpanded} onClose={onClose} onExport={onExport} />;
  // —— 数据筛选 Skill：确定性硬规则的候选、逐条证据和数据口径。——
  if (result && result.__dt === 'data_screen' && !loading && !error) {
    const rows = Array.isArray(result.candidates) ? result.candidates : [];
    const universe = result.universe || {}, coverage = result.as_of_coverage || {}, universeCoverage = result.universe_coverage || {};
    const snapshot = result.data_snapshot || {}, rules = (result.compiled_spec && result.compiled_spec.rules) || [];
    const warnings = Array.from(new Set([...(result.warnings || []), ...(result._warnings || [])].map(String).filter(Boolean)));
    const runId = result.screen_run_id || result.run_id || '';
    const selectorPreset = result.source_selector && result.source_selector.preset;
    const isMaTrend = selectorPreset === 'ma_trend';
    const selectorLabel = isMaTrend ? '沿均线上升' : '自定义硬规则';
    const amountWindow = (result.compiled_spec && result.compiled_spec.filters && result.compiled_spec.filters.amount_window) || 20;
    const ruleMap = Object.fromEntries(rules.map(rule => [rule.id, rule]));
    const tableColumns = isMaTrend ? '48px minmax(150px,1.5fr) 86px 86px 86px 100px 82px' : '48px minmax(170px,1.6fr) 90px 90px 110px 82px';
    const resolvedN = +(universeCoverage.n_resolved == null ? universe.n_resolved : universeCoverage.n_resolved) || null;
    const effectiveValid = universeCoverage.effective_valid_bars == null ? coverage.valid_bars : universeCoverage.effective_valid_bars;
    const coverageRatio = universeCoverage.effective_valid_ratio != null
      ? +universeCoverage.effective_valid_ratio
      : (universe.id === 'all_a' && resolvedN ? (+effectiveValid || 0) / resolvedN : (coverage.peak_valid_bars ? coverage.valid_bars / coverage.peak_valid_bars : null));
    const f2 = v => (v == null || v !== v) ? '—' : (+v).toFixed(2);
    const fp = v => (v == null || v !== v) ? '—' : ((+v) * 100).toFixed(2) + '%';
    const coverageDisplay = coverageRatio == null ? '—' : fp(coverageRatio) + (resolvedN && effectiveValid != null ? ' · ' + effectiveValid + '/' + resolvedN : '');
    return (
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: expanded ? 'calc(100vh - 90px)' : 350, background: 'var(--paper)', borderTop: '1px solid var(--ink)', boxShadow: '0 -12px 32px rgba(28,24,20,0.12)', zIndex: 8, display: 'flex', flexDirection: 'column', animation: 'fadeIn .3s ease', transition: 'height .2s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 18px', borderBottom: '1px solid var(--line-soft)' }}>
          <span style={{ width: 20, height: 20, borderRadius: 5, background: rows.length ? 'var(--dai)' : '#9a6b16', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{rows.length ? '✓' : '0'}</span>
          <span className="serif" style={{ fontSize: 14, fontWeight: 600 }}>数据筛选 Skill · {selectorLabel}</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{result.as_of || '—'} · {universe.label || universe.id || '未标注股票池'}</span>
          {runId && <span className="mono" title="可追溯的筛选运行编号" style={{ fontSize: 8.5, color: 'var(--ink-3)', border: '1px solid var(--line)', borderRadius: 4, padding: '2px 6px' }}>{runId}</span>}
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <span onClick={() => setExpanded(e => !e)} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>{expanded ? '⤡ 收起' : '⤢ 展开'}</span>
            <span onClick={onExport} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>↧ 导出报告</span>
            <span onClick={onClose} style={{ fontSize: 16, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 4px' }}>✕</span>
          </span>
        </div>
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '12px 18px 16px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6,minmax(88px,1fr))', gap: 8, marginBottom: 11 }}>
            {[
              ['命中总数', result.n_matched == null ? '—' : result.n_matched],
              ['本次返回', rows.length],
              ['实际评估', result.n_evaluated == null ? '—' : result.n_evaluated],
              ['载入标的', result.n_loaded == null ? '—' : result.n_loaded],
              ['有效覆盖', coverageDisplay],
              ['快照 PIT', snapshot.strict_pit === true ? '严格' : '当前口径'],
            ].map(([label, value], i) => <div key={i} style={{ border: '1px solid var(--line-soft)', borderRadius: 7, padding: '7px 9px', background: i === 0 ? 'rgba(52,101,85,.05)' : 'transparent' }}><div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.06em' }}>{label}</div><div className="mono" style={{ marginTop: 2, fontSize: 15, fontWeight: 600, color: i === 0 ? 'var(--dai)' : 'var(--ink-1)' }}>{value}</div></div>)}
          </div>
          {!snapshot.strict_pit && <div className="mono" style={{ marginBottom: 9, padding: '6px 9px', border: '1px dashed #9a6b16', borderRadius: 6, color: '#9a6b16', fontSize: 9.5, lineHeight: 1.5 }}>股票名称、ST 与上市状态来自当前快照，不代表历史时点严格 PIT；价格与成交数据仍绑定到 {result.as_of || '本次有效日'}。</div>}
          {warnings.length > 0 && <div className="mono" style={{ marginBottom: 9, color: '#9a6b16', fontSize: 9.5, lineHeight: 1.5 }}>{warnings.join(' · ')}</div>}
          <div style={{ border: '1px solid var(--line-soft)', borderRadius: 8, overflow: 'hidden' }}>
            <div className="mono" style={{ display: 'grid', gridTemplateColumns: tableColumns, gap: 8, padding: '7px 10px', background: 'var(--paper-2)', color: 'var(--ink-3)', fontSize: 8.5, letterSpacing: '.05em' }}><span>排名</span><span>股票</span><span>{isMaTrend ? '贴线分' : '排序分'}</span><span>收盘价</span>{isMaTrend && <span>乖离率</span>}<span>{amountWindow}日均额</span><span>证据</span></div>
            {rows.length === 0 && <div style={{ padding: '26px 14px', textAlign: 'center', color: 'var(--ink-3)', fontSize: 11 }}>没有股票同时满足全部硬规则。{isMaTrend ? '可以调整均线窗口、乖离范围或流动性门槛后重跑。' : '请审阅每条受控规则与股票池口径后再调整条件。'}</div>}
            {rows.map((row, i) => {
              const key = 'screen:' + (row.code || i), opened = openRow === key;
              const evRows = Object.keys(row.evidence || {}).map(id => ({ id, rule: ruleMap[id] || {}, ...(row.evidence[id] || {}) }));
              const bias = row.score_evidence && row.score_evidence.raw_bias;
              return <React.Fragment key={key}>
                <div onClick={() => setOpenRow(opened ? null : key)} style={{ display: 'grid', gridTemplateColumns: tableColumns, gap: 8, alignItems: 'center', padding: '8px 10px', borderTop: i ? '1px solid var(--line-soft)' : 'none', cursor: 'pointer', fontSize: 10.5 }}>
                  <span className="mono" style={{ color: 'var(--ink-3)' }}>#{row.rank || i + 1}</span>
                  <span><b style={{ color: 'var(--ink-1)' }}>{row.name || '未命名'}</b><span className="mono" style={{ display: 'block', marginTop: 1, fontSize: 8.5, color: 'var(--ink-3)' }}>{row.code || '—'}{row.industry ? ' · ' + row.industry : ''}</span></span>
                  <span className="mono" style={{ color: 'var(--dai)', fontWeight: 600 }}>{f2(row.score)}</span>
                  <span className="mono">{f2(row.close)}</span>
                  {isMaTrend && <span className="mono">{fp(bias)}</span>}
                  <span className="mono">{row.avg_amount_yi == null ? '—' : f2(row.avg_amount_yi) + ' 亿'}</span>
                  <span className="mono" style={{ color: 'var(--dai)' }}>{evRows.length ? evRows.filter(x => x.passed).length + '/' + evRows.length : '—'} {opened ? '▴' : '▾'}</span>
                </div>
                {opened && <div style={{ padding: '8px 10px 10px 66px', borderTop: '1px dashed var(--line-soft)', background: 'rgba(52,101,85,.035)', display: 'grid', gridTemplateColumns: expanded ? 'repeat(2,minmax(250px,1fr))' : '1fr', gap: 6 }}>
                  {evRows.map(ev => <div key={ev.id} style={{ display: 'flex', gap: 8, alignItems: 'baseline', minWidth: 0 }}><span className="mono" style={{ flex: '0 0 auto', color: ev.passed ? 'var(--dai)' : 'var(--zhu)', fontSize: 9 }}>{ev.passed ? '✓' : '×'}</span><span style={{ minWidth: 0, fontSize: 9.5, color: 'var(--ink-2)' }}><b>{ev.label || ev.id}</b><span className="mono" style={{ marginLeft: 6, color: 'var(--ink-3)' }}>{_screenEvidenceValue(ev.rule, ev)}</span></span></div>)}
                </div>}
              </React.Fragment>;
            })}
          </div>
          {expanded && <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.25fr) minmax(260px,.75fr)', gap: 10, marginTop: 10 }}>
            <RCard title="本次硬规则">{rules.map((rule, i) => <div key={rule.id || i} style={{ display: 'flex', gap: 8, marginBottom: 4, fontSize: 10, lineHeight: 1.45 }}><span className="mono" style={{ color: 'var(--dai)' }}>{i + 1}.</span><span>{rule.label || rule.id}</span></div>)}</RCard>
            <RCard title="可追溯数据口径"><div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', lineHeight: 1.55 }}>筛选规则摘要 · {String(result.spec_digest || '').slice(0, 16) || '—'}<br />数据快照摘要 · {String(snapshot.digest || '').slice(0, 16) || '—'}<br />快照行数 · {snapshot.rows == null ? '—' : snapshot.rows}<br />快照股票 · {snapshot.codes == null ? '—' : snapshot.codes}<br />股票池解析 · {universe.n_resolved == null ? '—' : universe.n_resolved}<br />有效日覆盖 · {coverage.valid_bars == null ? '—' : coverage.valid_bars} / 峰值 {coverage.peak_valid_bars == null ? '—' : coverage.peak_valid_bars}</div></RCard>
          </div>}
        </div>
      </div>
    );
  }
  // —— 通用过拟合审计: PASS / FAIL / INCOMPLETE 三态独立视图。——
  if (result && result.__dt === 'overfit_audit' && !loading && !error) {
    const status = String(result.status || 'INCOMPLETE').toUpperCase();
    const metrics = result.metrics || {}, ev = result.evidence || {}, thresholds = result.thresholds || {};
    const haircut = metrics.haircut || {};
    const orth = result.orthogonalization || {}, orthSummary = orth.summary || {};
    const reasons = status === 'FAIL' ? (result.failed_checks || []) : (result.reasons || []);
    const auditWarnings = Array.from(new Set([...(result.warnings || []), ...(result._warnings || [])].map(String).filter(Boolean)));
    const statusLabel = { PASS: '通过', FAIL: '未通过', INCOMPLETE: '证据不足' }[status] || status;
    const statusColor = status === 'PASS' ? 'var(--dai)' : (status === 'FAIL' ? 'var(--zhu)' : '#9a6b16');
    const canPersistAudit = status === 'PASS' && result.evidence_verified === true;
    const governedPolicy = result.policy_authority === 'server_governor_policy';
    const evidenceLabel = ({
      server_trial_ledger: '服务端 TrialLedger 已验证',
      server_trial_ledger_unbound: '服务端 TrialLedger 存在，但未绑定本次入选收益',
      caller_supplied: '调用方诊断输入 · 不可解锁 PASS',
    })[result.evidence_authority] || (result.evidence_verified ? '已验证的服务端证据' : '未验证证据');
    const policyLabel = governedPolicy ? '服务端 Governor 固定策略' : '调用方诊断策略 · 不具备权威';
    const policyDigest = String(result.audit_policy_digest || '');
    const snapshotDigest = String(result.data_snapshot_digest || ev.data_snapshot_digest || '');
    const snapshotAuthority = result.data_snapshot_authority || ev.data_snapshot_authority || '';
    const snapshotAuthorityLabel = snapshotAuthority === 'runtime_panel_content'
      ? '运行面板内容绑定' : (snapshotAuthority || '未绑定');
    const snapshotStrictPit = result.data_snapshot_strict_pit != null
      ? result.data_snapshot_strict_pit : ev.data_snapshot_strict_pit;
    const selectionObjectiveLabel = ev.selection_objective === 'mean_rank_ic'
      ? '基础门合格集内平均 RankIC（条件式 PBO）'
      : (ev.selection_objective === 'mean_period_return'
          ? '旧版分块平均收益（非现行可信口径）'
          : (ev.selection_objective || '—'));
    const selectionScopeLabel = ev.selection_eligibility_scope === 'full_validation_base_gate'
      ? '全验证基础联合门 shortlist' : (ev.selection_eligibility_scope || '—');
    const f3 = v => (v == null || v !== v) ? '—' : (+v).toFixed(3);
    const fp = v => (v == null || v !== v) ? '—' : ((+v) * 100).toFixed(1) + '%';
    const gateColor = (v, ok) => (v == null || v !== v) ? 'var(--ink-3)' : (ok ? 'var(--dai)' : 'var(--zhu)');
    const minTrl = metrics.min_track_record_length;
    return (
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: expanded ? 'calc(100vh - 90px)' : 328, background: 'var(--paper)', borderTop: '1px solid var(--ink)', boxShadow: '0 -12px 32px rgba(28,24,20,0.12)', zIndex: 8, display: 'flex', flexDirection: 'column', animation: 'fadeIn .3s ease', transition: 'height .2s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 18px', borderBottom: '1px solid var(--line-soft)' }}>
          <span style={{ width: 20, height: 20, borderRadius: 5, background: statusColor, color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{status === 'PASS' ? '✓' : (status === 'FAIL' ? '×' : 'ⓘ')}</span>
          <span className="serif" style={{ fontSize: 14, fontWeight: 600 }}>过拟合审计 · {statusLabel}</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{result.family_id ? ('研究家族 ' + result.family_id + ' · ') : ''}{result.date_start && result.date_end ? (result.date_start + ' → ' + result.date_end) : '未绑定完整试验家族'}</span>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <span onClick={() => setExpanded(e => !e)} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>{expanded ? '⤡ 收起' : '⤢ 展开'}</span>
            {canPersistAudit && <span onClick={onSaveCard} className="serif" style={{ fontSize: 12, color: 'var(--dai)', border: '1px solid var(--dai)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>⊕ 经验卡</span>}
            {canPersistAudit && <span onClick={onSaveFactor} className="serif" style={{ fontSize: 12, color: 'var(--paper)', background: 'var(--yin)', border: '1px solid var(--yin)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>存入因子库</span>}
            <span onClick={onExport} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>↧ 导出报告</span>
            <span onClick={onClose} style={{ fontSize: 16, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 4px' }}>✕</span>
          </span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '13px 18px' }}>
          <div style={{ display: 'flex', gap: 22, marginBottom: 13, flexWrap: 'wrap' }}>
            {[[ '年化 Sharpe', f3(metrics.observed_sharpe_annualized), 'var(--ink)' ], [ 'PSR', fp(metrics.psr), gateColor(metrics.psr, metrics.psr >= thresholds.psr_min) ], [ 'DSR', fp(metrics.dsr), gateColor(metrics.dsr, metrics.dsr >= thresholds.dsr_min) ], [ 'PBO', fp(metrics.pbo), gateColor(metrics.pbo, metrics.pbo <= thresholds.pbo_max) ], [ '校正后 PSR', fp(haircut.adjusted_psr), gateColor(haircut.adjusted_psr, haircut.adjusted_psr >= thresholds.adjusted_psr_min) ], [ 'MinTRL', minTrl == null ? '—' : Math.ceil(+minTrl) + '期', 'var(--ink-1)' ], [ '实际样本', ev.selected_observations == null ? '—' : ev.selected_observations + '期', 'var(--ink-1)' ], [ '试验家族', (ev.matrix_candidates == null ? '—' : ev.matrix_candidates) + ' / ' + (ev.n_trials == null ? '—' : ev.n_trials), 'var(--ink-1)' ]].map(([l, v, c], i) => (
              <div key={i}><div className="mono" style={{ fontSize: 8.5, letterSpacing: '.08em', color: 'var(--ink-3)' }}>{l}</div><div className="mono" style={{ fontSize: 17, fontWeight: 600, color: c, marginTop: 2 }}>{v}</div></div>
            ))}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: expanded ? '1fr 1fr' : 'minmax(0,1.2fr) minmax(260px,.8fr)', gap: 12 }}>
            <RCard title={status === 'INCOMPLETE' ? '还缺哪些证据' : (status === 'FAIL' ? '未通过的闸门' : '统计闸门已通过')}>
              {reasons.length ? reasons.map((x, i) => <div key={i} className="mono" style={{ fontSize: 10.5, color: status === 'FAIL' ? 'var(--zhu)' : 'var(--ink-2)', lineHeight: 1.55 }}>• {String(x)}</div>) : <div style={{ fontSize: 11, color: 'var(--dai)', lineHeight: 1.55 }}>所需证据齐全，PSR、DSR、PBO、多重检验校正和 MinTRL 覆盖全部过门。</div>}
            </RCard>
            <RCard title="证据契约">
              <div className="mono" style={{ fontSize: 10, color: 'var(--ink-2)', lineHeight: 1.55 }}>收益源 · {result.source || '—'}<br />上游正交化 · {orth.requested ? ((orth.applied ? '已应用' : '未形成可用残差') + (orthSummary.output_coverage == null ? '' : ' · 覆盖 ' + ((+orthSummary.output_coverage) * 100).toFixed(1) + '%')) : '未请求'}<br />收益矩阵 · {ev.matrix_observations == null ? '—' : ev.matrix_observations} 期 × {ev.matrix_candidates == null ? '—' : ev.matrix_candidates} 候选（PSR / DSR / Haircut）<br />选优矩阵 · {ev.selection_score_observations == null ? '—' : ev.selection_score_observations} 期 × {ev.selection_score_candidates == null ? '—' : ev.selection_score_candidates} 候选 · 合格 {ev.eligible_candidates == null ? '—' : ev.eligible_candidates}<br />TrialLedger · {ev.n_trials == null ? '未提供' : ev.n_trials + ' 次'}<br />数据快照 · {snapshotAuthorityLabel}{snapshotDigest ? ' · ' + snapshotDigest.slice(0, 12) + '…' : ''}<br />严格 PIT · {snapshotStrictPit === true ? '是' : (snapshotStrictPit === false ? '否' : '—')}<br />策略权威 · {policyLabel}<br />策略摘要 · {policyDigest ? policyDigest.slice(0, 16) + '…' : '—'}<br />固定门槛 · PSR≥{fp(thresholds.psr_min)} · DSR≥{fp(thresholds.dsr_min)} · PBO≤{fp(thresholds.pbo_max)}<br />证据权威 · {evidenceLabel}<br />PBO 选优口径 · {selectionObjectiveLabel}<br />合格集口径 · {selectionScopeLabel}<br />入选路径在矩阵中 · {ev.selected_in_trial_matrix ? '是' : '否'}<br />完整家族 · {ev.complete_trial_family ? '是' : '否'}</div>
              <div style={{ marginTop: 6, fontSize: 10, color: 'var(--ink-3)', lineHeight: 1.5 }}>INCOMPLETE 不等于失败，它表示还不能用完整的实验证据证明没有过拟合。系统不会用单次回测冒充审计通过。</div>
            </RCard>
          </div>
          {!canPersistAudit && <div className="mono" style={{ marginTop: 9, padding: '7px 9px', border: '1px dashed var(--line)', borderRadius: 7, fontSize: 9.5, color: '#9a6b16' }}>持久化硬门：仅 PASS 且 evidence_verified=true 时可存因子或经验卡；当前结果只可查看与导出。</div>}
          {auditWarnings.length > 0 && <div className="mono" style={{ marginTop: 9, fontSize: 9.5, color: '#9a6b16' }}>{auditWarnings.join(' · ')}</div>}
        </div>
      </div>
    );
  }
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
                  ['夏普 · 中位', f2(sd.median != null ? sd.median : result.sharpe), 'var(--ink)'],
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
  // —— 个股时序IC 结果:独立视图(report 那套 KPI/十分位/净值不适用单票) ——
  if (result && result.codes_tsic && !loading && !error) {
    const rows = result.codes_tsic || [], sm = result.summary || {};
    const tmax = Math.max(0.01, ...rows.map(r => Math.abs(r.tsic || 0)));
    const f3 = v => (v == null ? '—' : ((v >= 0 ? '+' : '') + (+v).toFixed(3)));   // 带符号3位
    const pc0 = v => (v == null ? '—' : Math.round(v * 100) + '%');                // 百分比0位
    const sgn = v => (v == null ? 'var(--ink-2)' : (v >= 0 ? 'var(--zhu)' : 'var(--dai)'));
    const hlLabel = sm.half_life != null ? ('≈' + (+sm.half_life).toFixed(0) + '日')
      : (sm.still_rising ? '峰未见顶' : (sm.peak_h != null ? '未减半' : '—'));
    // 单票体检明细 · 小分组渲染(label + 若干 KV)
    const kvGroup = (label, items) => (
      <div style={{ display: 'inline-flex', flexDirection: 'column', gap: 3 }}>
        <span className="mono" style={{ fontSize: 7.5, color: 'var(--ink-3)', letterSpacing: '.06em' }}>{label}</span>
        <div style={{ display: 'flex', gap: 11 }}>
          {items.map(([l, v, c], j) => (
            <span key={j} style={{ display: 'inline-flex', flexDirection: 'column' }}>
              <span className="mono" style={{ fontSize: 7.5, color: 'var(--ink-3)' }}>{l}</span>
              <span className="mono" style={{ fontSize: 11, fontWeight: 600, color: c }}>{v}</span>
            </span>
          ))}
        </div>
      </div>
    );
    // 分位桶迷你柱(x=桶 低→高 按因子升序,y=桶均收益,零基线)
    const bucketSpark = buckets => {
      const bmax = Math.max(1e-6, ...buckets.map(b => Math.abs(b[1] || 0)));
      const W = 9 * buckets.length + 2, H = 28, mid = H / 2;
      return (
        <svg viewBox={'0 0 ' + W + ' ' + H} style={{ width: W, height: H }}>
          <line x1="0" y1={mid} x2={W} y2={mid} stroke="var(--ink-3)" strokeWidth="0.4" strokeDasharray="2 2" />
          {buckets.map((b, j) => {
            const hh = Math.abs(b[1] || 0) / bmax * (mid - 2), up = (b[1] || 0) >= 0;
            return <rect key={j} x={9 * j + 2} y={up ? mid - hh : mid} width="6" height={Math.max(0.5, hh)} fill={up ? 'var(--zhu)' : 'var(--dai)'} {..._hv('桶' + b[0] + ' · 均收益 ' + ((b[1] >= 0 ? '+' : '') + (b[1] * 100).toFixed(2) + '%'))} />;
          })}
        </svg>
      );
    };
    return (
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: expanded ? 'calc(100vh - 90px)' : 304, background: 'var(--paper)', borderTop: '1px solid var(--ink)', boxShadow: '0 -12px 32px rgba(28,24,20,0.12)', zIndex: 8, display: 'flex', flexDirection: 'column', animation: 'fadeIn .3s ease', transition: 'height .2s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 18px', borderBottom: '1px solid var(--line-soft)' }}>
          <span style={{ width: 20, height: 20, borderRadius: 5, background: 'var(--dai)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>✓</span>
          <span className="serif" style={{ fontSize: 14, fontWeight: 600 }}>运行完成 · 个股时序IC</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{result._universe + ' · ' + result._label + ' · 未来' + (sm.fwd_days || 20) + '日'}</span>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <span onClick={() => setExpanded(e => !e)} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>{expanded ? '⤡ 收起' : '⤢ 展开'}</span>
            <span onClick={onExport} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>↧ 导出报告</span>
            <span onClick={onClose} style={{ fontSize: 16, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 4px' }}>✕</span>
          </span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '13px 18px' }}>
          <div style={{ display: 'flex', gap: 22, marginBottom: 12, flexWrap: 'wrap' }}>
            {[['样本(只)', sm.n_codes != null ? sm.n_codes : rows.length, 'var(--ink)'], ['均值时序IC', sm.mean_tsic != null ? (+sm.mean_tsic).toFixed(3) : '—', (sm.mean_tsic >= 0 ? 'var(--zhu)' : 'var(--dai)')], ['正占比', sm.pos_ratio != null ? Math.round(sm.pos_ratio * 100) + '%' : '—', 'var(--ink-1)'], ['显著占比|t|≥2', sm.sig_ratio != null ? Math.round(sm.sig_ratio * 100) + '%' : '—', 'var(--ink-1)'], ['平均样本数', sm.avg_n || '—', 'var(--ink-1)'], ['峰值时序IC', sm.peak_ic != null ? (f3(sm.peak_ic) + '@' + (sm.peak_h != null ? sm.peak_h : '—') + '日') : '—', sgn(sm.peak_ic)], ['IC半衰期', hlLabel, sgn(sm.peak_ic)]].map(([l, v, c], i) => (
              <div key={i}><div className="mono" style={{ fontSize: 8.5, letterSpacing: '.08em', color: 'var(--ink-3)' }}>{l}</div><div className="mono" style={{ fontSize: 17, fontWeight: 600, color: c, marginTop: 2 }}>{v}</div></div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 16, marginBottom: 10, flexWrap: 'wrap', alignItems: 'flex-end', padding: '8px 11px', background: 'rgba(28,24,20,0.025)', borderRadius: 8 }}>
            <span className="mono" style={{ fontSize: 8.5, letterSpacing: '.06em', color: 'var(--ink-3)', alignSelf: 'center' }}>池级时序体检 ·</span>
            {[['均值ICIR', sm.mean_icir != null ? (+sm.mean_icir).toFixed(2) : '—', sgn(sm.mean_icir), 'ICIR=滚动窗IC均值/标准差,衡量预测力跨时间稳定性;|ICIR|>0.5尚可、>1强'],
              ['IC胜率', pc0(sm.ic_win_pool), 'var(--ink-1)', 'IC>0 的滚动窗占比(逐股后池均),方向一致性'],
              ['命中率', pc0(sm.mean_hit), sm.mean_hit != null ? (sm.mean_hit >= 0.5 ? 'var(--zhu)' : 'var(--dai)') : 'var(--ink-2)', '方向命中率(池均);0.5=无信息,逆向因子<0.5仍可由PT判显著'],
              ['PT显著', pc0(sm.pt_sig_ratio), 'var(--ink-1)', 'Pesaran-Timmermann |z|≥1.96 的股票占比(检验前抽稀到不重叠样本)'],
              ['NW-t显著', pc0(sm.nw_sig_ratio), 'var(--ink-1)', '预测回归斜率 |Newey-West HAC t|≥2 的股票占比(修重叠致残差自相关)'],
              ['Pearson-IC', f3(sm.mean_pearson), sgn(sm.mean_pearson), '线性IC(池均),与秩IC互补;二者背离=非线性/有离群'],
              ['分位单调', sm.mean_mono != null ? (+sm.mean_mono).toFixed(2) : '—', sgn(sm.mean_mono), '按自身因子分位的未来收益单调性(池均);+1=完美单调递增'],
              ['均值R²ₒₛ', sm.mean_r2os != null ? (+sm.mean_r2os).toFixed(3) : '—', sgn(sm.mean_r2os), '样本外 R²_OS(Campbell-Thompson)池均;>0=因子样本外预测胜「历史均值」基准=真可信(样本内强但≤0=过拟合/数据挖掘)'],
              ['R²ₒₛ>0', pc0(sm.r2os_pos_ratio), 'var(--ink-1)', '样本外 R²_OS>0 的股票占比(扩展窗逐点预测、训练集滞后防前视)'],
              ['CW显著', pc0(sm.cw_sig_ratio), 'var(--ink-1)', 'Clark-West 单侧 z≥1.645(约5%)的股票占比;MSPE 调整 + NW 修重叠']
            ].map(([l, v, c, tipx], i) => (
              <span key={i} title={tipx} style={{ display: 'inline-flex', flexDirection: 'column', cursor: 'help' }}>
                <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', letterSpacing: '.04em' }}>{l}</span>
                <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: c }}>{v}</span>
              </span>
            ))}
          </div>
          {Array.isArray(sm.decay) && sm.decay.length > 0 && (
            <div style={{ display: 'flex', gap: 14, marginBottom: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
              <span className="mono" style={{ fontSize: 8.5, letterSpacing: '.06em', color: 'var(--ink-3)' }}>衰减剖面 · 池均时序IC@前向窗口:</span>
              {sm.decay.map((d, i) => (<span key={i} className="mono" title={d[0] === sm.peak_h ? '★ |IC| 峰值窗口' : ''} style={{ fontSize: 11, color: (d[1] >= 0 ? 'var(--zhu)' : 'var(--dai)'), fontWeight: (d[0] === (sm.fwd_days || 20) ? 700 : 400) }}>{(d[0] === sm.peak_h ? '★' : '') + d[0] + '日 ' + ((d[1] >= 0 ? '+' : '') + (+d[1]).toFixed(3))}</span>))}
            </div>
          )}
          {Array.isArray(sm.period_ic) && sm.period_ic.length > 0 && (
            <div style={{ display: 'flex', gap: 14, marginBottom: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
              <span className="mono" style={{ fontSize: 8.5, letterSpacing: '.06em', color: 'var(--ink-3)' }} title="逐自然年池均时序IC,看预测力跨行情(牛/熊/震荡)的稳定性">分时段 · 逐年池均IC:</span>
              {sm.period_ic.map((p, i) => (<span key={i} className="mono" title={'参与股数 ' + p[2]} style={{ fontSize: 11, color: (p[1] >= 0 ? 'var(--zhu)' : 'var(--dai)') }}>{p[0] + ' ' + ((p[1] >= 0 ? '+' : '') + (+p[1]).toFixed(3))}</span>))}
            </div>
          )}
          {Array.isArray(sm.timing_nav) && sm.timing_nav.length >= 2 && (() => {
            const tp = sm.timing_pool || {}, nav = sm.timing_nav;
            const vals = nav.flatMap(p => [p[1], p[2]]).filter(v => v != null);
            const vmin = Math.min(...vals), vmax = Math.max(...vals);
            const W = 920, H = 132, padX = 8, padTop = 12, padBot = 16;
            const xOf = i => padX + (nav.length > 1 ? i / (nav.length - 1) : 0) * (W - 2 * padX);
            const yOf = v => padTop + (vmax > vmin ? (1 - (v - vmin) / (vmax - vmin)) : 0.5) * (H - padTop - padBot);
            const pathOf = k => nav.map((p, i) => (i ? 'L' : 'M') + xOf(i).toFixed(1) + ' ' + yOf(p[k]).toFixed(1)).join(' ');
            const won = (tp.delta_sharpe != null && tp.delta_sharpe >= 0);
            const pct1 = v => (v == null ? '—' : ((v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'));
            return (
              <div style={{ marginBottom: 12 }}>
                <div style={{ display: 'flex', gap: 20, marginBottom: 6, flexWrap: 'wrap', alignItems: 'baseline' }}>
                  <span className="serif" style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-1)' }}>单票择时 · 净值 vs 买入持有</span>
                  {[['池择时Sharpe', tp.pool_sharpe != null ? (+tp.pool_sharpe).toFixed(2) : '—', sgn(tp.pool_sharpe), '池级等权择时组合年化Sharpe(扣5bps换手成本)'],
                    ['持有Sharpe', tp.bh_sharpe != null ? (+tp.bh_sharpe).toFixed(2) : '—', 'var(--ink-2)', '买入持有(成本公平:仅首日进场计费)年化Sharpe'],
                    ['vs持有差', tp.delta_sharpe != null ? ((tp.delta_sharpe >= 0 ? '+' : '') + (+tp.delta_sharpe).toFixed(2)) : '—', sgn(tp.delta_sharpe), 'pool_sharpe - bh_sharpe;>0 才算择时真有经济价值'],
                    ['胜持有', pc0(tp.frac_beat_bh), 'var(--ink-1)', '同日策略日收益>买入持有的占比'],
                    ['超额年化', pct1(tp.ann_excess), sgn(tp.ann_excess), '策略年化收益 - 买入持有年化收益'],
                    ['策略回撤', tp.pool_maxdd != null ? (tp.pool_maxdd * 100).toFixed(1) + '%' : '—', 'var(--dai)', '池级策略净值最大回撤']
                  ].map(([l, v, c, tipx], i) => (
                    <span key={i} title={tipx} style={{ display: 'inline-flex', flexDirection: 'column', cursor: 'help' }}>
                      <span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', letterSpacing: '.04em' }}>{l}</span>
                      <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: c }}>{v}</span>
                    </span>
                  ))}
                </div>
                <RCard title={'池级等权择时净值(实线)vs 买入持有(虚线)· ' + (tp.n_days || 0) + '日 · long/flat · 成本5bps'}>
                  <svg viewBox={'0 0 ' + W + ' ' + H} style={{ width: '100%', height: 'auto', display: 'block' }} preserveAspectRatio="none">
                    <line x1={padX} y1={yOf(1.0)} x2={W - padX} y2={yOf(1.0)} stroke="var(--ink-3)" strokeWidth="0.5" strokeDasharray="3 3" />
                    <path d={pathOf(2)} fill="none" stroke="var(--ink-3)" strokeWidth="1.2" strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />
                    <path d={pathOf(1)} fill="none" stroke={won ? 'var(--zhu)' : 'var(--dai)'} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
                    {nav.map((p, i) => <circle key={i} cx={xOf(i)} cy={yOf(p[1])} r="2.2" fill="transparent" style={{ cursor: 'pointer' }} {..._hv(p[0] + ' · 策略 ' + (+p[1]).toFixed(3) + ' · 持有 ' + (+p[2]).toFixed(3) + ' · 超额 ' + ((p[1] / p[2] - 1) * 100).toFixed(1) + '%')} />)}
                  </svg>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>
                    <span>{nav[0][0]}</span>
                    <span>{'策略 ' + (+nav[nav.length - 1][1]).toFixed(3) + ' vs 持有 ' + (+nav[nav.length - 1][2]).toFixed(3) + '(' + (won ? 'Sharpe胜' : 'Sharpe负') + ')'}</span>
                    <span>{nav[nav.length - 1][0]}</span>
                  </div>
                </RCard>
              </div>
            );
          })()}
          <RCard title={'逐股票时序IC · 因子 vs 自身未来 ' + (sm.fwd_days || 20) + ' 日收益(Spearman,中线=0;t=重叠校正显著性)'}>
            <div style={{ display: 'grid', gap: '6px 16px' }}>
              {rows.slice(0, 40).map((r, i) => {
                const op = openRow === i, hasD = r.tsic != null;
                return (
                <div key={i}>
                  <div onClick={() => hasD && setOpenRow(op ? null : i)} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10, cursor: hasD ? 'pointer' : 'default', padding: '1px 0' }}>
                    <span className="mono" style={{ flex: '0 0 9px', fontSize: 8, color: 'var(--ink-3)' }}>{hasD ? (op ? '▾' : '▸') : ''}</span>
                    <span className="mono" style={{ color: 'var(--ink-2)', flex: '0 0 88px' }}>{r.code}</span>
                    <span style={{ flex: 1, height: 7, background: 'var(--line)', borderRadius: 3, position: 'relative', overflow: 'hidden' }}>
                      <span style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: 'var(--ink-3)' }} />
                      {r.tsic != null && <span style={{ position: 'absolute', top: 0, bottom: 0, left: (r.tsic >= 0 ? '50%' : (50 + r.tsic / tmax * 50) + '%'), width: (Math.abs(r.tsic || 0) / tmax * 50) + '%', background: (r.tsic >= 0 ? 'var(--zhu)' : 'var(--dai)') }} />}
                    </span>
                    <span className="mono" style={{ width: 56, textAlign: 'right', color: (r.tsic >= 0 ? 'var(--zhu)' : 'var(--dai)'), fontWeight: 600 }}>{r.tsic != null ? ((r.tsic >= 0 ? '+' : '') + r.tsic.toFixed(3)) : '样本不足'}</span>
                    <span className="mono" style={{ width: 46, textAlign: 'right', color: (Math.abs(r.t || 0) >= 2 ? (r.tsic >= 0 ? 'var(--zhu)' : 'var(--dai)') : 'var(--ink-3)'), fontSize: 9 }}>{r.t != null ? ('t' + (+r.t).toFixed(1)) : ''}</span>
                    <span className="mono" style={{ width: 50, textAlign: 'right', color: 'var(--ink-3)', fontSize: 9 }}>N={r.n}</span>
                  </div>
                  {op && hasD && (
                    <div style={{ margin: '3px 0 7px 17px', padding: '8px 11px', background: 'rgba(28,24,20,0.025)', borderRadius: 7, display: 'flex', flexDirection: 'column', gap: 7, animation: 'fadeIn .2s ease' }}>
                      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
                        {kvGroup('稳定性 · 滚动窗ICIR', [
                          ['ICIR', r.icir != null ? (+r.icir).toFixed(2) : '—', sgn(r.icir)],
                          ['IC均', f3(r.ic_mean), sgn(r.ic_mean)],
                          ['IC标', r.ic_std != null ? (+r.ic_std).toFixed(3) : '—', 'var(--ink-2)'],
                          ['IC胜率', pc0(r.ic_win), 'var(--ink-2)'],
                          ['窗', r.n_win != null ? r.n_win : '—', 'var(--ink-3)'],
                        ])}
                        {kvGroup('预测回归 · β/Newey-West', [
                          ['β', r.beta != null ? (+r.beta).toFixed(4) : '—', sgn(r.beta)],
                          ['NW-t', r.nw_t != null ? ((+r.nw_t).toFixed(2) + (r.nw_lag != null ? ' L=' + r.nw_lag : '')) : '—', (r.nw_sig ? sgn(r.beta) : 'var(--ink-3)')],
                          ['NW-p', r.nw_p != null ? (+r.nw_p).toFixed(3) : '—', 'var(--ink-2)'],
                          ['R²', r.r2 != null ? (+r.r2).toFixed(4) : '—', 'var(--ink-2)'],
                          ['', r.nw_sig ? '~显著' : '', (r.tsic >= 0 ? 'var(--zhu)' : 'var(--dai)')],
                        ])}
                        {kvGroup('方向择时 · 命中/PT', [
                          ['命中', pc0(r.hit), r.hit != null ? (r.hit >= 0.5 ? 'var(--zhu)' : 'var(--dai)') : 'var(--ink-2)'],
                          ['PT-z', r.pt != null ? (+r.pt).toFixed(2) : '—', (r.pt != null && Math.abs(r.pt) >= 1.96 ? sgn(r.pt) : 'var(--ink-3)')],
                          ['PT-p', r.pt_p != null ? (+r.pt_p).toFixed(3) : '—', 'var(--ink-2)'],
                          ['thinN', r.pt_n != null ? r.pt_n : '—', 'var(--ink-3)'],
                        ])}
                        {kvGroup('样本外 · R²ₒₛ/Clark-West', [
                          ['R²ₒₛ', r.r2_os != null ? (+r.r2_os).toFixed(3) : '—', sgn(r.r2_os)],
                          ['CW-z', r.cw != null ? (+r.cw).toFixed(2) : '—', (r.cw != null && r.cw >= 1.645 ? 'var(--zhu)' : 'var(--ink-3)')],
                          ['CW-p', r.cw_p != null ? (+r.cw_p).toFixed(3) : '—', 'var(--ink-2)'],
                          ['n', r.n_oos != null ? r.n_oos : '—', 'var(--ink-3)'],
                        ])}
                      </div>
                      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'center' }}>
                        {kvGroup('线性 · 分位单调', [
                          ['Pearson', f3(r.pearson), sgn(r.pearson)],
                          ['价差', f3(r.spread), sgn(r.spread)],
                          ['单调', r.mono != null ? (+r.mono).toFixed(2) : '—', sgn(r.mono)],
                          ['桶', r.q_used != null ? r.q_used : '—', 'var(--ink-3)'],
                        ])}
                        {Array.isArray(r.buckets) && r.buckets.length > 0 && (
                          <div style={{ display: 'inline-flex', flexDirection: 'column', gap: 3 }}>
                            <span className="mono" style={{ fontSize: 7.5, color: 'var(--ink-3)', letterSpacing: '.06em' }}>分位桶 · 未来收益(低→高)</span>
                            {bucketSpark(r.buckets)}
                          </div>
                        )}
                      </div>
                      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'center' }}>
                        {kvGroup('择时 · long/flat vs 买入持有', [
                          ['Sharpe', r.tb_sharpe != null ? (+r.tb_sharpe).toFixed(2) : '—', sgn(r.tb_sharpe)],
                          ['持有Sh', r.tb_bh_sharpe != null ? (+r.tb_bh_sharpe).toFixed(2) : '—', 'var(--ink-3)'],
                          ['Calmar', r.tb_calmar != null ? (+r.tb_calmar).toFixed(2) : '—', sgn(r.tb_calmar)],
                          ['回撤', r.tb_maxdd != null ? (r.tb_maxdd * 100).toFixed(1) + '%' : '—', 'var(--dai)'],
                          ['换手', r.tb_turnover != null ? (+r.tb_turnover).toFixed(1) : '—', 'var(--ink-3)'],
                          ['仓位', r.tb_exposure != null ? Math.round(r.tb_exposure * 100) + '%' : '—', 'var(--ink-3)'],
                          ['胜持有', r.tb_beat_bh == null ? '—' : (r.tb_beat_bh ? '✓' : '✗'), (r.tb_beat_bh ? 'var(--zhu)' : 'var(--ink-3)')],
                          ['CER增益', r.tb_cer_gain != null ? ((r.tb_cer_gain >= 0 ? '+' : '') + (r.tb_cer_gain * 100).toFixed(1) + '%') : '—', sgn(r.tb_cer_gain)],
                        ])}
                      </div>
                      <div style={{ fontSize: 8.5, color: 'var(--ink-3)', lineHeight: 1.5 }}>ICIR=跨窗稳定性(非窗内显著);NW-t 经 Newey-West HAC 修重叠(L=窗-1);PT 先抽稀到不重叠样本,命中&lt;50%+|z|大=逆向有效;桶/价差短历史薄,参考池均;R²ₒₛ&gt;0=样本外胜「历史均值」基准(扩展窗·训练滞后防前视),CW=Clark-West 单侧显著;择时=因子 trailing 标准化&gt;0 满仓否则空仓·扣5bps换手 vs 买入持有(成本公平),胜持有✓/CER增益&gt;0 才有经济价值(逆向因子做多必负)。</div>
                    </div>
                  )}
                </div>
                );
              })}
            </div>
            <div style={{ marginTop: 8, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>注:正 = 因子值高时该股未来更易涨;<b style={{ color: 'var(--yin)' }}>单票口径</b>(截面 IC 在单票退化,故用时序 IC)。{(result._warnings || []).slice(0, 1).join('')}</div>
          </RCard>
        </div>
      </div>
    );
  }
  // —— 事件研究 结果:独立视图(CAR 曲线 + 各前向窗口表 + 逐年 + 期望值;离散触发口径) ——
  if (result && result.car_curve && !loading && !error) {
    const hz = result.horizons || [], car = result.car_curve || [], sm = result.summary || {}, byYear = result.by_year || [];
    const pct = v => (v == null ? '—' : ((v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%'));
    const col = v => (v == null ? 'var(--ink-2)' : (v >= 0 ? 'var(--zhu)' : 'var(--dai)'));
    const cmax = Math.max(0.0005, ...car.map(p => Math.abs(p[1] || 0)));
    const W = 920, H = 130, padX = 8, padY = 14;
    const xOf = i => padX + (car.length > 1 ? i / (car.length - 1) : 0) * (W - 2 * padX);
    const yOf = v => (H / 2) - (v / cmax) * (H / 2 - padY);
    const carPath = car.map((p, i) => (i ? 'L' : 'M') + xOf(i).toFixed(1) + ' ' + yOf(p[1]).toFixed(1)).join(' ');
    const ymax = Math.max(0.001, ...byYear.map(y => Math.abs(y[2] || 0)));
    return (
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: expanded ? 'calc(100vh - 90px)' : 304, background: 'var(--paper)', borderTop: '1px solid var(--ink)', boxShadow: '0 -12px 32px rgba(28,24,20,0.12)', zIndex: 8, display: 'flex', flexDirection: 'column', animation: 'fadeIn .3s ease', transition: 'height .2s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 18px', borderBottom: '1px solid var(--line-soft)' }}>
          <span style={{ width: 20, height: 20, borderRadius: 5, background: 'var(--dai)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>✓</span>
          <span className="serif" style={{ fontSize: 14, fontWeight: 600 }}>运行完成 · 事件研究</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{result._universe + ' · ' + result._label}</span>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <span onClick={() => setExpanded(e => !e)} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>{expanded ? '⤡ 收起' : '⤢ 展开'}</span>
            <span onClick={onExport} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>↧ 导出报告</span>
            <span onClick={onClose} style={{ fontSize: 16, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 4px' }}>✕</span>
          </span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '13px 18px' }}>
          <div style={{ display: 'flex', gap: 22, marginBottom: 12 }}>
            {[['事件数', sm.n_events != null ? sm.n_events : '—', 'var(--ink)'], ['触发率', sm.event_rate != null ? (sm.event_rate * 100).toFixed(1) + '%' : '—', 'var(--ink-1)'], ['命中率@' + (sm.head_h || 5) + '日', sm.head_win_rate != null ? (sm.head_win_rate * 100).toFixed(0) + '%' : '—', 'var(--ink-1)'], ['异常收益@' + (sm.head_h || 5) + '日', pct(sm.head_excess), col(sm.head_excess)], ['t 值@' + (sm.head_h || 5) + '日', sm.head_t != null ? (+sm.head_t).toFixed(2) : '—', (Math.abs(sm.head_t || 0) >= 2 ? col(sm.head_excess) : 'var(--ink-2)')]].map(([l, v, c], i) => (
              <div key={i}><div className="mono" style={{ fontSize: 8.5, letterSpacing: '.08em', color: 'var(--ink-3)' }}>{l}</div><div className="mono" style={{ fontSize: 17, fontWeight: 600, color: c, marginTop: 2 }}>{v}</div></div>
            ))}
          </div>
          <RCard title={'累计异常收益 CAR · 触发后第 1…' + (car.length ? car[car.length - 1][0] : 20) + ' 日(市场调整,中线=0)'}>
            <svg viewBox={'0 0 ' + W + ' ' + H} style={{ width: '100%', height: 'auto', display: 'block' }} preserveAspectRatio="none">
              <line x1={padX} y1={H / 2} x2={W - padX} y2={H / 2} stroke="var(--ink-3)" strokeWidth="0.6" strokeDasharray="3 3" />
              {car.length > 1 && <path d={carPath} fill="none" stroke={col(car[car.length - 1][1])} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />}
              {car.map((p, i) => <circle key={i} cx={xOf(i)} cy={yOf(p[1])} r="2.4" fill={col(p[1])} style={{ cursor: 'pointer' }} {..._hv('第 ' + p[0] + ' 日 · CAR ' + pct(p[1]))} />)}
            </svg>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}><span>第 1 日</span><span>{'CAR 终值 ' + (car.length ? pct(car[car.length - 1][1]) : '—')}</span><span>{'第 ' + (car.length ? car[car.length - 1][0] : 20) + ' 日'}</span></div>
          </RCard>
          <RCard title="各前向窗口 · 原始/异常收益 · 命中率 · t值 · 盈亏比/期望值">
            <div style={{ display: 'grid', gridTemplateColumns: '54px 1fr 1fr 1fr 1fr 1fr 1.3fr', gap: '5px 10px', fontSize: 10, alignItems: 'center' }}>
              {['窗口', '样本', '原始收益', '异常收益', '命中率', 't 值', '盈亏比/期望'].map((h, i) => <span key={'h' + i} className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.04em' }}>{h}</span>)}
              {hz.map((d, i) => [
                <span key={'a' + i} className="mono" style={{ color: 'var(--ink-1)', fontWeight: 600 }}>{d.h + '日'}</span>,
                <span key={'b' + i} className="mono" style={{ color: 'var(--ink-2)' }}>{d.n}</span>,
                <span key={'c' + i} className="mono" style={{ color: col(d.mean_ret) }}>{pct(d.mean_ret)}</span>,
                <span key={'d' + i} className="mono" style={{ color: col(d.mean_excess), fontWeight: 600 }}>{pct(d.mean_excess)}</span>,
                <span key={'e' + i} className="mono" style={{ color: 'var(--ink-1)' }}>{d.win_rate != null ? (d.win_rate * 100).toFixed(0) + '%' : '—'}</span>,
                <span key={'f' + i} className="mono" style={{ color: (Math.abs(d.t_stat || 0) >= 2 ? col(d.mean_excess) : 'var(--ink-2)') }}>{d.t_stat != null ? (+d.t_stat).toFixed(2) : '—'}</span>,
                <span key={'g' + i} className="mono" style={{ color: 'var(--ink-2)', fontSize: 9 }}>{(d.profit_factor != null ? (+d.profit_factor).toFixed(2) : '—') + ' / ' + pct(d.expectancy)}</span>
              ])}
            </div>
          </RCard>
          {byYear.length > 0 && (
            <RCard title={'逐年异常收益 · 触发后 5 日(跨市场稳定性,中线=0)'}>
              <div style={{ display: 'grid', gap: '6px 16px' }}>
                {byYear.map((y, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10 }}>
                    <span className="mono" style={{ color: 'var(--ink-2)', flex: '0 0 78px' }}>{y[0] + ' (' + y[1] + '次)'}</span>
                    <span style={{ flex: 1, height: 7, background: 'var(--line)', borderRadius: 3, position: 'relative', overflow: 'hidden' }}>
                      <span style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: 'var(--ink-3)' }} />
                      <span style={{ position: 'absolute', top: 0, bottom: 0, left: (y[2] >= 0 ? '50%' : (50 + y[2] / ymax * 50) + '%'), width: (Math.abs(y[2] || 0) / ymax * 50) + '%', background: col(y[2]) }} />
                    </span>
                    <span className="mono" style={{ width: 64, textAlign: 'right', color: col(y[2]), fontWeight: 600 }}>{pct(y[2])}</span>
                  </div>
                ))}
              </div>
            </RCard>
          )}
          <div style={{ marginTop: 8, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>注:<b style={{ color: 'var(--yin)' }}>事件研究口径</b> — 截面 IC 对稀疏触发是错口径,这里用触发后的市场调整异常收益 CAR(调整与显著性方式见下方说明)。{(result._warnings || []).join(' ')}</div>
        </div>
      </div>
    );
  }
  // —— 关系稳定度 结果:独立视图(共振/跟随关系因子的描述性体检:水平+粘性+正占比) ——
  if (result && result.codes_relstat && !loading && !error) {
    const rows = result.codes_relstat || [], sm = result.summary || {};
    const lmax = Math.max(0.01, ...rows.map(r => Math.abs(r.level || 0)));
    return (
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: expanded ? 'calc(100vh - 90px)' : 304, background: 'var(--paper)', borderTop: '1px solid var(--ink)', boxShadow: '0 -12px 32px rgba(28,24,20,0.12)', zIndex: 8, display: 'flex', flexDirection: 'column', animation: 'fadeIn .3s ease', transition: 'height .2s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 18px', borderBottom: '1px solid var(--line-soft)' }}>
          <span style={{ width: 20, height: 20, borderRadius: 5, background: 'var(--dai)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>✓</span>
          <span className="serif" style={{ fontSize: 14, fontWeight: 600 }}>诊断完成 · 描述统计 / 关系稳定度</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{((result.requested_codes || []).join(', ') || result._universe) + ' · ' + result._label}</span>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <span onClick={() => setExpanded(e => !e)} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>{expanded ? '⤡ 收起' : '⤢ 展开'}</span>
            <span onClick={onExport} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>↧ 导出报告</span>
            <span onClick={onClose} style={{ fontSize: 16, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 4px' }}>✕</span>
          </span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '13px 18px' }}>
          <div style={{ display: 'flex', gap: 22, marginBottom: 12 }}>
            {[['样本(只)', sm.n_codes != null ? sm.n_codes : rows.length, 'var(--ink)'], ['均值水平', sm.mean_level != null ? (+sm.mean_level).toFixed(3) : '—', (sm.mean_level >= 0 ? 'var(--zhu)' : 'var(--dai)')], ['平均粘性(lag1)', sm.mean_stickiness != null ? (+sm.mean_stickiness).toFixed(2) : '—', 'var(--ink-1)'], ['平均正占比', sm.mean_pos_ratio != null ? Math.round(sm.mean_pos_ratio * 100) + '%' : '—', 'var(--ink-1)'], ['平均样本数', sm.avg_n || '—', 'var(--ink-1)']].map(([l, v, c], i) => (
              <div key={i}><div className="mono" style={{ fontSize: 8.5, letterSpacing: '.08em', color: 'var(--ink-3)' }}>{l}</div><div className="mono" style={{ fontSize: 17, fontWeight: 600, color: c, marginTop: 2 }}>{v}</div></div>
            ))}
          </div>
          <div style={{ fontSize: 10, color: 'var(--ink-2)', marginBottom: 10 }}>
            仅描述统计，不是回测或策略晋级通过。
            {' 请求区间 ' + (result.start || '未记录') + ' → ' + (result.end || '未记录')}
            {' · 实际有效样本 ' + (result.sample_start || '未记录') + ' → ' + (result.sample_end || '未记录')}
          </div>
          <RCard title={'逐股票描述统计 · 均值水平(中线=0)· 粘=lag1自相关 · %=正占比'}>
            <div style={{ display: 'grid', gap: '6px 16px' }}>
              {rows.slice(0, 40).map((r, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10 }}>
                  <span className="mono" style={{ color: 'var(--ink-2)', flex: '0 0 92px' }}>{r.code}</span>
                  <span style={{ flex: 1, height: 7, background: 'var(--line)', borderRadius: 3, position: 'relative', overflow: 'hidden' }}>
                    <span style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: 'var(--ink-3)' }} />
                    {r.level != null && <span style={{ position: 'absolute', top: 0, bottom: 0, left: (r.level >= 0 ? '50%' : (50 + r.level / lmax * 50) + '%'), width: (Math.abs(r.level || 0) / lmax * 50) + '%', background: (r.level >= 0 ? 'var(--zhu)' : 'var(--dai)') }} />}
                  </span>
                  <span className="mono" style={{ width: 52, textAlign: 'right', color: (r.level >= 0 ? 'var(--zhu)' : 'var(--dai)'), fontWeight: 600 }}>{r.level != null ? ((r.level >= 0 ? '+' : '') + r.level.toFixed(3)) : '样本不足'}</span>
                  <span className="mono" style={{ width: 60, textAlign: 'right', color: 'var(--ink-3)', fontSize: 9 }}>{r.stickiness != null ? '粘 ' + (+r.stickiness).toFixed(2) : ''}</span>
                  <span className="mono" style={{ width: 75, textAlign: 'right', color: 'var(--ink-3)', fontSize: 9 }}>{r.vol != null ? '波动 ' + (+r.vol).toFixed(4) : ''}</span>
                  <span className="mono" style={{ width: 40, textAlign: 'right', color: 'var(--ink-3)', fontSize: 9 }}>{r.pos_ratio != null ? Math.round(r.pos_ratio * 100) + '%' : ''}</span>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 8, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>注:均值水平=该关系因子时序均值(如平均共振相关);<b style={{ color: 'var(--yin)' }}>粘性</b>=lag1 自相关(越高越稳/不跳);正占比=同向时间占比。描述性体检,非预测力(预测力看「个股时序IC」)。{(result._warnings || []).slice(0, 1).join('')}</div>
          </RCard>
        </div>
      </div>
    );
  }
  // —— 风险度量: 独立视图(VaR/CVaR 三法对照 + 损失分布直方图 + EVT + Kupiec)——
  if (result && result.method === 'attrib' && !loading && !error) {
    const pa = (v, d = 2) => (v == null ? '—' : ((v * 100).toFixed(d) + '%'));
    const exps = result.exposures || [];
    const ppy = result.ppy || 12;
    const warns = (result.warnings || []).concat(result._warnings || []);
    const freqLab = ({ day: '日频', week: '周频', month: '月频' })[result.freq] || result.freq || '';
    const FAC_CN = { MKT: '市场 MKT', SMB: '规模 SMB(小盘减大盘)', HML: '价值 HML(高BM减低BM)', WML: '动量 WML(赢家减输家)' };
    // 收益贡献分解(年化):alpha + 各因子贡献 + 残差 → 加总≈策略年化均值
    const comps = [['alpha', result.alpha, 'var(--zhu)']]
      .concat(exps.map(e => [FAC_CN[e.name] || e.name, e.contribution, 'var(--ink-1)']))
      .concat([['残差', result.residual_mean, 'var(--ink-3)']])
      .map(([nm, v, c]) => [nm, (v == null ? 0 : v * ppy), c]);
    const cMax = Math.max(1e-9, ...comps.map(x => Math.abs(x[1])));
    return (
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: expanded ? 'calc(100vh - 90px)' : 304, background: 'var(--paper)', borderTop: '1px solid var(--ink)', boxShadow: '0 -12px 32px rgba(28,24,20,0.12)', zIndex: 8, display: 'flex', flexDirection: 'column', animation: 'fadeIn .3s ease', transition: 'height .2s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 18px', borderBottom: '1px solid var(--line-soft)' }}>
          <span style={{ width: 20, height: 20, borderRadius: 5, background: 'var(--dai)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>✓</span>
          <span className="serif" style={{ fontSize: 14, fontWeight: 600 }}>运行完成 · 风格归因</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{(result._universe || '') + ' · ' + (result._label || '') + (result.n != null ? ' · ' + result.n + '期' + (freqLab ? '(' + freqLab + ')' : '') : '')}</span>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <span onClick={() => setExpanded(e => !e)} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>{expanded ? '⤡ 收起' : '⤢ 展开'}</span>
            <span onClick={onExport} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>↧ 导出报告</span>
            <span onClick={onClose} style={{ fontSize: 16, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 4px' }}>✕</span>
          </span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '13px 18px' }}>
          {result.ok_model === false ? (
            <div style={{ fontSize: 12, color: 'var(--dai)', lineHeight: 1.7, padding: '10px 0' }}>风格归因未启用:<br />{result.reason || warns.join(' ') || '组合期收益样本不足(归因需 ≥12 期);请加宽时间窗或用更密调仓频率。'}</div>
          ) : (<React.Fragment>
            <div style={{ display: 'flex', gap: 22, marginBottom: 12, flexWrap: 'wrap' }}>
              {[['样本期数', result.n != null ? result.n : '—', 'var(--ink)'],
                ['alpha(年化)', pa(result.alpha_annual), (result.alpha_annual >= 0 ? 'var(--zhu)' : 'var(--dai)')],
                ['alpha t值(HAC)', result.alpha_t != null ? (+result.alpha_t).toFixed(2) + (result.alpha_sig ? ' ★' : '') : '—', 'var(--ink-1)'],
                ['R²(风格解释力)', result.r2 != null ? (+result.r2).toFixed(3) : '—', 'var(--ink-1)'],
                ['策略收益(年化)', pa(result.strategy_mean_annual), (result.strategy_mean_annual >= 0 ? 'var(--zhu)' : 'var(--dai)')]].map(([l, v, c], i) => (
                <div key={i}><div className="mono" style={{ fontSize: 8.5, letterSpacing: '.08em', color: 'var(--ink-3)' }}>{l}</div><div className="mono" style={{ fontSize: 17, fontWeight: 600, color: c, marginTop: 2 }}>{v}</div></div>
              ))}
            </div>
            <RCard title={'因子暴露 β · OLS + Newey-West HAC(★=|t|≥2 显著;边际=单因子回归暴露)'}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <thead><tr>{['风格因子', '暴露 β', 't值(HAC)', '显著', '因子均值', '收益贡献(年化)', '边际 t'].map((h, i) => (<th key={i} className="mono" style={{ textAlign: i ? 'right' : 'left', fontWeight: 400, fontSize: 8.5, padding: '3px 6px', letterSpacing: '.04em', color: 'var(--ink-3)' }}>{h}</th>))}</tr></thead>
                <tbody>
                  {exps.map((e, i) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--line-soft)' }}>
                      <td className="mono" style={{ padding: '5px 6px', fontWeight: 600 }}>{FAC_CN[e.name] || e.name}</td>
                      <td className="mono" style={{ textAlign: 'right', fontWeight: 600, color: (e.beta >= 0 ? 'var(--ink-1)' : 'var(--dai)') }}>{e.beta != null ? (+e.beta).toFixed(3) : '—'}</td>
                      <td className="mono" style={{ textAlign: 'right', color: 'var(--ink-2)' }}>{e.t != null ? (+e.t).toFixed(2) : '—'}</td>
                      <td className="mono" style={{ textAlign: 'right', color: (e.sig ? 'var(--zhu)' : 'var(--ink-3)'), fontWeight: 600 }}>{e.sig ? '★' : '·'}</td>
                      <td className="mono" style={{ textAlign: 'right', color: 'var(--ink-2)' }}>{pa(e.factor_mean)}</td>
                      <td className="mono" style={{ textAlign: 'right', fontWeight: 600, color: (e.contribution >= 0 ? 'var(--zhu)' : 'var(--dai)') }}>{pa(e.contribution != null ? e.contribution * ppy : null)}</td>
                      <td className="mono" style={{ textAlign: 'right', color: 'var(--ink-3)' }}>{e.marg_t != null ? (+e.marg_t).toFixed(2) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </RCard>
            <RCard title={'收益贡献分解(年化)· alpha + 各风格贡献 + 残差 = 策略收益(朱=正/黛=负)'}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5, padding: '2px 0' }}>
                {comps.map(([nm, v, c], i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8 }} {..._hv(nm + ' · 年化贡献 ' + (v * 100).toFixed(2) + '%')}>
                    <span className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', width: 150, flex: '0 0 150px', textAlign: 'right' }}>{nm}</span>
                    <div style={{ flex: 1, position: 'relative', height: 13, background: 'rgba(28,24,20,0.04)', borderRadius: 3 }}>
                      <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: 'var(--ink-3)' }} />
                      <div style={{ position: 'absolute', top: 1, bottom: 1, height: 11,
                        left: v >= 0 ? '50%' : (50 - Math.abs(v) / cMax * 48) + '%',
                        width: (Math.abs(v) / cMax * 48) + '%',
                        background: v >= 0 ? 'var(--zhu)' : 'var(--dai)', opacity: 0.62, borderRadius: 2 }} />
                    </div>
                    <span className="mono" style={{ fontSize: 10, fontWeight: 600, width: 56, flex: '0 0 56px', color: (v >= 0 ? 'var(--zhu)' : 'var(--dai)') }}>{(v * 100).toFixed(2) + '%'}</span>
                  </div>
                ))}
              </div>
            </RCard>
            <div style={{ marginTop: 8, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>注:策略期收益对四风格因子收益做 OLS + Newey-West HAC(期收益非重叠→lag {result.nw_lag != null ? result.nw_lag : 1})。β=风格暴露(载荷),R²=风格解释力,alpha=风格无法解释的超额(A股无干净无风险利率→未减 rf,非严格 CAPM α),贡献=β×因子均值。风格因子(MKT全池等权/SMB按total_mv/HML按1/pb/WML按mom_120)在所选股票池内构建。{warns.slice(0, 1).join('')}</div>
          </React.Fragment>)}
        </div>
      </div>
    );
  }
  if (result && result.method === 'tvbeta' && !loading && !error) {
    const bp = result.beta_path || [];
    const warns = (result.warnings || []).concat(result._warnings || []);
    const freqLab = ({ day: '日频', week: '周频', month: '月频' })[result.freq] || result.freq || '';
    const sb = result.static_beta;
    const ppy = result.ppy || 52;
    const filtVals = bp.map(r => r[1]).filter(v => v != null && isFinite(v));
    const fMin = filtVals.length ? Math.min(...filtVals) : null, fMax = filtVals.length ? Math.max(...filtVals) : null;
    const pa2 = (v, d = 2) => (v == null ? '—' : (+v).toFixed(d));
    // β(t) 路径图:置信带 + 平滑线(全样本去噪)+ 滤波线(实时因果)+ 静态β参考 + β=1 参考
    const W = 1000, H = 240, padL = 44, padR = 14, padT = 12, padB = 28;
    const n = bp.length;
    const allY = bp.flatMap(r => [r[1], r[3], r[4]]).concat([sb, 1.0]).filter(v => v != null && isFinite(v));
    let yMin = allY.length ? Math.min(...allY) : 0, yMax = allY.length ? Math.max(...allY) : 1.5;
    const yPad = (yMax - yMin) * 0.08 || 0.1; yMin -= yPad; yMax += yPad;
    const xs = i => padL + (n <= 1 ? 0 : (i / (n - 1)) * (W - padL - padR));
    const ys = v => padT + (1 - (v - yMin) / ((yMax - yMin) || 1)) * (H - padT - padB);
    const smPts = bp.map((r, i) => xs(i) + ',' + ys(r[2])).join(' ');
    const ftPts = bp.map((r, i) => xs(i) + ',' + ys(r[1])).join(' ');
    const bandPath = bp.length
      ? ('M' + bp.map((r, i) => xs(i) + ',' + ys(r[3])).join(' L') + ' L' + bp.slice().reverse().map((r, i) => xs(n - 1 - i) + ',' + ys(r[4])).join(' L') + ' Z')
      : '';
    const ytickVals = Array.from({ length: 5 }, (_, i) => yMin + (i / 4) * (yMax - yMin));
    const xtickIdx = n <= 1 ? [0] : [0, Math.floor(n / 4), Math.floor(n / 2), Math.floor(3 * n / 4), n - 1];
    return (
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: expanded ? 'calc(100vh - 90px)' : 304, background: 'var(--paper)', borderTop: '1px solid var(--ink)', boxShadow: '0 -12px 32px rgba(28,24,20,0.12)', zIndex: 8, display: 'flex', flexDirection: 'column', animation: 'fadeIn .3s ease', transition: 'height .2s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 18px', borderBottom: '1px solid var(--line-soft)' }}>
          <span style={{ width: 20, height: 20, borderRadius: 5, background: 'var(--dai)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>✓</span>
          <span className="serif" style={{ fontSize: 14, fontWeight: 600 }}>运行完成 · 时变β(Kalman)</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{(result._universe || '') + ' · ' + (result._label || '') + (result.n != null ? ' · ' + result.n + '期' + (freqLab ? '(' + freqLab + ')' : '') : '') + (result.market ? ' · 市场=' + result.market : '')}</span>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <span onClick={() => setExpanded(e => !e)} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>{expanded ? '⤡ 收起' : '⤢ 展开'}</span>
            <span onClick={onExport} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>↧ 导出报告</span>
            <span onClick={onClose} style={{ fontSize: 16, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 4px' }}>✕</span>
          </span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '13px 18px' }}>
          {result.ok_model === false ? (
            <div style={{ fontSize: 12, color: 'var(--dai)', lineHeight: 1.7, padding: '10px 0' }}>时变β未启用:<br />{result.reason || warns.join(' ') || '组合期收益样本不足(时变β需 ≥24 期);请加宽时间窗或用更密调仓频率。'}</div>
          ) : (<React.Fragment>
            <div style={{ display: 'flex', gap: 22, marginBottom: 12, flexWrap: 'wrap' }}>
              {[['静态β(全样本OLS)', pa2(sb, 3), 'var(--ink)'],
                ['当前β(平滑末值)', pa2(result.beta_end, 3), (result.beta_end >= 1 ? 'var(--dai)' : 'var(--zhu)')],
                ['滤波β区间(实时)', (fMin != null ? pa2(fMin, 2) + ' ~ ' + pa2(fMax, 2) : '—'), 'var(--ink-1)'],
                ['R²(市场解释力)', pa2(result.r2, 3), 'var(--ink-1)'],
                ['平滑度 qr', (result.qr != null ? (+result.qr).toExponential(1) : '—'), 'var(--ink-2)'],
                ['样本期数', result.n != null ? result.n : '—', 'var(--ink-1)']].map(([l, v, c], i) => (
                <div key={i}><div className="mono" style={{ fontSize: 8.5, letterSpacing: '.08em', color: 'var(--ink-3)' }}>{l}</div><div className="mono" style={{ fontSize: 17, fontWeight: 600, color: c, marginTop: 2 }}>{v}</div></div>
              ))}
            </div>
            <RCard title={'市场β 随时间演化 · 平滑(全样本·去噪)+ 滤波(实时·因果)+ ±2se 带 · 朱虚线=静态β / 灰点线=β1'}>
              <svg viewBox={'0 0 ' + W + ' ' + H} width="100%" style={{ height: 'auto', display: 'block' }}>
                {ytickVals.map((v, i) => (
                  <g key={'y' + i}>
                    <line x1={padL} y1={ys(v)} x2={W - padR} y2={ys(v)} stroke="var(--line-soft)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
                    <text x={padL - 5} y={ys(v) + 3} textAnchor="end" fontSize="9" fill="var(--ink-3)" className="mono">{v.toFixed(2)}</text>
                  </g>
                ))}
                {(1.0 >= yMin && 1.0 <= yMax) && <line x1={padL} y1={ys(1.0)} x2={W - padR} y2={ys(1.0)} stroke="var(--ink-3)" strokeWidth="1" strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />}
                {sb != null && <line x1={padL} y1={ys(sb)} x2={W - padR} y2={ys(sb)} stroke="var(--zhu)" strokeWidth="1.2" strokeDasharray="6 4" vectorEffect="non-scaling-stroke" opacity="0.7" />}
                {bandPath && <path d={bandPath} fill="var(--zhu)" opacity="0.10" stroke="none" />}
                {bp.length > 1 && <polyline points={ftPts} fill="none" stroke="var(--dai)" strokeWidth="1" opacity="0.55" vectorEffect="non-scaling-stroke" />}
                {bp.length > 1 && <polyline points={smPts} fill="none" stroke="var(--zhu)" strokeWidth="2" vectorEffect="non-scaling-stroke" />}
                {xtickIdx.map((i, k) => (
                  <text key={'x' + k} x={xs(i)} y={H - padB + 16} textAnchor={k === 0 ? 'start' : (k === xtickIdx.length - 1 ? 'end' : 'middle')} fontSize="8.5" fill="var(--ink-3)" className="mono">{(bp[i] && bp[i][0]) || ''}</text>
                ))}
              </svg>
              <div style={{ display: 'flex', gap: 16, marginTop: 6, fontSize: 9, color: 'var(--ink-3)' }}>
                <span><span style={{ display: 'inline-block', width: 14, height: 2, background: 'var(--zhu)', verticalAlign: 'middle', marginRight: 4 }} />平滑 β(t)·去噪</span>
                <span><span style={{ display: 'inline-block', width: 14, height: 2, background: 'var(--dai)', opacity: 0.55, verticalAlign: 'middle', marginRight: 4 }} />滤波 β(t)·实时因果</span>
                <span><span style={{ display: 'inline-block', width: 14, height: 0, borderTop: '1.2px dashed var(--zhu)', verticalAlign: 'middle', marginRight: 4 }} />静态β {pa2(sb, 3)}</span>
              </div>
            </RCard>
            <div style={{ marginTop: 8, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>注:策略期收益对市场期收益做时变参数回归 r=α_t+β_t·m_t(系数随机游走,Kalman 滤波 + RTS 平滑)。<b>滤波 β(t)</b>=每期实时单边因果估计(无前视,含估计噪声);<b>平滑 β(t)</b>=全样本双边去噪(看历史演化)。平滑度 qr 由浓缩似然 MLE 自选——<b>平滑线接近平直 = 无统计显著 β 漂移</b>(滤波线的周度抖动属估计噪声,非真漂移);平滑线随时间起伏 = 真实择时/风格切换。年化 α≈{pa2(result.alpha_mean != null ? result.alpha_mean * ppy * 100 : null, 2)}%。{warns.slice(0, 1).join('')}</div>
          </React.Fragment>)}
        </div>
      </div>
    );
  }
  if (result && result.method === 'garch' && !loading && !error) {
    const gd = result.garch || {};
    const pg = (v, d = 2) => (v == null ? '—' : ((v * 100).toFixed(d) + '%'));
    const annf = Math.sqrt(result.ppy || 52);
    const vp = result.vol_path || [];               // [date, gvol, evol](单期口径)
    const fc = result.forecast || [];               // [step, vol, ann_vol]
    const warns = (result.warnings || []).concat(result._warnings || []);
    const gSer = vp.map(p => p[1] * annf), eSer = vp.map(p => p[2] * annf);   // 年化条件波动
    const fcSer = fc.map(p => p[2]);                                          // 预测(已年化)
    const uncA = gd.uncond_vol_annual;
    const freqLab = ({ day: '日频', week: '周频', month: '月频' })[result.freq] || result.freq || '';
    const _shortD = s => String(s || '').slice(2, 10);
    return (
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: expanded ? 'calc(100vh - 90px)' : 304, background: 'var(--paper)', borderTop: '1px solid var(--ink)', boxShadow: '0 -12px 32px rgba(28,24,20,0.12)', zIndex: 8, display: 'flex', flexDirection: 'column', animation: 'fadeIn .3s ease', transition: 'height .2s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 18px', borderBottom: '1px solid var(--line-soft)' }}>
          <span style={{ width: 20, height: 20, borderRadius: 5, background: 'var(--dai)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>✓</span>
          <span className="serif" style={{ fontSize: 14, fontWeight: 600 }}>运行完成 · 条件波动预测</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{(result._universe || '') + ' · ' + (result._label || '') + (result.n != null ? ' · ' + result.n + '期' + (freqLab ? '(' + freqLab + ')' : '') : '')}</span>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <span onClick={() => setExpanded(e => !e)} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>{expanded ? '⤡ 收起' : '⤢ 展开'}</span>
            <span onClick={onExport} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>↧ 导出报告</span>
            <span onClick={onClose} style={{ fontSize: 16, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 4px' }}>✕</span>
          </span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '13px 18px' }}>
          {result.ok_model === false ? (
            <div style={{ fontSize: 12, color: 'var(--dai)', lineHeight: 1.7, padding: '10px 0' }}>条件波动模型未启用:<br />{result.reason || warns.join(' ') || '组合收益样本不足(GARCH 需 ≥60 期);请加宽时间窗或用更密调仓频率。'}</div>
          ) : (<React.Fragment>
            <div style={{ display: 'flex', gap: 22, marginBottom: 12, flexWrap: 'wrap' }}>
              {[['样本期数', result.n != null ? result.n : '—', 'var(--ink)'], ['当前年化波动(GARCH)', pg(result.current_vol_annual), 'var(--zhu)'], ['下一步预测年化波动', pg(result.next_vol_annual), 'var(--dai)'], ['无条件波动(年化)', pg(uncA), 'var(--ink-1)'], ['持续度 α+β', gd.persistence != null ? (+gd.persistence).toFixed(3) : '—', 'var(--ink-1)'], ['EWMA 年化波动', pg(result.ewma_vol_annual), 'var(--ink-2)']].map(([l, v, c], i) => (
                <div key={i}><div className="mono" style={{ fontSize: 8.5, letterSpacing: '.08em', color: 'var(--ink-3)' }}>{l}</div><div className="mono" style={{ fontSize: 17, fontWeight: 600, color: c, marginTop: 2 }}>{v}</div></div>
              ))}
            </div>
            {gSer.length > 1 && (() => {
              const N = gSer.length + fcSer.length, W = 920, H = 132, padBot = 16;
              const allv = gSer.concat(eSer, fcSer, uncA != null ? [uncA] : []);
              const vmax = Math.max(...allv) * 1.05, vmin = Math.min(...allv) * 0.95, vrng = (vmax - vmin) || 1;
              const xOf = i => (N > 1 ? i / (N - 1) * W : 0);
              const yOf = v => (H - padBot) - (v - vmin) / vrng * (H - padBot - 4);
              const ml = gSer.length;
              const poly = (arr, off) => arr.map((v, i) => xOf(i + off).toFixed(1) + ',' + yOf(v).toFixed(1)).join(' ');
              const fcPts = (ml ? [xOf(ml - 1).toFixed(1) + ',' + yOf(gSer[ml - 1]).toFixed(1)] : []).concat(fcSer.map((v, i) => xOf(ml + i).toFixed(1) + ',' + yOf(v).toFixed(1))).join(' ');
              const dAx = [vp[0] && _shortD(vp[0][0]), vp[(ml - 1) >> 1] && _shortD(vp[(ml - 1) >> 1][0]), vp[ml - 1] && _shortD(vp[ml - 1][0])];
              return (
                <RCard title={'年化条件波动路径 + 向前 ' + (result.horizon || fcSer.length) + ' 步预测 · GARCH(朱实线)/ EWMA(灰线)/ 预测(朱虚线)/ 无条件波动(黛虚线)'}>
                  <svg viewBox={'0 0 ' + W + ' ' + (H + 14)} style={{ width: '100%', height: 'auto' }}>
                    {uncA != null && <line x1="0" y1={yOf(uncA).toFixed(1)} x2={W} y2={yOf(uncA).toFixed(1)} stroke="var(--dai)" strokeWidth="1" strokeDasharray="5 3" />}
                    {ml > 0 && <line x1={xOf(ml - 1).toFixed(1)} y1="0" x2={xOf(ml - 1).toFixed(1)} y2={H - padBot} stroke="var(--ink-3)" strokeWidth="0.5" strokeDasharray="2 3" />}
                    <polyline points={poly(eSer, 0)} fill="none" stroke="rgba(28,24,20,0.30)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
                    <polyline points={poly(gSer, 0)} fill="none" stroke="var(--zhu)" strokeWidth="1.4" vectorEffect="non-scaling-stroke" />
                    <polyline points={fcPts} fill="none" stroke="var(--zhu)" strokeWidth="1.4" strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />
                    {fcSer.map((v, i) => <circle key={i} cx={xOf(ml + i).toFixed(1)} cy={yOf(v).toFixed(1)} r="1.6" fill="var(--dai)" {..._hv('预测 +' + (i + 1) + ' 步 · 年化 ' + (v * 100).toFixed(2) + '%')} />)}
                    {[0, 1, 2].map(i => { const v = vmin + vrng * (i / 2); return <text key={'y' + i} x="2" y={(yOf(v) - 2).toFixed(1)} className="mono" style={{ fontSize: 7, fill: 'var(--ink-3)' }}>{(v * 100).toFixed(1) + '%'}</text>; })}
                    {dAx.map((d, i) => d ? <text key={'x' + i} x={(i === 0 ? 2 : i === 1 ? W / 2 : W - 4).toFixed(1)} y={H + 8} textAnchor={i === 0 ? 'start' : i === 1 ? 'middle' : 'end'} className="mono" style={{ fontSize: 7, fill: 'var(--ink-3)' }}>{d}</text> : null)}
                  </svg>
                </RCard>
              );
            })()}
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 4 }}>
              <div style={{ flex: '1 1 320px', padding: '9px 12px', background: 'rgba(28,24,20,0.025)', borderRadius: 8 }}>
                <div className="serif" style={{ fontSize: 12, fontWeight: 600, marginBottom: 5 }}>GARCH(1,1) 参数 · 高斯 MLE{gd.converged === false ? '(未收敛 → EWMA 等价降级)' : ''}</div>
                <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                  {[['ω', gd.omega != null ? (+gd.omega).toExponential(2) : '—'], ['α(冲击)', gd.alpha != null ? (+gd.alpha).toFixed(3) : '—'], ['β(记忆)', gd.beta != null ? (+gd.beta).toFixed(3) : '—'], ['持续度 α+β', gd.persistence != null ? (+gd.persistence).toFixed(3) : '—'], ['对数似然', gd.loglik != null ? (+gd.loglik).toFixed(1) : '—'], ['收敛', gd.converged ? '✓' : '✗']].map(([l, v], i) => (
                    <span key={i} style={{ display: 'inline-flex', flexDirection: 'column' }}><span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>{l}</span><span className="mono" style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-1)' }}>{v}</span></span>
                  ))}
                </div>
              </div>
              <div style={{ flex: '1 1 250px', padding: '9px 12px', background: 'rgba(28,24,20,0.025)', borderRadius: 8 }}>
                <div className="serif" style={{ fontSize: 12, fontWeight: 600, marginBottom: 5 }}>波动预测走向</div>
                <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'baseline' }}>
                  {[['+1 步', pg(result.next_vol_annual)], ['+' + (result.horizon || fcSer.length) + ' 步', fcSer.length ? pg(fcSer[fcSer.length - 1]) : '—'], ['无条件', pg(uncA)]].map(([l, v], i) => (
                    <span key={i} style={{ display: 'inline-flex', flexDirection: 'column' }}><span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>{l}</span><span className="mono" style={{ fontSize: 12, fontWeight: 600 }}>{v}</span></span>
                  ))}
                  {result.next_vol_annual != null && uncA != null && (
                    <span className="mono" style={{ fontSize: 11, fontWeight: 600, color: result.next_vol_annual < uncA ? 'var(--zhu)' : 'var(--dai)' }}>{result.next_vol_annual < uncA ? '↗ 波动偏低 · 预计回升' : '↘ 波动偏高 · 预计回落'}</span>
                  )}
                </div>
              </div>
            </div>
            <div style={{ marginTop: 8, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>注:EWMA(λ={result.ewma_lambda != null ? (+result.ewma_lambda).toFixed(2) : '0.94'})= RiskMetrics 指数加权波动;GARCH(1,1)= 用 MLE 拟合波动聚集(α=对冲击的反应、β=对历史波动的记忆,α+β=持续度);多步预测随步数均值回复到无条件波动。单期口径=调仓频率({freqLab})。{warns.slice(0, 1).join('')}</div>
          </React.Fragment>)}
        </div>
      </div>
    );
  }
  if (result && result.method === 'risk' && !loading && !error) {
    const rk = result.risk || {};
    const pct = (v, d = 2) => (v == null ? '—' : ((v * 100).toFixed(d) + '%'));
    const lv = rk.levels || {}, l95 = lv['95'] || {}, l99 = lv['99'] || {};
    const evt = rk.evt || {}, kp = rk.kupiec || {}, mc = rk.mc || {};
    const warns = (rk.warnings || []).concat(result._warnings || []);
    return (
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: expanded ? 'calc(100vh - 90px)' : 304, background: 'var(--paper)', borderTop: '1px solid var(--ink)', boxShadow: '0 -12px 32px rgba(28,24,20,0.12)', zIndex: 8, display: 'flex', flexDirection: 'column', animation: 'fadeIn .3s ease', transition: 'height .2s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 18px', borderBottom: '1px solid var(--line-soft)' }}>
          <span style={{ width: 20, height: 20, borderRadius: 5, background: 'var(--dai)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>✓</span>
          <span className="serif" style={{ fontSize: 14, fontWeight: 600 }}>运行完成 · 风险度量</span>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{(result._universe || '') + ' · ' + (result._label || '') + (rk.n_periods != null ? ' · ' + rk.n_periods + '期' : '')}</span>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <span onClick={() => setExpanded(e => !e)} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>{expanded ? '⤡ 收起' : '⤢ 展开'}</span>
            <span onClick={onExport} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>↧ 导出报告</span>
            <span onClick={onClose} style={{ fontSize: 16, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 4px' }}>✕</span>
          </span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '13px 18px' }}>
          {rk.enabled === false ? (
            <div style={{ fontSize: 12, color: 'var(--dai)', lineHeight: 1.7, padding: '10px 0' }}>风险度量未启用(组合收益样本不足):<br />{warns.join(' ') || '需 ≥20 个组合期收益点;请加宽时间窗或用更密调仓频率。'}</div>
          ) : (<React.Fragment>
            <div style={{ display: 'flex', gap: 22, marginBottom: 12, flexWrap: 'wrap' }}>
              {[['样本期数', rk.n_periods != null ? rk.n_periods : '—', 'var(--ink)'], ['年化波动', pct(rk.ann_vol), 'var(--ink-1)'], ['VaR 95%(历史)', pct(l95.hist_var), 'var(--dai)'], ['CVaR 95%', pct(l95.hist_cvar), 'var(--zhu)'], ['VaR 99%(历史)', pct(l99.hist_var), 'var(--dai)'], ['EVT 尾型 ξ', evt.enabled ? (+evt.shape_xi).toFixed(2) : '—', 'var(--ink-1)']].map(([l, v, c], i) => (
                <div key={i}><div className="mono" style={{ fontSize: 8.5, letterSpacing: '.08em', color: 'var(--ink-3)' }}>{l}</div><div className="mono" style={{ fontSize: 17, fontWeight: 600, color: c, marginTop: 2 }}>{v}</div></div>
              ))}
            </div>
            <RCard title={'VaR / CVaR · 三法对照(损失=负收益的上分位;单期口径=调仓频率,默认周频)'}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <thead><tr>{['置信度', 'VaR 历史', 'VaR 参数', 'VaR 蒙特卡罗', 'CVaR 历史', 'CVaR 参数'].map((h, i) => (<th key={i} className="mono" style={{ textAlign: i ? 'right' : 'left', fontWeight: 400, fontSize: 8.5, padding: '3px 6px', letterSpacing: '.04em', color: 'var(--ink-3)' }}>{h}</th>))}</tr></thead>
                <tbody>
                  {[['95%', l95, mc.var95], ['99%', l99, mc.var99]].map(([lab, L, mcv], i) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--line-soft)' }}>
                      <td className="mono" style={{ padding: '5px 6px', fontWeight: 600 }}>{lab}</td>
                      <td className="mono" style={{ textAlign: 'right', color: 'var(--dai)', fontWeight: 600 }}>{pct(L.hist_var)}</td>
                      <td className="mono" style={{ textAlign: 'right', color: 'var(--ink-2)' }}>{pct(L.param_var)}</td>
                      <td className="mono" style={{ textAlign: 'right', color: 'var(--ink-2)' }}>{pct(mcv)}</td>
                      <td className="mono" style={{ textAlign: 'right', color: 'var(--zhu)', fontWeight: 600 }}>{pct(L.hist_cvar)}</td>
                      <td className="mono" style={{ textAlign: 'right', color: 'var(--ink-2)' }}>{pct(L.param_cvar)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </RCard>
            {rk.hist && rk.hist.counts && rk.hist.counts.length > 1 && (() => {
              const cs = rk.hist.counts, eg = rk.hist.edges, cmax = Math.max(1, ...cs);
              const W = 920, H = 120, padBot = 14, xmin = eg[0], xmax = eg[eg.length - 1];
              const xOf = v => (xmax > xmin ? (v - xmin) / (xmax - xmin) * W : 0);
              const var95r = l95.hist_var != null ? -l95.hist_var : null, cvar95r = l95.hist_cvar != null ? -l95.hist_cvar : null;
              return (
                <RCard title={'组合期收益分布 · VaR95(朱色虚线)/ CVaR95(黛色实线)· 左尾=亏损'}>
                  <svg viewBox={'0 0 ' + W + ' ' + H} style={{ width: '100%', height: 'auto' }}>
                    {cs.map((c, i) => {
                      const x0 = xOf(eg[i]), x1 = xOf(eg[i + 1]), hh = c / cmax * (H - padBot);
                      return <rect key={i} x={x0.toFixed(1)} y={(H - padBot - hh).toFixed(1)} width={Math.max(0.5, x1 - x0 - 0.6).toFixed(1)} height={hh.toFixed(1)} fill={eg[i] < 0 ? 'rgba(168,57,45,0.5)' : 'rgba(28,24,20,0.22)'} {..._hv((eg[i] * 100).toFixed(1) + '%~' + (eg[i + 1] * 100).toFixed(1) + '% · ' + c + '期')} />;
                    })}
                    <line x1={xOf(0).toFixed(1)} y1="0" x2={xOf(0).toFixed(1)} y2={H - padBot} stroke="var(--ink-3)" strokeWidth="0.5" strokeDasharray="2 2" />
                    {var95r != null && <line x1={xOf(var95r).toFixed(1)} y1="0" x2={xOf(var95r).toFixed(1)} y2={H - padBot} stroke="var(--zhu)" strokeWidth="1.2" strokeDasharray="4 2" />}
                    {cvar95r != null && <line x1={xOf(cvar95r).toFixed(1)} y1="0" x2={xOf(cvar95r).toFixed(1)} y2={H - padBot} stroke="var(--dai)" strokeWidth="1.2" />}
                  </svg>
                </RCard>
              );
            })()}
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 4 }}>
              <div style={{ flex: '1 1 290px', padding: '9px 12px', background: 'rgba(28,24,20,0.025)', borderRadius: 8 }}>
                <div className="serif" style={{ fontSize: 12, fontWeight: 600, marginBottom: 5 }}>EVT 极值理论 · POT + GPD 尾部</div>
                {evt.enabled ? (
                  <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                    {[['尾型', evt.tail_note || '—'], ['形状 ξ', (+evt.shape_xi).toFixed(3)], ['VaR 99.5%', pct(evt.var_995)], ['VaR 99.9%', pct(evt.var_999)], ['CVaR 99.5%', evt.cvar_995 != null ? pct(evt.cvar_995) : '—'], ['超阈值数', evt.n_exceed]].map(([l, v], i) => (
                      <span key={i} style={{ display: 'inline-flex', flexDirection: 'column' }}><span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>{l}</span><span className="mono" style={{ fontSize: 12, fontWeight: 600, color: (String(l).indexOf('VaR') >= 0 ? 'var(--dai)' : 'var(--ink-1)') }}>{v}</span></span>
                    ))}
                  </div>
                ) : <div style={{ fontSize: 10, color: 'var(--ink-3)' }}>{evt.note || 'EVT 未启用'}</div>}
              </div>
              <div style={{ flex: '1 1 250px', padding: '9px 12px', background: 'rgba(28,24,20,0.025)', borderRadius: 8 }}>
                <div className="serif" style={{ fontSize: 12, fontWeight: 600, marginBottom: 5 }}>Kupiec VaR 回测 · 95%</div>
                {kp.n ? (
                  <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'baseline' }}>
                    {[['突破', kp.breaches + '/' + kp.n], ['突破率', pct(kp.breach_rate)], ['期望率', pct(kp.expected_rate)], ['LR 统计', kp.lr_stat != null ? (+kp.lr_stat).toFixed(3) : '—']].map(([l, v], i) => (
                      <span key={i} style={{ display: 'inline-flex', flexDirection: 'column' }}><span className="mono" style={{ fontSize: 8, color: 'var(--ink-3)' }}>{l}</span><span className="mono" style={{ fontSize: 12, fontWeight: 600 }}>{v}</span></span>
                    ))}
                    <span className="mono" style={{ fontSize: 11, fontWeight: 600, color: kp.reject_5pct ? 'var(--dai)' : 'var(--zhu)' }}>{kp.reject_5pct ? '✗ 校准存疑(拒绝5%)' : '✓ 校准良好'}</span>
                  </div>
                ) : <div style={{ fontSize: 10, color: 'var(--ink-3)' }}>样本不足</div>}
              </div>
            </div>
            <div style={{ marginTop: 8, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>注:VaR=给定置信度下的最大潜在损失(损失分布分位);CVaR/ES=超过 VaR 的平均损失(尾部更稳健);EVT 用极值分布(GPD)拟合尾部估超高分位罕见巨亏;Kupiec 看历史突破率是否偏离理论=VaR 校准检验。单期口径=调仓频率(默认周频)。{warns.slice(0, 1).join('')}</div>
          </React.Fragment>)}
        </div>
      </div>
    );
  }
  // 真数据填充 (布局/样式不变): KPI / IC序列 / 十分位 / 净值 全部取自引擎报告
  const ic = (result && result.ic) || {}, pf = (result && result.portfolio) || {}, qt = (result && result.quantile) || {};
  const presentation = wfResultPresentation(result);
  const kpi = [
    ['RankIC', _n2(ic.rank_ic_mean, 4), (ic.rank_ic_mean >= 0 ? 'up' : 'down')],
    ['ICIR', _n2(ic.icir, 2), ''],
    ['IC胜率', _pct(ic.ic_win_rate, 0), (ic.ic_win_rate >= 0.5 ? 'up' : '')],
    ['IC-t值', _n2(ic.ic_tstat, 1), ''],
    [presentation.annualLabel, _pct(pf.net_ann != null ? pf.net_ann : pf.ann_return), ((pf.net_ann != null ? pf.net_ann : pf.ann_return) >= 0 ? 'up' : 'down')],
    ...(pf.gross_ann != null ? [['年化(毛)', _pct(pf.gross_ann), (pf.gross_ann >= 0 ? 'up' : 'down')]] : []),
    ['Sharpe', _n2(pf.sharpe, 2), ''],
    ...(pf.sortino != null ? [['Sortino', _n2(pf.sortino, 2), (pf.sortino >= 0 ? 'up' : 'down')]] : []),
    ['最大回撤', _pct(pf.max_drawdown), 'down'],
    ['Calmar', _n2(pf.calmar, 2), ''],
    ...(pf.information_ratio != null ? [['信息比率', _n2(pf.information_ratio, 2), (pf.information_ratio >= 0 ? 'up' : 'down')]] : []),
    ...(pf.win_rate != null ? [['组合胜率', _pct(pf.win_rate, 0), (pf.win_rate >= 0.5 ? 'up' : '')]] : []),
    ['换手率', _pct(pf.turnover, 0), ''],
    ...(pf.total_cost != null ? [['总成本', _pct(pf.total_cost, 2), 'down']] : []),
    ['单调性', _n2(qt.monotonicity, 2), (qt.monotonicity >= 0 ? 'up' : 'down')],
  ];
  // #4 口径修真:优先用逐期 rank-IC 序列(与"RankIC"标题一致;抗异常值),无则回退 Pearson ic_series。
  const icSerFull = (ic.rank_ic_series || ic.ic_series || []);
  const icShow = icSerFull.slice(-60).map(p => p[1]);     // 近60期(去掉原来死板的24期截断)
  const icMax = Math.max(1e-6, ...icShow.map(v => Math.abs(v)));
  const icBars = icShow.map(v => v / icMax * 40);
  const icBarW = Math.max(2, Math.min(11, 292 / Math.max(1, icBars.length)));   // 条宽随期数自适应
  // IC 月度热力图: 按 年-月 聚合 ic_series 求均值
  const _mon = {};
  icSerFull.forEach(p => { const ym = String(p[0]).slice(0, 7); if (ym.length === 7) { (_mon[ym] = _mon[ym] || [0, 0]); _mon[ym][0] += p[1]; _mon[ym][1]++; } });
  const heatYears = [...new Set(Object.keys(_mon).map(k => k.slice(0, 4)))].sort();
  const heatMax = Math.max(1e-6, ...Object.values(_mon).map(([s, c]) => Math.abs(s / c)));
  // #2 截面 IC 衰减谱(因子对 1/3/5/10/20 日前向收益的 RankIC;后端 ic_decay,空则不渲染)
  const icd = (result && result.ic_decay) || [];
  const icdPeak = (result && result.ic_decay_peak) || null;
  const icdMax = Math.max(1e-6, ...icd.map(d => Math.abs(d.rank_ic || 0)));
  const dec = (qt.group_ann_return || []);
  const decMax = Math.max(1e-6, ...dec.map(v => Math.abs(v)));
  const decBars = dec.map(v => v / decMax * 74);
  const navPairs = (pf.nav_series || []);
  const nav = navPairs.map(p => p[1]);
  const benchIsReal = !!(pf.bench300_nav && pf.bench300_nav.length);   // W6 真沪深300 优先于等权全池兜底
  const bench = ((benchIsReal ? pf.bench300_nav : pf.benchmark_nav) || []).map(p => p[1]);
  const benchLabel = benchIsReal ? '沪深300' : '等权全池';
  // W6 回撤水下图:每点距历史最高净值的跌幅(≤0);面积从顶线(0)向下挂
  let _pk = -Infinity;
  const ddSeries = navPairs.map(p => { _pk = Math.max(_pk, p[1]); return _pk > 0 ? p[1] / _pk - 1 : 0; });
  const ddMin = ddSeries.length ? Math.min(0, ...ddSeries) : 0;
  const ddRng = Math.abs(ddMin) || 1;
  const ddArea = ddSeries.length > 1
    ? '0,0 ' + ddSeries.map((v, i) => (i / Math.max(1, ddSeries.length - 1) * 420).toFixed(1) + ',' + (Math.abs(v) / ddRng * 84).toFixed(1)).join(' ') + ' 420,0'
    : '';
  // W6 月度收益热力图:每月末净值环比(上月末→本月末),首月以净值起点 1 为基
  const _navMon = {};
  navPairs.forEach(p => { const ym = String(p[0]).slice(0, 7); if (ym.length === 7) _navMon[ym] = p[1]; });
  const _mretKeys = Object.keys(_navMon).sort();
  const _mret = {}; let _pv = 1.0;
  _mretKeys.forEach(ym => { if (_pv > 0) _mret[ym] = _navMon[ym] / _pv - 1; _pv = _navMon[ym]; });
  const mretYears = [...new Set(_mretKeys.map(k => k.slice(0, 4)))].sort();
  const mretMax = Math.max(1e-6, ...Object.values(_mret).map(v => Math.abs(v)));
  const allv = nav.concat(bench);
  const lo = allv.length ? Math.min(...allv) : 0, hi = allv.length ? Math.max(...allv) : 1, rng = (hi - lo) || 1;
  const ptsOf = (arr, w) => arr.map((v, i) => (i / (Math.max(1, arr.length - 1)) * w).toFixed(1) + ',' + (88 - (v - lo) / rng * 80).toFixed(1)).join(' ');
  // ── 坐标轴辅助(据工作流真数据补全横纵轴)──
  const _shortD = s => String(s || '').slice(2, 10);                       // YY-MM-DD
  const icDates = icSerFull.slice(-60).map(p => _shortD(p[0]));            // IC 时序 x 轴(调仓日)
  const navDates = navPairs.map(p => _shortD(p[0]));                       // 净值 x 轴(日期)
  const _ax3 = arr => (arr.length ? [arr[0], arr[(arr.length - 1) >> 1], arr[arr.length - 1]] : ['', '', '']);
  const icAx = _ax3(icDates), navAx = _ax3(navDates);                      // 首/中/末 三个日期标
  const decVmax = Math.max(0, ...(dec.length ? dec : [0]));                // 十分位 y 轴上界
  const decVmin = Math.min(0, ...(dec.length ? dec : [0]));               // 下界(含 0)
  const decSpan = (decVmax - decVmin) || 1;
  const spread = qt.long_short_spread;
  const isPf = !!(result && result.method === 'portfolio_build');
  const orth = (result && result.orthogonalization) || null;
  const structuredFactor = !!(result && result.factor_spec && result.factor_spec.schema === 'guanlan.factor_spec.v1');
  const insight = result
    ? (((result._warnings && result._warnings.length) ? result._warnings.join(' · ') + ' ' : '')
        + (isPf
            ? ('组合构建 · /portfolio/build · ' + (result.n_holdings || 0) + ' 只目标持仓(' + (result.weighting || '') + (result.industry_neutral ? ' · 行业中性' : '') + ')。')
            : ((spread != null ? '十分位多空价差 ' + _pct(spread) + '。' : '')
               + presentation.diagnostic + ' · ' + nav.length + (presentation.isBacktest ? ' 个回测净值点。' : ' 个分析组合曲线点。'))))
    : '';
  return (
    <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: expanded ? 'calc(100vh - 90px)' : 304, background: 'var(--paper)', borderTop: '1px solid var(--ink)', boxShadow: '0 -12px 32px rgba(28,24,20,0.12)', zIndex: 8, display: 'flex', flexDirection: 'column', animation: 'fadeIn .3s ease', transition: 'height .2s ease' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 18px', borderBottom: '1px solid var(--line-soft)' }}>
        <span style={{ width: 20, height: 20, borderRadius: 5, background: durableWaiting || completedWithErrors ? '#9a6b16' : (error ? 'var(--zhu)' : 'var(--dai)'), color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{durableWaiting ? '⋯' : error ? '✕' : loading ? '⋯' : completedWithErrors ? '!' : '✓'}</span>
        <span className="serif" style={{ fontSize: 14, fontWeight: 600 }}>{durableWaiting ? '持久任务运行中 · 自动重连' : error ? '运行失败' : loading ? '运行中 · 引擎计算' : completedWithErrors ? '运行完成 · 含节点错误' : presentation.resultTitle}</span>
        <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{result ? (result._universe + ' · ' + result._label) : ''}</span>
        {result && result.neutralize && result.neutralize.applied
          ? <span className="mono" title={result.neutralize.note || ''} style={{ fontSize: 9, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 4, padding: '2px 7px' }}>{'已中性化' + (result.neutralize.by ? ' · ' + result.neutralize.by : '')}</span>
          : (result && result.neutralize && result.neutralize.requested
              ? <span className="mono" title={result.neutralize.note || ''} style={{ fontSize: 9, color: 'var(--dai)', border: '1px dashed var(--line)', borderRadius: 4, padding: '2px 7px' }}>中性化跳过</span>
               : null)}
        {orth && orth.requested
          ? <span className="mono" title={orth.note || ''} style={{ fontSize: 9, color: orth.applied ? 'var(--paper)' : '#9a6b16', background: orth.applied ? 'var(--yin)' : 'transparent', border: orth.applied ? 'none' : '1px dashed #9a6b16', borderRadius: 4, padding: '2px 7px' }}>{orth.applied ? '已正交化' : '正交化未物化'}</span>
          : null}
        {structuredFactor ? <span className="mono" title="保存后可按因子库名称完整重放同一变换" style={{ fontSize: 9, color: 'var(--dai)', border: '1px solid var(--dai)', borderRadius: 4, padding: '2px 7px' }}>factor_spec · 可重放</span> : null}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {durableWaiting && <span onClick={onStopWaiting} className="serif" title="不取消服务端任务" style={{ fontSize: 12, color: '#9a6b16', border: '1px solid #9a6b16', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>停止等待 / 放弃重连</span>}
          <span onClick={() => setExpanded(e => !e)} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>{expanded ? '⤡ 收起' : '⤢ 展开'}</span>
          <span onClick={onExport} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '5px 11px', cursor: 'pointer' }}>↧ 导出报告</span>
          <span onClick={onClose} style={{ fontSize: 16, color: 'var(--ink-3)', cursor: 'pointer', padding: '0 4px' }}>✕</span>
        </span>
      </div>
      {durableWaiting ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div style={{ maxWidth: 650, display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', border: '1px solid #9a6b16', borderRadius: 9, background: 'rgba(154,107,22,0.05)' }}>
            <span style={{ width: 22, height: 22, flex: '0 0 auto', border: '2.5px solid var(--line)', borderTopColor: '#9a6b16', borderRadius: '50%', animation: 'spin .8s linear infinite' }} />
            <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.6 }}>{error || '正在按任务编号查询服务端进度。默认会持续重连，避免重复启动重任务。'}<br /><b style={{ color: '#9a6b16' }}>「停止等待」只停本地轮询；服务端任务可能仍在运行，重新运行可能重复计算。</b></span>
          </div>
        </div>
      ) : loading ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
          <span style={{ width: 22, height: 22, border: '2.5px solid var(--line)', borderTopColor: 'var(--dai)', borderRadius: '50%', animation: 'spin .8s linear infinite' }} />
          <span className="mono" style={{ fontSize: 12, color: 'var(--ink-3)' }}>引擎正在按工作流节点计算，结果类型以实际终端为准…</span>
        </div>
      ) : error ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <div className="mono" style={{ fontSize: 12, color: 'var(--zhu)', border: '1px solid var(--zhu)', borderRadius: 8, padding: '12px 16px', background: 'rgba(185,74,61,0.05)', maxWidth: 560 }}>引擎报错 · {error}</div>
        </div>
      ) : (<>
      <div style={{ flex: 1, overflowY: 'auto', padding: '13px 18px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '210px 1fr 1fr', gap: 13 }}>
        <RCard title={presentation.metricTitle}>
          <div style={{ display: 'grid', gridTemplateColumns: 'auto auto', justifyContent: 'center', alignContent: 'center', columnGap: 26, rowGap: 9, textAlign: 'center', height: '100%' }}>
            {kpi.map(([l, v, d], i) => (
              <div key={i} title={_kpiTip(l, presentation.isBacktest)} style={{ cursor: 'help' }}><div className="mono" style={{ fontSize: 8.5, letterSpacing: '.12em', color: 'var(--ink-3)' }}>{l}</div>
                <div className={'mono ' + (d || '')} style={{ fontSize: 15, fontWeight: 500, color: d ? undefined : 'var(--ink)', marginTop: 2 }}>{v}</div></div>
            ))}
          </div>
        </RCard>
        <RCard title="IC 时间序列 · 横轴=调仓日 纵轴=RankIC">
          <svg viewBox="0 0 320 116" style={{ width: '100%', height: 'auto', display: 'block' }}>
            <text x="28" y="13" textAnchor="end" fontSize="7.5" fill="var(--ink-3)">{'+' + _n2(icMax, 3)}</text>
            <text x="28" y="55" textAnchor="end" fontSize="7.5" fill="var(--ink-3)">0</text>
            <text x="28" y="97" textAnchor="end" fontSize="7.5" fill="var(--ink-3)">{'−' + _n2(icMax, 3)}</text>
            <line x1="32" y1="52" x2="318" y2="52" stroke="var(--line)" />
            {icShow.map((v, i) => {
              const step = 286 / Math.max(1, icShow.length), bw = Math.max(1, Math.min(11, step) - 1.5), x = 32 + i * step, h = v / icMax * 42;
              const tt = (icDates[i] || '') + ' · RankIC ' + _n2(v, 3);
              return v >= 0
                ? <rect key={i} x={x} y={52 - h} width={bw} height={h} fill="var(--zhu)" style={{ cursor: 'pointer' }} {..._hv(tt)} />
                : <rect key={i} x={x} y={52} width={bw} height={-h} fill="var(--dai)" style={{ cursor: 'pointer' }} {..._hv(tt)} />;
            })}
            {icAx.map((d, i) => <text key={i} x={i === 0 ? 32 : i === 1 ? 175 : 318} y="112" textAnchor={i === 0 ? 'start' : i === 1 ? 'middle' : 'end'} fontSize="7.5" fill="var(--ink-3)">{d}</text>)}
          </svg>
        </RCard>
        <RCard title="十分位年化超额 · 横轴=因子分组(低→高) 纵轴=年化%">
          <svg viewBox="0 0 320 116" style={{ width: '100%', height: 'auto', display: 'block' }}>
            {(() => { const yOf = v => 10 + (decVmax - v) / decSpan * 84, zy = yOf(0); const n = Math.max(1, dec.length), cw = 286 / n; return (<>
              <text x="28" y="13" textAnchor="end" fontSize="7.5" fill="var(--ink-3)">{_pct(decVmax, 0)}</text>
              <text x="28" y={(zy + 3).toFixed(1)} textAnchor="end" fontSize="7.5" fill="var(--ink-3)">0</text>
              {decVmin < 0 && <text x="28" y="97" textAnchor="end" fontSize="7.5" fill="var(--ink-3)">{_pct(decVmin, 0)}</text>}
              <line x1="32" y1={zy.toFixed(1)} x2="318" y2={zy.toFixed(1)} stroke="var(--line)" />
              {dec.map((v, i) => { const y = yOf(v); return <rect key={i} x={(32 + i * cw + cw * 0.16).toFixed(1)} y={Math.min(y, zy).toFixed(1)} width={(cw * 0.68).toFixed(1)} height={Math.abs(y - zy).toFixed(1)} fill={v < 0 ? 'var(--dai)' : 'var(--zhu)'} style={{ cursor: 'pointer' }} {..._hv('第' + (i + 1) + ' 组(' + (i === 0 ? '低' : i === dec.length - 1 ? '高' : '中') + ') · 年化超额 ' + _pct(v))} />; })}
              {dec.map((v, i) => <text key={'g' + i} x={(32 + i * cw + cw / 2).toFixed(1)} y="112" textAnchor="middle" fontSize="7" fill="var(--ink-3)">{i + 1}</text>)}
            </>); })()}
          </svg>
          <div style={{ display: 'flex', gap: 12, marginTop: 4 }}>
            <span className="mono" title="组序与组收益的秩相关;越接近 ±1 越单调,因子越可信" style={{ fontSize: 9, color: 'var(--ink-3)' }}>单调性 {_n2(qt.monotonicity, 2)}</span>
            <span className="mono" style={{ fontSize: 9, color: (spread >= 0 ? 'var(--zhu)' : 'var(--dai)') }}>多空 {_pct(spread)}</span>
          </div>
        </RCard>
        </div>
        {orth && orth.requested && (() => {
          const sm = orth.summary || {}, exps = orth.exposure_summary || [], failed = orth.reason_counts || {};
          const ratio = sm.n_dates ? Math.round((sm.n_ok_dates || 0) / sm.n_dates * 100) : 0;
          return (
            <div style={{ marginTop: 13 }}>
              <RCard title={'因子正交化 · ' + (orth.applied ? '已物化 ' + (sm.n_ok_dates || 0) + '/' + (sm.n_dates || 0) + '日 (' + ratio + '%)' : '失败关闭')}>
                <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                  <div style={{ flex: '1 1 250px', fontSize: 10.5, color: 'var(--ink-2)', lineHeight: 1.55 }}>{orth.note || '无摘要'}<br /><span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>输出覆盖 {sm.output_coverage == null ? '—' : (sm.output_coverage * 100).toFixed(1) + '%'} · 可用/输入 {sm.usable_fraction == null ? '—' : (sm.usable_fraction * 100).toFixed(1) + '%'} · 最大条件数 {sm.max_condition_number == null ? '—' : (+sm.max_condition_number).toFixed(1)}</span></div>
                  <div style={{ flex: '2 1 420px', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    {exps.slice(0, 6).map((row, i) => <span key={i} title={row.exposure} style={{ minWidth: 90 }}><span className="mono" style={{ display: 'block', maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 8.5, color: 'var(--ink-3)' }}>{row.exposure}</span><span className="mono" style={{ fontSize: 11, color: 'var(--ink-1)' }}>{row.mean_abs_before == null ? '—' : (+row.mean_abs_before).toFixed(3)} → <b style={{ color: 'var(--dai)' }}>{row.mean_abs_after == null ? '—' : (+row.mean_abs_after).toFixed(3)}</b></span></span>)}
                    {!exps.length && <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>无可用暴露摘要</span>}
                  </div>
                </div>
                {Object.keys(failed).length > 0 && <div className="mono" style={{ marginTop: 7, fontSize: 9, color: '#9a6b16' }}>跳过日期 · {Object.keys(failed).map(k => k + ' × ' + failed[k]).join(' · ')}</div>}
              </RCard>
            </div>
          );
        })()}
        {icd.length > 0 && (
          <div style={{ marginTop: 13 }}>
            <RCard title={'IC 衰减谱 · 横轴=前向 horizon(日) 纵轴=RankIC' + (icdPeak ? ' · |IC|峰值 ' + icdPeak.h + ' 日(最优持有期参考)' : '')}>
              <svg viewBox="0 0 320 100" style={{ width: '100%', height: 'auto', display: 'block' }}>
                <text x="28" y="13" textAnchor="end" fontSize="7.5" fill="var(--ink-3)">{'+' + _n2(icdMax, 3)}</text>
                <text x="28" y="50" textAnchor="end" fontSize="7.5" fill="var(--ink-3)">0</text>
                <line x1="32" y1="47" x2="318" y2="47" stroke="var(--line)" />
                {icd.map((d, i) => {
                  const n = Math.max(1, icd.length), step = 286 / n, x = 32 + i * step + step * 0.25, bw = step * 0.5;
                  const v = d.rank_ic || 0, h = v / icdMax * 38, isPk = icdPeak && d.h === icdPeak.h;
                  const tt = 'h=' + d.h + ' 日 · RankIC ' + _n2(v, 4) + (d.rank_icir != null ? ' · ICIR ' + _n2(d.rank_icir, 2) : '');
                  return (<g key={i}>
                    <rect x={x} y={v >= 0 ? 47 - h : 47} width={bw} height={Math.abs(h)} fill={isPk ? 'var(--yin)' : (v >= 0 ? 'var(--zhu)' : 'var(--dai)')} style={{ cursor: 'pointer' }} {..._hv(tt)} />
                    <text x={x + bw / 2} y="94" textAnchor="middle" fontSize="7.5" fill={isPk ? 'var(--yin)' : 'var(--ink-3)'} fontWeight={isPk ? 600 : 400}>{d.h}d</text>
                  </g>);
                })}
              </svg>
              <div style={{ marginTop: 4, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>注:同一因子对 1/3/5/10/20 日<b>前向收益</b>的截面 RankIC 谱(引擎逐 horizon 真算,PIT 无前视)。<b style={{ color: 'var(--yin)' }}>|IC| 峰值 horizon</b> ≈ 因子信息最强的持有期;衰减快=高频信号、慢=慢变信号。{icdPeak ? ' 本因子峰值在 ' + icdPeak.h + ' 日。' : ''}</div>
            </RCard>
          </div>
        )}
        <div style={{ marginTop: 13 }}>
        <RCard title={'组合净值 vs ' + benchLabel + ' · 横轴=日期 纵轴=净值(归一)'}>
          <svg viewBox="0 0 1000 156" style={{ width: '100%', height: 'auto', display: 'block' }}>
            {(() => { const yOf = v => 16 + (hi - v) / rng * 120, xOf = (i, n) => 50 + i / Math.max(1, n - 1) * 934; return (<>
              <text x="44" y="20" textAnchor="end" fontSize="9" fill="var(--ink-3)">{_n2(hi, 2)}</text>
              {hi >= 1 && lo <= 1 && <line x1="50" y1={yOf(1).toFixed(1)} x2="984" y2={yOf(1).toFixed(1)} stroke="var(--line)" strokeDasharray="3 4" vectorEffect="non-scaling-stroke" />}
              {hi >= 1 && lo <= 1 && <text x="44" y={(yOf(1) + 3).toFixed(1)} textAnchor="end" fontSize="9" fill="var(--ink-3)">1.00</text>}
              <text x="44" y="139" textAnchor="end" fontSize="9" fill="var(--ink-3)">{_n2(lo, 2)}</text>
              {nav.length > 1 && <polyline points={nav.map((v, i) => xOf(i, nav.length).toFixed(1) + ',' + yOf(v).toFixed(1)).join(' ')} fill="none" stroke="var(--zhu)" strokeWidth="1.8" vectorEffect="non-scaling-stroke" />}
              {bench.length > 1 && <polyline points={bench.map((v, i) => xOf(i, bench.length).toFixed(1) + ',' + yOf(v).toFixed(1)).join(' ')} fill="none" stroke="var(--ink-3)" strokeWidth="1.3" strokeDasharray="5 4" vectorEffect="non-scaling-stroke" />}
              {bench.map((v, i) => <circle key={'b' + i} cx={xOf(i, bench.length).toFixed(1)} cy={yOf(v).toFixed(1)} r="6" fill="transparent" style={{ cursor: 'pointer' }} {..._hv((navDates[i] || '') + ' · ' + benchLabel + ' ' + _n2(v, 3))} />)}
              {nav.map((v, i) => <circle key={'n' + i} cx={xOf(i, nav.length).toFixed(1)} cy={yOf(v).toFixed(1)} r="6" fill="transparent" style={{ cursor: 'pointer' }} {..._hv((navDates[i] || '') + ' · 组合净值 ' + _n2(v, 3))} />)}
              {navAx.map((d, i) => <text key={i} x={i === 0 ? 50 : i === 1 ? 517 : 984} y="152" textAnchor={i === 0 ? 'start' : i === 1 ? 'middle' : 'end'} fontSize="9" fill="var(--ink-3)">{d}</text>)}
            </>); })()}
          </svg>
          <div style={{ display: 'flex', gap: 14, marginTop: 4 }}><span className="mono" style={{ fontSize: 9, color: 'var(--zhu)' }}>— 组合</span><span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{'--- ' + benchLabel}</span></div>
        </RCard>
        </div>
        {result && result.oos && result.oos.enabled && (() => {
          const o = result.oos;
          const isSh = o.metric === 'sharpe';
          const mlabel = isSh ? 'Sharpe' : 'RankIC';
          const isv = isSh ? (o.is || {}).sharpe : (o.is || {}).rank_ic;
          const oosv = isSh ? (o.oos || {}).sharpe : (o.oos || {}).rank_ic;
          const fmt = v => (v == null ? '—' : (isSh ? _n2(v, 2) : _n2(v, 4)));
          const VC = { robust: 'rgb(74,107,92)', degraded: '#b8860b', overfit: 'var(--zhu)', insufficient: 'var(--ink-3)', na: 'var(--ink-3)' };
          const VL = { robust: '稳健', degraded: '衰减', overfit: '疑似过拟合', insufficient: '期数不足', na: '不适用' };
          const col = VC[o.verdict] || 'var(--ink-3)';
          return (
            <div style={{ marginTop: 13 }}>
              <RCard title={'样本外体检 · 过拟合 · ' + mlabel + (o.split_date ? ' · 切于 ' + o.split_date : '')}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap' }}>
                  <div><div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.1em' }}>样本内 IS</div><div className="mono" style={{ fontSize: 16, fontWeight: 500 }}>{fmt(isv)}</div></div>
                  <span style={{ color: 'var(--ink-3)' }}>→</span>
                  <div><div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.1em' }}>样本外 OOS</div><div className="mono" style={{ fontSize: 16, fontWeight: 500, color: col }}>{fmt(oosv)}</div></div>
                  <div><div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', letterSpacing: '.1em' }}>衰减 OOS/IS</div><div className="mono" style={{ fontSize: 16, fontWeight: 500, color: col }}>{o.decay_ratio != null ? _pct(o.decay_ratio, 0) : '—'}</div></div>
                  <span style={{ marginLeft: 'auto', fontSize: 12, fontFamily: 'var(--serif)', color: 'var(--paper)', background: col, borderRadius: 7, padding: '5px 12px' }}>{VL[o.verdict] || o.verdict}</span>
                </div>
                <div style={{ marginTop: 7, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>{(o.note || '') + ' · 口径(wiki):样本内好不算数,样本外仍保持才算验证;样本外塌缩/反号 = 数据挖掘 / 过拟合。'}</div>
              </RCard>
            </div>
          );
        })()}
        {result && result.walkforward && result.walkforward.enabled && (() => {
          const wf = result.walkforward, isIC = wf.metric === 'rank_ic', segs = wf.segments || [];
          const vmax = Math.max(1e-6, ...segs.map(s => Math.abs((isIC ? s.rank_ic : s.sharpe) || 0)));
          const mean = isIC ? wf.mean_ic : wf.mean_sharpe, posPct = wf.pos_ratio != null ? Math.round(wf.pos_ratio * 100) : null;
          return (
            <div style={{ marginTop: 13 }}>
              <RCard title={'Walk-forward 滚动前进 · ' + segs.length + ' 段 ' + (isIC ? 'RankIC' : 'Sharpe') + ' · 均值 ' + (mean != null ? (isIC ? _n2(mean, 4) : _n2(mean, 2)) : '—') + ' · 正收益段 ' + (posPct != null ? posPct + '%' : '—')}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
                  {segs.map((s, i) => { const v = (isIC ? s.rank_ic : s.sharpe) || 0; const h = Math.abs(v / vmax) * 54; return (
                    <div key={i} style={{ flex: 1, textAlign: 'center', cursor: 'help' }} title={s.period + ' · ' + (isIC ? 'RankIC ' + _n2(v, 4) : 'Sharpe ' + _n2(v, 2) + ' · 年化 ' + _pct(s.ann_return))}>
                      <div style={{ height: 60, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end' }}>
                        <div style={{ height: h, width: '64%', margin: '0 auto', borderRadius: '3px 3px 0 0', background: (v >= 0 ? 'rgb(74,107,92)' : 'var(--zhu)') }} />
                      </div>
                      <div className="mono" style={{ fontSize: 12, fontWeight: 500, marginTop: 3, color: (v >= 0 ? 'rgb(74,107,92)' : 'var(--zhu)') }}>{isIC ? _n2(v, 3) : _n2(v, 2)}</div>
                      <div className="mono" style={{ fontSize: 7.5, color: 'var(--ink-3)', marginTop: 2 }}>{String(s.period || '').split('~').map(d => d.slice(2, 7)).join('→')}</div>
                    </div>
                  ); })}
                </div>
                <div style={{ marginTop: 6, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>把评测窗切成连续子区间逐段算(多次样本外,华泰时序交叉验证):各段多为正且稳 = 跨市场环境稳健;个别段好其余崩 = 侥幸/不稳。</div>
              </RCard>
            </div>
          );
        })()}
        {result && result.rolling_sharpe && result.rolling_sharpe.length > 1 && (() => {
          const rs = result.rolling_sharpe, vals = rs.map(p => p[1]);
          const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), rng = (hi - lo) || 1;
          const yOf = v => 12 + (hi - v) / rng * 100, xOf = i => 40 + i / Math.max(1, rs.length - 1) * 940;
          const dts = rs.map(p => String(p[0]).slice(2, 10));
          return (
            <div style={{ marginTop: 13 }}>
              <RCard title="滚动夏普 · 横轴=日期 纵轴=滚动窗口 Sharpe(子区间稳定性)">
                <svg viewBox="0 0 1000 132" style={{ width: '100%', height: 'auto', display: 'block' }}>
                  <text x="34" y="16" textAnchor="end" fontSize="9" fill="var(--ink-3)">{_n2(hi, 2)}</text>
                  <line x1="40" y1={yOf(0).toFixed(1)} x2="980" y2={yOf(0).toFixed(1)} stroke="var(--line)" vectorEffect="non-scaling-stroke" />
                  <text x="34" y={(yOf(0) + 3).toFixed(1)} textAnchor="end" fontSize="9" fill="var(--ink-3)">0</text>
                  <text x="34" y="116" textAnchor="end" fontSize="9" fill="var(--ink-3)">{_n2(lo, 2)}</text>
                  <polyline points={rs.map((p, i) => xOf(i).toFixed(1) + ',' + yOf(p[1]).toFixed(1)).join(' ')} fill="none" stroke="var(--zhu)" strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
                  {rs.map((p, i) => <circle key={i} cx={xOf(i).toFixed(1)} cy={yOf(p[1]).toFixed(1)} r="6" fill="transparent" style={{ cursor: 'pointer' }} {..._hv((dts[i] || '') + ' · 滚动Sharpe ' + _n2(p[1], 2))} />)}
                  {[0, rs.length >> 1, rs.length - 1].map((idx, j) => <text key={j} x={j === 0 ? 40 : j === 1 ? 510 : 980} y="128" textAnchor={j === 0 ? 'start' : j === 1 ? 'middle' : 'end'} fontSize="9" fill="var(--ink-3)">{dts[idx] || ''}</text>)}
                </svg>
              </RCard>
            </div>
          );
        })()}
        <div style={{ marginTop: 13 }}>
          <RCard title="IC 月度热力图 · 红=正IC(越深越强) 绿=负IC">
            {heatYears.length ? (
              <div style={{ display: 'grid', gridTemplateColumns: '34px repeat(12, 1fr)', gap: 2 }}>
                <div></div>
                {['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'].map(m => <div key={'hm' + m} style={{ textAlign: 'center', fontSize: 8, color: 'var(--ink-3)' }}>{m}</div>)}
                {heatYears.flatMap(y => [
                  <div key={y} style={{ fontSize: 8.5, color: 'var(--ink-3)', fontFamily: 'var(--mono)', display: 'flex', alignItems: 'center' }}>{y}</div>,
                  ...Array.from({ length: 12 }, (_, mi) => {
                    const mm = String(mi + 1).padStart(2, '0'); const c = _mon[y + '-' + mm];
                    if (!c) return <div key={y + mm} style={{ height: 15, background: 'rgba(28,24,20,0.04)', borderRadius: 2 }} />;
                    const v = c[0] / c[1]; const a = (0.18 + 0.82 * Math.min(1, Math.abs(v) / heatMax)).toFixed(2);
                    return <div key={y + mm} title={y + '-' + mm + ' · IC ' + v.toFixed(3)} style={{ height: 15, borderRadius: 2, background: (v >= 0 ? 'rgba(185,74,61,' + a + ')' : 'rgba(74,107,92,' + a + ')') }} />;
                  })
                ])}
              </div>
            ) : <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>无 IC 序列(样本太短)</div>}
          </RCard>
        </div>
        {navPairs.length > 1 && (
          <div style={{ marginTop: 13 }}>
            <RCard title={'回撤水下图 · 距历史高点跌幅(最深 ' + _pct(ddMin) + ')'}>
              <svg viewBox="0 0 420 96" style={{ width: '100%', height: 96 }}>
                <line x1="0" y1="1" x2="420" y2="1" stroke="var(--line)" />
                {ddArea && <polygon points={ddArea} fill="rgba(185,74,61,0.16)" stroke="var(--zhu)" strokeWidth="1.2" />}
              </svg>
              <div style={{ marginTop: 4, fontSize: 9, color: 'var(--ink-3)' }}>水线 0 = 历史新高;越往下 = 回撤越深 / 越久未回血(只看最大回撤数字看不出的恢复体验)。</div>
            </RCard>
          </div>
        )}
        {navPairs.length > 1 && mretYears.length > 0 && (
          <div style={{ marginTop: 13 }}>
            <RCard title="月度收益热力图 · 红=正收益(越深越强) 绿=负收益">
              <div style={{ display: 'grid', gridTemplateColumns: '34px repeat(12, 1fr)', gap: 2 }}>
                <div></div>
                {['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'].map(m => <div key={'mm' + m} style={{ textAlign: 'center', fontSize: 8, color: 'var(--ink-3)' }}>{m}</div>)}
                {mretYears.flatMap(y => [
                  <div key={'mr' + y} style={{ fontSize: 8.5, color: 'var(--ink-3)', fontFamily: 'var(--mono)', display: 'flex', alignItems: 'center' }}>{y}</div>,
                  ...Array.from({ length: 12 }, (_, mi) => {
                    const mm = String(mi + 1).padStart(2, '0'); const v = _mret[y + '-' + mm];
                    if (v == null) return <div key={y + mm} style={{ height: 15, background: 'rgba(28,24,20,0.04)', borderRadius: 2 }} />;
                    const a = (0.18 + 0.82 * Math.min(1, Math.abs(v) / mretMax)).toFixed(2);
                    return <div key={y + mm} title={y + '-' + mm + ' · ' + _pct(v)} style={{ height: 15, borderRadius: 2, background: (v >= 0 ? 'rgba(185,74,61,' + a + ')' : 'rgba(74,107,92,' + a + ')') }} />;
                  })
                ])}
              </div>
            </RCard>
          </div>
        )}
        {(() => {
          const W = (result && (result.combine_weights || result.weights)) || [];
          if (!W.length || W.length < 2) return null;
          const cm = result && result.combine;
          const CM = { equal: '等权', ic: 'IC 加权', icir: 'ICIR 加权' };
          const wmax = Math.max(1e-6, ...W.map(w => Math.abs(w.weight || 0)));
          return (
            <div style={{ marginTop: 13 }}>
              <RCard title={'多因子合成 · ' + W.length + ' 因子 · ' + (CM[cm] || cm || '等权') + (result.combine_note ? ' · ' + result.combine_note : '')}>
                <div style={{ display: 'grid', gap: '6px 16px' }}>
                  {W.map((w, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10 }}>
                      <span className="mono" style={{ color: 'var(--ink-2)', flex: '0 0 200px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={w.name}>{w.name}</span>
                      <span style={{ flex: 1, height: 6, background: 'var(--line)', borderRadius: 3, overflow: 'hidden' }}>
                        <span style={{ display: 'block', height: '100%', width: (Math.abs(w.weight || 0) / wmax * 100).toFixed(1) + '%', background: ((w.weight || 0) < 0 ? 'var(--zhu)' : 'var(--dai)') }} />
                      </span>
                      <span className="mono" style={{ width: 52, textAlign: 'right', color: 'var(--ink-1)' }}>{_pct(w.weight, 1)}</span>
                      {(w.ic != null || w.icir != null) && <span className="mono" style={{ width: 130, textAlign: 'right', color: 'var(--ink-3)', fontSize: 9 }}>{w.ic != null ? 'IC ' + (+w.ic).toFixed(3) : ''}{w.icir != null ? ' · ICIR ' + (+w.icir).toFixed(2) : ''}</span>}
                    </div>
                  ))}
                </div>
                {(() => {
                  const pbo = result && result.pbo;
                  if (!pbo || !pbo.enabled || pbo.pbo == null) return null;
                  const p = +pbo.pbo;
                  const col = p > 0.5 ? 'var(--zhu)' : (p < 0.3 ? 'var(--dai)' : 'var(--yin)');
                  const verdict = p > 0.5 ? '高 · 选优过拟合' : (p < 0.3 ? '低 · 较稳健' : '中等');
                  return (
                    <div style={{ marginTop: 9, paddingTop: 8, borderTop: '1px solid var(--line)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10 }}>
                        <span style={{ color: 'var(--ink-2)', flex: '0 0 auto' }}>过拟合概率 PBO(CSCV)</span>
                        <span style={{ flex: 1, height: 6, background: 'var(--line)', borderRadius: 3, overflow: 'hidden' }}>
                          <span style={{ display: 'block', height: '100%', width: (p * 100).toFixed(0) + '%', background: col }} />
                        </span>
                        <span className="mono" style={{ color: col, fontWeight: 600, flex: '0 0 auto' }}>{(p * 100).toFixed(0)}% · {verdict}</span>
                      </div>
                      <div style={{ marginTop: 4, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>注:对 {pbo.n_candidates} 个成员因子做 CSCV({pbo.n_blocks} 块 / {pbo.n_combos} 组合)—— IS 最优成员在 OOS 落入后半的概率;&gt;50% 提示「挑最好的那个因子」多为数据挖掘虚高,&lt;30% 较稳健(López de Prado)。</div>
                    </div>
                  );
                })()}
                <div style={{ marginTop: 7, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>注:IC/ICIR 加权的权重<b style={{ color: 'var(--yin)' }}>只用样本内</b>(前60%调仓日)估出再固定应用,不引入未来函数;负权重 = 该因子样本内反向(已自动翻转)。</div>
              </RCard>
            </div>
          );
        })()}
        {(() => {
          const hold = (result && (result.holdings || result._pf_weights)) || [];
          if (!hold.length) return null;
          const wmax = Math.max(1e-6, ...hold.map(h => Math.abs(h.weight || 0)));
          const wsum = hold.reduce((a, h) => a + (h.weight || 0), 0);
          const wlabel = result._pf_weighting || result.weighting || '';
          const asof = result._pf_asof || result.asof || '';
          const WMAP = { equal: '等权', mktcap: '市值加权', inv_vol: '反波动', risk_parity: '风险平价', min_var: '最小方差', max_sharpe: '最大夏普', true_risk_parity: '真风险平价', black_litterman: 'Black-Litterman' };
          const indN = result.industry_neutral || result._pf_industry_neutral;
          const ra = (result && result.risk_attr) || null;   // #4 组合层风险归因(欧拉分解;失败 None)
          return (
            <div style={{ marginTop: 13 }}>
              <RCard title={'目标持仓 · ' + hold.length + ' 只 · ' + (WMAP[wlabel] || wlabel || '等权') + (indN ? ' · 行业中性' : '') + (asof ? ' · ' + asof : '') + ' · ∑权重 ' + _pct(wsum, 1)}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(168px, 1fr))', gap: '5px 16px' }}>
                  {hold.slice(0, 48).map((h, i) => {
                    const rp = h.risk_pct;   // 成分风险占比(0..1);缺数据票 null
                    const conc = (rp != null && (h.weight || 0) > 0 && rp > (h.weight || 0) * 1.15);  // 险占比显著高于权重=风险集中
                    return (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 10 }}>
                        <span className="mono" style={{ color: 'var(--ink-3)', width: 66, flexShrink: 0 }}>{h.code}</span>
                        <span style={{ flex: 1, height: 6, background: 'var(--line)', borderRadius: 3, overflow: 'hidden' }}>
                          <span style={{ display: 'block', height: '100%', width: (Math.abs(h.weight || 0) / wmax * 100).toFixed(1) + '%', background: 'var(--dai)' }} />
                        </span>
                        <span className="mono" style={{ width: 40, textAlign: 'right', color: 'var(--ink-1)', flexShrink: 0 }}>{_pct(h.weight, 1)}</span>
                        {rp != null && <span className="mono" title={'成分风险占比(欧拉分解 CRᵢ/σ_p)' + (conc ? ' · 高于权重→风险集中' : '')} style={{ width: 42, textAlign: 'right', flexShrink: 0, color: conc ? 'var(--zhu)' : 'var(--ink-3)', fontWeight: conc ? 600 : 400 }}>险{_pct(rp, 0)}</span>}
                      </div>
                    );
                  })}
                </div>
                {ra && ra.port_vol != null && (
                  <div style={{ marginTop: 7, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>组合波动(结构·年化)<b style={{ color: 'var(--yin)' }}>{_pct(ra.port_vol * Math.sqrt(252), 1)}</b> · 逐持仓<b style={{ color: 'var(--zhu)' }}>险%</b>=成分风险贡献(欧拉分解 CRᵢ/σ_p,标红=显著高于权重→风险集中)· 覆盖 {ra.covered}/{ra.total} 票 · 成分VaR正态近似 · 当前持仓结构快照(非回测 / 非业绩)</div>
                )}
                <div style={{ marginTop: 7, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>注:最新一期<b style={{ color: 'var(--yin)' }}>目标持仓权重</b>(下单 / 展示参考)。回测节点默认 <b>TopN 等权</b>;若在回测节点选「持仓定权」或把本「组合构建」接到回测 pf 口,则<b style={{ color: 'var(--dai)' }}>真按该权重序列逐期回测</b>(反波动/风险平价用截至各调仓日的滚动波动,防前视)。{hold.length > 48 ? ' 仅显示前 48 只。' : ''}</div>
              </RCard>
            </div>
          );
        })()}
        <div style={{ marginTop: 8, fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5 }}>{presentation.note}</div>
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '9px 18px', borderTop: '1px solid var(--line-soft)', background: 'rgba(168,57,45,0.05)' }}>
        <span style={{ width: 22, height: 22, borderRadius: 6, background: 'var(--yin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>瀾</span>
        <span className="serif" style={{ fontSize: 12.5, color: 'var(--ink-1)', lineHeight: 1.55 }}>{insight}</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignSelf: 'center', flexShrink: 0 }}>
          <span onClick={onSaveCard} className="serif" style={{ fontSize: 12, color: 'var(--ink-1)', border: '1px solid var(--line)', borderRadius: 7, padding: '6px 11px', cursor: 'pointer', whiteSpace: 'nowrap' }}>⊕ 沉淀为经验卡</span>
          <span onClick={onSaveFactor} className="serif" style={{ fontSize: 12, color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 7, padding: '6px 11px', cursor: 'pointer', whiteSpace: 'nowrap' }}>存入因子库</span>
        </span>
      </div>
      </>)}
      {tip && <div style={{ position: 'fixed', left: tip.x + 14, top: tip.y + 14, zIndex: 9999, pointerEvents: 'none', background: 'rgba(28,24,20,0.92)', color: 'var(--paper)', fontFamily: 'var(--mono)', fontSize: 11, padding: '5px 9px', borderRadius: 6, boxShadow: '0 4px 14px rgba(0,0,0,0.25)', whiteSpace: 'nowrap' }}>{tip.t}</div>}
    </div>
  );
}

window.WorkflowApp = WorkflowApp;
