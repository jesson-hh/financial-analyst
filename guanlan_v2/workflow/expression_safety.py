"""Fail-closed, static DSL guard for Agent-authored workflow graphs.

No eval, panel loading, registry mutation or compute invocation occurs here.
This is an authoring gate, not an OS sandbox or a replacement for runtime
data/recipe binding. Opaque custom Python and presealed factor_spec products
are deliberately refused; use an explicit DSL -> orthogonalize graph instead.
"""
from __future__ import annotations

import ast
import inspect
import math
from functools import lru_cache
from pathlib import Path
from types import FunctionType
from typing import Any

MAX_CHARS = 8192
MAX_NODES = 512
MAX_DEPTH = 32
MAX_CALLS = 80
MAX_WINDOW = 2048
MAX_WINDOW_SUM = 8192
MAX_CONSTANT = 1.0e12
MAX_POWER = 16
MAX_GRAPH_NODES = 100
MAX_REFERENCES = 12
_BIN = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
        ast.BitAnd, ast.BitOr, ast.BitXor)
_UNARY = (ast.UAdd, ast.USub, ast.Invert)
_CMP = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_STYLE = {'beta', 'β', 'vol', 'volatility', '波动', 'liquidity', '流动性', 'momentum', '动量'}
_SIZE = {'none', 'total_mv', 'circ_mv', '不控制', '总市值', '流通市值', '', '否'}


@lru_cache(maxsize=1)
def _vocabulary():
    from financial_analyst.factors.zoo import expr, operators
    # The engine is the source of truth; no permissive fallback vocabulary.
    signatures = {name: inspect.signature(getattr(operators, 'abs_' if name == 'abs' else name))
                  for name in expr._OP_NAMES}
    return expr._FIELD_NAMES, expr._OP_NAMES, signatures


def _literal(node):
    sign = 1
    depth = 0
    while isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        depth += 1
        if depth > MAX_DEPTH:
            return None
        if isinstance(node.op, ast.USub):
            sign *= -1
        node = node.operand
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return sign * node.value
    return None


def _bounded(value, cap=MAX_CONSTANT):
    # Compare arbitrary-size ints before isfinite (which converts to float).
    return type(value) in (int, float) and abs(value) <= cap and math.isfinite(value)


def _registered_error(name: str, allow_future: bool, seen: frozenset[str]) -> list[str]:
    """Accept audited builtin implementation or the exact engine DSL closure.

    Merely possessing a registry name/formula_text/metadata is not sufficient:
    an arbitrary Python compute function must never gain trust from that.
    """
    if name in seen or len(seen) >= 4:
        return ['注册因子依赖循环或层数超过 4']
    try:
        from financial_analyst.factors.zoo import expr as engine_expr
        from financial_analyst.factors.zoo.registry import get
        spec = get(name)
    except KeyError:
        return [f'未知字段或未注册因子: {name}']
    except Exception:
        return ['注册因子目录不可用；请先使用显式纯 DSL']
    compute = spec.compute
    if not isinstance(compute, FunctionType):
        return [f'注册因子 {name} 为不可审计调用对象；请展开纯 DSL']
    # Repo-owned builtin implementations are trusted code, unlike registry
    # metadata. Require BOTH exact module family and on-disk source location.
    family_modules = {'alpha101', 'gtja191', 'qlib158', 'academic'}
    parts = str(compute.__module__).split('.')
    if len(parts) == 5 and parts[:3] == ['financial_analyst', 'factors', 'zoo'] \
            and parts[3] in family_modules and parts[4] == 'alphas':
        expected = Path(engine_expr.__file__).resolve().parent / parts[3] / 'alphas.py'
        if Path(compute.__code__.co_filename).resolve() == expected.resolve():
            return []
    prototype = engine_expr.compile_factor('close')  # constructs closure; never evaluates it
    if compute.__code__ is prototype.__code__:
        cells = dict(zip(compute.__code__.co_freevars, compute.__closure__ or ()))
        try:
            bound_expr = cells['expr'].cell_contents
        except (KeyError, ValueError):
            return [f'注册因子 {name} 缺少确定的 DSL 定义']
        return _validate_expression(bound_expr, allow_future, seen | {name})
    return [f'注册因子 {name} 的 Python/结构化实现不可静态确认安全；请先展开纯 DSL']


def validate_expression(expr: Any, *, allow_future: bool = False) -> list[str]:
    """Return [] on success; otherwise bounded, user-facing static errors.

    Negative delay/delta is reserved for explicit prediction-label sinks.
    Named registered factors are references only, never callable DSL names.
    """
    return _validate_expression(expr, allow_future, frozenset())


def _validate_expression(expr, allow_future, seen):
    if type(expr) is not str or not expr.strip():
        return ['表达式必须是非空字符串']
    text = expr.strip()
    if len(text) > MAX_CHARS:
        return [f'表达式超过 {MAX_CHARS} 字符']
    if '__' in text:
        return ['表达式禁止双下划线名称']
    try:
        fields, ops, signatures = _vocabulary()
    except Exception:
        return ['DSL 引擎词表不可用；拒绝使用猜测白名单']
    if text.isidentifier() and text not in fields and text not in ops:
        return _registered_error(text, allow_future, seen)
    try:
        tree = ast.parse(text, mode='eval')
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return ['表达式语法错误或嵌套过深']
    stack = [(tree, 0)]
    count = calls = window_sum = 0
    errors = []
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            errors.append(f'表达式复杂度超限（节点≤{MAX_NODES}，深度≤{MAX_DEPTH}）')
            break
        children = []
        if isinstance(node, ast.Expression):
            children = [node.body]
        elif isinstance(node, ast.Name):
            if node.id not in fields:
                errors.append(f'非字段名称 {node.id} 不能在数学表达式中引用；注册因子须单独引用')
        elif isinstance(node, ast.Constant):
            value = node.value
            if not _bounded(value):
                errors.append('只允许绝对值≤1e12的有限数值常量')
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, _UNARY):
            children = [node.operand]
        elif isinstance(node, ast.BinOp) and isinstance(node.op, _BIN):
            if isinstance(node.op, ast.Pow):
                power = _literal(node.right)
                if not _bounded(power, MAX_POWER):
                    errors.append('幂指数必须是绝对值≤16的有限数值字面量')
                # Allow small literal powers (e.g. 60 ** 0.5) but prevent
                # nested Python big-integer powers before a Series participates.
                if not any(isinstance(x, ast.Name) and x.id in fields for x in ast.walk(node.left)):
                    base = _literal(node.left)
                    if not _bounded(base) or not _bounded(power, MAX_POWER) or abs(base) > 10000:
                        errors.append('纯常量 ** 只接受小型字面量底数与有界指数；禁止嵌套幂')
            children = [node.left, node.right]
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], _CMP):
            children = [node.left, node.comparators[0]]
        elif isinstance(node, ast.Call):
            calls += 1
            if not isinstance(node.func, ast.Name) or node.func.id not in ops:
                errors.append('只允许直接调用引擎白名单数学算子；禁止属性或未知函数调用')
            elif any(keyword.arg is None for keyword in node.keywords) or any(isinstance(x, ast.Starred) for x in node.args):
                errors.append('算子调用禁止 *args / **kwargs 展开')
            else:
                name = node.func.id
                try:
                    if len({x.arg for x in node.keywords}) != len(node.keywords):
                        raise TypeError('重复关键字')
                    bound = signatures[name].bind(*node.args, **{x.arg: x.value for x in node.keywords})
                except TypeError:
                    errors.append(f'算子 {name} 的实参不符合引擎真实签名 {signatures[name]}')
                else:
                    args = bound.arguments
                    n = _literal(args['n']) if 'n' in args else None
                    if 'n' in args:
                        lower = -MAX_WINDOW if allow_future and name in ('delay', 'delta') else (0 if name in ('delay', 'delta') else 1)
                        if type(n) is not int or not lower <= n <= MAX_WINDOW:
                            errors.append(f'{name}.n 必须是 {lower}..{MAX_WINDOW} 的整数字面量')
                        else:
                            window_sum += abs(n)
                    for key in ('p', 'a', 'm', 'min_periods'):
                        if key not in args:
                            continue
                        value = _literal(args[key])
                        cap = MAX_POWER if key == 'p' else MAX_CONSTANT
                        if not _bounded(value, cap):
                            errors.append(f'{name}.{key} 必须是有界有限数值字面量')
                        if key in ('m', 'min_periods') and (type(value) is not int or type(n) is not int or not 1 <= value <= n or (key == 'm' and value == n)):
                            errors.append(f'{name}.{key} 必须为正整数且不超过窗口（sma.m 须小于 n）')
                    if name == 'sma' and n == 1:
                        errors.append('sma.n 必须大于默认或显式 m')
            children = list(node.args) + [keyword.value for keyword in node.keywords]
        else:
            errors.append(f'不允许的表达式语法: {type(node).__name__}')
        if calls > MAX_CALLS or window_sum > MAX_WINDOW_SUM:
            errors.append(f'表达式算子/累计窗口预算超限（调用≤{MAX_CALLS}，窗口合计≤{MAX_WINDOW_SUM}）')
            break
        if len(errors) >= 8:
            break
        stack.extend((child, depth + 1) for child in children)
    return list(dict.fromkeys(errors))[:8]


def _controls(value: Any) -> list[str]:
    if value is None or value == '':
        return []
    if type(value) is list and all(type(x) is str for x in value):
        return [x.strip() for x in value if x.strip()]
    if type(value) is str:
        text = value.strip().replace('；', ';').replace('\r', '\n')
        if '(' not in text and ')' not in text:
            text = text.replace('，', ',').replace(',', ';')
        return [x.strip() for x in text.replace(';', '\n').split('\n') if x.strip()]
    raise ValueError('控制因子必须是字符串列表或分号分隔 DSL')


def validate_graph_expressions(graph: Any) -> list[str]:
    """Audit every executable expression entry; never rewrites the graph.

    Covers formula, custom feature.tag, factorlib expr/name/query, all supplied
    factor_spec, orthogonalize old_factors/styles/size. Library lookups are exact
    and static; fuzzy/UI query selection is refused for Agent execution.
    """
    if type(graph) is not dict or type(graph.get('nodes')) is not list:
        return ['graph.nodes 必须是数组']
    nodes = graph['nodes']
    if len(nodes) > MAX_GRAPH_NODES:
        return [f'Agent 图最多 {MAX_GRAPH_NODES} 个节点']
    errors = []
    library = None
    edges = graph.get('edges') or []
    if type(edges) is not list or len(edges) > 512:
        return ['graph.edges 必须是最多 512 项的数组']
    def check(value, path, future=False):
        errors.extend(f'{path}: {error}' for error in validate_expression(value, allow_future=future))
    for node in nodes:
        if type(node) is not dict or type(node.get('params', {})) is not dict:
            errors.append('节点和 params 必须是对象'); continue
        nid, kind, p = str(node.get('id', '?')), node.get('type'), node.get('params', {})
        prefix = f'节点 {nid}'
        if p.get('factor_spec') is not None or p.get('factor_spec_digest'):
            errors.append(prefix + ': Agent 暂不执行外带预封存 factor_spec；请展开纯 DSL → orthogonalize，保留原历史图')
        if kind == 'formula':
            destinations = [e.get('to') for e in edges if type(e) is dict and type(e.get('from')) is list and e['from'] and e['from'][0] == node.get('id')]
            types = {str(n.get('id')): n.get('type') for n in nodes if type(n) is dict}
            label_only = bool(destinations) and all(type(to) is list and len(to) == 2 and to[1] == 'label' and types.get(str(to[0])) == 'feature' for to in destinations)
            check(p.get('expr'), prefix + '.expr', label_only)
        elif kind == 'feature':
            tag = p.get('tag', '')
            if type(tag) is not str:
                errors.append(prefix + '.tag 必须是字符串')
            elif tag.strip().lower() not in ('', 'ic', 'fwd_ret', 'fwd', 'forward', 'forward_return', 'return', 'ret'):
                check(tag, prefix + '.tag', True)
        elif kind == 'factorlib':
            picked, name, query = p.get('expr', ''), p.get('name', ''), p.get('query', '')
            if picked:
                check(picked, prefix + '.expr')
            if query and (type(query) is not str or not query.strip().isidentifier() or '__' in query):
                errors.append(prefix + '.query 只能是库名称检索，不接受表达式或代码')
            if name:
                if type(name) is not str or not name.strip().isidentifier() or '__' in name:
                    errors.append(prefix + '.name 必须是精确、不可执行的库名称'); continue
                try:
                    if library is None:
                        from guanlan_v2.factorlib.store import LibraryFactorStore
                        library = LibraryFactorStore().load_all()  # raw JSON, never compile/register
                    if len(library) > 4096:
                        raise ValueError('库条目过多，请直接使用纯 DSL')
                    hits = [row for row in library if str(row.get('name', '')).lower() == name.strip().lower()]
                    if len(hits) != 1:
                        raise ValueError('必须精确命中唯一库记录；禁止模糊选取，请先展开纯 DSL')
                    hit = hits[0]
                    if hit.get('factor_spec') is not None:
                        raise ValueError('结构化注册因子暂不静态放行；请展开纯 DSL → orthogonalize')
                    check(hit.get('expr'), prefix + '.name→库定义')
                except Exception as exc:
                    errors.append(prefix + '.name: ' + str(exc)[:220])
            elif not picked:
                errors.append(prefix + ': 查询词不能充当执行因子；请提供精确 name 或纯 DSL expr')
        elif kind == 'orthogonalize':
            size = p.get('size', 'total_mv')
            if type(size) is not str or size.strip().lower() not in _SIZE:
                errors.append(prefix + '.size 不在命名控制白名单')
            try:
                styles = _controls(p.get('styles'))
                if len(styles) > MAX_REFERENCES or any(s.lower() not in _STYLE for s in styles):
                    errors.append(prefix + '.styles 只接受 beta/volatility/liquidity/momentum 及现有中文别名')
                for key in ('old_factors', 'existing_factors'):
                    values = _controls(p.get(key))
                    if len(values) > MAX_REFERENCES:
                        errors.append(prefix + f'.{key} 最多 {MAX_REFERENCES} 项')
                    else:
                        for i, value in enumerate(values):
                            check(value, prefix + f'.{key}[{i}]')
            except ValueError as exc:
                errors.append(prefix + ': ' + str(exc))
        if len(errors) >= 30:
            break
    return list(dict.fromkeys(errors))[:30]
