"""矿权日报 Agent —— LangGraph 状态机。

图长这样::

                    ┌─ gather_news ────┐
    START → plan ───┼─ gather_resources┼→ assess_risks → draft → verify ─┬→ render → END
                    └─ gather_prices ──┘                               └→ draft（回炉一次）

三个**设计决定**，每个都是被现实教育出来的：

1. **取数是确定性的，模型只负责「问什么」和「怎么写」。**
   三个数据源（新闻/储量/价格）是固定的，用 LLM 去自由决定调哪个工具，
   只会引入不确定性而没有任何收益。所以 plan 让模型规划检索词，
   draft 让模型组织叙述，中间的取数、统计、判趋势、挑风险全走代码。

2. **三个数据源并行扇出（fan-out）。**
   它们之间没有依赖，串行跑等于白等两倍时间。LangGraph 的并行分支天然支持，
   三个节点写的是 state 里三个不同的键，不需要 reducer。

3. **draft → verify 之间有一条回头边，且只回头一次。**
   verify 会把草稿里出现的**每一个数字**拿去证据里查。
   查不到就说明模型在编，带着「哪些数字无出处」的反馈重写一次；
   第二次还编就直接把可疑数字列进告警——**宁可标出来，也不假装没看见**。
"""

from __future__ import annotations

import asyncio
import operator
import re
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from mining_daily.agent.llm import ChatLLM, LLMUnavailable, extract_json
from mining_daily.agent.mcp_hub import MCPHub
from mining_daily.common import fixtures


class BriefState(TypedDict, total=False):
    """整条流水线的共享状态。每个节点只写自己负责的键。

    `trace` 和 `warnings` 挂了 `operator.add` reducer：三个取数节点在**同一个超步**
    里并行写这两个键，没有 reducer 的话 LangGraph 会直接抛 InvalidUpdateError
    （并发写同一个通道是未定义行为）。挂了 reducer 之后语义变成"追加"，
    每个节点只管返回自己那一条，不用关心别人写了什么。
    """

    query: str
    focus: dict[str, Any]
    news: list[dict]
    resources: list[dict]
    prices: dict[str, Any]
    risks: list[dict]
    draft: str
    verify_feedback: list[str]
    retries: int
    markdown: str
    warnings: Annotated[list[str], operator.add]
    trace: Annotated[list[str], operator.add]


# --------------------------------------------------------------------------
# 主题规划
# --------------------------------------------------------------------------
# 中文品种 → 英文词元。一个中文词可以展开成多个英文词元，
# 因为中文口语的粒度比英文 fixture 的字段粒度粗。
COMMODITY_ZH2EN: dict[str, tuple[str, ...]] = {
    "锂": ("lithium", "spodumene"),
    "碳酸锂": ("lithium", "lithium_carbonate"),
    "锂辉石": ("spodumene", "lithium"),
    "钽": ("tantalum",),
    "金": ("gold",),
    "黄金": ("gold",),
    "铜": ("copper",),
    "镍": ("nickel",),
    "锌": ("zinc",),
    "铁": ("iron", "iron_ore"),
    "铁矿石": ("iron_ore", "iron"),
}

# 产业链上下游联动：问到锂，只看下游碳酸锂会漏掉成本端的锂辉石（反之亦然）。
# 日报里这两个价格要一起看才有意义，所以命中一个就把另一个带进来。
RELATED_TOKENS: dict[str, tuple[str, ...]] = {
    "lithium": ("spodumene",),
    "lithium_carbonate": ("spodumene",),
    "spodumene": ("lithium_carbonate",),
}


def _expand_tokens(query: str) -> set[str]:
    """把查询拆成可比较的词元，并把中文品种名展开成英文。"""
    tokens = set(re.findall(r"[a-z0-9_]{2,}", query.lower()))
    for zh, ens in COMMODITY_ZH2EN.items():
        if zh in query:
            tokens.update(ens)
    for token in list(tokens):
        tokens.update(RELATED_TOKENS.get(token, ()))
    return tokens


def plan_focus_local(query: str) -> dict[str, Any]:
    """确定性规划：不依赖 LLM，保证离线也能跑。

    做法是**用查询词去给报告和价格序列打分**，而不是维护一张硬编码映射表 ——
    这样加一份新报告 / 新价格序列时，不用改这里。
    """
    tokens = _expand_tokens(query)

    # 选报告：公司/项目/品种/标题里命中词元越多越相关
    scored_reports = []
    for rep in fixtures.load_reports():
        text = " ".join(
            str(rep.get(k, ""))
            for k in ("company", "project", "commodity", "title", "reporting_standard")
        ).lower()
        score = sum(1 for t in tokens if t and t in text)
        if score:
            scored_reports.append((score, rep))
    scored_reports.sort(key=lambda x: (-x[0], x[1]["report_id"]))
    report_ids = [r["report_id"] for _s, r in scored_reports[:3]]

    # 选价格序列：同样按命中打分，取前 2 条（主品种 + 上游原料）
    series_scored = []
    for sid, series in fixtures.load_price_series().items():
        text = " ".join(
            str(series.get(k, "")) for k in ("series_id", "commodity", "display_name_zh", "benchmark")
        ).lower()
        score = sum(1 for t in tokens if t and t in text)
        if score:
            series_scored.append((score, sid))
    series_scored.sort(key=lambda x: (-x[0], x[1]))
    price_series = [sid for _s, sid in series_scored[:2]]

    # 从命中的报告里反推品种，作为新闻检索和价格查询的锚
    commodities: list[str] = []
    for rep in fixtures.load_reports():
        if rep["report_id"] in report_ids:
            for c in str(rep.get("commodity", "")).split(","):
                c = c.strip()
                if c and c not in commodities:
                    commodities.append(c)

    keywords = sorted(t for t in tokens if len(t) > 2)[:6]
    return {
        "commodities": commodities,
        "keywords": keywords,
        "report_ids": report_ids,
        "price_series": price_series,
        "news_queries": [query.strip()] + [f"{query} 价格", f"{query} 产量"],
        "days": 200,
        "planner": "local",
    }


PLANNER_SYSTEM = """你是一名矿业研究助理。用户要一份矿产日报，请规划检索参数。

只输出 JSON，字段：
  commodities: 英文品种名数组（如 ["lithium"]）
  keywords: 英文/中文检索关键词数组（公司名、项目名、矿种、地名）
  news_queries: 3 条用于新闻检索的自然语言查询，中英文皆可
  days: 新闻回溯天数（整数，默认 200）

注意：keywords 要写**专有名词**（Pilbara、Pilgangoora、spodumene、Altura 这类），
不要写「矿产」「价格」这种泛词。"""


async def node_plan(state: BriefState) -> dict:
    """规划节点。先本地算一遍兜底，有 key 再让模型补充检索词。"""
    query = state["query"]
    focus = plan_focus_local(query)
    trace = [f"plan: 本地规划命中 {len(focus['report_ids'])} 份报告 / "
             f"{len(focus['price_series'])} 条价格序列"]

    llm = ChatLLM()
    if llm.available:
        try:
            result = await asyncio.to_thread(
                llm.complete, PLANNER_SYSTEM, f"用户需求：{query}", json_mode=True
            )
            planned = extract_json(result.text) or {}
            # 只采纳「补充性」字段：报告/序列的选择仍以本地打分为准，
            # 避免模型凭空造出不存在的 report_id 或 series_id
            if planned.get("news_queries"):
                focus["news_queries"] = [str(q) for q in planned["news_queries"]][:3]
            if planned.get("keywords"):
                merged = set(focus["keywords"]) | {str(k) for k in planned["keywords"]}
                focus["keywords"] = sorted(merged)[:10]
            if planned.get("days"):
                focus["days"] = int(planned["days"])
            focus["planner"] = "deepseek+local"
            trace.append(f"plan: LLM 补充检索词 -> {focus['news_queries']}")
        except Exception as exc:  # noqa: BLE001 - 规划失败不该阻断整份简报
            trace.append(f"plan: LLM 规划失败({type(exc).__name__})，沿用本地规划")
    else:
        trace.append("plan: 未配置 API key，使用本地规划")

    return {"focus": focus, "trace": trace, "retries": 0}


# --------------------------------------------------------------------------
# 三个取数节点（并行）
# --------------------------------------------------------------------------
async def node_gather_news(state: BriefState, hub: MCPHub) -> dict:
    focus = state["focus"]
    collected: dict[str, dict] = {}
    seen: set[str] = set()

    for q in focus["news_queries"]:
        payload = await hub.call("news", "search", {"query": q, "days": focus["days"], "limit": 6})
        if not payload.get("ok"):
            continue
        for item in payload.get("items", []):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            collected[item["id"]] = item

    ranked = sorted(collected.values(), key=lambda i: -i.get("relevance_score", 0))[:8]

    # 只给 top-3 取正文：正文更长更贵，只值得为最重要的几条付这个成本
    for item in ranked[:3]:
        detail = await hub.call("news", "fetch_article", {"url": item["id"]})
        if detail.get("ok"):
            item["body"] = detail.get("body", "")

    return {"news": ranked, "trace": [f"gather_news: 命中 {len(ranked)} 条（含 3 条正文）"]}


async def node_gather_resources(state: BriefState, hub: MCPHub) -> dict:
    focus = state["focus"]
    rows: list[dict] = []
    abstained: list[dict] = []

    for report_id in focus["report_ids"]:
        payload = await hub.call("pdf", "extract_resources", {"pdf_url": f"fixture://{report_id}"})
        # 注意 ExtractResourcesResult 上没有 ok 字段（它不在「可恢复失败」那一类里），
        # 所以判据是「ok 不为 False 且真拿到了行」，而不是「ok 为真」。
        if payload.get("ok") is not False and payload.get("rows"):
            for row in payload["rows"]:
                rows.append(
                    {
                        **row,
                        "report_id": payload.get("report_id"),
                        "company": payload.get("company"),
                        "project": payload.get("project"),
                        "effective_date": payload.get("effective_date"),
                        "reporting_standard": payload.get("reporting_standard"),
                        "source_url": payload.get("provenance", {}).get("source_url"),
                    }
                )
        else:
            abstained.append(
                {
                    "report_id": report_id,
                    "reason": (payload.get("extraction", {}).get("warnings") or ["无有效返回"])[0],
                }
            )

    return {
        "resources": rows,
        "warnings": [f"储量解析弃权：{a['report_id']} —— {a['reason']}" for a in abstained],
        "trace": [f"gather_resources: 解析出 {len(rows)} 行资源量，{len(abstained)} 份弃权"],
    }


async def node_gather_prices(state: BriefState, hub: MCPHub) -> dict:
    focus = state["focus"]
    prices: dict[str, Any] = {}
    warnings: list[str] = []

    for sid in focus["price_series"]:
        spot = await hub.call("price", "get_price", {"commodity": sid})
        trend = await hub.call("price", "get_trend", {"commodity": sid, "days": 90})
        if spot.get("ok") and trend.get("ok"):
            prices[sid] = {"spot": spot, "trend": trend}
        else:
            warnings.append(
                f"行情缺失：{sid} —— {spot.get('error') or trend.get('error', '未知原因')}"
            )

    return {
        "prices": prices,
        "warnings": warnings,
        "trace": [f"gather_prices: 取得 {len(prices)} 个品种的现货价与 90 天走势"],
    }


# --------------------------------------------------------------------------
# 风险
# --------------------------------------------------------------------------
def assess_risks(state: BriefState) -> list[dict]:
    """风险提示**全部由规则产生**，每条都必须挂得住一个具体数字或事实。

    刻意不让 LLM 自由发挥「风险」：模型很擅长生产听起来合理但无出处的话，
    那正是日报最不该有的东西。模型能做的只是把这些已成立的条目润色成句子。
    """
    risks: list[dict] = []

    for sid, block in (state.get("prices") or {}).items():
        trend = block["trend"]
        stats = trend["stats"]
        name = trend["display_name_zh"]
        unit = trend["unit"]

        if stats["change_pct"] <= -10:
            risks.append(
                {
                    "level": "high",
                    "title": f"{name}价格显著下行",
                    "detail": (
                        f"近 {trend['days']} 天由 {stats['first']:,.0f} 跌至 {stats['last']:,.0f} "
                        f"{unit}，跌幅 {abs(stats['change_pct']):.1f}%，"
                        f"区间最低 {stats['min']:,.0f}（{stats['low_date']}）。"
                    ),
                    "evidence": trend["provenance"]["source_url"],
                }
            )
        elif stats["change_pct"] >= 10:
            risks.append(
                {
                    "level": "medium",
                    "title": f"{name} 价格快速上行",
                    "detail": (
                        f"近 {trend['days']} 天涨幅 {stats['change_pct']:.1f}%，"
                        f"区间最高 {stats['max']:,.0f}（{stats['high_date']}）。"
                        "高位追涨需注意回落风险。"
                    ),
                    "evidence": trend["provenance"]["source_url"],
                }
            )

        if stats["volatility_pct"] >= 1.5:
            risks.append(
                {
                    "level": "medium",
                    "title": f"{name} 波动率偏高",
                    "detail": f"日收益率标准差 {stats['volatility_pct']:.2f}%，报价噪音大，单日涨跌代表性有限。",
                    "evidence": trend["provenance"]["source_url"],
                }
            )

    for row in state.get("resources") or []:
        eff = row.get("effective_date") or ""
        if eff and eff[:4].isdigit() and int(eff[:4]) <= 2016:
            risks.append(
                {
                    "level": "medium",
                    "title": f"{row.get('company')} 储量数据时效性",
                    "detail": (
                        f"采用的资源量生效日为 {eff}（{row.get('reporting_standard')}），"
                        "距今已久，且该报告标注为历史估算，不宜直接与最新披露对比。"
                    ),
                    "evidence": row.get("source_url"),
                }
            )

    for item in state.get("news") or []:
        text = f"{item.get('title','')} {item.get('summary_zh','')}"
        if item.get("sentiment") == "negative" or "risk" in (item.get("topics") or []):
            risks.append(
                {
                    "level": "medium",
                    "title": f"新闻面：{item['title'][:40]}",
                    "detail": (item.get("summary_zh") or "")[:160],
                    "evidence": item.get("url"),
                    "published_at": item.get("published_at"),
                }
            )
        if item.get("source_verified") == "search_only":
            risks.append(
                {
                    "level": "low",
                    "title": "部分引用链接未经实测",
                    "detail": (
                        f"《{item['title'][:36]}》的原文链接在抓取时未能实测返回 200（可能被反爬拦截），"
                        "引用前建议人工打开确认。"
                    ),
                    "evidence": item.get("url"),
                }
            )

    seen = set()
    unique = []
    for r in risks:
        if r["title"] in seen:
            continue
        seen.add(r["title"])
        unique.append(r)
    return unique


async def node_assess_risks(state: BriefState) -> dict:
    risks = assess_risks(state)
    return {"risks": risks, "trace": [f"assess_risks: 由规则产生 {len(risks)} 条风险提示"]}


# --------------------------------------------------------------------------
# 起草 / 校验 / 渲染
# --------------------------------------------------------------------------
DRAFT_SYSTEM = """你是一名矿业分析师，为投资团队写每日简报。

铁律：
1. **只使用给定证据里的数字**。证据里没有的数字一个字都不要写。
2. 每个数据点后面用 `[S1]` `[S2]` 这样的标记标注来源编号，编号取自证据的 sources 列表。
3. 不写「预计」「有望」「或将」这类没有依据的推测；只陈述证据支持的事实。
4. 中文撰写。数字保留原单位（不要自行换算）。

输出 Markdown，用这四个二级标题，不要加别的标题：
## 一、本周要闻
## 二、资源量与储量
## 三、价格走势
## 四、风险提示"""


def _evidence_payload(state: BriefState) -> dict:
    """把证据整理成给模型看的紧凑结构，并**在这里就把来源编号定下来**。

    来源编号按「一条来源一个号」分配（`_sid` 内部去重）：一份报告出土 4 行资源量，
    引用清单里只该出现一次，而不是 S9/S10/S11/S12 四条一模一样的链接。
    """
    sources: list[dict] = []
    assigned: dict[tuple, str] = {}

    def _sid(kind: str, key: str, title: str, url: Optional[str]) -> str:
        k = (kind, key)
        if k in assigned:
            return assigned[k]
        sid = f"S{len(sources) + 1}"
        sources.append({"id": sid, "type": kind, "title": title, "url": url})
        assigned[k] = sid
        return sid

    news_brief = []
    for item in state.get("news") or []:
        sid = _sid("news", item["id"], item["title"], item["url"])
        news_brief.append(
            {
                "source": sid,
                "title": item["title"],
                "published_at": item["published_at"],
                "summary": item.get("summary_zh", ""),
                "body_excerpt": (item.get("body") or "")[:600],
                "sentiment": item.get("sentiment"),
            }
        )

    res_brief = []
    for row in state.get("resources") or []:
        sid = _sid(
            "report",
            row.get("report_id") or f"{row.get('company')}|{row.get('project')}",
            f"{row.get('company')} {row.get('project')}",
            row.get("source_url"),
        )
        res_brief.append(
            {
                "source": sid,
                "company": row.get("company"),
                "project": row.get("project"),
                "category": row["category"],
                "commodity": row.get("commodity"),
                # 提前四舍五入：归一化后的值是 float，直接喂给模型会出现
                # "含钽 1,406.1363 t" 这种把浮点误差当精度写进简报的情况
                "tonnage_mt": round(row["ore_tonnage"]["normalized_value"], 2),
                "tonnage_unit": row["ore_tonnage"]["normalized_unit"],
                "grade": round(row["grade"]["value"], 4),
                "grade_unit": row["grade"]["unit"],
                "contained": round(row["contained_metal"]["normalized_value"], 2),
                "contained_unit": row["contained_metal"]["normalized_unit"],
                "effective_date": row.get("effective_date"),
                "standard": row.get("reporting_standard"),
            }
        )

    price_brief = []
    for sid, block in (state.get("prices") or {}).items():
        spot, trend = block["spot"], block["trend"]
        source_id = _sid(
            "price", sid, trend["display_name_zh"], spot["provenance"].get("source_url")
        )
        price_brief.append(
            {
                "source": source_id,
                "name": trend["display_name_zh"],
                "unit": trend["unit"],
                "spot_date": spot["date"],
                "spot_value": spot["value"],
                "trend_days": trend["days"],
                "change_pct": trend["stats"]["change_pct"],
                "min": trend["stats"]["min"],
                "max": trend["stats"]["max"],
                "trend_label": trend["trend_label"],
            }
        )

    return {
        "sources": sources,
        "news": news_brief,
        "resources": res_brief,
        "prices": price_brief,
        "risks": state.get("risks") or [],
    }


def render_template_draft(state: BriefState) -> str:
    """无 LLM 时的模板草稿。**数字和 LLM 版完全一致**，只是行文更机械。"""
    ev = _evidence_payload(state)
    lines: list[str] = []

    lines.append("## 一、本周要闻")
    if ev["news"]:
        for item in ev["news"]:
            lines.append(
                f"- **{item['title']}**（{item['published_at'][:10]}）[{item['source']}] "
                f"{item['summary']}"
            )
    else:
        lines.append("- 本期未检索到相关要闻。")

    lines.append("\n## 二、资源量与储量")
    if ev["resources"]:
        for r in ev["resources"]:
            lines.append(
                f"- **{r['company']} · {r['project']}**（{r['category']}，{r['standard']}，"
                f"生效日 {r['effective_date']}）[{r['source']}]："
                f"矿石量 {r['tonnage_mt']:,.2f} {r['tonnage_unit']}，"
                f"品位 {r['grade']} {r['grade_unit']}，"
                f"金属量 {r['contained']:,.2f} {r['contained_unit']}。"
            )
    else:
        lines.append("- 本期未取得可用的资源量数据（解析器弃权或未匹配到报告）。")

    lines.append("\n## 三、价格走势")
    if ev["prices"]:
        for p in ev["prices"]:
            lines.append(
                f"- **{p['name']}**（{p['unit']}）[{p['source']}]："
                f"{p['spot_date']} 报 {p['spot_value']:,.0f}；"
                f"近 {p['trend_days']} 天 {p['trend_label']}，"
                f"变动 {p['change_pct']:+.2f}%，"
                f"区间 {p['min']:,.0f} – {p['max']:,.0f}。"
            )
    else:
        lines.append("- 本期未取得行情数据。")

    lines.append("\n## 四、风险提示")
    if ev["risks"]:
        for r in ev["risks"]:
            lines.append(f"- 〔{r['level']}〕**{r['title']}**：{r['detail']}")
    else:
        lines.append("- 本期未触发预设风险规则。")

    return "\n".join(lines)


async def node_draft(state: BriefState) -> dict:
    llm = ChatLLM()
    feedback = state.get("verify_feedback") or []

    if not llm.available:
        return {
            "draft": render_template_draft(state),
            "trace": ["draft: 无 API key，使用模板草稿（数字口径与 LLM 版一致）"],
        }

    evidence = _evidence_payload(state)
    import json

    user = (
        f"用户需求：{state['query']}\n\n"
        f"证据（JSON）：\n```json\n{json.dumps(evidence, ensure_ascii=False, indent=1)}\n```"
    )
    if feedback:
        user += "\n\n上一稿被校验发现以下数字在证据中找不到，请**删掉或改正**后重写：\n" + "\n".join(
            f"- {f}" for f in feedback
        )

    try:
        result = await asyncio.to_thread(llm.complete, DRAFT_SYSTEM, user)
        return {
            "draft": result.text.strip(),
            "trace": [f"draft: DeepSeek({result.model}) 生成草稿 {len(result.text)} 字"
                      + ("（第 2 稿）" if feedback else "")],
        }
    except LLMUnavailable:
        return {"draft": render_template_draft(state), "trace": ["draft: LLM 不可用，回落模板"]}
    except Exception as exc:  # noqa: BLE001 - 调用失败也要出简报
        return {
            "draft": render_template_draft(state),
            "trace": [f"draft: LLM 调用失败({type(exc).__name__})，回落模板"],
        }


# 匹配带千分位的数字：1,234 / 111,650 / 1.05 / 233
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _canon_number(token: str) -> set[str]:
    """把一个数字串归一到若干"等价写法"，用于跨写法比对。

    这一步是必须的，而且**很容易写错**：证据是 JSON，数值一律是 `233.0` / `145650.0`，
    而草稿写的是人话 `233` / `145,650`。如果只用 `rstrip("0")` 归一，
    `"233.0".rstrip("0")` 得到的是 `"233."`（留着小数点），
    于是 `233` 匹配不上 `233.0` —— 校验器会把**每一个照抄证据的数字**都判成幻觉。
    正确做法是先把小数点后的尾零去掉，再去掉可能剩下的裸小数点。
    """
    plain = token.replace(",", "")
    out = {plain}
    if "." in plain:
        trimmed = plain.rstrip("0").rstrip(".")
        out.add(trimmed)
        out.add(trimmed + ".0")
        if trimmed.isdigit():
            out.add(trimmed)  # 233.0 -> 233
    else:
        out.add(plain.rstrip("0") or "0")  # 145650 -> 14565（宽松匹配万/千分位截断）
    return {x for x in out if x}


def _evidence_numbers(state: BriefState) -> set[str]:
    """证据里出现过的所有数字的所有等价写法，用于校验草稿。"""
    import json

    raw = json.dumps(_evidence_payload(state), ensure_ascii=False)
    numbers: set[str] = set()
    for token in _NUM_RE.findall(raw):
        numbers |= _canon_number(token)
    return numbers


async def node_verify(state: BriefState) -> dict:
    """校验草稿里的每个数字是否都在证据里出现。

    这是**引用忠实度（faithfulness）的最低成本实现**：不判断句子对不对，
    只判断数字有没有出处。幻觉最常以「精确但虚构的数字」的形式出现，
    而这一条规则就能把绝大多数挡下来。

    定位是「宁可漏报也不要误报」：误报会让模型去改一个本来正确的数字（越改越糟），
    所以这里放得比较松 —— 单位换算、四舍五入、千分位差异都算命中。
    """
    draft = state.get("draft", "")
    if not draft:
        return {"verify_feedback": [], "trace": ["verify: 草稿为空，跳过"]}

    known = _evidence_numbers(state)
    suspicious: list[str] = []
    for token in _NUM_RE.findall(draft):
        plain = token.replace(",", "")
        if len(plain) < 3:  # 1–2 位数多为序号/年份片段/百分位，噪音太大不判
            continue
        if _canon_number(token) & known:
            continue
        suspicious.append(token)

    # 同一稿里重复出现的同一个数字只报一次
    suspicious = list(dict.fromkeys(suspicious))

    if suspicious and state.get("retries", 0) < 1:
        return {
            "verify_feedback": [f"数字 {s} 在证据中找不到出处" for s in suspicious[:8]],
            "retries": state.get("retries", 0) + 1,
            "trace": [f"verify: 发现 {len(suspicious)} 个无出处数字，打回重写（第 1 次）"],
        }

    warnings = []
    if suspicious:
        warnings.append(
            "以下数字未能与证据对齐，已在简报末尾标出，请人工核对：" + "、".join(suspicious[:8])
        )

    return {
        "verify_feedback": [],
        "warnings": warnings,
        "trace": [f"verify: 数字引用校验完成（{len(suspicious)} 处存疑）"],
    }


def route_after_verify(state: BriefState) -> str:
    return "draft" if state.get("verify_feedback") else "render"


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------
def build_graph(hub: MCPHub, llm: Optional[ChatLLM] = None):
    """编译状态图。hub 由闭包注入 —— 它持有子进程会话，不能塞进 state。

    注意这里用的是真正的 `async def` 包装函数，**不是 lambda**。
    LangGraph 靠 `iscoroutinefunction` 判断节点该 await 还是直接当返回值，
    而 `lambda s: node_gather_news(s, hub)` 只是个普通函数，返回的 coroutine
    会被当成 state 更新字典丢掉 —— 现象是各取数节点根本没执行
    （"coroutine was never awaited"），且 state 里三个键全是空的。
    """
    # render.py 在模块级 import 本模块的 _evidence_payload，所以这里只能函数内 import，
    # 否则两边会形成循环导入。
    from mining_daily.agent.render import node_render

    graph = StateGraph(BriefState)

    async def gather_news(state: BriefState) -> dict:
        return await node_gather_news(state, hub)

    async def gather_resources(state: BriefState) -> dict:
        return await node_gather_resources(state, hub)

    async def gather_prices(state: BriefState) -> dict:
        return await node_gather_prices(state, hub)

    async def render(state: BriefState, _hub: MCPHub = hub) -> dict:
        # render 也要 hub —— 简报末尾的「工具调用记录」直接来自 hub.calls
        return await node_render(state, hub)

    graph.add_node("plan", node_plan)
    graph.add_node("gather_news", gather_news)
    graph.add_node("gather_resources", gather_resources)
    graph.add_node("gather_prices", gather_prices)
    graph.add_node("assess_risks", node_assess_risks)
    graph.add_node("draft", node_draft)
    graph.add_node("verify", node_verify)
    graph.add_node("render", render)

    graph.add_edge(START, "plan")
    # 三个数据源并行扇出：它们互不依赖，串行跑等于白等两倍时间
    graph.add_edge("plan", "gather_news")
    graph.add_edge("plan", "gather_resources")
    graph.add_edge("plan", "gather_prices")
    graph.add_edge("gather_news", "assess_risks")
    graph.add_edge("gather_resources", "assess_risks")
    graph.add_edge("gather_prices", "assess_risks")
    graph.add_edge("assess_risks", "draft")
    graph.add_edge("draft", "verify")
    graph.add_conditional_edges(
        "verify", route_after_verify, {"draft": "draft", "render": "render"}
    )
    graph.add_edge("render", END)

    return graph.compile()
