# tests/test_gat_model.py
# 纯 torch 掩码图注意力门禁:形状 + 掩码生效(图注意力命门) + 训练有效。
import numpy as np
import torch

from guanlan_v2.strategy.compute.gat_model import _GATLayer, GAT, train_gat, predict_gat


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
