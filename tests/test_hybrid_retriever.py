"""离线冒烟测试：验证统一混合检索（向量 + BM25 + RRF）逻辑与输出契约。

不依赖网络 / FAISS / DashScope：用 FakeFAQProcessor 模拟向量召回支路，
仅验证新增的 BM25 + RRF 融合、置信度、引用溯源等核心逻辑。
"""
import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.faq_data import FAQ_ITEMS
from backend.rag_retriever import HybridFAQRetriever, BM25, _tokenize


# --------------------------- Fake 向量检索器 --------------------------- #
def _fake_embed(text: str):
    """确定性字符袋向量（仅用于离线测试，使相似中文获得高余弦）。

    仅对 CJK 字符编码，便于用「纯英文无关查询」稳定触发低置信度分支。
    """
    dim = 64
    vec = [0.0] * dim
    for ch in text:
        if "一" <= ch <= "鿿":
            vec[ord(ch) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


class FakeFAQProcessor:
    """模拟 FAQProcessor 的 .faq_data / .search / .get_stats 接口。"""

    def __init__(self, faq_data):
        self.faq_data = list(faq_data)

    def search(self, query, k=3):
        qv = _fake_embed(query)
        scored = []
        for i, faq in enumerate(self.faq_data):
            dv = _fake_embed(faq["question"])
            dot = sum(a * b for a, b in zip(qv, dv))
            scored.append((dot, i))
        scored.sort(reverse=True)
        out = []
        for dot, i in scored[:k]:
            faq = self.faq_data[i]
            out.append({
                "question": faq["question"],
                "answer": faq["answer"],
                "category": faq.get("category", "其他"),
                "score": dot,
            })
        return out

    def add_faqs_batch(self, items):
        for it in items:
            self.faq_data.append({
                "question": it["question"], "answer": it["answer"],
                "category": it.get("category", "其他"),
            })
        return len(items)

    def get_stats(self):
        return {"total_faqs": len(self.faq_data), "index_dimension": 64}


# ------------------------------- 测试 ------------------------------- #
def test_bm25_runs():
    docs = [_tokenize("如何申请退款"), _tokenize("蓝牙连接不上怎么办")]
    bm25 = BM25(docs)
    scores = bm25.get_scores(_tokenize("申请退款"))
    assert len(scores) == 2
    assert scores[0] > 0  # 第一条相关


def test_rrf_favors_both_methods():
    # 向量把 A 排第一、关键词把 B 排第一 → RRF 应让两路都靠前的项胜出
    vector_rank = ["A", "B", "C"]
    keyword_rank = ["B", "A", "D"]
    fused = HybridFAQRetriever._rrf_fuse(vector_rank, keyword_rank)
    assert fused[0] in ("A", "B")          # 两路都靠前
    assert "C" not in fused[:2] or "D" not in fused[:2]


def test_search_returns_expected_shape():
    proc = FakeFAQProcessor(FAQ_ITEMS)
    retriever = HybridFAQRetriever(proc)

    results = retriever.search("蓝牙连接不上怎么办", k=3)
    assert results, "检索不应为空"
    top = results[0]
    # 输出契约（供上层 confidence / 引用溯源使用）
    for key in ("question", "answer", "category", "score",
                "vector_score", "keyword_score", "rrf_score", "confidence"):
        assert key in top, f"缺少字段 {key}"
    assert 0.0 <= top["confidence"] <= 1.0
    # 该查询应命中「蓝牙连接不上」相关 FAQ
    assert "蓝牙" in top["question"]


def test_low_confidence_query():
    proc = FakeFAQProcessor(FAQ_ITEMS)
    retriever = HybridFAQRetriever(proc)
    # 与知识库无关的查询（纯英文，CJK 编码为 0）→ 低置信
    results = retriever.search("apple blockchain metaverse quantum", k=3)
    assert not results or results[0]["confidence"] < 0.35


def test_incremental_add_rebuilds_bm25():
    proc = FakeFAQProcessor(FAQ_ITEMS)
    retriever = HybridFAQRetriever(proc)
    before = retriever.get_stats()["total_faqs"]
    retriever.add_faqs_batch([
        {"question": "如何开具增值税专用发票", "answer": "联系财务", "category": "发票"}
    ])
    after = retriever.get_stats()["total_faqs"]
    assert after == before + 1
    # 新增项应可被检索到
    hit = retriever.search("增值税专用发票怎么开", k=3)
    assert any("增值税" in r["question"] for r in hit)


if __name__ == "__main__":
    test_bm25_runs()
    test_rrf_favors_both_methods()
    test_search_returns_expected_shape()
    test_low_confidence_query()
    test_incremental_add_rebuilds_bm25()
    print("✓ 全部混合检索冒烟测试通过")
