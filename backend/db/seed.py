"""合成数据生成（无第三方依赖）

生成 ~count 条合成订单写入 SQLite，幂等：仅当 orders 表为空时执行。
数据由固定随机种子生成，可复现，用于演示"真实数据层"而不依赖外部业务数据。
"""
import random
from datetime import datetime, timedelta

FIRST_NAMES = ["张", "李", "王", "刘", "陈", "杨", "赵", "黄", "周", "吴",
               "徐", "孙", "马", "朱", "胡", "林", "郭", "何", "高", "罗"]
LAST_NAMES = ["伟", "芳", "娜", "秀英", "敏", "静", "丽", "强", "磊", "军",
              "洋", "勇", "艳", "杰", "娟", "涛", "明", "超", "霞", "平"]
PRODUCTS = [
    ("蓝牙耳机", 299.0), ("机械键盘", 599.0), ("鼠标垫", 199.0),
    ("显示器支架", 899.0), ("显示器", 1299.0), ("智能手表", 1599.0),
    ("无线充电器", 159.0), ("移动电源", 129.0), ("降噪耳机", 1099.0),
    ("机械鼠标", 349.0), ("USB集线器", 99.0), ("笔记本支架", 179.0),
    ("摄像头", 459.0), ("固态硬盘", 699.0), ("路由器", 399.0),
]
STATUSES = ["已签收", "运输中", "待支付", "已支付", "已发货", "已完成", "退款中"]
REFUNDABLE_STATUSES = {"已签收", "运输中", "已支付", "已发货", "已完成"}
PAYMENTS = ["支付宝", "微信支付", "银行卡", "花呗"]
CITIES = [
    ("北京市", "朝阳区"), ("上海市", "浦东新区"), ("广州市", "天河区"), ("深圳市", "南山区"),
    ("杭州市", "西湖区"), ("成都市", "武侯区"), ("武汉市", "洪山区"), ("南京市", "鼓楼区"),
    ("西安市", "雁塔区"), ("重庆市", "渝中区"),
]


def _gen_orders(count: int = 800) -> list:
    rng = random.Random(42)  # 固定种子，保证可复现
    orders = []
    now = datetime.now()
    for i in range(count):
        order_id = str(100000 + i)                       # 6 位数字订单号，唯一
        name = rng.choice(FIRST_NAMES) + rng.choice(LAST_NAMES)
        prod, base_price = rng.choice(PRODUCTS)
        amount = round(base_price * rng.uniform(0.9, 1.2), 2)
        status = rng.choice(STATUSES)
        refundable = 1 if status in REFUNDABLE_STATUSES else 0
        city, district = rng.choice(CITIES)
        phone = "1" + str(rng.randint(30, 99)) + "".join(
            str(rng.randint(0, 9)) for _ in range(9)
        )
        order_date = (now - timedelta(days=rng.randint(0, 120))).strftime("%Y-%m-%d")
        created_at = now.strftime("%Y-%m-%d %H:%M:%S")
        orders.append({
            "order_id": order_id,
            "customer_name": name,
            "phone": phone,
            "product": prod,
            "amount": amount,
            "status": status,
            "refundable": refundable,
            "payment_method": rng.choice(PAYMENTS),
            "shipping_address": f"{city}{district}xxx街道xxx号",
            "order_date": order_date,
            "created_at": created_at,
        })
    return orders


def run_seed(db, count: int = 800, force: bool = False) -> int:
    """向 orders 表灌入合成数据。返回实际写入条数。"""
    from backend.db.repository import OrderRepository
    repo = OrderRepository(db)
    if not force and repo.count() > 0:
        return 0
    orders = _gen_orders(count)
    for o in orders:
        repo.save(o)
    return len(orders)
