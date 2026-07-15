"""统一混合检索（Hybrid Retrieval）— RAG 检索层

将原来并存的两套 FAQ 检索（rag.FAQProcessor 的 FAISS 向量检索、
tools.search_faq 的关键词检索）统一为单一检索入口：

    1. 向量召回（FAISS 余弦）  —— 语义匹配，能够理解同义改写
    2. 关键词召回（BM25）       —— 精确词面匹配，补足向量对专名/编号的短板
    3. RRF 融合（Reciprocal Rank Fusion） —— 融合两路排序，去除单一召回偏差
    4. 检索置信度 + 引用溯源    —— 供上层做「低置信转人工」与「答案可解释」

设计要点（面试可讲）：
- 向量与关键词各有所长，RRF 在**不引入额外权重超参**的情况下融合两路排序，
  比简单加权更鲁棒，对召回顺序改动小、易解释。
- confidence 直接复用向量余弦相似度（0~1，天然可解释），用于阈值判定，
  避免给融合分数再套一层不可解释的归一化。
- 全部复用已有 FAQProcessor 的向量索引，BM25 为轻量纯 Python 实现、零额外依赖。
"""
from __future__ import annotations

import math
import re
from typing import List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # 避免导入时拉起 numpy/faiss，仅在类型检查时依赖
    from backend.rag import FAQProcessor


# --------------------------------------------------------------------------- #
# 轻量 BM25（Okapi BM25，纯 Python 实现，零额外依赖）
# --------------------------------------------------------------------------- #
class BM25:
    """Okapi BM25 倒排打分，用于关键词召回支路。"""

    def __init__(self, docs: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = docs
        self.N = len(docs)
        self.avgdl = (sum(len(d) for d in docs) / self.N) if self.N else 0.0

        # 文档频率 df
        df: Dict[str, int] = {}
        for d in docs:
            for w in set(d):
                df[w] = df.get(w, 0) + 1
        # idf（加一平滑，避免未登录词 idf 为负）
        self.idf = {
            w: math.log((self.N - freq + 0.5) / (freq + 0.5) + 1.0)
            for w, freq in df.items()
        }

    def get_scores(self, query: List[str]) -> List[float]:
        scores = [0.0] * self.N
        for q in query:
            idf = self.idf.get(q)
            if idf is None:
                continue
            for i, d in enumerate(self.docs):
                freq = d.count(q)
                if freq == 0:
                    continue
                denom = freq + self.k1 * (1 - self.b + self.b * len(d) / (self.avgdl or 1.0))
                scores[i] += idf * freq * (self.k1 + 1) / denom
        return scores


def _tokenize(text: str) -> List[str]:
    """中英文混合分词：中文按字切分（兼顾专名/短语），英文数字按词切分。"""
    text = (text or "").lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    tokens += re.findall(r"[一-鿿]", text)  # CJK 单字
    return tokens


# --------------------------------------------------------------------------- #
# 统一混合检索器
# --------------------------------------------------------------------------- #
class HybridFAQRetriever:
    """FAQ 混合检索器：向量 + BM25 + RRF 融合。"""

    def __init__(self, faq_processor: FAQProcessor):
        """
        Args:
            faq_processor: 已有的 FAISS 向量检索器（提供向量召回与索引管理）
        """
        self.faq_processor = faq_processor
        self.faq_data = faq_processor.faq_data
        self.faq_by_question: Dict[str, Dict[str, Any]] = {
            f["question"]: f for f in self.faq_data
        }
        self._build_bm25()

    # ----------------------------- 内部方法 ----------------------------- #
    def _build_bm25(self) -> None:
        docs = [
            _tokenize(f["question"] + " " + f.get("answer", ""))
            for f in self.faq_data
        ]
        self.bm25 = BM25(docs)

    def _vector_search(self, query: str, k: int) -> List[Dict[str, Any]]:
        """向量支路：直接复用 FAQProcessor 的 FAISS 余弦召回。"""
        return self.faq_processor.search(query, k=k)

    def _keyword_search(self, query: str, k: int) -> List[Dict[str, Any]]:
        """关键词支路：BM25 召回。"""
        if not self.faq_data:
            return []
        kw_scores = self.bm25.get_scores(_tokenize(query))
        order = sorted(range(len(kw_scores)), key=lambda i: kw_scores[i], reverse=True)[:k]
        max_kw = max((kw_scores[i] for i in order), default=0.0)
        results = []
        for i in order:
            faq = self.faq_data[i]
            results.append({
                "question": faq["question"],
                "answer": faq["answer"],
                "category": faq.get("category", "其他"),
                "keyword_score": round(kw_scores[i] / max_kw, 4) if max_kw > 0 else 0.0,
            })
        return results

    @staticmethod
    def _rrf_fuse(vector_rank: Dict[str, int], keyword_rank: Dict[str, int],
                  k: int = 60) -> List[str]:
        """Reciprocal Rank Fusion：融合两路排序，不依赖分数量纲。"""
        fused: Dict[str, float] = {}
        for ranking in (vector_rank, keyword_rank):
            for rank, q in enumerate(ranking):
                fused[q] = fused.get(q, 0.0) + 1.0 / (k + rank + 1)
        return sorted(fused, key=lambda q: fused[q], reverse=True)

    # ----------------------------- 对外接口 ----------------------------- #
    def search(self, query: str, k: int = 3, top_candidates: int = 10) -> List[Dict[str, Any]]:
        """
        混合检索：向量 + BM25 + RRF 融合，返回带置信度与引用信息的 Top-k。

        Returns:
            [
              {
                "question": str, "answer": str, "category": str,
                "score": float,          # 向量余弦（兼容旧阈值逻辑）
                "vector_score": float,   # 向量余弦
                "keyword_score": float,  # BM25 归一化（0~1）
                "rrf_score": float,      # RRF 融合分
                "confidence": float,     # 检索置信度（= 向量余弦，0~1）
              }, ...
            ]
        """
        total = len(self.faq_data)
        if total == 0:
            return []

        # 1) 两路召回
        vec_top = self._vector_search(query, k=min(top_candidates, total))
        kw_top = self._keyword_search(query, k=min(top_candidates, total))

        # 2) 构造排序映射
        vector_rank = [r["question"] for r in vec_top]
        keyword_rank = [r["question"] for r in kw_top]

        # 3) RRF 融合 → 候选排序
        ranked = self._rrf_fuse(vector_rank, keyword_rank)[:k]

        # 4) 组装结果（带入两路分数）
        vec_map = {r["question"]: r for r in vec_top}
        kw_map = {r["question"]: r for r in kw_top}
        results: List[Dict[str, Any]] = []
        for q in ranked:
            faq = self.faq_by_question.get(q)
            if faq is None:
                continue
            vscore = vec_map[q]["score"] if q in vec_map else 0.0
            kscore = kw_map[q]["keyword_score"] if q in kw_map else 0.0
            results.append({
                "question": faq["question"],
                "answer": faq["answer"],
                "category": faq.get("category", "其他"),
                "score": vscore,
                "vector_score": vscore,
                "keyword_score": kscore,
                "rrf_score": round(
                    sum(
                        1.0 / (60 + idx)
                        for idx, src in enumerate([vector_rank, keyword_rank])
                        if q in src
                    ), 5
                ),
                "confidence": vscore,
            })
        return results

    # --------------------------- 增量 / 管理 --------------------------- #
    def add_faq(self, question: str, answer: str, category: str = "其他") -> bool:
        added = self.faq_processor.add_faq(question, answer, category)
        if added:
            self._reindex()
        return added

    def add_faqs_batch(self, items: List[Dict[str, str]]) -> int:
        added = self.faq_processor.add_faqs_batch(items)
        if added > 0:
            self._reindex()
        return added

    def _reindex(self) -> None:
        """向量索引由 FAQProcessor 重建后，同步刷新本地 BM25 与映射。"""
        self.faq_data = self.faq_processor.faq_data
        self.faq_by_question = {f["question"]: f for f in self.faq_data}
        self._build_bm25()

    def get_stats(self) -> Dict[str, Any]:
        stats = self.faq_processor.get_stats()
        stats["retrieval"] = "hybrid(vector + bm25 + rrf)"
        return stats


# --------------------------------------------------------------------------- #
# 进程内默认检索器（供 tools.search_faq 等无状态工具访问）
# --------------------------------------------------------------------------- #
_default_retriever: Optional[HybridFAQRetriever] = None


def set_default_retriever(retriever: HybridFAQRetriever) -> None:
    global _default_retriever
    _default_retriever = retriever


def get_default_retriever() -> Optional[HybridFAQRetriever]:
    return _default_retriever
