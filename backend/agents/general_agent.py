"""通用咨询Agent

处理支付方式、会员权益、客服联系方式等通用问题
"""
from backend.agents.base_agent import BaseAgent
from backend.llm import QwenLLM
from backend.tools import search_faq, search_policy, get_current_time, escalate_to_human


class GeneralAgent(BaseAgent):
    """通用咨询客服"""

    def __init__(self, llm: QwenLLM):
        """
        初始化GeneralAgent

        Args:
            llm: QwenLLM实例
        """
        # 定义可用工具
        tools = [search_faq, search_policy, get_current_time, escalate_to_human]

        # 系统提示词
        system_prompt = """你是专业的通用咨询客服。你的职责是帮助用户解答各类通用问题。

你可以处理以下问题：
- 支付方式咨询（支持的支付方式、支付流程等）
- 会员权益咨询（会员等级、积分规则、优惠券使用等）
- 客服联系方式（工作时间、联系方式等）
- 账户相关问题
- 隐私政策、用户协议咨询
- 其他不属于退货退款、技术支持、订单查询的问题

工作流程：
1. 热情问候用户，了解其咨询意图
2. 使用工具搜索相关FAQ或政策信息
3. 清晰、友好地回答用户问题
4. 如无法解答或用户要求，转接人工客服

可用工具（通过 function calling 直接调用，无需拼接文本格式）：
- search_faq：搜索 FAQ 知识库
- search_policy：查询政策
- get_current_time：获取当前时间
- escalate_to_human：转接人工客服（reason / priority）

回答原则：
- 态度友好、耐心
- 使用礼貌用语（您好、请、谢谢等）
- 如不确定答案，主动说明并建议转接人工
- 涉及敏感信息时，提醒用户注意保护隐私

请使用工具获取必要信息后再回答用户问题。"""

        super().__init__(
            name="GeneralAgent",
            tools=tools,
            system_prompt=system_prompt,
            llm=llm
        )

    def handle_inquiry(self, inquiry_type: str, user_message: str) -> dict:
        """
        处理特定类型的咨询

        Args:
            inquiry_type: 咨询类型（payment/membership/contact/other）
            user_message: 用户消息

        Returns:
            处理结果
        """
        # 构建查询关键词
        keywords_map = {
            "payment": "支付方式",
            "membership": "会员权益",
            "contact": "客服联系方式",
            "other": "通用咨询"
        }

        keyword = keywords_map.get(inquiry_type, "通用咨询")

        messages = [
            {"role": "user", "content": f"用户咨询类型：{keyword}\n用户问题：{user_message}"}
        ]

        return self.execute(messages)

    def escalate(self, reason: str, priority: str = "normal") -> dict:
        """
        转接人工客服

        Args:
            reason: 转接原因
            priority: 优先级（low/normal/high/urgent）

        Returns:
            转接结果
        """
        result = escalate_to_human.invoke({
            "reason": reason,
            "priority": priority
        })

        return {
            "escalated": True,
            "reason": reason,
            "priority": priority,
            "result": result,
            "message": "已为您转接人工客服，请稍候..."
        }
