"""订单相关工具集"""
import random
from datetime import datetime, timedelta
from langchain.tools import tool
from typing import Dict, Any

# ========== 预设订单数据 ==========
PRESET_ORDERS = {
    "67890": {"status": "已签收", "amount": 299.00, "refundable": True, "product": "蓝牙耳机", "date": "2026-06-15"},
    "12345": {"status": "运输中", "amount": 599.00, "refundable": True, "product": "机械键盘", "date": "2026-06-20"},
    "11111": {"status": "已签收", "amount": 199.00, "refundable": True, "product": "鼠标垫", "date": "2026-06-10"},
    "33333": {"status": "已签收", "amount": 899.00, "refundable": False, "product": "显示器支架", "date": "2026-05-01"},
    "55555": {"status": "待支付", "amount": 1299.00, "refundable": False, "product": "显示器", "date": "2026-06-22"},
}


@tool
def query_order_status(order_id: str) -> Dict[str, Any]:
    """
    查询订单状态

    Args:
        order_id: 订单ID

    Returns:
        包含订单状态、订单ID和金额的字典
    """
    # 优先查预设数据
    if order_id in PRESET_ORDERS:
        order = PRESET_ORDERS[order_id]
        return {
            "order_id": order_id,
            "status": order["status"],
            "amount": order["amount"],
            "refundable": order["refundable"],
            "product": order["product"],
            "date": order["date"],
        }

    # 不在预设中 → 返回不存在
    return {
        "order_id": order_id,
        "status": "不存在",
        "amount": 0,
        "refundable": False,
        "product": "未知",
        "date": "",
    }


@tool
def approve_refund(order_id: str, refund_amount: float = None) -> Dict[str, Any]:
    """
    审批退款申请

    Args:
        order_id: 订单ID
        refund_amount: 退款金额，不指定则全额退款

    Returns:
        包含审批结果、退款金额和原因的字典
    """
    # 查订单
    order = PRESET_ORDERS.get(order_id, {"refundable": True, "amount": refund_amount or 0})

    if not order.get("refundable", True):
        return {
            "approved": False,
            "refund_amount": 0.0,
            "reason": "退款申请未通过：订单已超过退款期限或不符合退款条件",
            "suggestion": "请联系人工客服了解更多详情"
        }

    refund_amount = refund_amount or order.get("amount", 0)
    return {
        "approved": True,
        "refund_amount": refund_amount,
        "reason": "退款申请已批准，款项将在3-5个工作日内退回原支付账户",
        "refund_id": f"RF{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "processing_time": "3-5个工作日"
    }


@tool
def get_order_detail(order_id: str) -> Dict[str, Any]:
    """
    获取订单详细信息

    Args:
        order_id: 订单ID

    Returns:
        订单详细信息
    """
    if order_id in PRESET_ORDERS:
        order = PRESET_ORDERS[order_id]
        return {
            "order_id": order_id,
            "product": order["product"],
            "price": order["amount"],
            "date": order["date"],
            "status": order["status"],
            "customer_name": "张三",
            "shipping_address": "北京市朝阳区xxx街道xxx号",
            "payment_method": "支付宝"
        }

    # 不在预设中 → 模拟生成
    products = ["iPhone 15", "MacBook Pro", "AirPods", "iPad", "Apple Watch"]
    return {
        "order_id": order_id,
        "product": random.choice(products),
        "price": round(random.uniform(100, 10000), 2),
        "date": (datetime.now() - timedelta(days=random.randint(1, 30))).strftime("%Y-%m-%d"),
        "status": random.choice(["待支付", "已支付", "已发货", "已完成"]),
        "customer_name": "张三",
        "shipping_address": "北京市朝阳区xxx街道xxx号",
        "payment_method": "支付宝"
    }


__all__ = [
    "query_order_status",
    "approve_refund",
    "get_order_detail",
]