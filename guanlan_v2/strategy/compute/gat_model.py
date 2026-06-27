# -*- coding: utf-8 -*-
"""纯 PyTorch 掩码图注意力(GAT)模型 + 训练/推理。主 env(torch CPU)可单测;GPU 脚本 import 之。
关系维度:节点=个股,边=收益相关图(gat_io.build_corr_graph 的 0/1 邻接 + 自环)。无引擎依赖。"""
from __future__ import annotations

from typing import List

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


def _zscore_1d(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype="float64")
    sd = a.std()
    return (a - a.mean()) / sd if sd > 0 else (a - a.mean())


class _GATLayer(nn.Module):
    """单头图注意力:e_ij = LeakyReLU(a_src·Wh_i + a_dst·Wh_j),非邻居 -inf 掩码 + 行 softmax + 邻居加权。"""

    def __init__(self, in_dim: int, out_dim: int, *, alpha: float = 0.2):
        super().__init__()
        self.W = nn.Linear(in_dim, out_dim, bias=False)
        self.a_src = nn.Linear(out_dim, 1, bias=False)
        self.a_dst = nn.Linear(out_dim, 1, bias=False)
        self.leaky = nn.LeakyReLU(alpha)

    def forward(self, h: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        Wh = self.W(h)                                   # (N, out)
        e = self.a_src(Wh) + self.a_dst(Wh).transpose(0, 1)   # (N, N): e_ij = a_src(Wh_i)+a_dst(Wh_j)
        e = self.leaky(e)
        e = e.masked_fill(mask <= 0, torch.finfo(e.dtype).min)  # 非邻居 -inf
        att = torch.softmax(e, dim=1)                    # 每节点对其邻居归一
        return att @ Wh                                  # (N, out)


class GAT(nn.Module):
    def __init__(self, in_dim: int, hidden: int = 32):
        super().__init__()
        self.l1 = _GATLayer(in_dim, hidden)
        self.l2 = _GATLayer(hidden, hidden)
        self.head = nn.Linear(hidden, 1)

    def forward(self, X: torch.Tensor, A: torch.Tensor) -> torch.Tensor:
        h = F.elu(self.l1(X, A))
        h = F.elu(self.l2(h, A))
        return self.head(h).squeeze(-1)                  # (N,)


def train_gat(X_list: List[np.ndarray], A_list: List[np.ndarray], y_list: List[np.ndarray], *,
              device: str = "cpu", epochs: int = 60, lr: float = 1e-3, hidden: int = 32,
              seed: int = 0, return_losses: bool = False):
    """每日一个图 (X,A,y);仅 finite-label 节点入损失(横截面 z 标签 MSE);Adam。返回训练好的 GAT。"""
    torch.manual_seed(seed)
    graphs = []
    for X, A, y in zip(X_list, A_list, y_list):
        m = np.isfinite(y)
        if int(m.sum()) < 20:
            continue
        graphs.append((
            torch.tensor(X, dtype=torch.float32, device=device),
            torch.tensor(A, dtype=torch.float32, device=device),
            torch.tensor(m, device=device),
            torch.tensor(_zscore_1d(y[m]), dtype=torch.float32, device=device),
        ))
    if not graphs:
        raise ValueError("无可训练图(每日 finite 标签 < 20)")
    model = GAT(X_list[0].shape[1], hidden=hidden).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    model.train()
    losses: List[float] = []
    for _ in range(epochs):
        tot, nb = 0.0, 0
        for Xt, At, m, yz in graphs:
            opt.zero_grad()
            pred = model(Xt, At)[m]
            loss = F.mse_loss(pred, yz)
            loss.backward()
            opt.step()
            tot += loss.item(); nb += 1
        losses.append(tot / max(1, nb))
    return (model, losses) if return_losses else model


def predict_gat(model: GAT, X: np.ndarray, A: np.ndarray, *, device: str = "cpu") -> np.ndarray:
    model.eval()
    with torch.no_grad():
        Xt = torch.tensor(X, dtype=torch.float32, device=device)
        At = torch.tensor(A, dtype=torch.float32, device=device)
        return model(Xt, At).detach().cpu().numpy()
