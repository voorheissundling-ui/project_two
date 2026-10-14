"""`mining-daily` 命令行入口。

    uv run mining-daily "给我生成一份关于 Pilbara 锂矿的今日简报"

默认把 Markdown 打到 stdout（方便 `> report.md` 重定向），
过程日志走 stderr —— **stdout 只放最终产物**，这样管道里拿到的永远是干净的简报。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mining_daily.agent.graph import build_graph
from mining_daily.agent.mcp_hub import MCPHub
from mining_daily.config import settings


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mining-daily",
        description="矿权日报 Agent —— 通过三个 MCP server 聚合新闻、储量与行情，生成 Markdown 简报。",
    )
    parser.add_argument(
        "query",
        nargs="?",
        default="给我生成一份关于 Pilbara 锂矿的今日简报",
        help="自然语言需求。例：'给我生成一份关于 Pilbara 锂矿的今日简报'",
    )
    parser.add_argument("-o", "--out", help="输出 Markdown 到指定文件（默认打到 stdout）")
    parser.add_argument("--json", dest="json_out", help="同时把完整状态（含证据）存成 JSON")
    parser.add_argument("--trace", action="store_true", help="把节点执行轨迹打到 stderr")
    return parser.parse_args(argv)


async def run(query: str, *, show_trace: bool = False) -> dict:
    """跑一次完整流水线，返回最终 state。"""
    async with MCPHub() as hub:
        app = build_graph(hub)
        state = await app.ainvoke({"query": query})
        if show_trace:
            print("\n[节点轨迹]", file=sys.stderr)
            for line in state.get("trace", []):
                print(f"  · {line}", file=sys.stderr)
            print("\n[工具调用]", file=sys.stderr)
            for call in hub.calls:
                flag = "ok " if call.ok else "ERR"
                print(
                    f"  {flag} {call.server}.{call.tool}({json.dumps(call.arguments, ensure_ascii=False)})"
                    f" {call.elapsed_ms}ms",
                    file=sys.stderr,
                )
    return state


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        state = asyncio.run(run(args.query, show_trace=args.trace))
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130

    markdown = state.get("markdown") or "（未生成内容）"

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        print(f"简报已写入 {path}", file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK，中文会炸
        print(markdown)

    if args.json_out:
        # state 里的 trace/warnings 都是普通 list，可以直接序列化
        Path(args.json_out).write_text(
            json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(f"完整状态已写入 {args.json_out}", file=sys.stderr)

    if not settings.llm_provider == "offline" and not (state.get("focus") or {}).get("planner", "").startswith("deepseek"):
        print(
            "提示：未检测到可用的 DeepSeek 凭据，本次使用本地规划 + 模板草稿。\n"
            "      数字口径与 LLM 版一致，只是行文更机械。配置 MINING_DAILY_DEEPSEEK_API_KEY 即可启用。",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
