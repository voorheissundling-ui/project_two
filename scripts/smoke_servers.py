"""三个 MCP server 的联调自检：**走真实 stdio 协议**逐个调用每个工具。

为什么不用 in-process 直接 import 调函数：
本项目的卖点之一是「能被 Claude Desktop / Cursor 直接接进去」，
那走的就是 stdio + JSON-RPC。只有真的用 ClientSession 连一次，
才能证明 tool schema 能序列化、返回值能过协议——直接调函数会漏掉这一层。

用法：
    uv run python scripts/smoke_servers.py
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO = Path(__file__).resolve().parents[1]


def _payload(result) -> dict:
    """取结构化返回。MCP 2.x 会同时给 TextContent 和 structuredContent。"""
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    for block in result.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                return {"_text": block.text}
    return {}


async def _call(session: ClientSession, name: str, args: dict) -> dict:
    result = await session.call_tool(name, args)
    if getattr(result, "isError", False):
        return {"_error": _payload(result).get("_text", "tool error")}
    return _payload(result)


async def run_server(module: str, calls: list[tuple[str, dict]]) -> None:
    # 把子进程的日志压到 CRITICAL：server 内部对「工具抛异常」会打一份富文本
    # traceback，那正是我们**故意**要测的路径（见 news/price 的错误分支），
    # 不压掉的话自检输出会被淹没。
    env = {"MINING_DAILY_LOG_LEVEL": "CRITICAL", "PYTHONIOENCODING": "utf-8"}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        cwd=str(REPO),
        env=env,
    )
    print("=" * 96)
    print(f"server: {module}")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"  serverInfo: {init.server_info.name} v{init.server_info.version}")
            tools = await session.list_tools()
            print(f"  tools     : {[t.name for t in tools.tools]}")
            for name, args in calls:
                payload = await _call(session, name, args)
                print(f"  --> {name}({json.dumps(args, ensure_ascii=False)})")
                print(f"      {json.dumps(payload, ensure_ascii=False)[:900]}")


async def main() -> None:
    await run_server(
        "mining_daily.servers.news_server",
        [
            ("search", {"query": "Pilbara 锂 价格", "days": 120, "limit": 3}),
            ("search", {"query": "锂", "days": 7, "limit": 3}),
            # 下面两条走的是「可恢复错误」分支：必须返回结构化失败体，
            # 而不是让 SDK 抛出 `Error executing tool ...`（那样 Agent 读不到原因）
            ("fetch_article", {"url": "https://example.com/not-in-corpus"}),
            ("search", {"query": "   "}),
        ],
    )
    await run_server(
        "mining_daily.servers.price_server",
        [
            ("get_price", {"commodity": "碳酸锂"}),
            ("get_trend", {"commodity": "碳酸锂", "days": 90}),
            ("get_price", {"commodity": "铜", "date_str": "2026-10-04"}),
            ("get_price", {"commodity": "不存在的品种"}),
            ("get_trend", {"commodity": "锂", "days": 0}),
        ],
    )
    await run_server(
        "mining_daily.servers.pdf_server",
        [
            ("extract_resources", {"pdf_url": "fixture://pilgangoora-mre-2016-south"}),
            ("extract_resources", {"pdf_url": "fixture://barrick-mineral-resources-2024"}),
            ("extract_resources", {"pdf_url": "fixture://does-not-exist"}),
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())
