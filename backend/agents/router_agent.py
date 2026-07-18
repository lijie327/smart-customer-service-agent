"""路由 Agent

负责分析用户意图并路由到相应的专业 Agent
"""
import json
from typing import Dict, Any, Tuple, List
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from backend.models import AgentType
from backend.agents.base_agent import BaseAgent
from backend.llm import QwenLLM


def _to_lc_messages(messages) -> List[Any]:
    """把 [{"role","content"}] 转成 LangChain 消息（供 BaseChatModel 调用）。"""
    lc: List[Any] = []
    for m in messages or []:
        role = m.get("role")
        content = m.get("content", "")
        if role == "user":
            lc.append(HumanMessage(content=content))
        elif role == "assistant":
            lc.append(AIMessage(content=content))
        elif role == "system":
            lc.append(SystemMessage(content=content))
    return lc


class RouterAgent:
    """路由 Agent - 意图识别和任务分发"""

    def __init__(self, llm: QwenLLM):
        """
        初始化 RouterAgent

        Args:
            llm: QwenLLM 实例
        """
        self.llm = llm
        self.system_prompt = """你是一个智能客服系统的意图识别专家。你的任务是分析用户消息，判断用户的意图，并将其分类到以下类别之一：

1. **refund** - 退货退款相关：用户询问退货流程、申请退款、退款进度、退货政策等
2. **tech_support** - 技术支持：用户询问产品使用方法、故障排除、技术问题、产品规格等
3. **order_query** - 订单查询：用户查询订单状态、物流信息、发货时间、订单详情等
4. **general** - 通用咨询：其他问题，如支付方式、会员权益、客服联系方式等

请分析用户消息，返回 JSON 格式的结果：
{
    "intent": "意图类别 (refund/tech_support/order_query/general)",
    "confidence": 置信度 (0.0-1.0),
    "reason": "判断理由"
}

只返回 JSON，不要有其他内容。"""

    # 关键词路由映射（无需 LLM 即可判断的强信号关键词）
    KEYWORD_ROUTES = {
        AgentType.REFUND: [
            "退货", "退款", "退钱", "退差价", "退换", "退吧", "退了", "想退",
            "质量问题", "有毛病", "瑕疵", "破损",
            "换货", "换一个", "退款流程", "退货流程", "退货政策",
            "怎么退款", "怎么退货", "申请退款", "申请退货",
            "不想要了", "想退掉", "能不能退", "退款到账",
            "已退货", "退款进度", "退款状态",
        ],
        AgentType.TECH_SUPPORT: [
            "使用方法", "说明书", "不会用", "怎么设置",
            "开不了机", "不开机", "死机", "卡顿", "闪退",
            "连不上", "蓝牙", "WiFi", "网络", "无法连接",
            "怎么安装", "怎么配置", "规格", "参数", "兼容",
            "故障", "报错", "错误代码", "出错了", "异常",
            "保修", "维修", "坏了怎么修", "保修期",
        ],
        AgentType.ORDER_QUERY: [
            "订单", "物流", "快递", "发货", "配送",
            "到哪了", "还没到", "什么时候到",
            "查询", "配送进度", "订单号",
            "修改地址", "改地址", "取消订单",
            "收货地址", "收货人",
        ],
    }

    def _keyword_precheck(self, message: str) -> Tuple[AgentType, float, str] | None:
        """
        关键词预检：在调用 LLM 之前先用关键词做快速匹配

        Args:
            message: 用户消息

        Returns:
            (AgentType, confidence, reason) 或 None（表示关键词未命中）
        """
        for agent_type, keywords in self.KEYWORD_ROUTES.items():
            for kw in keywords:
                if kw in message:
                    return agent_type, 0.95, f"关键词匹配：命中「{kw}」→ {agent_type.value}"
        return None

    def classify_intent(self, message: str, conversation_history: List[Dict] = None) -> Tuple[AgentType, float, str]:
        """
        分类用户意图（支持上下文感知 + 关键词预检）

        Args:
            message: 用户消息
            conversation_history: 对话历史记录

        Returns:
            (AgentType, confidence, reason) 元组
        """
        # ========== 0. 关键词预检（不依赖 LLM 的快速路径） ==========
        keyword_result = self._keyword_precheck(message)
        if keyword_result is not None:
            agent_type, confidence, reason = keyword_result
            return agent_type, confidence, reason

        # 构建包含对话历史的提示词
        history_context = ""
        recent_refund_count = 0
        recent_tech_count = 0
        recent_order_count = 0

        if conversation_history:
            # 分析最近几轮对话，统计各种意图的次数
            for msg in reversed(conversation_history[-6:]):  # 只看最近 6 轮
                if msg.get("role") == "assistant":
                    if msg.get("agent_used") == "refund":
                        recent_refund_count += 1
                    elif msg.get("agent_used") == "tech_support":
                        recent_tech_count += 1
                    elif msg.get("agent_used") == "order_query":
                        recent_order_count += 1

            # 如果有历史对话，添加到提示中
            history_str = "\n".join([
                f"{m.get('role', 'unknown')}: {m.get('content', '')}"
                for m in conversation_history[-4:]  # 最近 4 条消息
            ])
            history_context = f"\n\n之前的对话历史：\n{history_str}"

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"用户消息：{message}{history_context}"}
        ]

        try:
            response = self.llm.invoke(_to_lc_messages(messages), temperature=0.1)
            content = response.content

            # 解析 JSON 响应
            # 尝试提取 JSON 块
            import re
            json_pattern = r'\{[^{}]*\}'
            matches = re.findall(json_pattern, content, re.DOTALL)

            if matches:
                result = json.loads(matches[0])
                intent_str = result.get("intent", "general")
                confidence = float(result.get("confidence", 0.5))
                reason = result.get("reason", "")

                # 映射到 AgentType 枚举
                intent_map = {
                    "refund": AgentType.REFUND,
                    "tech_support": AgentType.TECH_SUPPORT,
                    "order_query": AgentType.ORDER_QUERY,
                    "general": AgentType.GENERAL
                }

                agent_type = intent_map.get(intent_str, AgentType.GENERAL)

                # 确保置信度在有效范围内
                confidence = max(0.0, min(1.0, confidence))

                # 【关键修复】上下文感知路由：如果上一轮在退货流程中，且当前消息简短模糊，保持退货 Agent
                if recent_refund_count >= 1 and len(message) < 20:
                    # 简短的消息（如"坏了"、"可以吗"）在退货流程中应继续走退货流程
                    agent_type = AgentType.REFUND
                    confidence = 0.9  # 提高置信度，因为上下文已经明确意图
                    reason = f"上下文检测：前 {recent_refund_count} 轮在退货流程中，当前为简短消息 '{message}'，保持退货处理"

                # 类似地，如果是技术支持流程中的简短提问，也保持技术支持
                elif recent_tech_count >= 1 and len(message) < 20:
                    agent_type = AgentType.TECH_SUPPORT
                    confidence = 0.85
                    reason = f"上下文检测：前 {recent_tech_count} 轮在技术支持流程中，当前为简短消息 '{message}'，保持技术支持"
                # 订单流程中的简短追问，保持订单查询（与退款/技术对称）
                elif recent_order_count >= 1 and len(message) < 20:
                    agent_type = AgentType.ORDER_QUERY
                    confidence = 0.88
                    reason = f"上下文检测：前 {recent_order_count} 轮在订单流程中，当前为简短消息 '{message}'，保持订单查询"

                return agent_type, confidence, reason
            else:
                # 解析失败，返回默认值
                return AgentType.GENERAL, 0.5, "意图识别失败，使用默认路由"

        except Exception as e:
            # 发生异常，返回默认值
            return AgentType.GENERAL, 0.5, f"意图识别异常：{str(e)}"

    def route(self, message: str, session_id: str = None, conversation_history: List[Dict] = None) -> Dict[str, Any]:
        """
        路由用户请求（支持上下文感知）

        Args:
            message: 用户消息
            session_id: 会话 ID（可选）
            conversation_history: 对话历史记录（可选）

        Returns:
            包含路由决策的字典
        """
        agent_type, confidence, reason = self.classify_intent(message, conversation_history)

        return {
            "agent_type": agent_type,
            "confidence": confidence,
            "reason": reason,
            "message": message
        }

    async def aclassify_intent(self, message: str, conversation_history: List[Dict] = None) -> Tuple[AgentType, float, str]:
        """
        异步意图分类（与 classify_intent 逻辑一致，但 LLM 调用走 `await ainvoke`）。

        在 FastAPI 的 async 端点中，同步 llm.invoke 会阻塞事件循环；
        此处改用 ainvoke 保证非阻塞。
        """
        keyword_result = self._keyword_precheck(message)
        if keyword_result is not None:
            return keyword_result

        history_context = ""
        recent_refund_count = 0
        recent_tech_count = 0
        recent_order_count = 0

        if conversation_history:
            for msg in reversed(conversation_history[-6:]):
                if msg.get("role") == "assistant":
                    if msg.get("agent_used") == "refund":
                        recent_refund_count += 1
                    elif msg.get("agent_used") == "tech_support":
                        recent_tech_count += 1
                    elif msg.get("agent_used") == "order_query":
                        recent_order_count += 1
            history_str = "\n".join([
                f"{m.get('role', 'unknown')}: {m.get('content', '')}"
                for m in conversation_history[-4:]
            ])
            history_context = f"\n\n之前的对话历史：\n{history_str}"

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"用户消息：{message}{history_context}"}
        ]

        try:
            response = await self.llm.ainvoke(_to_lc_messages(messages), temperature=0.1)
            content = response.content

            import re as _re
            json_pattern = r'\{[^{}]*\}'
            matches = _re.findall(json_pattern, content, _re.DOTALL)

            if matches:
                result = json.loads(matches[0])
                intent_str = result.get("intent", "general")
                confidence = float(result.get("confidence", 0.5))
                reason = result.get("reason", "")

                intent_map = {
                    "refund": AgentType.REFUND,
                    "tech_support": AgentType.TECH_SUPPORT,
                    "order_query": AgentType.ORDER_QUERY,
                    "general": AgentType.GENERAL
                }
                agent_type = intent_map.get(intent_str, AgentType.GENERAL)
                confidence = max(0.0, min(1.0, confidence))

                if recent_refund_count >= 1 and len(message) < 20:
                    agent_type = AgentType.REFUND
                    confidence = 0.9
                    reason = f"上下文检测：前 {recent_refund_count} 轮在退货流程中，当前为简短消息 '{message}'，保持退货处理"
                elif recent_tech_count >= 1 and len(message) < 20:
                    agent_type = AgentType.TECH_SUPPORT
                    confidence = 0.85
                    reason = f"上下文检测：前 {recent_tech_count} 轮在技术支持流程中，当前为简短消息 '{message}'，保持技术支持"
                elif recent_order_count >= 1 and len(message) < 20:
                    agent_type = AgentType.ORDER_QUERY
                    confidence = 0.88
                    reason = f"上下文检测：前 {recent_order_count} 轮在订单流程中，当前为简短消息 '{message}'，保持订单查询"

                return agent_type, confidence, reason
            else:
                return AgentType.GENERAL, 0.5, "意图识别失败，使用默认路由"
        except Exception as e:
            return AgentType.GENERAL, 0.5, f"意图识别异常：{str(e)}"

    async def aroute(self, message: str, session_id: str = None, conversation_history: List[Dict] = None) -> Dict[str, Any]:
        """异步路由（不阻塞 event loop）"""
        agent_type, confidence, reason = await self.aclassify_intent(message, conversation_history)
        return {
            "agent_type": agent_type,
            "confidence": confidence,
            "reason": reason,
            "message": message
        }


class RouterAgentWithLLM(BaseAgent):
    """基于 LLM 的路由 Agent（继承 BaseAgent）"""

    def __init__(self, llm: QwenLLM, tools: list = None):
        """
        初始化 RouterAgent

        Args:
            llm: QwenLLM 实例
            tools: 工具列表（路由 Agent 通常不需要工具）
        """
        system_prompt = """你是一个智能客服系统的路由专家。你的任务是：
1. 分析用户消息的意图
2. 判断应该由哪个专业 Agent 处理
3. 如果需要工具调用，使用以下格式：

工具调用格式：
[TOOL_CALL]tool_name(param1=value1, param2=value2)[/TOOL_CALL]

可用 Agent 类型：
- refund: 退货退款专员，处理退货、退款相关问题
- tech_support: 技术支持专家，处理产品使用、故障排除等问题
- order_query: 订单查询专员，处理订单状态、物流查询等问题
- general: 通用客服，处理其他问题

请根据用户消息，判断应该路由到哪个 Agent，并说明理由。"""

        super().__init__(
            name="RouterAgent",
            tools=tools or [],
            system_prompt=system_prompt,
            llm=llm
        )

        self.router = RouterAgent(llm)

    def execute(self, messages, **kwargs):
        """执行路由决策"""
        # 获取最后一条用户消息
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        # 进行意图分类
        result = self.router.route(user_message)

        return {
            "reply": f"意图已识别，将路由到 {result['agent_type'].value} Agent",
            "agent": self.name,
            "routing_decision": result,
            "tool_calls": []
        }
