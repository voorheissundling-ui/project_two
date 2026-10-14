"""最终 Markdown 装配 —— 把草稿、来源清单、调用留痕和口径说明拼成一份可交付简报。

这里有一条**不能让步**的规则：**风险提示不允许被模型吞掉**。

草稿是模型写的，模型有时候会把风险那节写得很短、甚至整节略过（尤其当证据里
风险条目不多时）。但风险提示是这份日报的核心交付物之一。所以 render 会逐条比对：
凡是规则生成的风险没在草稿里出现的，一律追加到「补充风险提示」里 ——
模型可以润色措辞，但不能决定「哪条风险不告诉你」。
"""

from __future__ import annotations

import re
from datetime import date

from mining_daily.agent.graph import _evidence_payload


def _fmt_date(iso: str) -> str:
    return (iso or "")[:10] or "—"


def _sources_block(ev: dict) -> str:
    if not ev["sources"]:
        return "_（本期无引用来源）_"
    lines = []
    for src in ev["sources"]:
        kind = {"news": "新闻", "report": "储量报告", "price": "行情"}.get(src["type"], src["type"])
        url = src.get("url") or ""
        title = src.get("title") or ""
        lines.append(f"- `{src['id']}` 〔{kind}〕{title}" + (f" — <{url}>" if url else " — _（无公开链接）_"))
    return "\n".join(lines)


def _calls_block(hub_calls: list) -> str:
    if not hub_calls:
        return "_（无工具调用记录）_"
    lines = ["| # | server | tool | 参数 | 结果 | 耗时 |", "|---|---|---|---|---|---|"]
    for i, call in enumerate(hub_calls, 1):
        import json

        args = json.dumps(call.arguments, ensure_ascii=False)
        if len(args) > 60:
            args = args[:57] + "…"
        status = "✅" if call.ok else "❌ 已降级"
        lines.append(
            f"| {i} | `{call.server}` | `{call.tool}` | `{args}` | {status} | {call.elapsed_ms} ms |"
        )
    return "\n".join(lines)


def _squash(s: str) -> str:
    """抹掉所有空白。模型经常在专有名词后多一个空格或换个行，
    逐字比对会把「同一条风险」判成「漏了」，结果正文和附录各出现一次。"""
    return re.sub(r"\s+", "", s)


def _risk_fragments(risk: dict) -> list[str]:
    """把一条风险拆成几个**只要命中一个就算覆盖**的指纹。

    为什么不能拿整条标题直接 `in` 一把梭：风险标题的构造是
    `{主体} {定性描述}`，而模型会把主体缩写或省略 ——
    规则写「Pilbara Minerals Limited 储量数据时效性」，
    草稿写「PLS 储量数据时效性。……」,整串匹配必然失败，
    于是同一条风险在「四、风险提示」和「补充风险提示」里各出现一遍，
    读起来像是两条不同的风险。

    指纹取三档，从严到松：
      1. 完整标题（抹空白后）
      2. 标题按空白/冒号切开后的**最后一段** —— 按构造规则它就是定性描述，
         主体名怎么缩写都不影响。**必须是最后一段而不是最长的一段**：
         「Pilbara Minerals Limited 储量数据时效性」里最长的是 "Minerals"(8 字符)，
         比 "储量数据时效性"(7 字符) 还长，取最长会挑中一个模型永远不会写的词。
      3. 详情里的日期/长数字（如 "2015-06-30"），这是最强的指纹
    """
    title = risk.get("title", "")
    fragments = [_squash(title)]

    # 标题形如 "新闻面：<某条新闻的标题>" 或 "Pilbara Minerals Limited 储量数据时效性"
    parts = [p for p in re.split(r"[\s：:]+", title) if len(p) >= 3]
    if parts:
        fragments.append(_squash(parts[-1]))

    fragments += re.findall(r"\d[\d:.\-/]{6,}", risk.get("detail", ""))
    return [f for f in fragments if f]


def _risk_covered(risk: dict, draft_flat: str) -> bool:
    return any(frag in draft_flat for frag in _risk_fragments(risk))


async def node_render(state: dict, hub=None) -> dict:
    """装配最终 Markdown。不调模型，纯字符串拼装 —— 渲染必须完全可预测。

    `hub` 由 build_graph 通过闭包注入（它持有子进程会话，不能塞进 state）。
    """
    ev = _evidence_payload(state)
    focus = state.get("focus") or {}
    warnings = list(state.get("warnings") or [])

    draft = state.get("draft", "").strip()

    # 模型可以润色风险，但不能决定「哪条风险不告诉你」。
    draft_flat = _squash(draft)
    missing_risks = [r for r in ev["risks"] if not _risk_covered(r, draft_flat)]
    supplement = ""
    if missing_risks:
        lines = ["\n## 补充风险提示", "", "_以下条目由确定性规则生成，未被草稿覆盖，原样附上：_", ""]
        for r in missing_risks:
            lines.append(f"- 〔{r['level']}〕**{r['title']}**：{r['detail']}")
        supplement = "\n".join(lines)

    # 数据口径以行情序列为准（三条链路都读同一个 fixture 目录，口径必然一致）
    origins = {
        block["spot"]["provenance"]["data_origin"]
        for block in (state.get("prices") or {}).values()
    }

    parts: list[str] = []
    parts.append(f"# 矿权日报 · {state['query']}")
    parts.append("")
    parts.append(
        f"**生成时间**：{date.today().isoformat()}　|　"
        f"**数据模式**：`{'离线 fixture' if 'live' not in origins else '真实抓取'}`　|　"
        f"**规划器**：`{focus.get('planner', 'local')}`"
    )
    parts.append("")
    parts.append(
        f"**检索范围**："
        + (f"报告 {', '.join(focus.get('report_ids') or ['—'])}；" if focus.get("report_ids") else "")
        + (f"行情 {', '.join(focus.get('price_series') or ['—'])}" if focus.get("price_series") else "")
    )
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append(draft or "_（草稿为空）_")
    if supplement:
        parts.append(supplement)

    if warnings:
        parts.append("\n## 数据质量告警\n")
        parts.extend(f"- ⚠️ {w}" for w in warnings)

    parts.append("\n---\n")
    parts.append("## 引用源\n")
    parts.append(_sources_block(ev))

    parts.append("\n## 工具调用记录\n")
    parts.append(
        "> 三个 MCP server 均以 stdio 子进程方式接入，本表为本次生成的真实调用留痕。\n"
    )
    parts.append(_calls_block(getattr(hub, "calls", []) if hub else []))

    parts.append("\n## 数据口径\n")
    parts.append(
        "- **数据来源**：离线 fixture（新闻标题/链接/时间取自公开报道，摘要为中文改写；"
        "价格基于公开锚点按几何布朗运动插值为日频序列；储量报告为按公开披露数据重建的 PDF）。\n"
        "- **单位归一化**：矿石量统一为 Mt，金属量黄金类为 Moz、锂/钽类为 t；品位保留原始单位"
        "（`g/t`、`%`、`% Li2O`、`ppm`），因为不同矿种的品位口径不可直接比较。\n"
        "- **风险提示**由确定性规则从数据中生成（价格跌幅/波动率、储量报告时效、新闻负面情绪、"
        "链接未实测），每条均可回溯到上表中的具体来源。\n"
    )

    # trace/warnings 都有 operator.add reducer，这里只返回自己新增的那一条
    return {"markdown": "\n".join(parts), "trace": ["render: 装配完成"]}
