"""RAG (Retrieval Augmented Generation) 系统

实现FAQ知识库的向量化检索
"""
import json
import os
from typing import List, Dict, Any, Optional
import numpy as np

from backend.faq_data import FAQ_ITEMS


class FAQProcessor:
    """FAQ处理器 - 基于FAISS的向量检索"""

    def __init__(self, embeddings, index_path: str = None):
        self.embeddings = embeddings
        self.index_path = index_path
        self.faiss = None
        self.index = None
        self.faq_data = []

        self.preset_faqs = FAQ_ITEMS

        self._init_index()

    def _init_index(self):
        try:
            import faiss
            self.faiss = faiss
        except ImportError:
            raise ImportError("请安装faiss-cpu: pip install faiss-cpu")

        if self.index_path and os.path.exists(f"{self.index_path}.index"):
            self._load_index()
        else:
            self._build_index_from_preset()

    def _build_index_from_preset(self):
        self.faq_data = self.preset_faqs.copy()
        self._build_index()

    def _build_index(self):
        if not self.faq_data:
            return

        questions = [faq["question"] for faq in self.faq_data]
        embeddings = []
        batch_size = 25
        for i in range(0, len(questions), batch_size):
            batch = questions[i:i + batch_size]
            batch_embeddings = self.embeddings.embed_documents(batch)
            embeddings.extend(batch_embeddings)

        embeddings_array = np.array(embeddings, dtype=np.float32)
        dimension = embeddings_array.shape[1]
        self.index = self.faiss.IndexFlatIP(dimension)
        self.faiss.normalize_L2(embeddings_array)
        self.index.add(embeddings_array)

        if self.index_path:
            self._save_index()

    def _save_index(self):
        if self.index and self.index_path:
            self.faiss.write_index(self.index, f"{self.index_path}.index")
            with open(f"{self.index_path}.json", "w", encoding="utf-8") as f:
                json.dump(self.faq_data, f, ensure_ascii=False, indent=2)

    def _load_index(self):
        if self.index_path:
            self.index = self.faiss.read_index(f"{self.index_path}.index")
            with open(f"{self.index_path}.json", "r", encoding="utf-8") as f:
                self.faq_data = json.load(f)

    def search(self, query: str, k: int = 3) -> List[Dict[str, Any]]:
        if not self.index or not self.faq_data:
            return []

        query_embedding = self.embeddings.embed_query(query)
        query_array = np.array([query_embedding], dtype=np.float32)
        self.faiss.normalize_L2(query_array)

        k = min(k, len(self.faq_data))
        scores, indices = self.index.search(query_array, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx >= 0:
                faq = self.faq_data[idx]
                results.append({
                    "question": faq["question"],
                    "answer": faq["answer"],
                    "category": faq.get("category", "其他"),
                    "score": float(score)
                })
        return results

    def add_faq(self, question: str, answer: str, category: str = "其他") -> bool:
        existing = self.search(question, k=1)
        if existing and existing[0]["score"] > 0.95:
            return False

        self.faq_data.append({"question": question, "answer": answer, "category": category})
        self._build_index()
        return True

    def add_faqs_batch(self, items: List[Dict[str, str]]) -> int:
        """
        批量添加 FAQ，只重建一次索引。

        Args:
            items: FAQ 列表，每项含 question/answer/category

        Returns:
            实际新增的数量（跳过重复项）
        """
        added = 0
        for item in items:
            question = item.get("question", "")
            answer = item.get("answer", "")
            category = item.get("category", "其他")
            if not question or not answer:
                continue
            # 批量模式下跳过逐条去重检查，统一重建后由向量去重
            existing = self.search(question, k=1)
            if existing and existing[0]["score"] > 0.95:
                continue
            self.faq_data.append({"question": question, "answer": answer, "category": category})
            added += 1

        if added > 0:
            self._build_index()
        return added

    def get_stats(self) -> Dict[str, Any]:
        categories = {}
        for faq in self.faq_data:
            category = faq.get("category", "其他")
            categories[category] = categories.get(category, 0) + 1

        return {
            "total_faqs": len(self.faq_data),
            "categories": categories,
            "index_dimension": self.index.d if self.index else 0
        }