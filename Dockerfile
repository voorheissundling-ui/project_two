# 单镜像跑通全部：3 个 MCP server + Agent client。
#
# 为什么不做成 4 个容器：Agent 是用 **stdio** 把 server 当子进程拉起来的
# （见 src/mining_daily/agent/mcp_hub.py），stdio 本来就跨不了容器边界。
# 真要拆成多容器，得把 transport 换成 streamable-http、再引入服务发现,
# 那是为了部署形态而牺牲架构清晰度——这份作业里不值得。
# 所以：**一个镜像，Agent 和 server 同进程树，这就是 stdio 的正确形态**。
FROM python:3.12-slim

# uv：装依赖比 pip 快一个数量级，且能严格按 uv.lock 复现
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    MINING_DAILY_MODE=fixture

WORKDIR /app

# 先只拷依赖清单再装依赖：这样改代码不会让依赖层缓存失效
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 再拷源码与数据
COPY src/ ./src/
COPY data/ ./data/
COPY scripts/ ./scripts/
COPY mcp-config.json RUN.md ./

# 让 `python -m mining_daily.*` 和 `mining-daily` 都能直接用
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src"

# 默认跑出那份样板简报；想换问题就覆盖 command
#   docker run --rm mining-daily mining-daily "给我生成一份关于 Pilbara 锂矿的今日简报"
ENTRYPOINT ["mining-daily"]
CMD ["给我生成一份关于 Pilbara 锂矿的今日简报"]
