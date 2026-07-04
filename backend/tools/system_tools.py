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


@tool
def log_interaction(session_id: str, user_message: str, bot_response: str) -> Dict[str, Any]:
    """
    记录用户与机器人的交互日志

    Args:
        session_id: 会话ID
        user_message: 用户消息
        bot_response: 机器人回复

    Returns:
        记录结果的字典
    """
    # 实际场景应该写入数据库或日志系统
    log_entry = {
        "session_id": session_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_message": user_message,
        "bot_response": bot_response,
        "status": "logged"
    }

    # 这里可以添加写入数据库或文件的逻辑
    # 例如：db.interaction_logs.insert(log_entry)

    return {
        "success": True,
        "message": "交互日志已记录",
        "log_id": str(uuid.uuid4())
    }


@tool
def validate_user_input(input_text: str, input_type: str = "general") -> Dict[str, Any]:
    """
    验证用户输入的合法性和安全性

    Args:
        input_text: 用户输入的文本
        input_type: 输入类型，如general/order_id/phone等

    Returns:
        验证结果字典，包含is_valid和message
    """
    import re

    # 检查是否为空
    if not input_text or not input_text.strip():
        return {
            "is_valid": False,
            "message": "输入不能为空"
        }

    # 检查长度
    if len(input_text) > 1000:
        return {
            "is_valid": False,
            "message": "输入内容过长，请限制在1000字以内"
        }

    # 检查是否包含危险字符（简单的XSS防护）
    dangerous_patterns = [
        r"<script.*?>.*?</script>",
        r"javascript:",
        r"on\w+\s*=",
        r"<iframe",
        r"<object",
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, input_text, re.IGNORECASE):
            return {
                "is_valid": False,
                "message": "输入包含不安全的内容"
            }

    # 特定类型的验证
    if input_type == "order_id":
        # 订单ID应该是字母数字组合
        if not re.match(r"^[A-Z0-9]{10,20}$", input_text.upper()):
            return {
                "is_valid": False,
                "message": "订单ID格式不正确，应为10-20位字母数字组合"
            }
    elif input_type == "phone":
        # 手机号验证
        if not re.match(r"^1[3-9]\d{9}$", input_text):
            return {
                "is_valid": False,
                "message": "手机号格式不正确"
            }

    return {
        "is_valid": True,
        "message": "输入验证通过",
        "sanitized_input": input_text.strip()
    }


# 导出所有工具
__all__ = [
    "get_current_time",
    "escalate_to_human",
    "log_interaction",
    "validate_user_input",
]
