# tests/test_gat_io.py
# GAT 数据准备纯函数门禁:横截面 z / PIT 截断 / 相关图对称自环 / 前向标签 PIT。
import numpy as np
import pandas as pd
from guanlan_v2.strategy.compute import gat_io


def _close_panel(n_days=120, codes=("A", "B", "C", "D", "E"), seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n_days)
    data = {c: 10.0 * np.cumprod(1 + rng.normal(0.001, 0.02, n_days)) for c in codes}
    return pd.DataFrame(data, index=idx)


def test_node_features_cross_sectional_z_and_shape():
    cp = _close_panel()
    vp = cp * 1000.0
    codes, X = gat_io.compute_node_features(cp, vp, cp.index[-1])
    assert X.shape == (len(codes), len(gat_io.DEFAULT_GAT_FACTORS))
    assert X.dtype == np.float32
    # 每列横截面近似零均值(z-score)
    assert np.allclose(X.mean(axis=0), 0.0, atol=1e-4)


def test_node_features_pit_no_future():
    cp = _close_panel()
    d = cp.index[60]
    codes1, X1 = gat_io.compute_node_features(cp, None, d)
    cp2 = cp.copy()
    cp2.iloc[70:] = cp2.iloc[70:] * 5.0   # 篡改 d 之后的未来
    codes2, X2 = gat_io.compute_node_features(cp2, None, d)
    assert codes1 == codes2
    assert np.allclose(X1, X2)             # 未来变化不影响 d 的特征(PIT)


def test_corr_graph_symmetric_selfloop_degree():
    cp = _close_panel()
    codes, _ = gat_io.compute_node_features(cp, None, cp.index[-1])
    A = gat_io.build_corr_graph(cp, cp.index[-1], codes, window=60, topk=2)
    n = len(codes)
    assert A.shape == (n, n)
    assert np.allclose(A, A.T)                       # 对称
    assert np.allclose(np.diag(A), 1.0)              # 自环
    assert ((A == 0) | (A == 1)).all()               # 0/1


def test_corr_graph_short_window_returns_identity():
    cp = _close_panel(n_days=120)
    codes, _ = gat_io.compute_node_features(cp, None, cp.index[-1])
    A = gat_io.build_corr_graph(cp, cp.index[3], codes, window=60, topk=2)  # 仅 ~4 日历史 < 5
    assert np.allclose(A, np.eye(len(codes)))        # 诚实退单位阵


def test_forward_label_value_and_pit_tail():
    cp = _close_panel()
    codes = list(cp.columns)
    d = cp.index[10]
    y = gat_io.forward_label(cp, d, codes, horizon=5)
    expected = cp.loc[cp.index[15], codes].values / cp.loc[d, codes].values - 1.0
    assert np.allclose(y, expected, atol=1e-6)
    # 末日附近无未来标签 → 全 nan
    y_tail = gat_io.forward_label(cp, cp.index[-1], codes, horizon=5)
    assert np.isnan(y_tail).all()


def test_rebalance_dates_nonoverlap_and_realized():
    cp = _close_panel(n_days=100)
    rd = gat_io.rebalance_dates(cp.index, horizon=5, start=None)
    assert len(rd) > 0
    # 非重叠 5 日步长
    pos = [cp.index.get_loc(d) for d in rd]
    assert all(b - a == 5 for a, b in zip(pos, pos[1:]))
    # 每个换仓日标签已实现(d + horizon ≤ 末日)
    assert all(cp.index.get_loc(d) + 5 <= len(cp.index) - 1 for d in rd)


def test_corr_graph_pit_no_future():
    # PIT 命门:相关图只用 ≤d 数据,篡改 d 之后未来不改变图(守护 .loc[:date] 截断)。
    cp = _close_panel()
    codes = list(cp.columns)
    d = cp.index[60]
    A1 = gat_io.build_corr_graph(cp, d, codes, window=40, topk=2)
    cp2 = cp.copy()
    cp2.iloc[70:] = cp2.iloc[70:] * 5.0
    A2 = gat_io.build_corr_graph(cp2, d, codes, window=40, topk=2)
    assert np.allclose(A1, A2)


def test_corr_graph_degenerate_node_self_loop_only():
    # 常量序列(相关性未定义/0)→ 该节点只连自己,不连任意低序号邻居。
    cp = _close_panel(n_days=120, codes=("A", "B", "C", "D"))
    cp["FLAT"] = 10.0
    codes = list(cp.columns)
    A = gat_io.build_corr_graph(cp, cp.index[-1], codes, window=60, topk=2)
    fi = codes.index("FLAT")
    assert A[fi].sum() == 1.0 and A[fi, fi] == 1.0


def test_node_features_volume_misaligned_turn_honest_not_stale():
    # 成交量末日早于 close 末日 → turn 诚实为 0(对齐 close 日历后缺量→NaN→z=0),非拿陈旧日成交量。
    cp = _close_panel(n_days=120, codes=("A", "B", "C", "D", "E"))
    vp = (cp * 1000.0).iloc[:-3]           # 成交量缺最后 3 个交易日
    _, X = gat_io.compute_node_features(cp, vp, cp.index[-1])
    j_turn = list(gat_io.DEFAULT_GAT_FACTORS).index("turn")
    assert np.allclose(X[:, j_turn], 0.0)


def test_corr_graph_connects_correlated_pair():
    # numpy 相关确实识别相关性:近乎完全相关的一对必互连(守护 corr 计算正确)。
    idx = pd.bdate_range("2024-01-01", periods=120)
    rng = np.random.default_rng(0)
    base = np.cumprod(1 + rng.normal(0.0, 0.02, 120))
    cp = pd.DataFrame({
        "X": 10.0 * base,
        "Y": 10.0 * base * 1.001,                                  # 与 X 近乎完全相关
        "Z": 10.0 * np.cumprod(1 + rng.normal(0.0, 0.02, 120)),    # 独立
        "W": 10.0 * np.cumprod(1 + rng.normal(0.0, 0.02, 120)),    # 独立
    }, index=idx)
    codes = list(cp.columns)
    A = gat_io.build_corr_graph(cp, idx[-1], codes, window=60, topk=1)
    ix = {c: i for i, c in enumerate(codes)}
    assert A[ix["X"], ix["Y"]] == 1.0 and A[ix["Y"], ix["X"]] == 1.0   # 高相关对必连


def test_corr_neighbors_shape_self_and_pit():
    # 稀疏 kNN 邻居:形状 (N, topk+1)、首列=自身、int dtype、PIT(篡改未来不改变邻居)。
    cp = _close_panel()
    codes = list(cp.columns)
    d = cp.index[60]
    topk = 2
    out = gat_io.build_corr_neighbors(cp, d, codes, window=40, topk=topk)
    n = len(codes)
    assert out.shape == (n, topk + 1)
    assert np.array_equal(out[:, 0], np.arange(n))    # 首列恒为自身
    assert np.issubdtype(out.dtype, np.integer)       # 整数索引
    # PIT:篡改 d 之后的未来行,重建邻居不变(守护 .loc[:date] 截断)。
    cp2 = cp.copy()
    cp2.iloc[70:] = cp2.iloc[70:] * 5.0
    out2 = gat_io.build_corr_neighbors(cp2, d, codes, window=40, topk=topk)
    assert np.array_equal(out, out2)


def test_corr_neighbors_connects_correlated_pair():
    # 近乎完全相关的一对 X/Y(Z/W 独立),topk=1:Y 必在 X 的邻居行,X 必在 Y 的邻居行。
    idx = pd.bdate_range("2024-01-01", periods=120)
    rng = np.random.default_rng(0)
    base = np.cumprod(1 + rng.normal(0.0, 0.02, 120))
    cp = pd.DataFrame({
        "X": 10.0 * base,
        "Y": 10.0 * base * 1.001,                                  # 与 X 近乎完全相关
        "Z": 10.0 * np.cumprod(1 + rng.normal(0.0, 0.02, 120)),    # 独立
        "W": 10.0 * np.cumprod(1 + rng.normal(0.0, 0.02, 120)),    # 独立
    }, index=idx)
    codes = list(cp.columns)
    out = gat_io.build_corr_neighbors(cp, idx[-1], codes, window=60, topk=1)
    ix = {c: i for i, c in enumerate(codes)}
    assert ix["Y"] in out[ix["X"]]    # Y 在 X 的邻居行
    assert ix["X"] in out[ix["Y"]]    # X 在 Y 的邻居行


def test_corr_neighbors_degenerate_all_self():
    # 常量序列(相关性未定义/0)→ 该节点整行邻居都填自身(零相关槽退化为自身)。
    cp = _close_panel(n_days=120, codes=("A", "B", "C", "D"))
    cp["FLAT"] = 10.0
    codes = list(cp.columns)
    out = gat_io.build_corr_neighbors(cp, cp.index[-1], codes, window=60, topk=2)
    fi = codes.index("FLAT")
    assert (out[fi] == fi).all()      # 整行全为自身索引
