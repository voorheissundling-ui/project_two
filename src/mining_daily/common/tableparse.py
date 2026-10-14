"""从 PDF 文本层解析 NI 43-101 / JORC 资源量表格。

为什么要单独写一个模块：三份报告的版式**完全不同**，一条正则打天下必然漏。

  Newmont（行列式）  Deposits | Tonnage (000 tonnes) | Grade (g/tonne) | Ounces (000) | Recovery
                    类别在**行标题**上（"Indicated Resource"）
  Barrick（并排列）  MEASURED | INDICATED | (M)+(I) | INFERRED 四组并排，
                    每组再分 Tonnes/Grade/Contained —— 类别在**分组表头**上
  Pilbara（JORC）    Li2O 用 %、Ta2O5 用 ppm、含金属量用 Mlb/t，一行两种金属

解析分四步，每一步都在踩一个真实的坑：

  1. **切单元格**：等宽排版下「≥2 个空格」是列边界。但**不能只切一次** ——
     Newmont 表头 `(000 tonnes) (g/tonne)` 两列之间只有 1 个空格，粗切会把它
     并成一列。所以切完还要按括号单位把「一格多单位」的格再拆开。
  2. **并排分组**：认出 `MEASURED / INDICATED / INFERRED` 标签行，记下位置，
     再把单位单元格**按顺序等分**到各组。等分的依据是这类表格的设计意图：
     每组用的是同一套子列（Tonnes/Grade/Contained），组数除得尽就等分。
     除不尽才退回「组标签中心 vs 段内单元格中心」的最优分段并给出告警。
  3. **竖着叠表头**：以单位行为基准把上方表头叠成 `Contained ozs(Moz)`。
     只叠**表题行以下**的行 —— 否则公司名 `Barrick Gold Corporation` 里的
     `Corporation` 会叠进列名，分组标签行也会把分组名混进列名。
  4. **数字归位**：交给上层按「数字中心点最近」处理（见 resource_extract.py）。

还有一条容易被忽略的护栏：**表标题行不是数据行**。
`Copper Mineral Resources(1)(3)(5) - As at December 31, 2024` 括号外有两个数字
（31、2024），按「≥2 个数字」的规则会被当成数据行吞掉，于是下一张表凭空多出一行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# 单元格：被 2 个及以上空格分隔的「单词块」。
CELL_RE = re.compile(r"\S+(?: \S+)*")

# 数字（含千分位与小数）。lookbehind 排除紧跟在字母/点号/斜杠后的，
# 免得把 "NI 43-101" 的 101、"43-101" 的 101 当成数据。
NUM_RE = re.compile(r"(?<![\w.\-/])(\d[\d,]*(?:\.\d+)?)")

PAREN_UNIT_RE = re.compile(r"\(([^()]{1,18})\)")

CATEGORY_TOKENS = {
    "MEASURED": "Measured",
    "INDICATED": "Indicated",
    "INFERRED": "Inferred",
}

# 派生列 / 合计行。它们不是独立资源类别 —— 不排除的话结果会凭空翻倍。
DERIVED_LABELS = {
    "measured and indicated": "Measured+Indicated",
    "measured & indicated": "Measured+Indicated",
    "m+i": "Measured+Indicated",
    "(m)+(i)": "Measured+Indicated",
    "total": "Total",
}

TITLE_HINT_RE = re.compile(
    r"mineral resource|resources\b|table\s*\d|estimate", re.IGNORECASE
)

# 标题行特征：出现「as at / as of / effective」或四位年份。
_HEADING_DATE_RE = re.compile(r"\b(as at|as of|effective|dated)\b|\b(19|20)\d{2}\b", re.IGNORECASE)

# 氧化物写法（Li2O / Ta2O5 / Fe2O3）不会和别的词撞车，可以直接子串匹配。
METAL_OXIDE = [("li2o", "lithium"), ("ta2o5", "tantalum"), ("fe2o3", "iron")]

# 品位基准（氧化物写法）。保留它是为了能区分「1.36% Li2O」和「1.36% Li」——
# 两者含义差一倍多，丢掉基准的品位数字没法对账。
OXIDE_RE = re.compile(r"(Li2O|Ta2O5|Fe2O3)", re.IGNORECASE)


def grade_basis(name: str) -> Optional[str]:
    m = OXIDE_RE.search(name)
    return m.group() if m else None

# 元素符号必须**整词**匹配。
# 反例：用子串匹配时 'ag' 会命中 "tonnage"（t-o-n-n-**a-g**-e），
# 于是「吨位列」被误判成「银品位列」。这是真实踩到的坑。
METAL_SYMBOL_RE = re.compile(r"(?<![a-z])(cu|au|ag|zn|ni|pb|mo)(?![a-z])", re.IGNORECASE)

METAL_WORD_RE = re.compile(
    r"\b(gold|copper|silver|lithium|tantalum|nickel|zinc|iron|lead|molybdenum)\b",
    re.IGNORECASE,
)

# 单位白名单。'M' / 'I' / 'M+I' 是分组标记不是单位，必须排除。
_UNIT_EXACT = {
    "mt", "kt", "t", "oz", "koz", "moz", "mlb", "g/t", "g/tonne",
    "ppm", "%", "000", "000 tonnes", "000 t", "000 oz", "tonnes",
}
_UNIT_HINT_RE = re.compile(r"tonne|ounce|/|^%|^ppm$|^000$|^m?lb$")


@dataclass
class Cell:
    start: int
    end: int
    text: str


@dataclass
class Column:
    start: int
    end: int
    name: str
    role: str  # tonnage | grade | contained | ignore
    unit: str = ""
    metal: Optional[str] = None
    group: Optional[str] = None

    @property
    def center(self) -> float:
        return (self.start + self.end) / 2.0


@dataclass
class TableSpec:
    """一张解析完成的表：列定义（可能带并排分组）+ 结构元信息。"""

    title: str
    commodity: Optional[str]
    header_lines: list[str]
    groups: list[tuple[Optional[str], list[Column]]] = field(default_factory=list)
    base_index: int = 0
    title_index: int = -1
    group_line: int = -1
    warnings: list[str] = field(default_factory=list)

    @property
    def all_columns(self) -> list[Column]:
        return [c for _g, cols in self.groups for c in cols]

    @property
    def is_grouped(self) -> bool:
        return len(self.groups) > 1 or (self.groups and self.groups[0][0] is not None)


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def split_cells(line: str) -> list[Cell]:
    return [Cell(m.start(), m.end(), m.group()) for m in CELL_RE.finditer(line)]


def _looks_like_unit(token: str) -> bool:
    t = token.strip().lower()
    if t in {"m", "i", "m+i", "m)+(", ""}:
        return False
    return t in _UNIT_EXACT or bool(_UNIT_HINT_RE.search(t))


def first_unit_paren(text: str) -> str:
    """取第一个**像单位**的括号内容。`Ounces(3) (000)` 必须拿到 `000` 而不是脚注 `3`。"""
    for m in PAREN_UNIT_RE.finditer(text):
        if _looks_like_unit(m.group(1)):
            return m.group(1).strip()
    return ""


# 列名关键词。用来拆「纯关键词串」的单元格，见 expand_cells。
_KEYWORD_RE = re.compile(
    r"(Tonnage|Tonnes?|Grade|Contained|Metal|Ounces?|Li2O|Ta2O5|Fe2O3|Recovery|Share|Category)",
    re.IGNORECASE,
)

# 裸单位也是列名（Barrick 的分组表头只有单位，没有列名）
_BARE_LABEL_RE = re.compile(r"(mt|kt|t|moz|koz|oz|mlb|ppm|%|li2o|ta2o5|fe2o3)", re.IGNORECASE)


def is_column_label(text: str) -> bool:
    """判断一段文字像不像「列名」。

    这条护栏专门挡住上一条**表说明行**被叠进列名 ——
    Pilbara 的表说明是 `Altura Mining Limited, February 2016 (0.4% Li2O cut-off)`，
    里面 `Limited,` 的字符区间正好压在 `Tonnes (Mt)` 上，
    不筛掉的话列名会变成 `Limited, Tonnes (Mt)`。
    """
    if _KEYWORD_RE.search(text):
        return True
    if any(_looks_like_unit(m.group(1)) for m in PAREN_UNIT_RE.finditer(text)):
        return True
    return bool(_BARE_LABEL_RE.fullmatch(text.strip()))


def _split_keyword_run(cell: Cell) -> Optional[list[Cell]]:
    """把「整格就是一个纯关键词串」的格拆开，例如 `Tonnes Grade Contained`。

    为什么需要：Barrick 表头第一组写的是 `Tonnes  Grade  Contained`（两个空格），
    后面几组却写成 `Tonnes Grade Contained`（一个空格）。粗切只认两个空格，
    于是第 2–4 组的三个子列被并成一格，列名全变成
    `Tonnes Grade Contained (Mt)` —— 三列含 "Contained"，全被判成金属量列。

    只在「整格都由关键词构成」时才拆，所以 `Deposits/Districts`、
    `ozs(Moz)`、`Contained Li2O (t)`（尾部带单位括号）都不受影响。
    """
    matches = list(_KEYWORD_RE.finditer(cell.text))
    if len(matches) < 2:
        return None
    if "".join(m.group() for m in matches) != cell.text.replace(" ", ""):
        return None
    return [
        Cell(cell.start + m.start(), cell.start + m.end(), m.group()) for m in matches
    ]


def expand_cells(line: str) -> list[Cell]:
    """先按 ≥2 空格粗切，再把「一个格里塞了多列」的格拆开。

    两种「一格多列」都要处理，都是真实版式里出现的：

      * `(000 tonnes) (g/tonne)` —— 两列之间只有 1 个空格，靠**括号单位**拆分。
        `Recovery(3) (%)` 里的 `(3)` 是脚注不是单位，所以不会被拆散。
      * `Tonnes Grade Contained` —— 靠**关键词串**拆分（见 _split_keyword_run）。
    """
    out: list[Cell] = []
    for cell in split_cells(line):
        units = [
            (m.start(), m.end(), m.group())
            for m in PAREN_UNIT_RE.finditer(cell.text)
            if _looks_like_unit(m.group(1))
        ]
        if len(units) >= 2:
            prev = 0
            for s, e, tok in units:
                prefix = cell.text[prev:s].strip()
                if prefix:
                    out.append(Cell(cell.start + prev, cell.start + s, prefix))
                out.append(Cell(cell.start + s, cell.start + e, tok))
                prev = e
            tail = cell.text[prev:].strip()
            if tail:
                out.append(Cell(cell.start + prev, cell.end, tail))
            continue

        kw = _split_keyword_run(cell)
        out.extend(kw if kw else [cell])
    return out


def numbers_outside_parens(line: str) -> list[str]:
    """取括号外的数字 —— 判断一行是不是数据行时用它。

    表头行的数字几乎都在括号里（`(000 tonnes)`、`(M)+(I)`），脚注编号也在括号里。
    先把括号内容抹掉，剩下的数字才算真正的「数据」。
    """
    return NUM_RE.findall(re.sub(r"\([^()]*\)", " ", line))


def is_separator(line: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) <= set("-=_·— ")


def is_blank(line: str) -> bool:
    return not line.strip()


def is_unit_header(line: str) -> bool:
    if sum(1 for u in PAREN_UNIT_RE.findall(line) if _looks_like_unit(u)) >= 2:
        return True
    upper = line.upper()
    return sum(1 for t in CATEGORY_TOKENS if t in upper) >= 2


def _looks_like_heading(line: str) -> bool:
    masked = re.sub(r"\([^()]*\)", " ", line)
    return bool(TITLE_HINT_RE.search(line) and _HEADING_DATE_RE.search(masked))


def is_data_row(line: str) -> bool:
    """数据行 = 以字母开头的标签 + ≥2 个括号外数字。

    两条护栏，都是踩出来的：
      * 「必须以字母开头」挡住正文里的 `104,672 m of drilling across 364 holes.`
      * 「标题行不算」挡住 `Copper Mineral Resources(1)(3)(5) - As at December 31, 2024`
        ——它括号外有 31 和 2024 两个数字，不挡就会被当成数据行，
        让下一张表凭空多出一行。
    """
    stripped = line.strip()
    if not stripped or not stripped[0].isalpha():
        return False
    if _looks_like_heading(stripped):
        return False
    return len(numbers_outside_parens(line)) >= 2


def detect_category(label: str) -> Optional[str]:
    low = label.lower().strip()
    for der, canon in DERIVED_LABELS.items():  # 派生列优先：'Measured and Indicated' 里也含 'indicated'
        if der in low:
            return canon
    for token, canon in CATEGORY_TOKENS.items():
        if token.lower() in low:
            return canon
    return None


def metal_from_text(text: str) -> Optional[str]:
    """从列名/行标签里认金属。氧化物直接匹配，元素符号必须整词匹配。"""
    low = text.lower()
    for token, metal in METAL_OXIDE:
        if token in low:
            return metal
    m = METAL_SYMBOL_RE.search(low)
    if m:
        return {
            "cu": "copper", "au": "gold", "ag": "silver",
            "zn": "zinc", "ni": "nickel", "pb": "lead", "mo": "molybdenum",
        }[m.group(1).lower()]
    m = METAL_WORD_RE.search(text)
    return m.group(1).lower() if m else None


_TONNAGE_UNIT_SET = {"mt", "kt", "t", "tonnes", "000 tonnes", "000 t"}
_CONTAINED_UNIT_SET = {"moz", "koz", "oz", "mlb", "000", "000 oz"}
_GRADE_UNIT_SET = {"g/t", "g/tonne", "ppm", "%"}


def classify_column(name: str) -> tuple[str, str, Optional[str]]:
    """列名 -> (role, unit, metal)。

    判定顺序：**先看单位，再看词**。反例：`Grade (g/tonne)` 用裸词匹配时，
    `tonnes?\\b` 会命中 g/**tonne**，于是品位列被判成吨位列，整表列位全错。
    单位括号是无歧义的，优先信它。
    """
    low = name.lower()
    unit = first_unit_paren(name)
    metal = metal_from_text(name)

    if any(k in low for k in ("share", "recovery", "jorc category", "deposit", "district")):
        return "ignore", "", None

    if re.search(r"contained|ounces?\b|\bozs?\b|\bmlb\b", low):
        return "contained", unit, metal

    u = unit.strip().lower()
    if u in _CONTAINED_UNIT_SET:
        return "contained", unit, metal
    if u in _TONNAGE_UNIT_SET:
        return "tonnage", unit, metal
    if u in _GRADE_UNIT_SET or u.startswith("%"):
        return "grade", unit, metal

    # 单位没写全时退回列名词。`(?<![/\w])` 是关键：避免 `g/tonne` 被当成 tonnes。
    if re.search(r"(?<![/\w])tonnes?\b|tonnage|\(mt\)|\(kt\)", low):
        return "tonnage", unit, metal
    if re.search(r"grade|g/t|g/tonne|ppm|\(%", low):
        return "grade", unit, metal
    return "ignore", unit, metal


# --------------------------------------------------------------------------
# 表头定位
# --------------------------------------------------------------------------
def pick_base_index(header_lines: list[str]) -> int:
    """挑「单位行」作列位基准：单位括号最多的那一行（并列取靠下的）。

    不能简单地取「第一个 is_unit_header 的行」—— Barrick 的分组标签行
    `MEASURED (M) INDICATED (I) ...` 也满足 is_unit_header（类别词 ≥2），
    但它没有列位置信息，拿来当基准会整表错位。
    """
    best, best_units = 0, -1
    for idx, line in enumerate(header_lines):
        units = sum(1 for u in PAREN_UNIT_RE.findall(line) if _looks_like_unit(u))
        if units >= best_units:
            best, best_units = idx, units
    return best


def find_groups(header_lines: list[str]) -> tuple[list[tuple[str, int, int]], int]:
    """找并排分组表头。返回 ([(类别, 标签起点, 标签终点)], 所在行号)。"""
    for idx, line in enumerate(header_lines):
        hits: list[tuple[str, int, int]] = []
        for cell in split_cells(line):
            low = cell.text.lower().strip()
            canon = None
            for token, name in CATEGORY_TOKENS.items():
                if token.lower() in low:
                    canon = name
                    break
            if canon is None and ("(m)+(i)" in low or low == "m+i"):
                canon = "Measured+Indicated"
            if canon:
                hits.append((canon, cell.start, cell.end))
        if len(hits) >= 2:
            return hits, idx
    return [], -1


def find_title(header_lines: list[str]) -> tuple[str, Optional[str], int]:
    """表题取**最靠近数据行**的标题行，避免抓到上一张表残留的说明文字。"""
    for idx in range(len(header_lines) - 1, -1, -1):
        line = header_lines[idx]
        if TITLE_HINT_RE.search(line) and not is_unit_header(line):
            return line.strip(), metal_from_text(line), idx
    if header_lines:
        first = header_lines[0].strip()
        return first, metal_from_text(first), 0
    return "", None, -1


# --------------------------------------------------------------------------
# 并排分组的列归属
# --------------------------------------------------------------------------
def partition_cells(
    cells: list[Cell], groups: list[tuple[str, int, int]]
) -> tuple[list[list[Cell]], Optional[str]]:
    """把有序的单位单元格分给各组。返回 (分组->单元格, 告警)。

    首选**等分**：并排表的设计意图就是每组用同一套子列（Tonnes/Grade/Contained），
    所以组数除得尽就等分 —— Barrick 金表 12 格 4 组、铜表 9 格 3 组都整除。

    除不尽才退回「组标签中心 vs 段内单元格中心均值」的最优连续分段。
    这时说明版式不规则，必须给告警而不是假装算对了。
    """
    n, k = len(cells), len(groups)
    if k == 0 or n == 0:
        return [], None
    if k == 1:
        return [cells], None
    if k > n:
        return [[] for _ in groups], f"分组数({k})多于单位单元格数({n})，无法分配列"

    if n % k == 0:
        size = n // k
        return [cells[i * size : (i + 1) * size] for i in range(k)], None

    from itertools import combinations

    label_centers = [(s + e) / 2.0 for _c, s, e in groups]
    centers = [c.center for c in cells]
    best_cuts, best_cost = None, None
    for cuts in combinations(range(1, n), k - 1):
        bounds = (0,) + cuts + (n,)
        cost = 0.0
        for gi in range(k):
            seg = centers[bounds[gi] : bounds[gi + 1]]
            cost += abs(sum(seg) / len(seg) - label_centers[gi])
        if best_cost is None or cost < best_cost:
            best_cuts, best_cost = cuts, cost

    bounds = (0,) + best_cuts + (n,)
    parts = [cells[bounds[i] : bounds[i + 1]] for i in range(k)]
    warn = f"单位单元格({n})不能被分组数({k})整除，已按标签位置近似分组，建议人工核对"
    return parts, warn


def stack_columns(
    header_lines: list[str],
    base_index: int,
    stack_idx: list[int],
    seed_cells: Optional[list[Cell]] = None,
) -> list[Column]:
    """以 base_index（单位行）为基准列，把 stack_idx 里的行竖着叠成列名。"""
    base_cells = seed_cells if seed_cells is not None else expand_cells(header_lines[base_index])

    columns: list[Column] = []
    for cell in base_cells:
        parts = [cell.text]
        for idx in stack_idx:
            if idx == base_index:
                continue
            for cand in expand_cells(header_lines[idx]):
                if cand.start < cell.end and cand.end > cell.start:
                    # 只叠真正的列名，避免把上一行的表说明句叠进来
                    if is_column_label(cand.text) and cand.text not in parts:
                        parts.insert(0, cand.text)
                    break
        name = " ".join(parts)
        role, unit, metal = classify_column(name)
        columns.append(
            Column(start=cell.start, end=cell.end, name=name, role=role, unit=unit, metal=metal)
        )
    return columns


def build_table_spec(header_lines: list[str]) -> TableSpec:
    """把一堆表头行编译成 TableSpec。"""
    title, commodity, title_index = find_title(header_lines)
    base_index = pick_base_index(header_lines)
    groups, group_line = find_groups(header_lines)
    warnings: list[str] = []

    # 只叠「表题行以下」的表头。否则公司名里的 'Corporation' 会被叠进列名，
    # `Barrick Gold Corporation` + `(Mt)` 会变成 'Corporation (Mt)'。
    low = [i for i in range(len(header_lines)) if i > title_index]

    if groups and group_line >= 0:
        stack_idx = [i for i in low if i != group_line]
        cells = expand_cells(header_lines[base_index])
        parts, warn = partition_cells(cells, groups)
        if warn:
            warnings.append(warn)
        spec_groups: list[tuple[Optional[str], list[Column]]] = []
        for (canon, _s, _e), group_cells in zip(groups, parts):
            cols = stack_columns(header_lines, base_index, stack_idx, seed_cells=group_cells)
            for c in cols:
                c.group = canon
            spec_groups.append((canon, cols))
        return TableSpec(
            title, commodity, header_lines, spec_groups,
            base_index, title_index, group_line, warnings,
        )

    cols = stack_columns(header_lines, base_index, low)
    return TableSpec(
        title, commodity, header_lines, [(None, cols)],
        base_index, title_index, group_line, warnings,
    )


# --------------------------------------------------------------------------
# 表块切分
# --------------------------------------------------------------------------
def _is_header_like(line: str) -> bool:
    if is_blank(line) or is_separator(line) or len(line) > 120:
        return False
    # 全大写的长行通常是页眉/页脚水印（fixture PDF 每页都有），不是表头。
    # 判据里必须带上「没有连续空格」—— Barrick 的分组表头
    # `MEASURED (M)    INDICATED (I)    INFERRED` 也全大写，但它靠列间距排版，
    # 只看大小写会把整行分组表头当水印丢掉，分组信息就没了。
    stripped = line.strip()
    if len(stripped) > 40 and stripped.upper() == stripped and "  " not in stripped:
        return False
    if numbers_outside_parens(line) and not TITLE_HINT_RE.search(line):
        return False
    return any(ch.isalpha() for ch in line)


def find_table_blocks(lines: list[str]) -> list[tuple[list[str], list[str]]]:
    """切出 (表头行, 数据行) 块。

    以「单位行」为锚点：向上最多回看 5 行收集表头（遇空行/水印即停，
    防止吞掉上一张表的脚注），向下收集连续数据行（容忍 1–2 行分隔线/空行）。
    """
    blocks: list[tuple[list[str], list[str]]] = []
    i, n = 0, len(lines)
    while i < n:
        if not is_unit_header(lines[i]):
            i += 1
            continue

        # 向上收集表头，**遇到表题行即停**。
        # 表题（`Table 2 - ... Estimate, historical`）之上就不属于本表了 ——
        # 不设这条边界，回看会一路吃进上一张表的表说明行，
        # 那些文字的字符区间会压到本表列名上，叠出 `Limited, Tonnes (Mt)` 这种列名。
        start, steps = i, 0
        while start > 0 and steps < 5 and _is_header_like(lines[start - 1]):
            start -= 1
            steps += 1
            if TITLE_HINT_RE.search(lines[start]):
                break
        header_lines = lines[start : i + 1]

        j, rows, gap = i + 1, [], 0
        while j < n and gap <= 2:
            line = lines[j]
            if is_separator(line) or is_blank(line):
                gap += 1
                j += 1
                continue
            if is_data_row(line):
                rows.append(line)
                gap = 0
                j += 1
                continue
            break

        if rows:
            blocks.append((header_lines, rows))
        i = max(j, i + 1)
    return blocks
