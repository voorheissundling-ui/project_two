# RUN.md —— 5 分钟跑通

三种跑法，按你的场景挑一种。**都不需要 API key，都不需要联网。**

---

## 方式 A：本机 uv（推荐，约 2 分钟）

```bash
# 1. 装依赖（uv 会自动建 .venv 并锁版本；首次约 1 分钟）
uv sync

# 2. 出简报 —— 默认就是面试要的那条输入
uv run mining-daily "给我生成一份关于 Pilbara 锂矿的今日简报"

# 3. 存成文件 + 看节点轨迹
uv run mining-daily "给我生成一份关于 Pilbara 锂矿的今日简报" -o out/brief.md --trace
```

看到一份以 `# 矿权日报 · ...` 开头的 Markdown、且包含
**一、本周要闻 / 二、资源量与储量 / 三、价格走势 / 四、风险提示 / 引用源 / 工具调用记录**
六个部分，就算跑通了。

> Windows 用户在 PowerShell 里遇到中文乱码，先执行 `chcp 65001`。

### 想让简报由大模型撰写（可选）

```bash
cp .env.example .env
# 编辑 .env，填 MINING_DAILY_DEEPSEEK_API_KEY=sk-xxx
uv run mining-daily "给我生成一份关于 Pilbara 锂矿的今日简报"
```

**不填也能跑**：会自动回落到本地规划 + 模板草稿，**数字口径与 LLM 版完全一致**，
只是行文更机械。这是有意设计的 —— 见下文「为什么没 key 也能出简报」。

### 自检三个 MCP server 的协议层

```bash
uv run python scripts/smoke_servers.py
```

这条走的是**真实 stdio + JSON-RPC**（`ClientSession` ↔ `stdio_client`），
而不是 in-process 直接调函数 —— 只有这样才验证得到「能被 Claude Desktop 接进去」这件事。
输出里每个 server 都会列出 `serverInfo`、`tools`，以及每次调用的结构化返回，
包含**故意构造的错误路径**（未知品种、错误日期、未知 URL、空查询、`days=0`）。

---

## 方式 B：Docker（约 5 分钟，主要是构建耗时）

```bash
# 出一条简报，结果落在 ./out/brief.md
docker compose up --build agent

# 顺带跑一遍 MCP server 协议自检
docker compose --profile test up --build smoke
```

或不用 compose：

```bash
docker build -t mining-daily:0.1.0 .
docker run --rm mining-daily:0.1.0 "给我生成一份关于 Pilbara 锂矿的今日简报"
```

> **为什么是一个镜像而不是 4 个容器**：Agent 用 stdio 把 server 当子进程拉起，
> stdio 本来就跨不了容器边界。拆多容器得换 streamable-http + 服务发现，
> 那是为部署形态牺牲架构清晰度。详见 [Dockerfile](Dockerfile) 顶部注释。

---

## 方式 C：接进 Claude Desktop / Cursor（不经过 Agent，直接用工具）

把 [mcp-config.json](mcp-config.json) 里的 `mcpServers` 整段粘进：

- **Claude Desktop**：`claude_desktop_config.json`
  （Windows: `%APPDATA%\Claude\claude_desktop_config.json`）
- **Cursor**：项目下的 `.cursor/mcp.json`

**唯一要改的地方**：三个 `args` 里 `--directory` 后面的绝对路径换成你自己的仓库路径。

重启客户端后，对话里问：

> 用 mining-news-mcp 搜一下 Pilbara 锂矿最近的新闻，再用 lme-price-mcp 取碳酸锂近 90 天走势。

这一步**不需要 Agent client，也不需要 API key** —— 它证明的是「按 MCP 协议交付，
能被任何 MCP 宿主直接消费」，而不是「我写了个只会自己调自己的私有客户端」。

---

## 常见问题

**`ModuleNotFoundError: No module named 'mining_daily'`**
用 `python` 直接跑而不是 `uv run`。项目是 src-layout，必须经 `uv run`（或 `pip install -e .`）
才会把 `src/` 挂上 `sys.path`。

**`FixtureMissing: fixture 数据不存在`**
`data/` 目录没跟着一起拷贝。fixture 是**有意提交进仓库**的（这是「离线可复现」的实现方式），
确认 `data/news/`、`data/prices/`、`data/reports/` 三个目录都在。

**简报里某一节写「本期未取得…」**
说明对应的 MCP server 降级了，不是整条流水线挂了。往上翻到 **「工具调用记录」** 表，
标 ❌ 的那一行就是原因。这是刻意的设计：单个数据源失败只降级当节，
不会让整份简报崩掉。

**`工具调用记录` 里出现 `Error executing tool xxx`**
说明命中了 MCP SDK 的异常屏蔽。本项目已把所有**可恢复**的错误改成结构化返回体
（`ok=false` + `hint` + `available`），正常不该再看到这句话；
真看到了说明踩到了未预期的程序缺陷，那个是该修的 bug。

**中文显示成乱码**
Windows 控制台默认 GBK。`chcp 65001`，或直接 `-o out/brief.md` 写文件再打开。

---

## 为什么没 key 也能出简报

`RUN.md` 承诺「5 分钟跑通」，那就意味着**不能有任何一个环节是「先申请个 key」**。

所以 LLM 在本项目里的定位是**增强，不是依赖**，边界划得很清楚：

| 环节 | 谁来做 | 理由 |
|---|---|---|
| 查询规划（拆检索词/选报告/选行情序列） | LLM，**失败回落本地打分** | 模型更会抓专有名词，但本地打分已足够 |
| 取数（调 3 个 MCP 工具） | **确定性代码** | 工具集固定，让模型自由选只是引入不确定性 |
| 统计/趋势/单位归一化 | **确定性代码** | 硬事实，不该由模型即兴发挥 |
| 风险识别 | **确定性规则** | 模型很擅长生产「听起来合理但无出处」的风险 |
| 叙述成文 | LLM，**失败回落模板** | 这是模型真正不可替代的地方 |
| 数字引用校验 | **确定性代码** | 见下 |

没有 key 时走 `render_template_draft()`，**用的是同一份证据、同一套数字**，
只是把「模型组织语言」换成「f-string 拼装」。所以你有 key 和没 key 跑出来的简报，
**数字部分逐字相同** —— 这一点可以用 `diff` 自己验证。

## 那个 draft → verify 的回头边在干什么

模型写完草稿后，`verify` 节点会把**草稿里出现的每一个数字**拿去证据里查一遍，
查不到就带着「哪些数字没有出处」的反馈打回重写一次；第二次还编，
就把可疑数字列进「数据质量告警」而不是假装没看见。

这是**引用忠实度（faithfulness）的最低成本实现**：不判断句子对不对，
只判断数字有没有出处 —— 因为幻觉最常以「精确但虚构的数字」的形式出现。

⚠️ 这里有个很容易写错、一旦写错就完全失效的细节：证据是 JSON，
数值一律是 `233.0` / `145650.0`，而草稿写的是人话 `233` / `145,650`。
如果归一化只做 `rstrip("0")`，`"233.0"` 会变成 `"233."`（留着小数点），
于是 `233` 匹配不上 `233.0` —— **校验器会把每一个照抄证据的数字都判成幻觉**，
然后驱动模型去改一个本来正确的数字。详见 `graph.py` 里 `_canon_number()` 的注释。
