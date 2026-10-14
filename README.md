# 矿权日报 Agent（MCP + LangGraph）

> 面试题 #2 交付物。按 MCP 协议实现 **3 个 MCP server + 1 个 Agent client**，
> 组合成「矿权日报」Agent —— 输入「给我生成一份关于 Pilbara 锂矿的今日简报」，
> 输出含**新闻摘要 + 储量数据 + 价格走势 + 风险提示 + 引用源链接**的 Markdown 简报。

```bash
uv sync
uv run mining-daily "给我生成一份关于 Pilbara 锂矿的今日简报"
```

**不需要 API key，不需要联网。** 详见 [RUN.md](RUN.md)（含 Docker 与 Claude Desktop 接法）。

---

## 架构

```
                        ┌───────────────────────────────────────────────┐
   自然语言输入  ──────▶ │  Agent client（LangGraph 状态图）              │ ──▶ Markdown 简报
                        │                                               │
                        │  plan ─┬─▶ gather_news ──────┐                │
                        │        ├─▶ gather_resources ─┼─▶ assess_risks │
                        │        └─▶ gather_prices ────┘        │       │
                        │                                      ▼       │
                        │              verify ◀── draft ◀──────┘       │
                        │                │  ▲                          │
                        │                └──┘ 数字无出处则打回重写一次    │
                        │                │                             │
                        │                └─▶ render ─▶ END             │
                        └───────────────┬───────────────────────────────┘
                                        │  MCP over stdio（JSON-RPC）
                 ┌──────────────────────┼──────────────────────┐
                 ▼                      ▼                      ▼
        ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────┐
        │ mining-news-mcp │   │ mineral-pdf-mcp  │   │  lme-price-mcp   │
        ├─────────────────┤   ├──────────────────┤   ├──────────────────┤
        │ search()        │   │ extract_         │   │ get_price()      │
        │ fetch_article() │   │   resources()    │   │ get_trend()      │
        └─────────────────┘   └──────────────────┘   └──────────────────┘
             BM25 +              按列位解析            别名解析 +
           时间衰减             NI 43-101 / JORC       走势统计
```

三个 server 各自独立、可单独被任何 MCP 宿主消费；Agent 只是其中一个消费者。

## 三个 MCP server

| server | 工具 | 说明 |
|---|---|---|
| `mining-news-mcp` | `search(query, days, limit, commodity)` | BM25（标题权重 4×）+ 指数时间衰减；中英文混检，专名（Pilbara / Pilgangoora）优先 |
| | `fetch_article(url)` | 按 URL 或 id 取正文与结构化实体 |
| `mineral-pdf-mcp` | `extract_resources(pdf_url)` | 从 NI 43-101 / JORC PDF 提取 Indicated/Inferred 资源量，含单位归一化与置信度 |
| `lme-price-mcp` | `get_price(commodity, date)` | 取某日价格，非交易日自动回退到最近交易日 |
| | `get_trend(commodity, days)` | 近 N 天走势统计（区间/涨跌幅/波动率/趋势标签） |

**每个返回值都带 `provenance`**（`data_origin` / `source_url` / `as_of` / `note`）——
离线 fixture 与真实抓取在结构上完全一致，只有这个字段能把它们区分开。
溯源不是可选项。

## 三条主要设计决定

### 1. 可恢复的错误是**返回类型**，不是异常

MCP SDK 出于安全考虑会屏蔽未预期异常的细节，客户端只能收到一句
`Error executing tool get_price` —— 「未收录该品种，可选：……」这段真正有用的信息全被吞掉，
Agent 既不知道错在哪，也没机会换个参数重试。

所以 `ToolFailure`（`ok=false` + `hint` + `available`）是**合法的返回类型**。
真正的程序缺陷仍然抛异常 —— 那才是该崩的。

### 2. LLM 是增强，不是依赖

一份日报不能因为没配 API key 就完全跑不出来，否则 `RUN.md` 承诺的「5 分钟跑通」不成立。
所以能力边界划得很清楚：

- LLM 负责 **query 规划** 与 **叙述成文**（失败自动回落本地打分 + 模板草稿）
- **取数、统计、判趋势、挑风险**全部由确定性代码完成

没 key 时走模板，**用的是同一份证据、同一套数字**，只是行文更机械。
有 key 和没 key 跑出来的简报，数字部分逐字相同。

### 3. 宁可弃权，也不给错答案

PDF 解析器带 `confidence` 与 `ABSTAIN_CONFIDENCE = 0.55`。
整体置信度低于阈值、或 PDF 没有文本层（扫描件，本项目未接 OCR）时**主动交白卷**，
而不是硬猜一个数。

在矿业尽调场景里，**一个错的储量数字比一个空的储量章节危害大得多**。

## 验证

```bash
uv run pytest tests/ -q                        # 88 个用例
uv run python scripts/smoke_servers.py         # 三个 server 的真实 stdio 协议自检
uv run python scripts/check_extraction.py      # 储量提取的字段级准确率
```

**储量提取：30/30 字段全对，最大误差 0.00026%**（容差 ±5%）。
四份 PDF 覆盖四种不同的表格排版 —— 并排分组表头、`000 tonnes` 单位、
JORC 多金属同表、`Mlb` 计量。只挑一份排版干净的来解析证明不了任何事，
**难度在排版多样性，不在数据量**。

测试分五层，因为坏起来的原因完全不同：

| 层 | 文件 | 管什么 |
|---|---|---|
| 解析算法 | `test_tableparse.py` | 单位分类、列拆分、表头扫描（每个用例对应一次真实踩坑） |
| 业务逻辑 | `test_servers.py` | 排序、别名、错误信息是否说人话 |
| 协议 | `test_mcp_protocol.py` | 真起子进程走 stdio JSON-RPC，schema 能不能序列化 |
| Agent | `test_agent.py` | 规划、数字引用校验、风险覆盖判定 |
| 准确率闸门 | `test_extraction_accuracy.py` | 字段级 ±5%，任一次改动让某个字段偏了立刻红 |

## 项目结构

```
src/mining_daily/
├── config.py                 三档数据模式（live/hybrid 未实现 → 启动即报错，见下）
├── common/
│   ├── schemas.py            工具出入参契约（Pydantic），MCP 的 outputSchema 来源
│   ├── fixtures.py           fixture 取数收口 —— 换 live 只需改这一处
│   ├── bm25.py               自写 BM25 + CJK 二元组分词 + 时间衰减
│   ├── tableparse.py         PDF 表格排版解析
│   ├── resource_extract.py   单位归一化、列位分配、弃权判定
│   ├── minipdf.py            纯标准库 PDF 写入器（生成 fixture 用的）
│   └── logging_setup.py      日志强制走 stderr（stdout 是 JSON-RPC 通道）
├── servers/                  三个 MCP server
└── agent/
    ├── mcp_hub.py            MCP 客户端池（子进程常驻 + 调用留痕）
    ├── llm.py                DeepSeek 封装 + 无 key 兜底
    ├── graph.py              LangGraph 状态机
    ├── render.py             Markdown 装配（含引用源与调用留痕）
    └── cli.py                `mining-daily` 命令
```

## 已知边界（说清楚，不藏着）

- **`live` / `hybrid` 模式会直接启动报错**，不是待办事项：真实抓取代码还没写，
  让它跑起来只会把 fixture 数据标成 `data_origin="live"`。
  **一个标错来源的数据比一个明确报错的服务危险得多。**
- 未接 OCR，扫描件 PDF 主动弃权。
- 价格日频序列由公开锚点按几何布朗运动插值，`stats` 反映的是构造走势，非真实市场走势。
- fixture 里哪些是真实抓取、哪些是合成的，逐字段列在 [DATA_NOTES.md](DATA_NOTES.md)。

## 延伸阅读

- [RUN.md](RUN.md) —— 三种跑法、常见问题、为什么没 key 也能出简报
- [DATA_NOTES.md](DATA_NOTES.md) —— fixture 的真实性边界与单位口径
- [docs/答题文档.md](docs/答题文档.md) —— 设计取舍与面试讲法
- [mcp-config.json](mcp-config.json) —— 直接接进 Claude Desktop / Cursor
