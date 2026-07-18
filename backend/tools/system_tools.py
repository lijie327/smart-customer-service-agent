"""系统工具集"""
import uuid
from datetime import datetime
from typing import Dict, Any
from langchain.tools import tool


@tool
def get_current_time() -> str:
    """
    获取当前系统时间

    Returns:
        当前时间的字符串表示，格式为YYYY-MM-DD HH:MM:SS
    """
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")


@tool
def escalate_to_human(reason: str, priority: str = "normal") -> Dict[str, Any]:
    """
    将问题升级给人工客服处理

    Args:
        reason: 升级原因，说明为什么需要人工处理
        priority: 优先级，可选值：low/normal/high/urgent，默认为normal

    Returns:
        包含升级结果、工单ID和原因的字典
    """
    # 生成唯一的工单ID
    ticket_id = f"TKT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:8].upper()}"

    # 验证优先级参数
    valid_priorities = ["low", "normal", "high", "urgent"]
    if priority not in valid_priorities:
        priority = "normal"

    # 根据优先级设置预计响应时间
    response_times = {
        "low": "24小时内",
        "normal": "4小时内",
        "high": "1小时内",
        "urgent": "15分钟内"
    }

    return {
        "escalated": True,
        "ticket_id": ticket_id,
        "reason": reason,
        "priority": priority,
        "estimated_response_time": response_times[priority],
        "status": "已创建",
        "message": f"已成功创建人工客服工单 {ticket_id}，客服将在{response_times[priority]}内与您联系",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


# 导出所有工具
__all__ = [
    "get_current_time",
    "escalate_to_human",
]
