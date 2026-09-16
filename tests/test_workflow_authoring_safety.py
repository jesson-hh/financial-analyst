"""The public Agent gate invokes static DSL validation before any execution."""
import copy
import pytest
from guanlan_v2.workflow.authoring import validate_authoring

GRAPH = {"nodes": [
    {"id": "s", "type": "source", "params": {"universe": "csi_fast"}},
    {"id": "f", "type": "formula", "params": {"expr": "rank(ts_sum(returns,5))"}},
    {"id": "a", "type": "analysis", "params": {"context_source_id": "s"}},
], "edges": [{"from": ["f", "out"], "to": ["a", "factor"]}]}


@pytest.mark.parametrize('expr', [
    'close.to_csv(1)', 'close[0]', '(x for x in close)',
    'open(1)', 'rank(**close)', 'delay(close,-1)',
    'ts_sum(returns,99999999)', 'close ** (2 ** 30)',
])
def test_authoring_refuses_non_dsl_or_future_features(expr):
    graph = copy.deepcopy(GRAPH)
    graph['nodes'][1]['params']['expr'] = expr
    before = copy.deepcopy(graph)
    out = validate_authoring(graph)
    assert not out['ok'] and out['errors']
    assert graph == before


def test_authoring_accepts_daily_research_math_and_rejects_unknown_frequency():
    graph = copy.deepcopy(GRAPH)
    graph['nodes'][0]['params']['research_freq'] = 'day'
    assert validate_authoring(graph)['ok']
    graph['nodes'][0]['params']['research_freq'] = 'minute'
    assert not validate_authoring(graph)['ok']


def test_negative_ic_rule_does_not_flip_on_same_test_sample():
    from guanlan_v2.workflow.api import _rule_critique
    out = _rule_critique('研究', {'rank_ic': -0.2}, GRAPH)
    assert out['ok']
    assert out['graph']['nodes'][1]['params']['expr'] == GRAPH['nodes'][1]['params']['expr']
    assert 'dir' not in out['graph']['nodes'][2]['params']
    assert out['requires_fresh_evaluation'] is True
