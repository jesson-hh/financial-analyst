"""Bounded live conversation smoke. Never supplies a graph on the Agent's behalf.

Start only a new test session; confirmation is an explicit separate action after
reviewing the exact pending tool arguments. No automatic always-allow choices.
"""
import argparse
import json
import os
from pathlib import Path
import requests

CHECKOUT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = os.environ.get('GUANLAN_BASE_URL', 'http://127.0.0.1:9999')
RECEIPT = CHECKOUT / 'var/agent_workflow_20260905/live_agent_receipt.json'
GOAL = '''请做一次自主搭建流程图的功能验收。请你自己读取工作流目录，构建并保存一张新流程图，然后用工作流执行，不要用独立分析工具代替，也不要改已有图或默认模型。
研究范围固定为 csi_fast 股票池，2024-01-01 至 2026-09-04，沪深300基准，日频研究和回测，20%样本外。研究5日动量和20日低波两项特征，用10棵树的随机森林，启用滚动重训，连接因子分析和回测两个终端。所有特征、模型和上下文必须明确连线。
先校验，有错最多修正两次。保存和运行按系统确认门等待我确认；执行后读真实结果，返回可打开的工作流卡和run_id，并说明数据覆盖、样本外指标及失败节点。不发布因子、不做实盘推荐、不写研究空间代码。功能跑通不代表策略可实战。'''


def post(base, path, data):
    r = requests.post(base.rstrip('/') + path, json=data, timeout=30)
    r.raise_for_status()
    out = r.json()
    if not out.get('ok'):
        raise RuntimeError(out)
    return out


def receipt():
    return json.loads(RECEIPT.read_text(encoding='utf-8'))


def events(root, sid):
    assert sid.startswith('cs_') and '/' not in sid and '\\' not in sid
    path = root / 'var/console/sessions' / sid / 'events.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['start', 'status', 'confirm', 'review'])
    p.add_argument('--confirm-id')
    p.add_argument('--base-url', default=DEFAULT_BASE)
    p.add_argument('--live-root', type=Path, default=CHECKOUT,
                   help='Guanlan checkout containing var/console (default: this checkout)')
    args = p.parse_args()
    root = args.live_root.expanduser().resolve()
    if not (root / 'guanlan_v2').is_dir():
        p.error(f'not a Guanlan checkout: {root}')
    if args.action == 'start':
        if RECEIPT.exists():
            raise RuntimeError('Existing receipt: do not duplicate the test session')
        result = post(args.base_url, '/console/sessions', {'workspace_id': 'rw_general', 'title': '验收 · Agent自主流程图与跨页恢复'})
        sid = result['meta']['id']
        data = {'sid': sid, 'workspace_id': 'rw_general', 'goal': GOAL}
        RECEIPT.parent.mkdir(parents=True, exist_ok=True)
        RECEIPT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        data['turn'] = post(args.base_url, '/console/send', {'sid': sid, 'text': GOAL})
        RECEIPT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(data['turn'], ensure_ascii=False))
        return
    rec = receipt()
    rows = events(root, rec['sid'])
    if args.action == 'confirm':
        pending = next(e for e in reversed(rows) if e.get('type') == 'confirm_request')
        assert args.confirm_id and args.confirm_id == pending.get('confirm_id')
        name = pending.get('tool') or pending.get('name') or pending.get('tool_name')
        assert name in {'ww_workflow_save', 'ww_workflow_run'}, name
        assert not any(e.get('type') == 'confirm_resolved' and e.get('confirm_id') == args.confirm_id for e in rows)
        print(json.dumps(post(args.base_url, '/console/confirm', {'turn_id': pending['turn_id'], 'confirm_id': args.confirm_id, 'choice': 'y'}), ensure_ascii=False))
    elif args.action == 'review':
        text = '请只查询刚才的工作流任务真实运行结果并审查，返回原有工作流卡与run_id。不要再次保存/运行，不要调用其他工具或发布。失败如实说明。'
        print(json.dumps(post(args.base_url, '/console/send', {'sid': rec['sid'], 'text': text}), ensure_ascii=False))
    else:
        selected = [e for e in rows if e.get('type') not in {'thinking_delta', 'text_delta', 'assistant_delta'}]
        print(json.dumps({'sid': rec['sid'], 'events': selected[-12:]}, ensure_ascii=False, indent=2)[:26000])


if __name__ == '__main__':
    main()
