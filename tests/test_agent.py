"""Agent 层的测试：规划、数字校验、风险覆盖、模板渲染。

重点是那几个**纯函数**——它们是整条流水线里唯一能被确定性验证的部分，
而且恰恰是 bug 最爱藏的地方（`_canon_number` 的一个小数点，
能让校验器把每一个正确数字都判成幻觉）。
"""

from __future__ import annotations

import pytest

from mining_daily.agent import graph
from mining_daily.agent.render import _risk_covered, _squash


class TestPlanFocus:
    def test_pilbara_query_picks_lithium_assets(self):
        """面试样板查询必须命中两份 Pilbara 报告 + 两条锂行情序列。"""
        focus = graph.plan_focus_local("给我生成一份关于 Pilbara 锂矿的今日简报")
        assert set(focus["report_ids"]) == {
            "pilgangoora-mre-2015-historical",
            "pilgangoora-mre-2016-south",
        }
        # 锂产业链上下游要一起看：只取碳酸锂会漏掉成本端的锂辉石
        assert set(focus["price_series"]) == {"lithium_carbonate_cn", "spodumene_sc6_cif_cn"}
        assert focus["planner"] == "local"

    def test_gold_query_picks_gold_reports(self):
        focus = graph.plan_focus_local("给我一份黄金简报")
        assert "barrick-mineral-resources-2024" in focus["report_ids"]
        assert "newmont-gold-resources-2025" in focus["report_ids"]

    def test_chinese_commodity_expands_to_english(self):
        """中文品种名必须展开成英文词元。

        fixture 里的 `commodity` 字段是英文（lithium / gold），
        不展开的话中文查询一个报告都匹配不上。
        """
        tokens = graph._expand_tokens("锂矿")
        assert "lithium" in tokens
        assert "spodumene" in tokens, "产业链联动词元没被带进来"

    def test_unknown_query_degrades_gracefully(self):
        """查一个不存在的矿种不该崩，只是什么都匹配不上。"""
        focus = graph.plan_focus_local("钯金和铑金")
        assert isinstance(focus["report_ids"], list)
        assert focus["days"] > 0


class TestNumberCanonicalization:
    """`_canon_number` —— 全项目最容易写错、写错就完全失效的一个函数。

    证据是 JSON（`233.0` / `145650.0`），草稿是人话（`233` / `145,650`）。
    如果归一化只做 `rstrip("0")`，`"233.0"` 会变成 `"233."`（留着小数点），
    于是 `233` 匹配不上 `233.0` —— 校验器会把**每一个照抄证据的数字**都判成幻觉，
    然后驱动模型去改一个本来正确的数字。越改越糟。
    """

    @pytest.mark.parametrize(
        "a,b",
        [
            ("233.0", "233"),
            ("145650.0", "145650"),
            ("145650.0", "145,650"),
            ("1.05", "1.05"),
            ("4800.0", "4800"),
            ("3967.8", "3,967.8"),
        ],
    )
    def test_json_and_human_forms_overlap(self, a, b):
        assert graph._canon_number(a) & graph._canon_number(b), f"{a} 与 {b} 被当成两个数"

    def test_trailing_dot_is_not_left_behind(self):
        assert "233." not in graph._canon_number("233.0")

    def test_distinct_numbers_do_not_collide(self):
        """不能松到把不同的数判成同一个 —— 那校验器就白装了。"""
        assert not (graph._canon_number("12345") & graph._canon_number("54321"))

    def test_verify_flags_fabricated_number(self):
        """编排层端到端：草稿里编一个证据里没有的数字，必须被抓出来。"""
        import asyncio

        state = {
            "news": [],
            "resources": [],
            "risks": [],
            "prices": {
                "lithium_carbonate_cn": {
                    "spot": {
                        "date": "2026-10-13",
                        "value": 111650.0,
                        "display_name_zh": "电池级碳酸锂",
                        "unit": "CNY/t",
                        "provenance": {"source_url": "https://smm.cn/", "data_origin": "fixture"},
                    },
                    "trend": {
                        "display_name_zh": "电池级碳酸锂",
                        "unit": "CNY/t",
                        "days": 90,
                        "trend_label": "下行",
                        "stats": {
                            "first": 144650.0,
                            "last": 111650.0,
                            "min": 109050.0,
                            "max": 145650.0,
                            "change_pct": -22.81,
                            "high_date": "2026-07-20",
                            "low_date": "2026-10-12",
                            "volatility_pct": 1.2,
                        },
                        "provenance": {"source_url": "https://smm.cn/", "data_origin": "fixture"},
                    },
                }
            },
        }
        # 111650 有出处；987654 是编的
        state["draft"] = "碳酸锂报 111,650 CNY/t，同比下跌 987,654 个百分点。"
        out = asyncio.run(graph.node_verify(state))
        assert out["verify_feedback"], "编造的数字没被抓出来"

        # 反馈里回显的是草稿里的**原样 token**（带千分位逗号），所以比较前先归一
        flat = [f.replace(",", "") for f in out["verify_feedback"]]
        assert any("987654" in f for f in flat), out["verify_feedback"]
        assert not any("111650" in f for f in flat), (
            f"把有出处的数字也判成了幻觉：{out['verify_feedback']}"
        )


class TestRiskCoverage:
    """风险覆盖判定 —— 它决定「补充风险提示」里会不会出现重复条目。"""

    def test_exact_title_match(self):
        risk = {"title": "部分引用链接未经实测", "detail": "..."}
        assert _risk_covered(risk, _squash("低：部分引用链接未经实测。原文…"))

    def test_company_name_abbreviated_by_model(self):
        """规则写全称、模型写缩写，仍要判定为「已覆盖」。

        否则同一条风险会在正文和附录里各出现一次，读起来像两条不同的风险。
        """
        risk = {
            "title": "Pilbara Minerals Limited 储量数据时效性",
            "detail": "采用的资源量生效日为 2015-06-30（JORC 2012）…",
        }
        draft = _squash("中：PLS 储量数据时效性。该报告标注为历史估算，不宜直接对比 [S9]。")
        assert _risk_covered(risk, draft)

    def test_whitespace_difference_ignored(self):
        risk = {"title": "碳酸锂价格显著下行", "detail": "近 90 天跌幅 22.8%"}
        assert _risk_covered(risk, _squash("碳酸锂 价格显著下行，跌幅 22.8%"))

    def test_genuinely_missing_risk_is_detected(self):
        """真被漏掉的风险必须还能被认出来 —— 否则这个函数就是恒真的摆设。"""
        risk = {"title": "Pilbara Minerals Limited 储量数据时效性", "detail": "2015-06-30"}
        draft = _squash("高：碳酸锂价格下行 22.8%。低：链接未经实测。")
        assert not _risk_covered(risk, draft)


class TestTemplateDraft:
    """模板草稿 —— 「没配 API key 也能出简报」这条承诺的实现。"""

    def _state(self):
        return {
            "query": "Pilbara 锂矿",
            "news": [
                {
                    "id": "n1",
                    "title": "PLS 复产",
                    "url": "https://example.com/n1",
                    "published_at": "2026-08-26T00:00:00Z",
                    "summary_zh": "摘要",
                }
            ],
            "resources": [
                {
                    "report_id": "r1",
                    "company": "Pilbara Minerals",
                    "project": "Pilgangoora",
                    "category": "Indicated",
                    "commodity": "lithium",
                    "ore_tonnage": {"normalized_value": 26.7, "normalized_unit": "Mt"},
                    "grade": {"value": 1.05, "unit": "% Li2O"},
                    "contained_metal": {"normalized_value": 280000.0, "normalized_unit": "t"},
                    "effective_date": "2016-02-29",
                    "reporting_standard": "JORC 2012",
                    "source_url": "https://example.com/r1",
                }
            ],
            "prices": {
                "lithium_carbonate_cn": {
                    "spot": {
                        "date": "2026-10-13",
                        "value": 111650.0,
                        "provenance": {"source_url": "https://smm.cn/", "data_origin": "fixture"},
                    },
                    "trend": {
                        "display_name_zh": "电池级碳酸锂",
                        "unit": "CNY/t",
                        "days": 90,
                        "trend_label": "下行",
                        "stats": {
                            "change_pct": -22.81,
                            "min": 109050.0,
                            "max": 145650.0,
                        },
                        "provenance": {"source_url": "https://smm.cn/", "data_origin": "fixture"},
                    },
                }
            },
            "risks": [
                {"level": "high", "title": "价格显著下行", "detail": "跌幅 22.8%", "evidence": "u"}
            ],
        }

    def test_has_all_five_required_sections(self):
        """#2 要求的五个交付要素一个都不能少。"""
        md = graph.render_template_draft(self._state())
        for section in ["一、本周要闻", "二、资源量与储量", "三、价格走势", "四、风险提示"]:
            assert section in md, f"模板草稿缺少章节：{section}"
        assert "26.70 Mt" in md and "1.05 % Li2O" in md and "280,000.00 t" in md
        assert "111,650" in md
        assert "价格显著下行" in md

    def test_empty_evidence_still_produces_all_sections(self):
        """数据全空也要出完整结构 —— 让读者看到「这节没有数据」而不是「这节不见了」。"""
        md = graph.render_template_draft({"query": "空", "news": [], "resources": [],
                                          "prices": {}, "risks": []})
        for section in ["一、本周要闻", "二、资源量与储量", "三、价格走势", "四、风险提示"]:
            assert section in md
        assert "本期未" in md


class TestRiskRules:
    def test_price_drop_rule_fires_with_number(self):
        state = {
            "prices": {
                "s": {
                    "trend": {
                        "display_name_zh": "碳酸锂",
                        "unit": "CNY/t",
                        "days": 90,
                        "stats": {
                            "change_pct": -22.8,
                            "first": 144650.0,
                            "last": 111650.0,
                            "min": 109050.0,
                            "max": 145650.0,
                            "low_date": "2026-10-12",
                            "high_date": "2026-07-20",
                            "volatility_pct": 1.2,
                        },
                        "provenance": {"source_url": "https://smm.cn/"},
                    }
                }
            }
        }
        risks = graph.assess_risks(state)
        drop = [r for r in risks if "下行" in r["title"]]
        assert drop, "跌幅 22.8% 竟然没触发下行风险"
        # 每条风险都必须挂得住一个具体数字 —— 这是「不让模型自由发挥风险」的落点
        assert "22.8" in drop[0]["detail"]
        assert drop[0]["evidence"] == "https://smm.cn/"

    def test_stale_report_rule_fires(self):
        risks = graph.assess_risks(
            {
                "resources": [
                    {
                        "company": "Pilbara Minerals",
                        "effective_date": "2015-06-30",
                        "reporting_standard": "JORC 2012",
                        "source_url": "u",
                    }
                ]
            }
        )
        assert any("时效性" in r["title"] for r in risks)

    def test_duplicate_titles_collapsed(self):
        state = {
            "news": [
                {"title": "T", "summary_zh": "", "sentiment": "negative", "url": "u1",
                 "source_verified": "http_200", "published_at": "2026-08-01"},
                {"title": "T", "summary_zh": "", "sentiment": "negative", "url": "u2",
                 "source_verified": "http_200", "published_at": "2026-08-02"},
            ]
        }
        risks = graph.assess_risks(state)
        titles = [r["title"] for r in risks]
        assert len(titles) == len(set(titles)), "同标题的风险没有去重"


class TestAgentNoKeyFallback:
    def test_llm_reports_unavailable_without_key(self, monkeypatch):
        from mining_daily.agent.llm import ChatLLM

        monkeypatch.setattr("mining_daily.agent.llm.settings.llm_provider", "offline", raising=False)
        assert ChatLLM().available is False

    def test_extract_json_handles_fenced_output(self):
        from mining_daily.agent.llm import extract_json

        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
        assert extract_json('好的，结果是 {"b": 2} 请查收') == {"b": 2}
        assert extract_json("完全不是 JSON") is None
