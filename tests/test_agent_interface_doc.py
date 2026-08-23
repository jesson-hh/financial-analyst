# -*- coding: utf-8 -*-
"""agent 接口文档漂移守护。

docs/agent_data_interfaces.md 由 scripts/gen_agent_interface_doc.py 全文生成
(Console 清单从 aggregate、MCP 从 legacy WW_TOOL_TABLE/tooltable 派生)。
本守护保证:①盘上文档与生成器输出逐字一致(改了工具表没再生 → 红);
②文档覆盖全部 MCP 工具与关键接入坑。glmcp README 教训:手抄计数必腐烂(58 vs 实际 64)。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_GEN_PATH = _REPO / "scripts" / "gen_agent_interface_doc.py"


def _load_gen():
    spec = importlib.util.spec_from_file_location("gen_agent_interface_doc", _GEN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_doc_in_sync_with_generator():
    gen = _load_gen()
    doc = Path(gen.DOC_PATH)
    assert doc.exists(), "文档缺席:跑 python scripts/gen_agent_interface_doc.py 生成"
    assert doc.read_text(encoding="utf-8") == gen.render_doc(), (
        "文档与生成器输出漂移(多半是 WW_TOOL_TABLE/tooltable 变了):"
        "跑 python scripts/gen_agent_interface_doc.py 再生"
    )


def test_doc_covers_all_mcp_tools_and_counts():
    gen = _load_gen()
    body = gen.render_doc()
    import guanlan_v2.console.tools as ct
    import guanlan_v2.glmcp.tooltable as tt

    tools = tt.build_mcp_tools()
    for t in tools:
        assert f"`{t['name']}`" in body, f"文档缺工具 {t['name']}"
    # 计数全部动态派生,任何一处硬编都会在表变动时经同步测试翻红
    assert f"共 {len(tools)} 个" in body
    assert f"{len(ct.WW_TOOL_TABLE)}" in body
    assert f"{len(ct.ALL_CONSOLE_TOOL_TABLE)}" in body
    assert f"共 {len(ct.CHANNEL_TOOL_TABLE)} 个" in body
    for tool in ct.CHANNEL_TOOL_TABLE:
        assert f"`{tool['name']}`" in body
    # console-UI-only 排除名单也要显形(告诉外部客户端为什么 MCP 里没有)
    for name in tt._EXCLUDED:
        assert f"`{name}`" in body


def test_doc_covers_access_pitfalls_and_redlines():
    gen = _load_gen()
    body = gen.render_doc()
    assert "/gl-mcp/" in body and "307" in body        # 带斜杠握手坑
    assert "GUANLAN_MCP_WRITE" in body                 # 写门
    assert "python -m guanlan_v2.glmcp" in body        # stdio 传输
    assert "重启 9999" in body                          # 生效条件
    assert "诚实" in body and "注入防御" in body        # 全池红线
