# NI 43-101 / JORC 储量源调研记录（截至 2026-10-14）

> `mineral-pdf-mcp` 的 fixture 取证底稿。数字来自公开检索命中，凡「未查证到」的**一律不编造**，
> fixture 里会显式标注 `data_origin: synthetic` 并在 `DATA_NOTES.md` 里列明。

## 1. Pilbara Minerals（ASX:PLS）Pilgangoora

### 1.1 最新 MRE —— 2025-06-11 公告，估算基准日 as at 31 March 2025

| 类别 | 矿石量 Mt | Li₂O % | Ta₂O₅ ppm | Fe₂O₃ % | 含 Li₂O | 含 Ta₂O₅ |
|---|---:|---:|---:|---:|---:|---:|
| Measured + Indicated | 376 | 1.29 | 119 | 0.59 | 4.8 Mt | 99 Mlb |
| Total (M+I+Inferred) | 446 | 1.28 | 122 | 0.59 | 5.7 Mt | 120 Mlb |
| **Inferred（分项）** | **未查证到** | — | — | — | — | — |

- 同比：含锂量 +23%，吨位 +10%（+39 Mt），品位 1.15% → **1.28% Li₂O**，M&I +14%。
- 依据 104,672 m / 364 孔钻探。
- **Inferred 分项未在任何检索结果中出现**（446 − 376 ≈ 70 Mt 只是算术推算）→ fixture 里这一行标 `synthetic`。

### 1.2 带 Indicated/Inferred 拆分的真实历史表格（更适合做 fixture）

**（a）Pilbara Minerals，2015-06 估计（转引自 PLS ASX 2015-06-02 / Beer & Co 研报）**

| 类别 | 吨位 Mt | Ta₂O₅ ppm | 含 Ta₂O₅ | Li₂O % | 含 Li₂O |
|---|---:|---:|---:|---:|---:|
| Indicated | 6.0 | 233 | 3.1 Mlb | 1.36 | 64,300 t |
| Inferred | 17.8 | 208 | 8.2 Mlb | 1.10 | 172,800 t |
| Total | 23.8 | 214 | 11.3 Mlb | 1.16 | 237,100 t |

**（b）Altura Mining（Pilgangoora 南区，2021 年被 PLS 收购），2016-02 PFS，0.4% Li₂O 截止品位**

| JORC 类别 | 吨位 Mt | Li₂O % | 含 Li₂O |
|---|---:|---:|---:|
| Indicated | 26.7 | 1.05 | 280,000 t |
| Inferred | 9.0 | 1.02 | 92,000 t |
| **Total** | **35.7** | **1.05** | **372,000 t** |

### 1.3 口径提醒（重要）

> **Pilbara Minerals 按 JORC（2012 版）披露，不发布 NI 43-101 技术报告。**
> 若 fixture 要求 NI 43-101 格式，正确做法是：**用 Newmont/Barrick 的 NI 43-101 表式做格式模板，
> 用 PLS 的 JORC 数据做内容**——两者字段可对齐（吨位 Mt / 品位 %·ppm·g/t / 含金属 Mt·Mlb·Moz）。
> 这也是 `mineral-pdf-mcp` 必须同时认两种表式的原因。

## 2. Ground truth 对比数据

### 2.1 Newmont —— Gold Resources, as at December 31, 2025（2026-02-19 发布）

| 类别 | 吨位 (000 t) | 品位 (g/t Au) | 盎司 (000 oz) | 回收率 |
|---|---:|---:|---:|---:|
| Measured | 548,800 | 0.53 | 9,300 | — |
| Indicated | 4,717,000 | 0.59 | 90,100 | — |
| Measured & Indicated | 5,265,900 | 0.59 | 99,400 | — |
| Inferred | 3,967,800 | 0.60 | 70,600 | 78% |

分行示例（as at Dec 31, 2024 表）：

| 矿床 | 类别 | 吨位 (000 t) | 品位 (g/t) | 盎司 (000) |
|---|---|---:|---:|---:|
| Ahafo South Underground (Ghana) | Indicated | 27,100 | 3.96 | 3,400 |
| Ahafo South Underground | Inferred | 11,500 | 3.10 | 1,200 |
| Peñasquito, Mexico (100%) | Indicated | 172,100 | 0.21 | 1,100 |
| Cadia Underground (100%) | Indicated | 1,245,100 | 0.36 | 14,200 |
| Cadia Underground | Inferred | 549,400 | 0.30 | 4,800 |

表式逐字：列顺序 `Tonnage (000 tonnes) | Grade (g/tonne) | Ounces(3) (000) | Metallurgical Recovery(3) (%)`；
表题 `Gold Resources(1)(2) – December 31, 2024`；千分位逗号；吨位四舍五入到 100,000 t、盎司到 100,000 oz。

真实脚注原文：
> (1) Resources are reported exclusive of reserves. Amounts presented may not recalculate in total due to rounding.
> (3) Ounces are estimates of metal contained in ore tonnages and do not include allowances for processing losses…

### 2.2 Barrick —— Mineral Resources, as at December 31, 2024（NI 43-101）

**金**

| 类别 | 吨位 Mt | 品位 (g/t Au) | 含金 Moz |
|---|---:|---:|---:|
| Measured | 450 | 1.68 | 24 |
| Indicated | 4,800 | 1.01 | 150 |
| Inferred | 1,400 | 0.90 | 41 |

**铜**

| 类别 | 吨位 Mt | 品位 (% Cu) | 含铜 Mt |
|---|---:|---:|---:|
| Measured | 600 | 0.38 | 2.3 |
| Indicated | 5,400 | 0.39 | 22 |
| Inferred | 1,300 | 0.30 | 3.9 |

分行示例：

| 资产 | 类别 | 吨位 Mt | 品位 (g/t) | Moz |
|---|---|---:|---:|---:|
| North America – Carlin surface | Measured | 8.8 | 1.29 | 0.37 |
| Carlin surface | Indicated | 96 | 2.06 | 6.4 |
| Carlin surface | Inferred | 29 | 1.30 | 1.2 |
| Africa & ME – Bulyanhulu underground | Indicated | 28 | 7.16 | 6.5 |
| Bulyanhulu underground | Inferred | 11 | 7.20 | 2.5 |
| Cortez (61.5%) total | Indicated | 140 | 2.45 | 11 |
| Cortez (61.5%) total | Inferred | 46 | 2.30 | 3.4 |

表题逐字：`Gold Mineral Resources¹,3,5,6,7,8` / `As at December 31, 2024`；
分组列名逐字：`MEASURED (M)⁹ | INDICATED (I) | (M)+(I) | INFERRED`，其下 `Tonnes (Mt) | Grade (g/t) | Contained ozs (Moz)`。
价格假设：2024 资源用 $1,900/oz Au、$4.00/lb Cu。

## 3. 排版特征（解析器必须处理的真实形态）

| 特征 | Newmont | Barrick |
|---|---|---|
| 吨位单位 | `(000 tonnes)`，如 `4,717,000` | `Mt`，如 `4,800` |
| 千分位逗号 | **有** | 无（数值小） |
| 品位小数位 | 2 位 | M/I 两位、**Inferred 仅一位**（`0.9` vs Indicated `1.01`） |
| 金属量单位 | `Ounces (000)` | `Contained ozs (Moz)` |
| 脚注 | 上标数字 + 文末编号列表 | 表题上标 + Endnote |
| 舍入声明 | *Amounts presented may not recalculate in total due to rounding.* | *Totals may not sum correctly due to rounding* |
| 分组表头 | Measured / Indicated / Measured & Indicated / Inferred | MEASURED / INDICATED / (M)+(I) / INFERRED |

**JORC 侧（PLS/Altura）**：Li₂O 用 `%`（两位小数），Ta₂O₅ 用 `ppm`（整数），Fe₂O₃ 用 `%`；
含 Li₂O 用 `Mt`（现代）或 `t`/`kt`（早期小规模）；含 Ta₂O₅ 用 `Mlb`。
截止品位：现代 0.2–0.3% Li₂O，2016 Altura 用 0.4%，早期也用 0.8%。

## 4. 解析器设计结论

1. **必须同时支持两套表式**：NI 43-101（Newmont/Barrick 式）与 JORC（PLS/Altura 式）。
2. **必须做单位归一**：`000 t` / `Mt` / `kt` → 统一 Mt；`oz` / `koz` / `Moz` → 统一 Moz；
   `Mlb` ↔ `t` 换算（1 Mlb = 453.59237 t）。
3. **必须区分 Inferred 的小数位习惯**：不能因为 `0.9` 只有一位小数就当成解析失败。
4. **必须抓脚注**：舍入声明决定了「Total ≠ 各行相加」是**正常的**，不该触发 abstain。
5. **必须认 `(M)+(I)` 这类合成列**：它是派生值，不该当独立类别入库。

## 5. 来源

- [PLS 2025-06-11 资源量提升（Mining Weekly）](https://www.miningweekly.com/article/pilbara-grows-pilgangoora-resource-by-23-2025-06-11)
- [smallcaps: Pilbara lifts Pilgangoora lithium resource](https://smallcaps.com.au/article/pilbara-minerals-lifts-pilgangoora-lithium-resource-grade-tonnage-gains)
- [Australian Mining: Pilgangoora resource gets a boost](https://www.australianmining.com.au/pilgangoora-resource-gets-a-boost/)
- [PLS Annual Report FY2026 (PDF)](https://www.pls.com/documents/pls-annual-report-2026-260821-final-(linked).pdf)
- [Altura Mining ASX 公告 (PDF)](https://media.abnnewswire.net/media/en/docs/80928-ASX-AJM-733384.pdf)
- [Newmont 2025 Mineral Reserves 新闻稿 (BusinessWire)](https://www.businesswire.com/news/home/20260219894602/en/Newmont-Reports-2025-Mineral-Reserves-of-118.2-Million-Gold-Ounces-and-12.5-Million-Tonnes-of-Copper)
- [Barrick Annual Report 2024 (PDF)](https://s25.q4cdn.com/322814910/files/doc_financial/annual_reports/2024/Barrick_Annual_Report_2024.pdf)
