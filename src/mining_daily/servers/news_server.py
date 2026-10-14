"""mining-news-mcp —— 矿业新闻检索与正文抓取的 MCP server。

暴露两个工具：
  search(query, days, limit, commodity)  —— 相关度 + 时间衰减排序的检索
  fetch_article(url)                     —— 按 URL 取单篇正文

一个必须讲清楚的设计点（`corpus_now`）：
离线语料的"今天"和真实世界的"今天"不是同一天。我们的新闻 fixture 最新一条是
2026-08-26，而系统真实日期是 2026-10-14。如果 `days=7` 按真实 now 过滤，结果恒为空，
整个 demo 直接哑火。

所以这里把 **"语料内的最新发布时间" 当作数据集的时间基准**（fixture 模式下），
并在 provenance.as_of 里如实透出，让 Agent 知道"这份简报到哪天为止"。
live 模式下 as_of 就是真实当前时间 —— 两种模式下语义一致：**"相对于数据最新时点的近 N 天"**。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from mcp.server.mcpserver import MCPServer

from mining_daily.common import fixtures
from mining_daily.common.bm25 import FieldedBM25, time_decay
from mining_daily.common.logging_setup import configure_logging
from mining_daily.common.schemas import (
    ArticleDetail,
    NewsItem,
    Provenance,
    SearchNewsResult,
    ToolFailure,
)
from mining_daily.config import settings

server = MCPServer(
    name="mining-news-mcp",
    version="0.1.0",
    instructions=(
        "矿业新闻聚合服务。search 用 BM25 + 时间衰减排序，专名（Pilbara / Pilgangoora / "
        "锂辉石）和中文都支持。返回的每一条都带 url 与 published_at，引用时请原样附上。"
    ),
)

# 字段权重：标题命中远比正文顺带提一句重要。
FIELD_WEIGHTS = {
    "title": 4.0,
    "summary_zh": 2.0,
    "topics": 2.0,
    "body": 1.0,
}

_index: Optional[FieldedBM25] = None


def _get_index() -> FieldedBM25:
    """索引懒加载：MCP server 启动时不读盘，第一次调用工具才建索引。"""
    global _index
    if _index is None:
        docs = []
        for item in fixtures.load_news():
            doc = dict(item)
            # 把结构化字段拼成可检索的文本，让 "锂价" 能命中 commodities=lithium 的条目
            ents = item.get("entities") or {}
            doc["_entities_text"] = " ".join(
                list(ents.get("companies", []))
                + list(ents.get("projects", []))
                + list(ents.get("tickers", []))
            )
            doc["topics"] = " ".join(item.get("topics", []))
            doc["body"] = item.get("body", "") + " " + doc["_entities_text"]
            docs.append(doc)
        _index = FieldedBM25(docs, FIELD_WEIGHTS)
    return _index


def _parse(iso: str) -> datetime:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def corpus_now() -> datetime:
    """数据集的时间基准。fixture 模式下 = 语料里最新一条的发布时间。"""
    if settings.mode != "fixture":
        return datetime.now(timezone.utc)
    stamps = [_parse(n["published_at"]) for n in fixtures.load_news()]
    return max(stamps) if stamps else datetime.now(timezone.utc)


@server.tool()
def search(
    query: str,
    days: int = 60,
    limit: int = 8,
    commodity: Optional[str] = None,
) -> SearchNewsResult | ToolFailure:
    """检索矿业新闻。

    用 BM25（标题权重 4 倍）+ 时间衰减排序；`days` 是相对**数据最新时点**的近 N 天窗口。

    Args:
        query: 自然语言查询，中英文皆可。例如 "Pilbara 锂价" / "offtake agreement"。
        days: 只返回最近 N 天发布的新闻。默认 60 天。传 0 表示不限时间。
        limit: 最多返回条数，默认 8。
        commodity: 可选品种过滤，如 lithium / copper / iron_ore。
    """
    if not query.strip():
        return ToolFailure(error="query 不能为空。", hint="例如 query='Pilbara 锂'。")
    if limit <= 0:
        return ToolFailure(error=f"limit 必须为正整数，收到 {limit}。", hint="例如 limit=8。")

    now = corpus_now()
    cutoff = now - timedelta(days=days) if days and days > 0 else None

    candidates = []
    for item in fixtures.load_news():
        published = _parse(item["published_at"])
        if cutoff and published < cutoff:
            continue
        if commodity and commodity.lower() not in [
            c.lower() for c in item.get("commodities", [])
        ]:
            continue
        candidates.append(item)

    scanned = len(candidates)
    scored: list[tuple[float, dict]] = []
    if candidates:
        index = _get_index()
        by_id = {d.raw["id"]: d for d in index._docs}
        now_iso = now.isoformat()
        for item in candidates:
            doc = by_id[item["id"]]
            base = index.score(query, doc)
            if base <= 0:
                continue
            scored.append((base * time_decay(item["published_at"], now_iso), item))

    scored.sort(key=lambda x: x[0], reverse=True)
    scored = scored[:limit]

    items = [
        NewsItem(
            id=item["id"],
            title=item["title"],
            url=item["url"],
            source=item["source"],
            published_at=item["published_at"],
            summary_zh=item["summary_zh"],
            commodities=item.get("commodities", []),
            regions=item.get("regions", []),
            topics=item.get("topics", []),
            sentiment=item.get("sentiment"),
            relevance_score=round(score, 4),
            source_verified=item.get("source_verified"),
        )
        for score, item in scored
    ]

    meta = fixtures.load_news_meta()
    return SearchNewsResult(
        query=query,
        days=days,
        scanned=scanned,
        count=len(items),
        items=items,
        provenance=Provenance(
            data_origin="fixture" if settings.mode == "fixture" else "live",
            source="mining.com / Australian Mining / MINING.COM 等 11 家媒体",
            source_url=None,
            as_of=now.date().isoformat(),
            note=(
                f"离线样本 {meta.get('count', '?')} 条；本数据集的'今天'取语料最新发布时间。"
                "标题/链接/时间为真实抓取，摘要为公开报道的中文改写。"
            ),
        ),
    )


@server.tool()
def fetch_article(url: str) -> ArticleDetail | ToolFailure:
    """按 URL 取回单篇新闻的完整正文与结构化实体。

    Args:
        url: 新闻链接。可以是 search 返回的真实 URL，也可以是新闻 id（如 news-2026-08-26-pls-fy26-profit）。
    """
    articles = fixtures.load_news()
    target = None
    for item in articles:
        if item["url"] == url or item["id"] == url:
            target = item
            break

    if target is None:
        # 退一步：用 URL 的最后一段做模糊匹配，容忍尾斜杠/查询串差异
        slug = url.rstrip("/").split("/")[-1].split("?")[0].lower()
        for item in articles:
            if slug and slug in item["url"].lower():
                target = item
                break

    if target is None:
        return ToolFailure(
            error=f"未收录该链接：{url}",
            hint=(
                f"当前离线语料共 {len(articles)} 条。请先用 search 拿到真实的 url 或新闻 id 再取正文，"
                "不要凭标题猜链接。"
            ),
            available=[f"{i['id']} | {i['title'][:48]}" for i in articles[:12]],
        )

    return ArticleDetail(
        id=target["id"],
        title=target["title"],
        url=target["url"],
        source=target["source"],
        published_at=target["published_at"],
        body=target["body"],
        entities=target.get("entities", {}),
        commodities=target.get("commodities", []),
        regions=target.get("regions", []),
        provenance=Provenance(
            data_origin="fixture" if settings.mode == "fixture" else "live",
            source=target["source"],
            source_url=target["url"],
            as_of=target["published_at"][:10],
            note=(
                f"链接可访问性：{target.get('source_verified')}"
                "（http_200=实测 200；search_only=来源真实但被反爬拦截未实测）"
            ),
        ),
    )


def main() -> None:
    configure_logging()  # 日志只走 stderr，不污染 stdio 上的 JSON-RPC
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
