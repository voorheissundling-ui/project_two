"""一个够用的 BM25 实现 —— 中文友好，零依赖。

为什么自己写而不用 rank_bm25 / jieba：
  1. `rank_bm25` 是纯英文分词，中文会被整句当成一个 token，直接失效；
  2. `jieba` 会带进来一个几 MB 的词典，而这里只是个新闻检索的排序器；
  3. 自己写能顺手把「时间衰减」和「字段加权」做进去（见 `FieldedBM25`），
     这两件事恰恰是**新闻检索**和普通文档检索最大的差别 —— 昨天的新闻和两年前的新闻
     即使词频一样，也不该排在同一个位置。

中文分词用 **字符二元组（bigram）**：不切词、不依赖词典，召回上对专名反而更好
（「锂辉石」「Pilgangoora」这类词不会被切错）。英文/数字按词切并小写化。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Sequence

_CJK = re.compile(r"[一-鿿]")
_ASCII_TOKEN = re.compile(r"[a-z0-9][a-z0-9\.\-_]*")
_WORD_SPLIT = re.compile(r"[^a-z0-9一-鿿]+")


def tokenize(text: str) -> list[str]:
    """英文按词、中文按字符 bigram。返回小写 token 列表。"""
    if not text:
        return []
    text = text.lower()
    tokens: list[str] = []

    # 英文/数字：直接按词切
    for chunk in re.split(r"([一-鿿]+)", text):
        if not chunk:
            continue
        if _CJK.search(chunk):
            # 中文段落：去噪后取 bigram，并保留单字（单字查询如「锂」也要能命中）
            cleaned = "".join(ch for ch in chunk if _CJK.match(ch))
            tokens.extend(cleaned)  # 单字
            tokens.extend(cleaned[i : i + 2] for i in range(len(cleaned) - 1))  # bigram
        else:
            tokens.extend(_ASCII_TOKEN.findall(chunk))
    return tokens


@dataclass
class _Doc:
    raw: dict
    tokens: list[str]
    length: int
    tf: Counter = field(default_factory=Counter)


class FieldedBM25:
    """带字段权重的 BM25。

    用法：
        idx = FieldedBM25(docs, weights={"title": 3.0, "summary_zh": 1.5, "body": 1.0})
        idx.search("Pilbara 锂价", top_k=5)

    字段权重解决的是「标题里出现 Pilbara」比「正文里顺带提了一次」重要得多。
    """

    def __init__(
        self,
        docs: Sequence[dict],
        field_weights: dict[str, float],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.k1 = k1
        self.b = b
        self.field_weights = field_weights
        self._docs: list[_Doc] = []

        for raw in docs:
            tokens: list[str] = []
            for name, weight in field_weights.items():
                value = raw.get(name) or ""
                if isinstance(value, (list, tuple)):
                    value = " ".join(str(v) for v in value)
                field_tokens = tokenize(str(value))
                # 字段权重通过「重复词项」实现 —— 简单，且与 BM25 的 tf 语义天然一致
                repeat = max(1, int(round(weight)))
                tokens.extend(field_tokens * repeat)
            self._docs.append(_Doc(raw=raw, tokens=tokens, length=len(tokens)))

        self._build_index()

    def _build_index(self) -> None:
        self.avg_len = (
            sum(d.length for d in self._docs) / len(self._docs) if self._docs else 1.0
        )
        self.df: Counter = Counter()
        for doc in self._docs:
            doc.tf = Counter(doc.tokens)
            for term in doc.tf:
                self.df[term] += 1
        self.n = len(self._docs)

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        # BM25 的 +0.5 平滑，避免 df == n 时 idf 变成负数
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def score(self, query: str, doc: _Doc) -> float:
        total = 0.0
        for term in tokenize(query):
            tf = doc.tf.get(term, 0)
            if not tf:
                continue
            denom = tf + self.k1 * (1 - self.b + self.b * doc.length / self.avg_len)
            total += self._idf(term) * (tf * (self.k1 + 1)) / denom
        return total

    def search(self, query: str, top_k: int = 10) -> list[tuple[float, dict]]:
        scored = [(self.score(query, d), d.raw) for d in self._docs]
        scored = [s for s in scored if s[0] > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]


def time_decay(published_iso: str, now_iso: str, half_life_days: float = 180.0) -> float:
    """按发布时间的指数衰减系数，半衰期默认 180 天。

    没有这一项的话，「近 7 天锂价变化」这类问题会被两年前的高词频文章污染 ——
    BM25 只看词频，完全不知道今天几号。
    """
    from datetime import datetime, timezone

    def parse(s: str) -> datetime:
        s = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return datetime.now(timezone.utc)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    delta_days = (parse(now_iso) - parse(published_iso)).total_seconds() / 86400.0
    if delta_days < 0:
        return 1.0
    return 0.5 ** (delta_days / half_life_days)
