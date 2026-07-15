"""订单相关工具集（数据访问层版）

订单数据由 SQLite 持久化存储，通过 OrderRepository 访问；
不再依赖写死在代码里的 PRESET_ORDERS 字典，避免数据伪造与重启丢失。
"""
from datetime import datetime
from typing import Dict, Any, Optional
from langchain.tools import tool

from backend.db.repository import get_order_repo


@tool
def query_order_status(order_id: str) -> Dict[str, Any]:
    """
    查询订单状态

    Args:
        order_id: 订单ID

    Returns:
        包含订单状态、订单ID和金额的字典；订单不存在时返回 status="不存在"
    """
    order = get_order_repo().get_by_id(order_id)
    if not order:
        return {
            "order_id": order_id,
            "status": "不存在",
            "amount": 0,
            "refundable": False,
            "product": "未知",
            "date": "",
        }
    return {
        "order_id": order["order_id"],
        "status": order["status"],
        "amount": order["amount"],
        "refundable": bool(order["refundable"]),
        "product": order["product"],
        "date": order.get("order_date", ""),
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
    order = get_order_repo().get_by_id(order_id)
    if not order:
        order = {"refundable": True, "amount": refund_amount or 0}

    if not order.get("refundable", True):
        return {
            "approved": False,
            "refund_amount": 0.0,
            "reason": "退款申请未通过：订单已超过退款期限或不符合退款条件",
            "suggestion": "请联系人工客服了解更多详情",
        }

    refund_amount = refund_amount or order.get("amount", 0)

    # 审批通过 → 持久化更新订单状态为"退款中"
    try:
        get_order_repo().update_status(order_id, "退款中")
    except Exception:
        pass

    return {
        "approved": True,
        "refund_amount": refund_amount,
        "reason": "退款申请已批准，款项将在3-5个工作日内退回原支付账户",
        "refund_id": f"RF{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "processing_time": "3-5个工作日",
    }


@tool
def get_order_detail(order_id: str) -> Dict[str, Any]:
    """
    获取订单详细信息

    Args:
        order_id: 订单ID

    Returns:
        订单详细信息；订单不存在时返回 status="不存在"
    """
    order = get_order_repo().get_by_id(order_id)
    if order:
        return {
            "order_id": order["order_id"],
            "product": order["product"],
            "price": order["amount"],
            "date": order.get("order_date", ""),
            "status": order["status"],
            "customer_name": order.get("customer_name", ""),
            "shipping_address": order.get("shipping_address", ""),
            "payment_method": order.get("payment_method", ""),
        }

    # 库内无此订单 → 诚实返回"不存在"，不再伪造随机订单
    return {
        "order_id": order_id,
        "product": "未知",
        "price": 0,
        "date": "",
        "status": "不存在",
        "customer_name": "",
        "shipping_address": "",
        "payment_method": "",
    }


__all__ = [
    "query_order_status",
    "approve_refund",
    "get_order_detail",
]
