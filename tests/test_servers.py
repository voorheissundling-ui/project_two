"""三个 MCP server 的**工具函数**行为测试（in-process 直接调用）。

这里刻意不走 stdio —— 协议层有 `test_mcp_protocol.py` 专门管。
这个文件关心的是**业务逻辑**：排序对不对、别名认不认、错误给不给得出人话。
分成两层是因为它们坏起来的原因完全不同：
业务错了要改算法，协议错了要改序列化，混在一起测会互相掩盖。
"""

from __future__ import annotations

import pytest

from mining_daily.common.schemas import ToolFailure
from mining_daily.servers import news_server, price_server


class TestNewsSearch:
    def test_query_returns_ranked_items(self):
        result = news_server.search("Pilbara 锂 价格", days=200, limit=5)
        assert not isinstance(result, ToolFailure)
        assert result.count > 0
        scores = [i.relevance_score for i in result.items]
        assert scores == sorted(scores, reverse=True), "返回结果没有按相关度降序"
        assert all(i.url.startswith("http") for i in result.items)

    def test_blank_query_returns_structured_failure(self):
        """空 query 必须返回 ToolFailure，而不是抛异常。

        抛异常的话 MCP SDK 会把它屏蔽成一句 `Error executing tool search`，
        Agent 既不知道错在哪，也没机会改。所以可恢复的业务错误要建模成返回类型。
        """
        result = news_server.search("   ")
        assert isinstance(result, ToolFailure)
        assert result.ok is False
        assert result.hint, "错误里没有给出下一步动作"

    def test_non_positive_limit_returns_structured_failure(self):
        result = news_server.search("锂", limit=0)
        assert isinstance(result, ToolFailure)
        assert "limit" in result.error

    def test_corpus_now_uses_dataset_time_basis(self):
        """`days` 是相对**语料最新发布时间**的窗口，不是相对系统当前时间。

        语料最新一条是 2026-08-26，系统当前时间是 2026-10-14。
        如果按真实 now 过滤，`days=7` 会永远返回空，整个 demo 哑火。
        """
        narrow = news_server.search("锂", days=7, limit=50)
        wide = news_server.search("锂", days=3650, limit=50)
        assert not isinstance(narrow, ToolFailure)
        assert not isinstance(wide, ToolFailure)
        assert narrow.count > 0, "窄窗口返回空 —— 时间基准又变回系统当前时间了"
        assert narrow.count <= wide.count

    def test_zero_days_means_no_time_limit(self):
        result = news_server.search("锂", days=0, limit=50)
        assert not isinstance(result, ToolFailure)
        assert result.count > 0


class TestNewsArticle:
    def test_fetch_by_id(self):
        first = news_server.search("Pilbara", days=3650, limit=1)
        item = first.items[0]
        detail = news_server.fetch_article(item.id)
        assert not isinstance(detail, ToolFailure)
        assert detail.id == item.id
        assert detail.body, "正文为空"

    def test_fetch_by_url(self):
        first = news_server.search("Pilbara", days=3650, limit=1)
        detail = news_server.fetch_article(first.items[0].url)
        assert not isinstance(detail, ToolFailure)

    def test_unknown_url_lists_alternatives(self):
        """未知链接要把**可用条目列出来**，否则 Agent 只能瞎猜。

        这是 ToolFailure 里 `available` 字段存在的全部理由。
        """
        result = news_server.fetch_article("https://example.com/definitely-not-here")
        assert isinstance(result, ToolFailure)
        assert result.available, "没给出可选列表，Agent 无法自我纠正"
        assert any("news-" in a for a in result.available)


class TestPrice:
    @pytest.mark.parametrize(
        "alias",
        ["锂", "碳酸锂", "电碳", "lithium", "lithium_carbonate_cn"],
    )
    def test_aliases_resolve_to_same_series(self, alias):
        """口语叫法必须都指向同一条序列。

        「锂价」到底指碳酸锂还是锂辉石，靠字符串相似度猜不准 ——
        所以别名表是显式枚举的，这个用例锁的就是那张表。
        """
        result = price_server.get_price(alias)
        assert not isinstance(result, ToolFailure), f"别名 {alias} 没被识别"
        assert result.series_id == "lithium_carbonate_cn"

    def test_unknown_commodity_lists_available(self):
        """可选列表里要带**中文名和单位**，不能只给 series_id。

        因为这份列表是给 LLM 看、用来重试的。只给 `lithium_carbonate_cn`
        这种内部 id，模型还得再猜一次它对应什么品种，等于没帮上忙。
        """
        result = price_server.get_price("不存在的品种")
        assert isinstance(result, ToolFailure)
        assert result.available, "没列出可选品种"
        assert any(a.startswith("lithium_carbonate_cn") for a in result.available)
        assert any("碳酸锂" in a for a in result.available), "列表里没有中文名，模型没法据此重试"

    def test_bad_date_format_is_recoverable(self):
        result = price_server.get_price("锂", "2026/10/04")
        assert isinstance(result, ToolFailure), "日期格式错误应该是可恢复错误，不是崩溃"

    def test_non_trading_day_falls_back(self):
        """非交易日的请求要回退到最近一个交易日，并如实透出实际日期。"""
        result = price_server.get_price("铜", "2026-10-04")
        assert not isinstance(result, ToolFailure)
        assert result.date <= "2026-10-04"
        assert result.requested_date == "2026-10-04"

    def test_trend_stats(self):
        result = price_server.get_trend("锂", days=90)
        assert not isinstance(result, ToolFailure)
        assert result.days == 90
        assert len(result.points) > 1
        assert result.stats.min <= result.stats.last <= result.stats.max
        assert result.trend_label

    def test_non_positive_days_is_recoverable(self):
        result = price_server.get_trend("锂", days=0)
        assert isinstance(result, ToolFailure)
        assert result.hint

    def test_provenance_is_always_present(self):
        """溯源信息不是可选项 —— 每个返回值都必须能回答「这数据哪来的」。"""
        result = price_server.get_price("锂")
        assert not isinstance(result, ToolFailure)
        assert result.provenance.data_origin in ("fixture", "live", "fixture_fallback")
        assert result.provenance.source


class TestPdfServer:
    def test_fixture_alias_resolves(self):
        from mining_daily.servers import pdf_server

        result = pdf_server.extract_resources("fixture://pilgangoora-mre-2016-south")
        assert result.rows, "没解析出任何行"
        assert result.report_id == "pilgangoora-mre-2016-south"
        assert all(r.confidence >= pdf_server.ABSTAIN_CONFIDENCE for r in result.rows)

    def test_missing_pdf_abstains_instead_of_guessing(self):
        """找不到 PDF 时**必须弃权**，而不是拿报告标题去猜数字。

        这是整个项目最重要的一条产品判断：在矿业尽调场景里，
        一个错的储量数字比一个空的储量章节危害大得多。
        """
        from mining_daily.servers import pdf_server

        result = pdf_server.extract_resources("fixture://does-not-exist")
        assert result.rows == []
        assert result.extraction.abstain is True
        assert result.extraction.warnings
