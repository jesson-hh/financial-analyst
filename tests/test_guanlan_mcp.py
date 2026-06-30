import asyncio


def test_build_mcp_tools_derivation():
    from guanlan_v2.mcp.tooltable import build_mcp_tools
    import guanlan_v2.console.tools as ct
    tools = build_mcp_tools()
    names = {t["name"] for t in tools}
    ww = {t["name"] for t in ct.WW_TOOL_TABLE}
    assert "ww_plan_update" not in names and "ww_show_page" not in names      # 去除 console-UI-only
    assert (ww - {"ww_plan_update", "ww_show_page"}) <= names                  # 其余 ww_ 全在
    assert {"alpha_list", "alpha_compare", "alpha_forge", "factor_report"} <= names
    assert len(tools) == 35                                                    # 28 ww_ + 7 alpha-zoo


def test_build_mcp_tools_annotations_and_gate():
    from guanlan_v2.mcp.tooltable import build_mcp_tools
    by = {t["name"]: t for t in build_mcp_tools()}
    assert by["ww_model_delete"]["destructive"] and by["ww_model_delete"]["gated"]      # confirm=True
    assert by["ww_model_set_default"]["gated"]
    assert by["ww_screen_factors"]["read_only"] and not by["ww_screen_factors"]["gated"]  # 只读
    assert (not by["ww_memory_write"]["read_only"]) and (not by["ww_memory_write"]["gated"])  # 写但不锁
    assert by["alpha_forge"]["destructive"] and by["alpha_forge"]["gated"]              # 唯一 alpha 写
    assert by["alpha_compare"]["read_only"] and not by["alpha_compare"]["gated"]        # 贵但只读=不锁
    assert by["alpha_list"]["read_only"] and not by["alpha_list"]["gated"]
