from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class AgentType(str, Enum):
    """Agent类型枚举"""

    ROUTER = "router"
    REFUND = "refund"
    TECH_SUPPORT = "tech_support"
    ORDER_QUERY = "order_query"
    GENERAL = "general"


class TicketRequest(BaseModel):
    """用户请求模型"""

    user_message: str = Field(..., description="用户消息内容")
    session_id: str = Field(..., description="会话ID")
    user_id: str = Field(..., description="用户ID")


class ToolCall(BaseModel):
    """工具调用模型"""

    tool_name: str = Field(..., description="工具名称")
    params: Dict[str, Any] = Field(default_factory=dict, description="工具参数")
    result: Optional[Any] = Field(None, description="工具执行结果")


class TicketResponse(BaseModel):
    """响应模型"""

    reply: str = Field(..., description="回复内容")
    agent_used: AgentType = Field(..., description="使用的Agent类型")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度分数")
    actions_taken: List[str] = Field(default_factory=list, description="执行的动作列表")
