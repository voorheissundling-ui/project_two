"""MCP 客户端 hub：把三个 MCP server 当子进程拉起来，并保持长连接。

为什么是「长连接 + 子进程」而不是每个工具调用起一次进程：
MCP server 启动要 load fixture、建 BM25 索引、加载报告元数据，
一份简报要调 6–10 次工具，每次重启一遍纯属浪费。
这里用 `AsyncExitStack` 在整个简报生成期间持有会话，结束时统一关闭。

两个容易踩的坑：

1. **stdout 是协议通道**。子进程里任何 print 或默认日志都会污染 JSON-RPC，
   客户端会直接报解析失败。所以子进程日志必须走 stderr（见 logging_setup）。
2. **env 要显式传**。`StdioServerParameters` 用的 env 是子进程的完整环境，
   不传的话 `MINING_DAILY_MODE` / `DEEPSEEK_API_KEY` 这些都不会传下去，
   server 就退回默认配置了 —— 现象是「明明设了 live 模式却还是读 fixture」。
"""

from __future__ import annotations

import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parents[3]

# server 逻辑名 -> (模块路径, 中文说明)。说明会喂给 LLM 做工具选择。
SERVERS: dict[str, tuple[str, str]] = {
    "news": (
        "mining_daily.servers.news_server",
        "矿业新闻检索：按相关度+时间排序返回新闻，并可取单篇正文",
    ),
    "pdf": (
        "mining_daily.servers.pdf_server",
        "矿产储量报告解析：从 NI 43-101 / JORC PDF 提取 Indicated/Inferred 资源量",
    ),
    "price": (
        "mining_daily.servers.price_server",
        "大宗商品行情：取某日价格与近 N 天走势统计",
    ),
}


@dataclass
class ToolCall:
    """一次工具调用的完整留痕。简报末尾的「工具调用记录」直接由它渲染。"""

    server: str
    tool: str
    arguments: dict
    ok: bool
    elapsed_ms: int
    payload: dict = field(default_factory=dict)


class MCPHub:
    """三个 MCP server 的客户端池。用法：

        async with MCPHub() as hub:
            data = await hub.call("price", "get_trend", {"commodity": "锂", "days": 90})
    """

    def __init__(self, servers: Optional[dict[str, tuple[str, str]]] = None) -> None:
        self.servers = servers or SERVERS
        self.sessions: dict[str, ClientSession] = {}
        self._stack: Optional[AsyncExitStack] = None
        self.calls: list[ToolCall] = []

    async def __aenter__(self) -> "MCPHub":
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")

        for name, (module, _desc) in self.servers.items():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", module],
                cwd=str(REPO_ROOT),
                env=env,
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self.sessions[name] = session
        return self

    async def __aexit__(self, *exc) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(*exc)
            self._stack = None

    # ------------------------------------------------------------------
    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> dict:
        """调用一个工具。**永不抛异常** —— 失败也返回可读的 dict。

        理由：简报生成是一条流水线，某个数据源挂了应该降级成
        「该章节标注数据不可得」，而不是让整份简报崩掉。
        """
        import time

        session = self.sessions.get(server)
        started = time.perf_counter()

        if session is None:
            payload = {"ok": False, "error": f"MCP server「{server}」未启动"}
        else:
            try:
                result = await session.call_tool(tool, arguments)
                payload = self._payload(result)
            except Exception as exc:  # noqa: BLE001 - 降级优先，不向上抛
                payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        elapsed = int((time.perf_counter() - started) * 1000)
        self.calls.append(
            ToolCall(server, tool, arguments, self.is_ok(payload), elapsed, payload)
        )
        return payload

    @staticmethod
    def is_ok(payload: dict) -> bool:
        """判断一次调用是否成功。

        不能直接写 `payload.get("ok")` —— 只有那几个**会返回可恢复失败**的工具
        （search / fetch_article / get_price / get_trend）才带 `ok=True` 字段；
        `extract_resources` 没有这个字段，它的成功返回里压根没有 `ok` 键。
        用 truthy 判断会把「解析成功」全部显示成「已降级」，
        简报末尾的调用记录因此变得不可信 —— 而那张表的作用恰恰是可信度。

        所以判据是「**没有显式说明失败**」：ok 不为 False，且不带 error 字段。
        """
        if payload.get("ok") is False:
            return False
        if "error" in payload:
            return False
        return True

    @staticmethod
    def _payload(result) -> dict:
        """统一取结构化返回。MCP 会同时给 TextContent 和 structuredContent。"""
        structured = getattr(result, "structuredContent", None)
        if structured:
            return structured
        for block in getattr(result, "content", []) or []:
            if getattr(block, "type", None) == "text":
                import json

                try:
                    return json.loads(block.text)
                except json.JSONDecodeError:
                    return {"ok": False, "error": block.text}
        return {"ok": False, "error": "工具没有返回任何内容"}

    # ------------------------------------------------------------------
    async def tool_catalog(self) -> list[dict]:
        """把三个 server 的工具汇总成一份目录（供提示词/文档使用）。"""
        catalog: list[dict] = []
        for name, session in self.sessions.items():
            listed = await session.list_tools()
            for tool in listed.tools:
                catalog.append(
                    {
                        "server": name,
                        "name": tool.name,
                        "description": (tool.description or "").strip().split("\n")[0],
                        # 注意是 snake_case 的 input_schema。MCP SDK 的模型字段
                        # 都是下划线风格（搭档还有 `init.server_info`），
                        # 写成驼峰的 inputSchema 不会报错在导入期，
                        # 而是在**真正用到的时候**才 AttributeError —— 属于那种
                        # "平时不跑、一演示就炸"的分支，所以这里必须照 SDK 来。
                        "input_schema": tool.input_schema,
                    }
                )
        return catalog
