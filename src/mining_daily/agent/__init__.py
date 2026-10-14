"""矿权日报 Agent —— 用 LangGraph 编排三个 MCP server 产出 Markdown 简报。

模块分工：

  * `mcp_hub`  —— MCP 客户端池（三个 server 以 stdio 子进程常驻）
  * `llm`      —— DeepSeek 封装 + 无 key 时的确定性兜底
  * `graph`    —— LangGraph 状态机：plan → 并行取数 → 风险 → 起草 → 校验 → 渲染
  * `render`   —— 最终 Markdown 装配（含引用源与调用留痕）
  * `cli`      —— `mining-daily` 命令行入口
"""

from mining_daily.agent.graph import BriefState, build_graph
from mining_daily.agent.llm import ChatLLM, LLMUnavailable
from mining_daily.agent.mcp_hub import MCPHub, ToolCall

__all__ = ["BriefState", "build_graph", "ChatLLM", "LLMUnavailable", "MCPHub", "ToolCall"]
