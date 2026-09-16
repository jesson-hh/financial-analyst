# docs — 观澜 V2 工程文档

| 文档 | 讲什么 |
|------|--------|
| [data_contract.md](data_contract.md) | 数据脐带——V2 如何只读引用 stock_data(`get_data_paths`)、实时 vs EOD 来源、铁律。 |
| [module_map.md](module_map.md) | 模块 × 页面 × 入口组件 × 后端端点 × 数据来源 全表 + 跨模块数据流 + 导航说明。 |
| [dev_guide.md](dev_guide.md) | 起服务、加页面、加后端、缓存陷阱、调试、中文文件名、验证纪律。 |
| [agent_data_interfaces.md](agent_data_interfaces.md) | agent 数据/操作接口总览(MCP/console 接入、工具总表、数据面导览、端点全集)。**生成式**:由 `scripts/gen_agent_interface_doc.py` 派生自 WW_TOOL_TABLE,禁止手改,守护测试防漂移。 |
| [runtime_credentials_and_test_isolation.md](runtime_credentials_and_test_isolation.md) | `var/secrets.env` 精确凭据白名单、只读状态探针、feature/write gate 边界与 pytest 输出隔离。 |
| [pit_production_preflight.md](pit_production_preflight.md) | 严格 PIT 的 HMAC/keyring、香港时区与 OS 校时部署前置检查。 |

总说明见上层 [../README.md](../README.md),架构见 [../ARCHITECTURE.md](../ARCHITECTURE.md)。
