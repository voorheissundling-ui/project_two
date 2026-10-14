"""统一日志配置。

MCP 走 stdio 时，**stdout 是协议通道**，任何 print 或默认日志写进 stdout
都会污染 JSON-RPC 流，客户端会直接报解析失败。所以这里只往 stderr 写。

另外把 mcp SDK 自己的 logger 默认压到 WARNING：
工具里抛业务异常（比如「未收录品种」）是**预期路径**而不是 bug，
SDK 会为此打一份富文本 traceback，刷屏且误导。
排查时用 `MINING_DAILY_LOG_LEVEL=DEBUG` 打开即可。
"""

from __future__ import annotations

import logging
import os
import sys


def configure_logging(name: str = "mining_daily") -> logging.Logger:
    level = os.getenv("MINING_DAILY_LOG_LEVEL", "WARNING").upper()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%H:%M:%S")
    )

    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.setLevel(level)

    # 让 SDK 的 logger 跟随同一档位，避免它自己往 stderr 打整段 traceback
    for sdk_logger in ("mcp", "mcp.server", "mcp.server.mcpserver", "httpx", "openai"):
        logging.getLogger(sdk_logger).setLevel(max(logging.WARNING, logging.getLevelName(level)))

    return logger
