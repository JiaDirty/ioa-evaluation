# MCP 集成说明

MCP 在本项目中用于连接外部工具，不用于 Agent 之间的通信。

## 配置文件

MCP 服务在 `config/mcp_servers.yaml` 中配置。每个服务包含：

- `server_id`：服务编号；
- `enabled`：是否启用；
- `transport`：传输方式；
- `endpoint` 或 `command`：接口地址或启动命令；
- `allowed_tools`：允许同步的工具；
- `risk_level`：风险等级；
- `timeout_seconds`：等待时间。

当前正式实现支持 HTTP JSON-RPC。配置中可以声明标准输入输出方式的服务，但运行程序暂未把它作为正式传输方式。

## 同步工具

相关接口：

- `GET /api/mcp/servers`；
- `POST /api/mcp/sync-tools`；
- `POST /api/mcp/servers/{server_id}/sync-tools`。

默认同步只处理已经启用的服务。

同步后的工具编号格式为：

```text
mcp:{server_id}:{tool_name}
```

示例：

```text
mcp:local-demo-mcp:demo_echo
```

## 工具允许名单

- `allowed_tools: []`：拒绝该服务提供的全部工具；
- `allowed_tools: ["*"]`：允许该服务公开的全部工具；
- 其他列表：只允许列表中名称完全匹配的工具。

## 安全边界

- MCP 只用于 Agent 调用工具；
- Agent 之间不能使用 MCP 传递任务；
- MCP 工具仍必须经过 ToolGateway 的参数和权限检查；
- 高风险工具需要 `high_risk_tool` 权限；
- 关键工具还需要 `critical_tool` 权限；
- 工具历史中的敏感参数会在保存时脱敏。

## 测试方式

自动测试使用固定的 JSON-RPC 假服务，覆盖 `tools/list` 和 `tools/call`。这样可以在不访问外部服务的情况下检查 MCP 集成边界。
