from guanlan_v2.workflow.migrations import migrate_graph


def test_single_formula_feature_analysis_is_rewired_losslessly():
    graph = {
        "nodes": [
            {"id": "s", "type": "source", "params": {"universe": "csi800"}},
            {"id": "f", "type": "formula", "params": {"expr": "close/lag(close,20)-1"}},
            {"id": "fe", "type": "feature", "params": {"tag": "IC"}},
            {"id": "a", "type": "analysis", "params": {}},
        ],
        "edges": [
            {"from": ["s", "data"], "to": ["fe", "src"]},
            {"from": ["f", "out"], "to": ["fe", "feat"]},
            {"from": ["fe", "fe"], "to": ["a", "factor"]},
        ],
    }

    migrated, report = migrate_graph(graph)

    assert report["changed"] is True
    assert report["runnable_candidate"] is True
    assert {"from": ["f", "out"], "to": ["a", "factor"]} in migrated["edges"]
    assert {"from": ["fe", "fe"], "to": ["a", "factor"]} not in migrated["edges"]
    assert graph["edges"][-1]["from"] == ["fe", "fe"]  # input is never mutated


def test_multi_formula_feature_path_is_reported_not_guessed():
    graph = {
        "nodes": [
            {"id": "f1", "type": "formula"},
            {"id": "f2", "type": "factorlib"},
            {"id": "fe", "type": "feature"},
            {"id": "a", "type": "analysis"},
        ],
        "edges": [
            {"from": ["f1", "out"], "to": ["fe", "feat"]},
            {"from": ["f2", "out"], "to": ["fe", "feat"]},
            {"from": ["fe", "fe"], "to": ["a", "factor"]},
        ],
    }

    migrated, report = migrate_graph(graph)

    assert report["changed"] is False
    assert report["runnable_candidate"] is False
    assert report["needs_review"][0]["code"] == "ambiguous_feature_to_factor"
    assert migrated["edges"] == graph["edges"]


def test_legacy_board_is_preserved_and_marked_for_review():
    graph = {
        "nodes": [
            {"id": "m", "type": "model", "params": {"model_id": "expired"}},
            {"id": "v", "type": "validate", "params": {}},
        ],
        "edges": [{"from": ["m", "out"], "to": ["v", "model"]}],
    }

    migrated, report = migrate_graph(graph)

    assert migrated["nodes"] == graph["nodes"]
    assert report["changed"] is False
    assert report["runnable_candidate"] is False
    assert {item["node_type"] for item in report["needs_review"]} == {"model", "validate"}
