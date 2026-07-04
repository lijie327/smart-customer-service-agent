"""知识库相关工具集"""
from typing import List, Dict, Any, Optional
from langchain.tools import tool

from backend.faq_data import FAQ_ITEMS, FAQ_POLICY

# 兼容旧引用的别名
FAQ_DATA = FAQ_ITEMS
POLICY_DATA = FAQ_POLICY


@tool
def search_faq(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """
    在FAQ知识库中检索相关问题

    Args:
        query: 用户查询内容
        top_k: 返回最相关的top_k个结果，默认为3

    Returns:
        相关的FAQ列表，每个包含question、answer和category
    """
    # 简单实现：基于关键词匹配
    # 实际场景应该使用FAISS向量检索

    query_lower = query.lower()
    results = []

    # 计算简单的关键词匹配分数
    for faq in FAQ_DATA:
        score = 0
        question_words = set(faq["question"].lower())
        answer_words = set(faq["answer"].lower())
        query_words = set(query_lower)

        # 计算交集
        score += len(question_words & query_words)
        score += len(answer_words & query_words)

        # 完全匹配加分
        if faq["question"] in query or query in faq["question"]:
            score += 10

        # 类别匹配加分
        if faq["category"] in query:
            score += 5

        if score > 0:
            results.append({
                **faq,
                "score": score
            })

    # 按分数排序，返回top_k
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


@tool
def search_policy(policy_type: str) -> Optional[Dict[str, Any]]:
    """
    搜索特定类型的政策信息

    Args:
        policy_type: 政策类型，如"退货政策"、"保修政策"、"配送政策"

    Returns:
        政策详情字典，如果未找到则返回None
    """
    # 尝试精确匹配
    if policy_type in POLICY_DATA:
        return POLICY_DATA[policy_type]

    # 尝试模糊匹配
    policy_type_lower = policy_type.lower()
    for key, policy in POLICY_DATA.items():
        if policy_type_lower in key.lower() or key.lower() in policy_type_lower:
            return policy

    # 根据关键词推断
    keyword_map = {
        "退货": "退货政策",
        "退款": "退货政策",
        "退换": "退货政策",
        "保修": "保修政策",
        "维修": "保修政策",
        "质保": "保修政策",
        "配送": "配送政策",
        "物流": "配送政策",
        "运费": "配送政策",
    }

    for keyword, policy_name in keyword_map.items():
        if keyword in policy_type_lower:
            return POLICY_DATA[policy_name]

    return None


# 导出所有工具
__all__ = [
    "search_faq",
    "search_policy",
]
