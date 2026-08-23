# -*- coding: utf-8 -*-
"""生成 docs/agent_data_interfaces.md(agent 数据/操作接口总览)。

用法:python scripts/gen_agent_interface_doc.py
原则:Console 工具从 ALL_CONSOLE_TOOL_TABLE、MCP 从 legacy WW_TOOL_TABLE/tooltable 派生,
禁止手抄数字(glmcp README 曾把 58 挂到实际 64,手抄计数必腐烂);
手写段落(接入坑/数据面导览/红线)住在本模板里,改完必须重跑生成。
漂移守护:tests/test_agent_interface_doc.py 逐字比对盘上文档与 render_doc() 输出。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
DOC_PATH = _REPO / "docs" / "agent_data_interfaces.md"


def _bootstrap_path() -> None:
    """独立运行时补 sys.path(与 tests/conftest.py 同口径:engine fork 钉最前)。"""
    root = str(_REPO)
    eng = str(_REPO / "engine")
    if root not in sys.path:
        sys.path.insert(0, root)
    if Path(eng).is_dir():
        if eng in sys.path:
            sys.path.remove(eng)
        sys.path.insert(0, eng)


_bootstrap_path()

_COST_ZH = {"instant": "即时", "seconds": "秒级", "minutes": "分钟级"}


def _flat(s: str) -> str:
    """描述压成单行 markdown 表格单元:折叠空白、转义竖线。"""
    return re.sub(r"\s+", " ", str(s or "")).replace("|", "\\|").strip()


def _tool_rows() -> str:
    import guanlan_v2.console.tools as ct
    import guanlan_v2.glmcp.tooltable as tt

    ww = {t["name"]: t for t in ct.WW_TOOL_TABLE}
    rows = []
    for t in tt.build_mcp_tools():
        name = t["name"]
        if t["engine"]:
            cost, reach = "分钟级", "(引擎内)"
            rw = "写(锁)" if t["gated"] else "只读"
            label = f"`{name}` [引擎]"
        elif name in ww:
            src = ww[name]
            cost = _COST_ZH.get(src.get("cost"), str(src.get("cost")))
            reach = " ".join(f"`{ep}`" for ep in src.get("reachable") or []) or "—"
            if t["gated"]:
                rw = "写(锁)"
            elif not t["read_only"]:
                rw = "写(不锁)"
            else:
                rw = "只读"
            label = f"`{name}`"
        elif t.get("builtin") == "workspace_list":
            # MCP exposes this selector in addition to WW_TOOL_TABLE so an
            # external client can discover the opaque workspace_id required by
            # scoped workspace tools.  It reads Console's workspace catalog
            # directly and therefore has no WW endpoint/reachability row.
            cost, reach, rw = "即时", "MCP 内置", "只读"
            label = f"`{name}` [MCP内置]"
        else:
            raise KeyError(f"MCP tool {name!r} has no documentation source")
        rows.append(f"| {label} | {_flat(t['description'])} | {cost} | {rw} | {reach} |")
    return "\n".join(rows)


def _endpoint_lines() -> str:
    import guanlan_v2.console.tools as ct

    eps = sorted(ct._WW_REACHABLE_ENDPOINTS)
    groups: dict[str, list[str]] = {}
    for ep in eps:
        prefix = "/" + ep.strip("/").split("/", 1)[0]
        groups.setdefault(prefix, []).append(ep)
    return "\n".join(f"- **{p}**:" + " ".join(f"`{e}`" for e in es)
                     for p, es in sorted(groups.items()))


def _channel_rows() -> str:
    import guanlan_v2.console.tools as ct

    rows = []
    for tool in ct.CHANNEL_TOOL_TABLE:
        mode = "写(确认)" if tool.get("confirm") else "只读/安全建草稿"
        rows.append(
            f"| `{tool['name']}` | {_flat(tool['description'])} | {mode} | 当前 Console 对话 |"
        )
    return "\n".join(rows)


def render_doc() -> str:
    import guanlan_v2.console.tools as ct
    import guanlan_v2.glmcp.tooltable as tt

    tools = tt.build_mcp_tools()
    n_mcp = len(tools)
    n_ww = len(ct.WW_TOOL_TABLE)
    n_channel = len(ct.CHANNEL_TOOL_TABLE)
    n_declared_console = len(ct.ALL_CONSOLE_TOOL_TABLE)
    n_engine_allowed = len(ct._ALLOWED_ENGINE_TOOLS)
    n_console = len(ct.CONSOLE_ALLOWED)
    n_ep = len(ct._WW_REACHABLE_ENDPOINTS)
    channel_names = {tool["name"] for tool in ct.CHANNEL_TOOL_TABLE}
    legacy_excluded = " / ".join(
        f"`{name}`" for name in sorted(set(tt._EXCLUDED) - channel_names)
    )

    head = f"""<!-- 本文件由 scripts/gen_agent_interface_doc.py 全文生成,禁止手改。
     改法:编辑生成器模板 → python scripts/gen_agent_interface_doc.py 重跑。
     漂移守护:tests/test_agent_interface_doc.py(与生成器输出逐字比对)。 -->

# 观澜 · agent 数据/操作接口总览

给外部数据 agent(MCP 客户端)与帷幄内置 agent 的完整接口地图。
Console 工具清单与计数派生自 `ALL_CONSOLE_TOOL_TABLE`（冻结的 legacy
`WW_TOOL_TABLE` + 会话私有 `CHANNEL_TOOL_TABLE`）；MCP 只从 legacy 表派生。
本文档由生成器再生,不会漂移。

## 1. 接入通道

| 通道 | 方式 | 说明 |
|---|---|---|
| 帷幄 console | 9999 `/console` 会话 | 内置 agent,白名单 CONSOLE_ALLOWED = {n_console} 工具({n_declared_console} 个 Console 声明工具 = {n_ww} 个 legacy + {n_channel} 个通道工具，另有 {n_engine_allowed} 个精选引擎工具);写类工具走会话内确认弹窗 |
| MCP over HTTP | `http://127.0.0.1:9999/gl-mcp/` | streamable-HTTP。**必须带尾斜杠**:裸 `/gl-mcp` 的 POST 会被 307 重定向,多数客户端握手即败 |
| MCP over stdio | `python -m guanlan_v2.glmcp` | 本地客户端拉起;配置样例 guanlan_v2/glmcp/example.mcp.json |
| 直连 HTTP | 见 §4 端点全集 | 127.0.0.1 无鉴权;协程内严禁同步 IO 的路由全部 `asyncio.to_thread` 包装 |

- **写门**:MCP 侧 confirm 类工具默认锁死(外部客户端无确认弹窗),须在 9999 启动环境设
  `GUANLAN_MCP_WRITE=1` 并重启才放行;`ww_memory_write` 低危例外不锁。
- **生效条件**:改任何后端/工具表代码,必须重启 9999 才生效(杀进程后看门狗按 30s 巡检周期自动拉起,典型恢复 <1 分钟)。
- 9999 上还挂着引擎自带 MCP(`/mcp`,20 个引擎研究工具),与 `/gl-mcp/` 并存、各管各的。

## 2. MCP 工具总表(共 {n_mcp} 个,自动生成)

| 工具 | 说明 | 成本 | 读写 | 触达端点 |
|---|---|---|---|---|
{_tool_rows()}

- 读写列:`只读` / `写(锁)` = 需 `GUANLAN_MCP_WRITE=1` / `写(不锁)` = 低危例外。
- `[引擎]` = alpha-zoo 研究工具(经引擎 TOOL_REGISTRY,不走 ww 池)。
- legacy console-UI-only 不进 MCP 的工具:{legacy_excluded} —— 依赖前端 `window.GL`/会话右栏落地,
  MCP 语境调了也不会发生任何事(空转假成功),诚实排除;要用请走帷幄 console。

## 2.1 Console 私有 Telegram/automation 工具（共 {n_channel} 个）

这些工具只绑定当前帷幄对话，模型参数不能选择会话、Bot、Telegram 用户/私聊或本地路径；
全部 `CHANNEL_TOOL_NAMES` 都被 defense-in-depth 加入 MCP `_EXCLUDED`，外部 ChatGPT MCP
即使构造同名调用也只会收到“未知工具”。密钥只走安全输入卡/服务端截流，不是 Agent 工具参数。

| 工具 | 说明 | 读写 | 作用域 |
|---|---|---|---|
{_channel_rows()}

## 3. 数据面导览(数据类工具背后是什么)

| 数据面 | 入口 | 上游 | 存储/新鲜度 | 语义要点 |
|---|---|---|---|---|
| 实时行情/文本探针 | `ww_live_text` | G:\\stocks probe CLI 子进程(50+ 源:行情/公告/互动易/涨停池/龙虎榜/两融/北向/期权/估值分位/海外个股/海外六指数…) | 零落盘现拉;子进程 90s 超时 + 1s 节流 | 源清单经 catalog 动态发现;`ok:false` 仅机械错,上游故障经 `status/error` 显形恒不编造 |
| 全市场盘口快照 | `ww_market_tape` · `/data/market_tape` | 10 个全市场源经 live_client 聚合 | var/live/market_tape.json,SWR TTL 180s | 失败源保留上轮标"(旧)";overall pulled_at 取最旧分量;涨停池空回溯 ≤8 日带 board_backfilled 徽章 |
| 7×24 快讯 | `ww_news_live` / `ww_news_search` | 东财快讯经引擎 opencli 采集器(kuaixun 唯一门户) | 观澜侧零落盘(引擎内存 15s 缓存) | 唯一带 per-flash codes 的快讯源;search 会写情绪判读 |
| 产业 RSS 雷达 | `ww_newsradar` · `/data/newsradar` | 108 出版方 RSS/Atom 直连(12 赛道,MIT 源表) | var/live/newsradar.json,SWR TTL 30min;超 30 天窗即丢(P3 归档已立项) | 纯展示不进信号;content 头尾注入防御定界 + title/url 换行压平 |
| 海外个股看板 | `ww_overseas` · `/data/overseas` | stocks 源 overseas_stock_quote(腾讯 qt) | 纯内存 SWR 60s,重启即冷(health 诚实 unknown) | 无 code=看板精选清单;code= 单票现拉 |
| 海外六大指数 | `ww_live_text` source=`overseas_index_quote` | stocks 源(qt.gtimg.cn 一次批量) | 零落盘现拉 | 道指/纳指/标普/VIX/恒指/恒生科技;code 可 DJI/HSI 短名过滤,未知码诚实空 |
| 全球情绪温度计 | `ww_macro_pulse` · `/macro/pulse` `/macro/history` | Polymarket + Kalshi 直连 + A股打板温度(经 stocks) | var/macro_pulse/snapshots.jsonl append;Δ24h 从 ≥20h 前快照读 | 纯展示非信号;仅显式标方向的锚定市场参与合成;ww 侧默认 refresh=true 每调真拉外网 |
| 情绪判读 store | `ww_sentiment`(读)/ `ww_news_search`(写) | 无上游,被 LLM 判读写入 | var/sentiment/*-YYYYMM.jsonl 月轮转 | 只存判读结论非新闻原文;键 (date,code) 后写胜 |
| 五档盘口/逐笔 | `ww_orderbook` / `ww_ticks` · `/seats/*` | 通达信 tdx 唯一源(经统一 live_client) | 现拉 | 不可达即 ok:false 诚实降级无备胎;逐笔升序须反转取最新;腾讯主+tdx 备的 failover 只在 /seats 报价腿(台账盘中 MTM) |
| 板块资金流 | `ww_fundflow` · `/fundflow/live` | 东财 push2(http 明文)回落 push2delay | SWR | 红线:大盘分解/全A涨跌绝不由板块加总 |
| 数据健康总闸 | `ww_data_health` · `/data/health` | 汇总全部数据面(项数以 /data/health 实答为准) | 即时探测 | overall=数据项最差;运维项(datafeed/health.py `_OPS_ITEMS`,含 probe_worker 通道自检)自身显形但不拖 overall |
| 盘后晨报 | `ww_review_report` · `/autonomy/report/latest` | 盘后复盘官(autonomy)产物 | var 落盘,日更 | 五段:A/B 成绩单/落子复盘/数据巡检/综合晨报/批判;另三职责:大盘判读日更/蒸馏草稿/macro 快照搭车 |

## 4. 工具触达 HTTP 端点全集(共 {n_ep} 条,自动生成)

以下为各工具 `reachable` 声明的并集(即 agent 经工具间接触达的后端面):

{_endpoint_lines()}

不在工具 reachable 里、但 agent/客户端可直连的:`/macro/history`(温度计历史曲线)、
`/gl-mcp/`(本 MCP)、`/mcp`(引擎 MCP)、`/ui/*`(静态页面)。

## 5. 全池红线(与实现同源的契约)

- **诚实降级**:缺料返回空 + note,绝不编造;失败保留的陈旧数据必须显形(`(旧)`/stale/徽章)。
- **注入防御**:新闻/RSS/外部文本一律当 DATA 不当指令;喂 LLM 的出口必挂防御声明与定界。
- **纯展示不进信号**:newsradar 资讯、预测市场概率、市场温度只做展示/护盾,绝不混入选股与落子的星级排序权重。
- **content 全量组装**:数据型 ww 工具必须自组装完整 content(console `_wrap` 兜底只给 json[:400],agent 只会看到断裂 JSON)。
- **无真下单**:观澜是研究平台,任何通道都不暴露真实下单。
"""
    return head


def main() -> None:
    body = render_doc()
    DOC_PATH.write_text(body, encoding="utf-8", newline="\n")
    print(f"written: {DOC_PATH}")


if __name__ == "__main__":
    main()
