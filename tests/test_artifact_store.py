"""Phase 0 — ArtifactStore 读写契约测试.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md §6.
Realign 阶段对齐契约 (workflow/protocols.py::ArtifactStoreProto):
- ``write(run_id, node_id, output_name, payload) -> str`` (4 参, 返 POSIX 相对路径字符串).
- ``read(run_id, node_id, output_name='output')`` (3 参, output_name 默认).
- ``exists(run_id, node_id, output_name='output')`` 同上.
- 文件名 = ``<output_name>.{json|parquet}`` (不再硬编 ``output``).
- 同 (run, node, output_name) 二次写 dict → DataFrame 不留 stale .json 幽灵.

测试矩阵:
1. dict round-trip → JSON 落盘, ``.json`` 后缀, write 返 POSIX 相对路径字符串.
2. DataFrame round-trip (含 NaN) → parquet 落盘, NaN 保真.
3. Series round-trip → parquet 落盘 (转 DataFrame).
4. 不存在的 (run_id, node_id) read → ``FileNotFoundError``.
5. 同 run_id 不同 node_id 并发 write 不冲突 (2 线程).
6. 同 (run_id, node_id) 不同 output_name → 独立文件并存.
7. 同 (run_id, node_id, output_name) 二次写 dict → DataFrame, 旧 .json 被清.
8. NaN/Inf sanitize 进 JSON → null.
9. np.ndarray → TypeError.
10. URI 字符串是 POSIX 形态, Windows 也用 ``/``.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from financial_analyst.workflow.artifacts import ArtifactStore
from financial_analyst.workflow.protocols import ArtifactStoreProto


# ---------------------------------------------------------------------------
# 0. ArtifactStore 满足 ArtifactStoreProto (runtime_checkable Protocol)
# ---------------------------------------------------------------------------


def test_artifact_store_satisfies_protocol(tmp_path: Path) -> None:
    """ArtifactStore 实例必须满足 runtime_checkable 的 ArtifactStoreProto."""
    store = ArtifactStore(root=tmp_path)
    assert isinstance(store, ArtifactStoreProto)


# ---------------------------------------------------------------------------
# 1. dict 写读
# ---------------------------------------------------------------------------


def test_write_read_dict(tmp_path: Path) -> None:
    """dict payload → JSON 文件, round-trip 内容一致, write 返 POSIX 相对路径字符串."""
    store = ArtifactStore(root=tmp_path)
    payload = {"codes": ["SH600519", "SZ000858"], "n": 2, "ratio": 0.5}

    uri = store.write("run_a", "universe", "output", payload)

    # write 返回 str (POSIX 相对路径), 后缀 .json
    assert isinstance(uri, str)
    assert uri.endswith(".json")
    # POSIX 风格 — Windows 上也用 /, 不是 \\
    assert "\\" not in uri
    assert uri == "workflow_runs/run_a/nodes/universe/output.json"

    # 物理文件存在
    physical_path = tmp_path / uri
    assert physical_path.exists()
    assert physical_path.suffix == ".json"

    # 读回 == 原值 (默认 output_name='output')
    got = store.read("run_a", "universe")
    assert got == payload
    # 显式传 output_name='output' 等价
    assert store.read("run_a", "universe", "output") == payload

    # 文件本身是合法 UTF-8 JSON
    with physical_path.open("r", encoding="utf-8") as f:
        parsed = json.load(f)
    assert parsed == payload


def test_write_read_nested_dict_with_chinese_keys(tmp_path: Path) -> None:
    """嵌套 dict + 中文 key (ensure_ascii=False) 也能 round-trip."""
    store = ArtifactStore(root=tmp_path)
    payload = {
        "概要": {"行业": "白酒", "评级": "强烈推荐"},
        "因子": [{"name": "rev_20", "ic": 0.052}, {"name": "rsi", "ic": -0.012}],
    }
    store.write("run_b", "report", "output", payload)
    assert store.read("run_b", "report") == payload


def test_write_read_list_payload(tmp_path: Path) -> None:
    """list payload 也走 JSON 路径."""
    store = ArtifactStore(root=tmp_path)
    payload = [1, 2, 3, "x", {"k": "v"}]
    store.write("run_c", "list_node", "output", payload)
    assert store.read("run_c", "list_node") == payload


def test_write_read_primitives(tmp_path: Path) -> None:
    """标量 (int / float / bool / str / None) 也是合法 JSON 顶层值."""
    store = ArtifactStore(root=tmp_path)
    # 注意每次 write 落到同 (run, node, output_name) 会覆盖, 用不同 node_id 隔离
    for i, payload in enumerate([42, 3.14, True, False, "hello", None]):
        store.write("run_p", f"prim_{i}", "output", payload)
        assert store.read("run_p", f"prim_{i}") == payload


# ---------------------------------------------------------------------------
# 2. DataFrame 写读 (含 NaN)
# ---------------------------------------------------------------------------


def test_write_read_dataframe(tmp_path: Path) -> None:
    """DataFrame → parquet, round-trip ``df.equals`` 真. 含 NaN 也保真."""
    store = ArtifactStore(root=tmp_path)
    df = pd.DataFrame(
        {
            "code": ["SH600519", "SZ000858", "SH601318"],
            "score": [1.2, np.nan, 0.8],
            "rank": [1, 2, 3],
        }
    )

    uri = store.write("run_df", "factor_table", "output", df)

    assert isinstance(uri, str)
    assert uri.endswith(".parquet")
    assert "\\" not in uri  # POSIX

    physical = tmp_path / uri
    assert physical.exists()
    assert physical.suffix == ".parquet"

    back = store.read("run_df", "factor_table")
    assert isinstance(back, pd.DataFrame)
    # 列顺序 + dtype + NaN 全部保真
    assert list(back.columns) == ["code", "score", "rank"]
    assert back.shape == (3, 3)
    # 用 ``equals`` 严格比 (NaN==NaN 为 True 的内置语义)
    assert back.equals(df), f"DataFrame round-trip 不匹配:\nback=\n{back}\norig=\n{df}"


def test_write_read_dataframe_only_floats_with_nan_and_inf(tmp_path: Path) -> None:
    """parquet 原生支持 NaN / Inf 浮点 (区别于 JSON 必须 sanitize 成 null)."""
    store = ArtifactStore(root=tmp_path)
    df = pd.DataFrame({"a": [1.0, np.nan, np.inf, -np.inf]})
    store.write("run_inf", "inf_table", "output", df)
    back = store.read("run_inf", "inf_table")
    # parquet 完整保 NaN / Inf
    assert back["a"].iloc[0] == 1.0
    assert np.isnan(back["a"].iloc[1])
    assert np.isinf(back["a"].iloc[2]) and back["a"].iloc[2] > 0
    assert np.isinf(back["a"].iloc[3]) and back["a"].iloc[3] < 0


# ---------------------------------------------------------------------------
# 3. Series 写读
# ---------------------------------------------------------------------------


def test_write_read_series(tmp_path: Path) -> None:
    """Series → 转 DataFrame → parquet. 读回时单列 DataFrame, name 保留."""
    store = ArtifactStore(root=tmp_path)
    s = pd.Series([0.1, 0.2, 0.3], name="rev_20")

    uri = store.write("run_s", "series_node", "output", s)
    assert uri.endswith(".parquet")

    back = store.read("run_s", "series_node")
    # 读回是 DataFrame (单列), 列名 == Series.name
    assert isinstance(back, pd.DataFrame)
    assert list(back.columns) == ["rev_20"]
    assert back.shape == (3, 1)
    # 数值保真
    assert back["rev_20"].tolist() == [0.1, 0.2, 0.3]


def test_write_read_series_unnamed(tmp_path: Path) -> None:
    """Series 没 name 时, ``to_frame()`` 用 column = 0 (pandas 默认)."""
    store = ArtifactStore(root=tmp_path)
    s = pd.Series([1.0, 2.0])  # name=None
    store.write("run_s2", "node_x", "output", s)
    back = store.read("run_s2", "node_x")
    assert isinstance(back, pd.DataFrame)
    assert back.shape == (2, 1)
    assert back.iloc[:, 0].tolist() == [1.0, 2.0]


# ---------------------------------------------------------------------------
# 4. 不存在 → FileNotFoundError
# ---------------------------------------------------------------------------


def test_read_missing_raises_file_not_found(tmp_path: Path) -> None:
    """没写过的 (run_id, node_id) → FileNotFoundError, 不是 KeyError / ValueError."""
    store = ArtifactStore(root=tmp_path)
    with pytest.raises(FileNotFoundError):
        store.read("never_existed_run", "never_existed_node")


def test_read_missing_node_after_other_node_written(tmp_path: Path) -> None:
    """同 run_id 下 node_a 写了, 读 node_b 仍然 FileNotFoundError."""
    store = ArtifactStore(root=tmp_path)
    store.write("run_x", "node_a", "output", {"k": "v"})
    with pytest.raises(FileNotFoundError):
        store.read("run_x", "node_b")


def test_exists_returns_false_for_missing(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path)
    assert store.exists("nope", "nope") is False


def test_exists_returns_true_after_write(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path)
    store.write("run_e", "node_e", "output", {"hello": "world"})
    assert store.exists("run_e", "node_e") is True


# ---------------------------------------------------------------------------
# 5. 并发: 同 run_id 不同 node_id (2 线程不冲突)
# ---------------------------------------------------------------------------


def test_concurrent_write_different_nodes_no_conflict(tmp_path: Path) -> None:
    """同 run_id 不同 node_id, 2 线程并行 write 不互踩.

    Workflow runner 是串行的 (spec §5.2), 但 ArtifactStore 不能假设串行 —
    比如 Phase 1 可能加并行调度, 或者用户在 REPL 同时写两个 run.
    最小契约: 不同 node_id 写到不同子目录, 文件系统层面不共享句柄.
    """
    store = ArtifactStore(root=tmp_path)
    errors: list[Exception] = []
    payloads = {
        "node_a": {"who": "a", "values": list(range(100))},
        "node_b": pd.DataFrame({"x": np.arange(100, dtype=float), "y": np.arange(100) * 0.1}),
    }

    def _worker(node_id: str) -> None:
        try:
            store.write("shared_run", node_id, "output", payloads[node_id])
        except Exception as e:  # pragma: no cover - 失败路径
            errors.append(e)

    threads = [threading.Thread(target=_worker, args=(nid,)) for nid in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)
        assert not t.is_alive(), f"线程 {t.name} 超时未结束"

    assert errors == [], f"并发写出错: {errors}"

    # 两条都能正确读回
    back_a = store.read("shared_run", "node_a")
    assert back_a == payloads["node_a"]
    back_b = store.read("shared_run", "node_b")
    assert isinstance(back_b, pd.DataFrame)
    assert back_b.equals(payloads["node_b"])


# ---------------------------------------------------------------------------
# 6. 多 output_name 并存 (Phase 1 多输出节点提前验证)
# ---------------------------------------------------------------------------


def test_multiple_output_names_coexist(tmp_path: Path) -> None:
    """同 (run_id, node_id) 不同 output_name → 两个文件并存, 互不覆盖."""
    store = ArtifactStore(root=tmp_path)
    store.write("run_multi", "alpha", "signal", {"x": 1})
    store.write("run_multi", "alpha", "positions", {"y": 2})

    assert store.read("run_multi", "alpha", "signal") == {"x": 1}
    assert store.read("run_multi", "alpha", "positions") == {"y": 2}
    assert store.exists("run_multi", "alpha", "signal")
    assert store.exists("run_multi", "alpha", "positions")
    # 默认 output_name='output' 不存在
    assert not store.exists("run_multi", "alpha")


# ---------------------------------------------------------------------------
# 7. 同 (run, node, output_name) 二次写 dict → DataFrame, 旧 .json 被清
# ---------------------------------------------------------------------------


def test_overwrite_changes_extension_cleans_stale(tmp_path: Path) -> None:
    """先写 dict (.json), 再用同名 output_name 写 DataFrame (.parquet), 不留 stale .json.

    这是 _find_output 幽灵 bug 的防御: 如果两个扩展都存在, 探测顺序决定读哪个,
    用户拿到的可能是旧 dict 而不是新 DataFrame.
    """
    store = ArtifactStore(root=tmp_path)
    node_dir = tmp_path / "workflow_runs" / "run_evo" / "nodes" / "evo"

    # 1. 写 dict → output.json
    store.write("run_evo", "evo", "output", {"v": 1})
    assert (node_dir / "output.json").exists()
    assert not (node_dir / "output.parquet").exists()

    # 2. 同名 output_name 写 DataFrame → output.parquet, 旧 output.json 必须消失
    df = pd.DataFrame({"a": [10, 20]})
    store.write("run_evo", "evo", "output", df)
    assert (node_dir / "output.parquet").exists(), "新 parquet 没落盘"
    assert not (node_dir / "output.json").exists(), (
        "旧 output.json 没被清, _find_output 会拿到 stale 数据"
    )

    # 读出来是新的 DataFrame
    back = store.read("run_evo", "evo", "output")
    assert isinstance(back, pd.DataFrame)
    assert back.equals(df)


def test_overwrite_changes_extension_reverse_direction(tmp_path: Path) -> None:
    """反向: 先写 DataFrame, 再用同名 output_name 写 dict, 不留 stale .parquet."""
    store = ArtifactStore(root=tmp_path)
    node_dir = tmp_path / "workflow_runs" / "run_evo2" / "nodes" / "evo"

    store.write("run_evo2", "evo", "output", pd.DataFrame({"a": [1]}))
    assert (node_dir / "output.parquet").exists()

    store.write("run_evo2", "evo", "output", {"new": True})
    assert (node_dir / "output.json").exists()
    assert not (node_dir / "output.parquet").exists(), (
        "旧 output.parquet 没被清"
    )

    back = store.read("run_evo2", "evo", "output")
    assert back == {"new": True}


# ---------------------------------------------------------------------------
# 8. NaN/Inf sanitize 进 JSON
# ---------------------------------------------------------------------------


def test_nan_inf_in_dict_become_null_in_json(tmp_path: Path) -> None:
    """JSON spec 不允许 NaN / Inf, 必须 sanitize 成 None.

    场景: 节点 compute 返回 ``{"ic": float('nan'), "ratio": float('inf')}``,
    artifacts.write 落到磁盘的 JSON 必须是 ``"ic": null``, 不能是 ``"ic": NaN``.
    """
    store = ArtifactStore(root=tmp_path)
    payload = {
        "ic_normal": 0.05,
        "ic_nan": float("nan"),
        "ic_pos_inf": float("inf"),
        "ic_neg_inf": float("-inf"),
        "nested": {"deep_nan": float("nan")},
        "list_with_nan": [1.0, float("nan"), 2.0],
    }
    uri = store.write("run_nan", "ic_table", "output", payload)

    # 直接读原始文件, 验证字面 null
    physical = tmp_path / uri
    raw = physical.read_text(encoding="utf-8")
    assert "NaN" not in raw, f"JSON 写出了非法的 NaN 字面量:\n{raw}"
    assert "Infinity" not in raw, f"JSON 写出了非法的 Infinity 字面量:\n{raw}"

    # 反序列化 — NaN/Inf 全部成了 None
    back = store.read("run_nan", "ic_table")
    assert back["ic_normal"] == 0.05
    assert back["ic_nan"] is None
    assert back["ic_pos_inf"] is None
    assert back["ic_neg_inf"] is None
    assert back["nested"]["deep_nan"] is None
    assert back["list_with_nan"] == [1.0, None, 2.0]


def test_numpy_scalars_in_dict_become_native(tmp_path: Path) -> None:
    """节点可能塞 ``np.float64`` / ``np.int64``, sanitize 要转 Python 原生."""
    store = ArtifactStore(root=tmp_path)
    payload = {
        "n_int64": np.int64(42),
        "n_float64": np.float64(3.14),
        "n_bool": np.bool_(True),
        "n_nan64": np.float64("nan"),
    }
    store.write("run_np", "scalars", "output", payload)
    back = store.read("run_np", "scalars")
    assert back["n_int64"] == 42
    assert back["n_float64"] == 3.14
    assert back["n_bool"] is True
    assert back["n_nan64"] is None  # NaN → null


# ---------------------------------------------------------------------------
# 9. 不支持类型 → TypeError
# ---------------------------------------------------------------------------


def test_write_rejects_ndarray_with_typeerror(tmp_path: Path) -> None:
    """``np.ndarray`` 不在路由表里 (spec §6.2 最后一行), 节点应自己转."""
    store = ArtifactStore(root=tmp_path)
    arr = np.array([1.0, 2.0, 3.0])
    with pytest.raises(TypeError, match="ndarray"):
        store.write("run_ndarr", "bad", "output", arr)


# ---------------------------------------------------------------------------
# 10. 覆盖 (同 (run_id, node_id, output_name) 二次写同类型)
# ---------------------------------------------------------------------------


def test_overwrite_same_node_same_type(tmp_path: Path) -> None:
    """同 (run_id, node_id, output_name) 二次 write 覆盖前值 (同类型, 仍 .json)."""
    store = ArtifactStore(root=tmp_path)
    store.write("run_o", "n", "output", {"v": 1})
    store.write("run_o", "n", "output", {"v": 2})
    assert store.read("run_o", "n") == {"v": 2}
    # 只有一个文件
    node_dir = tmp_path / "workflow_runs" / "run_o" / "nodes" / "n"
    files = [p for p in node_dir.iterdir() if p.suffix in (".json", ".parquet")]
    assert len(files) == 1
    assert files[0].name == "output.json"


# ---------------------------------------------------------------------------
# 11. root 属性 (runner 用来拼 artifacts_root)
# ---------------------------------------------------------------------------


def test_root_property_exposes_path(tmp_path: Path) -> None:
    """``store.root`` 暴露 workflow_runs 父目录, runner 用来拼 RunResult.artifacts_root."""
    store = ArtifactStore(root=tmp_path)
    assert store.root == tmp_path
    assert isinstance(store.root, Path)
