"""工具模块

导出所有可用工具供Agent使用
"""
from .order_tools import (
    query_order_status,
    approve_refund,
    get_order_detail,
)

from .knowledge_tools import (
    search_faq,
    search_policy,
)

from .system_tools import (
    get_current_time,
    escalate_to_human,
    log_interaction,
    validate_user_input,
)

# 所有工具列表，便于Agent加载
ALL_TOOLS = [
    # 订单相关工具
    query_order_status,
    approve_refund,
    get_order_detail,
    # 知识库工具
    search_faq,
    search_policy,
    # 系统工具
    get_current_time,
    escalate_to_human,
    log_interaction,
    validate_user_input,
]

# 按类别分组的工具
ORDER_TOOLS = [
    query_order_status,
    approve_refund,
    get_order_detail,
]

KNOWLEDGE_TOOLS = [
    search_faq,
    search_policy,
]

SYSTEM_TOOLS = [
    get_current_time,
    escalate_to_human,
    log_interaction,
    validate_user_input,
]

__all__ = [
    # 订单工具
    "query_order_status",
    "approve_refund",
    "get_order_detail",
    # 知识库工具
    "search_faq",
    "search_policy",
    # 系统工具
    "get_current_time",
    "escalate_to_human",
    "log_interaction",
    "validate_user_input",
    # 工具集合
    "ALL_TOOLS",
    "ORDER_TOOLS",
    "KNOWLEDGE_TOOLS",
    "SYSTEM_TOOLS",
]
