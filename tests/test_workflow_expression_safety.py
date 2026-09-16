"""Static Agent DSL guard: never load a panel or execute submitted expressions."""
import copy

import pytest

from guanlan_v2.workflow.expression_safety import validate_expression, validate_graph_expressions


@pytest.mark.parametrize('expr', [
    'close', 'rank(-delta(close, 20))',
    'filter_where(returns, (close > open) & (volume > 0))',
    'regbeta(returns, idx_ret, 120, min_periods=60)',
    'signedpower(close, 0.5)', 'sma(close, 20, 1)',
    'indneutralize(rank(close), industry)', 'abs(close-open)',
    'ts_mean(close, n=20)', 'scale(close, a=1.5)',
    'delay(close, 0)', 'close ** 2 / (60 ** 0.5)',
])
def test_safe_math(expr):
    assert validate_expression(expr) == []


@pytest.mark.parametrize('expr', [
    "close.to_csv('owned.csv')", "close.to_pickle('owned.pkl')",
    'close.to_numpy()', 'returns.map(close)', "close['x']", 'close.iloc[0]',
    "__import__('os')", '[close]', '{1: close}', '(lambda x:x)(close)',
    '[x for x in close]', '(x for x in close)', '(x := close)',
    'close if volume else open', 'close.__class__', 'close()',
    'np.power(close, 2)', 'unknown(close)', 'rank(*close)', 'rank(**close)',
    '(close > 0) and (open > 0)', 'close << 10', 'not_a_field',
    'ts_mean(close, 0)', 'ts_mean(close, -1)', 'ts_mean(close, 20.0)',
    'ts_mean(close, 2+3)', 'ts_mean(close, 1000000000)',
    'delay(close, -1)', 'delta(close, -1)', 'close ** close',
    'close ** 100000', '(2 ** 16) ** 16', 'sma(close, 1)',
    'sma(close, 10, 10)', 'rank(close, extra=1)', 'rank()',
    'regbeta(close, returns, 20, min_periods=21)', 'True', '1e309',
    'rank(close, x=close)', 'power(close, 100)', 'scale(close, a=close)',
])
def test_unsafe_or_unbounded_rejected(expr):
    assert validate_expression(expr)


def test_pathological_constants_and_complexity_return_errors_not_exceptions():
    huge = '9' * 1000
    for expr in (huge, 'close ** ' + huge, 'power(close, ' + huge + ')',
                 'ts_mean(close, ' + huge + ')', 'rank(' * 40 + 'close' + ')' * 40,
                 '+'.join(['close'] * 600), 'close' + ' ' * 9000 + '+open',
                 '+'.join(['ts_mean(close,2048)'] * 5),
                 'ts_mean(close, ' + '-' * 1500 + '1)',
                 'close ** ' + '-' * 1500 + '1'):
        assert validate_expression(expr)


def test_future_only_for_labels():
    assert validate_expression('delay(close, -5) / close - 1', allow_future=True) == []
    assert validate_expression('ts_mean(close, -5)', allow_future=True)
    assert validate_expression('delay(close, -2049)', allow_future=True)


def _registered(monkeypatch, name, compute, formula='rank(close)'):
    from financial_analyst.factors.zoo import registry
    monkeypatch.setitem(registry._REGISTRY, name, registry.AlphaSpec(
        name=name, family='test', description='', formula_text=formula, compute=compute))


def test_opaque_registered_python_not_trusted_by_metadata(monkeypatch):
    calls = []
    def opaque(panel):
        calls.append(panel)
        raise AssertionError('must never execute')
    _registered(monkeypatch, 'opaque_test', opaque)
    assert validate_expression('opaque_test')
    assert calls == []


def test_exact_dsl_closure_inspects_actual_definition_not_metadata(monkeypatch):
    from financial_analyst.factors.zoo.expr import compile_factor
    _registered(monkeypatch, 'safe_test', compile_factor('rank(close)'))
    _registered(monkeypatch, 'unsafe_test', compile_factor("close.to_csv('owned.csv')"))
    assert validate_expression('safe_test') == []
    assert validate_expression('unsafe_test')
    assert validate_expression('safe_test()')
    assert validate_expression('rank(safe_test)')


def test_builtin_names_are_references_not_calls():
    from financial_analyst.factors.zoo import registry
    names = [s.name for s in registry.list_alphas() if s.family in ('alpha101', 'gtja191', 'qlib158')]
    assert names
    for name in names:
        assert validate_expression(name) == [], name
    assert validate_expression(names[0] + '()')


def _graph(kind='formula', **params):
    return {'nodes': [{'id': 'n1', 'type': kind, 'params': params}], 'edges': []}


@pytest.mark.parametrize('graph', [
    _graph(expr="close.to_csv('x')"),
    _graph('feature', tag="close.to_csv('x')"),
    _graph('factorlib', expr="close.to_csv('x')"),
    _graph('factorlib', expr='close', query="close.to_csv('x')"),
    _graph('factorlib', name="close.to_csv('x')"),
    _graph('factorlib', query='momentum'),
    _graph('orthogonalize', old_factors=["close.to_csv('x')"]),
    _graph('orthogonalize', existing_factors="close.to_csv('x')"),
    _graph('orthogonalize', styles=["close.to_csv('x')"]),
    _graph('orthogonalize', size="close.to_csv('x')"),
    _graph('factorlib', expr='close', factor_spec={'expr': "close.to_csv('x')"}),
    _graph('factorlib', expr='close', factor_spec_digest='pretend-certified'),
])
def test_embedded_entry_points_fail_closed_without_mutation(graph):
    before = copy.deepcopy(graph)
    assert validate_graph_expressions(graph)
    assert graph == before


def test_control_dsl_and_aliases_compatible():
    assert validate_graph_expressions(_graph('orthogonalize', size='总市值',
        styles='beta,波动,liquidity,动量', old_factors='rank(close); correlation(close,volume,20)')) == []
    assert validate_graph_expressions(_graph('orthogonalize', size='none',
        old_factors=['rank(close)', 'ts_mean(volume,20)'])) == []
    assert validate_graph_expressions(_graph('orthogonalize', old_factors=['close'] * 13))


def test_label_sink_is_only_future_formula_exception():
    graph = _graph(expr='delay(close,-5)/close-1')
    graph['nodes'].append({'id': 'f', 'type': 'feature', 'params': {'tag': 'IC'}})
    graph['edges'] = [{'from': ['n1', 'out'], 'to': ['f', 'label']}]
    assert validate_graph_expressions(graph) == []
    graph['edges'].append({'from': ['n1', 'out'], 'to': ['f', 'feat']})
    assert validate_graph_expressions(graph)
    assert validate_graph_expressions(_graph('feature', tag='delay(close,-1)/close-1')) == []


@pytest.mark.parametrize('rows, expected_ok', [
    ([{'name': 'my_momentum', 'expr': 'rank(close)'}], True),
    ([{'name': 'my_momentum', 'expr': "close.to_csv('x')"}], False),
    ([{'name': 'my_momentum', 'expr': 'close', 'factor_spec': {'base': 'close'}}], False),
    ([{'name': 'my_momentum_v2', 'expr': 'close'}], False),
    ([{'name': 'my_momentum', 'expr': 'close'}, {'name': 'MY_MOMENTUM', 'expr': 'open'}], False),
])
def test_library_exact_static_lookup_never_compiles(monkeypatch, rows, expected_ok):
    from guanlan_v2.factorlib.store import LibraryFactorStore
    monkeypatch.setattr(LibraryFactorStore, 'load_all', lambda self: rows)
    def forbidden(*a, **kw):
        raise AssertionError('must not compile/list/register')
    monkeypatch.setattr(LibraryFactorStore, 'list_factors', forbidden)
    errors = validate_graph_expressions(_graph('factorlib', name='my_momentum'))
    assert (not errors) is expected_ok


def test_graph_structure_limits():
    assert validate_graph_expressions({'nodes': [], 'edges': 'not-an-array'})
    assert validate_graph_expressions({'nodes': [{}] * 101})
    assert validate_graph_expressions({'nodes': [{'params': []}]})
