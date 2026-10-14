"""协议层测试：**真的起子进程、真的走 stdio JSON-RPC**。

为什么不复用 `test_servers.py` 的直接函数调用：
本项目的核心交付物之一是「这三个 server 能被 Claude Desktop / Cursor 直接接进去」。
那句话只有在**真的过一遍 stdio + JSON-RPC** 之后才成立 ——
直接 import 调函数会把整层协议漏掉：工具 schema 能不能序列化、
Pydantic 返回值能不能过 `structuredContent`、子进程的 stdout 有没有被日志污染，
这些问题全都只在协议层才会暴露。

（这个坑我们真踩过：子进程里一句 print 或默认的 logging 就会污染 JSON-RPC 通道，
客户端直接报解析失败。所以 `logging_setup` 强制日志走 stderr。）
"""

from __future__ import annotations

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from mining_daily.agent.mcp_hub import SERVERS

pytestmark = pytest.mark.asyncio

# 子进程日志压到 CRITICAL：错误路径是**故意**要测的，
# 不压掉的话 server 会为每个预期内的错误打一份 traceback，自检输出没法看。
ENV = {"MINING_DAILY_LOG_LEVEL": "CRITICAL", "PYTHONIOENCODING": "utf-8"}


class _Conn:
    """把「起 server → 握手 → 调用 → 关掉」包成一个同步上下文，省掉每个用例的样板。"""

    def __init__(self, module: str):
        self.module = module

    def __enter__(self):
        import asyncio

        self._loop = asyncio.new_event_loop()
        return self

    def __exit__(self, *exc):
        self._loop.close()


async def _with_server(module: str):
    """异步上下文：yield 一个已握手的 ClientSession。"""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm():
        params = StdioServerParameters(
            command=__import__("sys").executable,
            args=["-m", module],
            env=ENV,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    return _cm()


def _payload(result) -> dict:
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    import json

    for block in getattr(result, "content", []) or []:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                return {"_raw": block.text}
    return {}


# 每个 server 一个用例：握手 + 列工具 + 调一个成功路径 + 调一个**失败路径**
@pytest.mark.parametrize(
    "server,tool,args,failure_tool,failure_args",
    [
        (
            "news",
            "search",
            {"query": "Pilbara 锂", "days": 3650, "limit": 3},
            "fetch_article",
            {"url": "https://example.com/not-in-corpus"},
        ),
        (
            "price",
            "get_price",
            {"commodity": "碳酸锂"},
            "get_price",
            {"commodity": "不存在的品种"},
        ),
        (
            "pdf",
            "extract_resources",
            {"pdf_url": "fixture://pilgangoora-mre-2016-south"},
            "extract_resources",
            {"pdf_url": "fixture://does-not-exist"},
        ),
    ],
)
async def test_server_roundtrip(server, tool, args, failure_tool, failure_args):
    from contextlib import asynccontextmanager

    params = StdioServerParameters(
        command=__import__("sys").executable,
        args=["-m", SERVERS[server][0]],
        env=ENV,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            # serverInfo 是 snake_case —— 踩过：写成 init.serverInfo 会 AttributeError
            assert init.server_info.name
            assert init.server_info.version

            listed = await session.list_tools()
            names = {t.name for t in listed.tools}
            assert tool in names, f"{server} 没暴露工具 {tool}，只有 {names}"
            # 有 description 才会被渲染进 outputSchema 文档给模型看
            assert all((t.description or "").strip() for t in listed.tools)

            ok = await session.call_tool(tool, args)
            assert not getattr(ok, "isError", False), f"{tool} 正常路径报错了"
            payload = _payload(ok)
            assert payload.get("provenance", {}).get("data_origin") == "fixture"

            # ---- 错误路径 ----
            # 失败必须以**正常返回的结构化失败体**回来，而不是让 SDK 抛出
            # `Error executing tool xxx`：后者会把 error/hint/available 全部吞掉，
            # Agent 既读不到原因，也没机会换个参数重试。
            #
            # 这里只能断言"没走异常路径"，不能断言 `ok is False` ——
            # 因为不同工具的失败表达方式**本来就不一样**：
            #   * 新闻/行情：可恢复错误 -> 返回 ToolFailure（ok=False）
            #   * 储量解析  ：找不到 PDF 不是"你参数错了"，而是"这份文件我读不了"，
            #                 所以返回 ok 缺省的正常体 + extraction.abstain=True
            # 非要统一成一种，只会让其中一边的表达力被削掉。
            bad = await session.call_tool(failure_tool, failure_args)
            bad_payload = _payload(bad)
            assert not getattr(bad, "isError", False), (
                "可恢复错误走了异常路径 —— SDK 会把它屏蔽成一句 "
                "'Error executing tool ...'，hint 和 available 全丢"
            )
            assert bad_payload, "错误返回体是空的"
            assert "Error executing tool" not in str(bad_payload), bad_payload


async def test_recoverable_errors_carry_hint_and_available():
    """可恢复错误必须带 `hint` 和 `available` —— 这是它值得存在的全部理由。"""
    params = StdioServerParameters(
        command=__import__("sys").executable,
        args=["-m", SERVERS["news"][0]],
        env=ENV,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "fetch_article", {"url": "https://example.com/nope"}
            )
            payload = _payload(result)
            assert payload["ok"] is False
            assert payload["error"], "没说错在哪"
            assert payload["hint"], "没给下一步动作"
            assert payload["available"], "没给可选列表"


async def test_pdf_abstains_on_missing_file_over_the_wire():
    """找不到 PDF 时弃权 —— 这个行为必须原样过协议，不能被序列化吃掉。"""
    params = StdioServerParameters(
        command=__import__("sys").executable,
        args=["-m", SERVERS["pdf"][0]],
        env=ENV,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "extract_resources", {"pdf_url": "fixture://does-not-exist"}
            )
            payload = _payload(result)
            assert payload["rows"] == []
            assert payload["extraction"]["abstain"] is True
            assert payload["extraction"]["warnings"]


async def test_tool_schema_is_serializable():
    """所有工具的 input_schema 必须是合法 JSON Schema。

    这一条看着废话，但它防的是「用了一个 SDK 认不出的类型标注」——
    那种情况下 server 能起来、能用，但宿主客户端拿不到参数说明，
    模型就不知道该怎么调。

    另外它顺手锁死了 SDK 的**字段命名风格**：明明是 `input_schema`
    而随手写成驼峰的 `inputSchema` 时，Pydantic 会在**真正访问的那一刻**
    才抛 AttributeError —— 属于"平时不跑、一演示就炸"的那类分支。
    """
    params = StdioServerParameters(
        command=__import__("sys").executable,
        args=["-m", SERVERS["price"][0]],
        env=ENV,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            for tool in listed.tools:
                schema = tool.input_schema
                assert isinstance(schema, dict)
                assert schema.get("type") == "object"
                assert "properties" in schema
