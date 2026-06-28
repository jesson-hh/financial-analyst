# tests/test_gat_model.py
# 纯 torch 掩码图注意力门禁:形状 + 掩码生效(图注意力命门) + 训练有效。
import numpy as np
import torch

from guanlan_v2.strategy.compute.gat_model import (
    _GATLayer, GAT, train_gat, predict_gat,
    _GATLayerSparse, GATSparse, train_gat_sparse, predict_gat_sparse,
)


def test_gat_forward_shape_finite():
    torch.manual_seed(0)
    N, F = 8, 5
    X = torch.randn(N, F)
    A = torch.eye(N)
    A[0, 1] = A[1, 0] = 1.0
    out = GAT(F, hidden=16)(X, A)
    assert out.shape == (N,)
    assert torch.isfinite(out).all()


def test_gat_layer_masks_non_neighbors():
    """单层掩码命门:改非邻居节点输入,目标节点输出不变(注意力只看邻居)。"""
    torch.manual_seed(0)
    layer = _GATLayer(3, 4)
    N = 5
    X = torch.randn(N, 3)
    mask = torch.eye(N)
    mask[0, 1] = mask[1, 0] = 1.0           # 0<->1 互为邻居;node3 是 node0 的非邻居
    out1 = layer(X, mask)
    X2 = X.clone()
    X2[3] = torch.randn(3)                   # 改 node3(node0 的非邻居)
    out2 = layer(X2, mask)
    assert torch.allclose(out1[0], out2[0], atol=1e-6)     # node0 不受非邻居影响
    assert not torch.allclose(out1[3], out2[3], atol=1e-6) # node3 自身变了


def test_gat_layer_neighbor_propagates():
    """补命门另一向:改真邻居输入,目标节点输出 DID 变 —— 证明消息真在边上传播(非纯自变换)。"""
    torch.manual_seed(0)
    layer = _GATLayer(3, 4)
    N = 5
    X = torch.randn(N, 3)
    mask = torch.eye(N)
    mask[0, 1] = mask[1, 0] = 1.0           # node1 是 node0 的真邻居
    out1 = layer(X, mask)
    X2 = X.clone()
    X2[1] = X[1] + 3.0                        # 改 node1(node0 的邻居)
    out2 = layer(X2, mask)
    assert not torch.allclose(out1[0], out2[0], atol=1e-6)  # node0 受真邻居影响(聚合确实生效)


def test_train_gat_loss_decreases_and_predict():
    rng = np.random.default_rng(0)
    X_list, A_list, y_list = [], [], []
    N, F = 40, 4
    w = rng.normal(size=F)
    for _ in range(8):                       # 8 个"日"图,y 为 X 的线性可学函数
        X = rng.normal(size=(N, F)).astype(np.float32)
        A = np.eye(N, dtype=np.float32)
        y = (X @ w + rng.normal(0, 0.1, N)).astype(np.float32)
        X_list.append(X); A_list.append(A); y_list.append(y)
    model, losses = train_gat(X_list, A_list, y_list, device="cpu", epochs=40, return_losses=True)
    assert losses[-1] < losses[0]            # 训练有效
    p = predict_gat(model, X_list[0], A_list[0], device="cpu")
    assert p.shape == (N,) and np.isfinite(p).all()


def test_sparse_layer_equals_dense_on_same_neighbors():
    """等价命门:稀疏 gather 注意力在相同邻居集上与稠密掩码注意力逐位等价(证明稀疏路径数学正确)。"""
    torch.manual_seed(0)
    Fin, O = 5, 4
    N = 6
    nbr_list = [[0, 1, 2], [1, 0, 3], [2, 0, 4], [3, 1, 5], [4, 2, 5], [5, 3, 4]]
    X = torch.randn(N, Fin)
    # 由同一邻居集构造稠密 0/1 掩码:mask[i,j]=1 当 j 在 nbr_list[i]。
    mask = torch.zeros(N, N)
    for i, nb in enumerate(nbr_list):
        for j in nb:
            mask[i, j] = 1.0
    dense = _GATLayer(Fin, O)
    sparse = _GATLayerSparse(Fin, O)
    # 把稠密层权重逐一复制进稀疏层(同初值才能对拍)。
    sparse.W.weight.data = dense.W.weight.data.clone()
    sparse.a_src.weight.data = dense.a_src.weight.data.clone()
    sparse.a_dst.weight.data = dense.a_dst.weight.data.clone()
    out_dense = dense(X, mask)
    out_sparse = sparse(X, torch.tensor(nbr_list))
    assert torch.allclose(out_dense, out_sparse, atol=1e-5)   # 逐位等价


def test_train_gat_sparse_loss_decreases():
    rng = np.random.default_rng(0)
    X_list, nbr_list, y_list = [], [], []
    N, Fin = 40, 4
    w = rng.normal(size=Fin)
    for _ in range(8):                       # 8 个"日"图,y 为 X 的线性可学函数;稀疏图用自环(self-only)。
        X = rng.normal(size=(N, Fin)).astype(np.float32)
        nbr = np.tile(np.arange(N)[:, None], (1, 3))     # (N,3) 全自身(只自注意)
        y = (X @ w + rng.normal(0, 0.1, N)).astype(np.float32)
        X_list.append(X); nbr_list.append(nbr); y_list.append(y)
    model, losses = train_gat_sparse(X_list, nbr_list, y_list, device="cpu", epochs=40, return_losses=True)
    assert losses[-1] < losses[0]            # 训练有效
    p = predict_gat_sparse(model, X_list[0], nbr_list[0], device="cpu")
    assert p.shape == (N,) and np.isfinite(p).all()
