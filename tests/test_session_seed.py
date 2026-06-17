# 抗重启记忆 · seed 冷 agent 回归锁
#
# 根因(已证): 对话历史只存后端 in-memory sessions 字典; 进程一重启(看门狗/崩溃/部署)
# 就全清空 → agent 变「冷」(messages 空) → 多轮失忆(浏览器实测 T3「确认」→「哪只股票」)。
# 修法: 前端每轮带近期对话, 后端用 _seed_agent_history 在冷 agent 上注入回来。
#
# 锁: ① 冷 agent 被客户端历史正确 seed ② 噪声/坏 role/空文本被剔 ③ 有界截断。
from financial_analyst.buddy.agent import Message
from financial_analyst.buddy.server import _seed_agent_history


class _ColdAgent:
    """最小 agent: 只需 messages 列表(对齐 BuddyAgent 冷启态)。"""

    def __init__(self):
        self.messages = []


def test_seed_populates_cold_agent():
    a = _ColdAgent()
    _seed_agent_history(a, [
        {"role": "user", "content": "你帮我看看立昂微"},
        {"role": "assistant", "content": "立昂微 (SH605358) 当日涨停, PB 6.85。"},
    ])
    assert [m.role for m in a.messages] == ["user", "assistant"]
    assert "立昂微" in a.messages[0].content
    assert isinstance(a.messages[0], Message)


def test_seed_skips_noise():
    a = _ColdAgent()
    _seed_agent_history(a, [
        {"role": "tool", "content": "ths_fund_flow 原始 JSON 一大坨"},  # 工具噪声
        {"role": "user", "content": ""},                                # 空
        {"role": "user", "content": "  你好  "},                         # 保留(strip)
        "notadict",                                                      # 非 dict
        {"role": "assistant"},                                          # 缺 content
    ])
    assert [m.role for m in a.messages] == ["user"]
    assert a.messages[0].content == "你好"


def test_seed_is_bounded():
    a = _ColdAgent()
    hist = [{"role": "user", "content": "x" * 100} for _ in range(40)]
    _seed_agent_history(a, hist, max_msgs=16, max_chars=8000)
    assert len(a.messages) <= 16


def test_seed_empty_is_noop():
    a = _ColdAgent()
    _seed_agent_history(a, None)
    _seed_agent_history(a, [])
    assert a.messages == []
