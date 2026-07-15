"""路由意图识别评测 harness

用途：
- 对 RouterAgent 的意图路由做端到端评测，输出路由准确率、各类 P/R/F1、混淆矩阵、
  关键词快路径 / LLM 路径占比，以及错误样本清单。
- 默认使用内置的离线 FakeLLM 分类器（无需网络 / API Key），用于验证评测链路本身；
- 传入 --real 且环境变量 DASHSCOPE_API_KEY 已配置时，自动切换为真实 QwenLLM，
  得到贴近生产的真实路由准确率。

运行：
    python tests/eval_router.py            # 离线（FakeLLM）
    python tests/eval_router.py --real     # 真实 LLM（需 DASHSCOPE_API_KEY）
    python tests/eval_router.py --async    # 走 aroute 异步路径
"""
import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from typing import List, Dict, Any, Tuple

# 让脚本可在项目根目录直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.agents.router_agent import RouterAgent
from backend.models import AgentType

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "router_eval.jsonl")
REPORT_PATH = os.path.join(os.path.dirname(__file__), "data", "router_eval_report.json")
CLASSES = ["refund", "tech_support", "order_query", "general"]


# ---------------------------------------------------------------------------
# 离线分类器：用关键词启发式近似一个"合理的 LLM"，仅用于验证评测链路离线可跑。
# 真实指标请使用 --real。
# ---------------------------------------------------------------------------
class FakeLLM:
    REFUND_KW = ["退", "换货", "质量", "瑕疵", "破损", "坏", "差价", "不想要",
                 "描述不一样", "毛病", "拿回来", "退了", "到账"]
    TECH_KW = ["转圈", "打不开", "屏幕", "充电", "充", "声音", "拍照", "模糊",
               "设置", "配置", "网络", "连不上", "蓝牙", "wifi", "机", "安装",
               "规格", "保修", "故障", "报错", "错误", "兼容", "死机", "卡顿",
               "闪退", "说明书", "维修", "怎么用"]
    ORDER_KW = ["订单", "物流", "快递", "发货", "配送", "到哪", "单号", "收到",
                "取消订单", "包裹", "站点", "签收", "揽收", "收货地址", "收货人"]

    def _classify(self, message: str) -> Tuple[str, float, str]:
        msg = message or ""
        # 技术优先：含"修"且伴随"坏/怎么" → 技术支持（避免"坏了怎么修"被退货款词抢走）
        if "修" in msg and ("坏" in msg or "怎么" in msg):
            return "tech_support", 0.80, "[FakeLLM] 维修/排障意图 → 技术"
        # "怎么用"：优惠/发票/会员等通用咨询除外
        if "怎么用" in msg:
            if any(g in msg for g in ("优惠", "发票", "会员", "券")):
                return "general", 0.70, "[FakeLLM] 优惠/会员类用法 → 通用"
            return "tech_support", 0.80, "[FakeLLM] 产品用法 → 技术"
        for kw in self.REFUND_KW:
            if kw in msg:
                return "refund", 0.82, f"[FakeLLM] 命中退款信号「{kw}」"
        for kw in self.TECH_KW:
            if kw in msg:
                return "tech_support", 0.80, f"[FakeLLM] 命中技术信号「{kw}」"
        for kw in self.ORDER_KW:
            if kw in msg:
                return "order_query", 0.81, f"[FakeLLM] 命中订单信号「{kw}」"
        return "general", 0.70, "[FakeLLM] 无明确业务信号 → 通用咨询"

    @staticmethod
    def _extract_user(messages: List[Dict[str, str]]) -> str:
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content", "")
                if content.startswith("用户消息："):
                    content = content[len("用户消息："):]
                # 去掉可能拼接的历史上下文
                idx = content.find("\n\n之前的对话历史：")
                if idx != -1:
                    content = content[:idx]
                return content.strip()
        return ""

    def invoke(self, messages: List[Dict[str, str]], temperature: float = 0.1) -> Dict[str, Any]:
        intent, conf, reason = self._classify(self._extract_user(messages))
        return {"content": json.dumps(
            {"intent": intent, "confidence": conf, "reason": reason},
            ensure_ascii=False)}

    async def ainvoke(self, messages: List[Dict[str, str]], temperature: float = 0.1) -> Dict[str, Any]:
        return self.invoke(messages, temperature)


def load_dataset(path: str) -> List[Dict[str, Any]]:
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
    return cases


def normalize_agent(agent) -> str:
    if isinstance(agent, AgentType):
        return agent.value
    return str(agent)


def evaluate(use_real: bool, use_async: bool) -> Dict[str, Any]:
    cases = load_dataset(DATA_PATH)

    if use_real and os.environ.get("DASHSCOPE_API_KEY"):
        from backend.llm import QwenLLM
        llm = QwenLLM()
        backend_name = "QwenLLM(real)"
    else:
        if use_real:
            print("⚠ 未检测到 DASHSCOPE_API_KEY，回退到离线 FakeLLM。")
        llm = FakeLLM()
        backend_name = "FakeLLM(offline)"

    router = RouterAgent(llm)

    y_true, y_pred, reasons, is_fast = [], [], [], []
    for c in cases:
        text = c["text"]
        history = c.get("history")
        if use_async:
            res = asyncio.run(router.aroute(text, conversation_history=history))
        else:
            res = router.route(text, conversation_history=history)
        pred = normalize_agent(res["agent_type"])
        y_true.append(c["expected"])
        y_pred.append(pred)
        reasons.append(res.get("reason", ""))
        is_fast.append("关键词匹配" in res.get("reason", ""))

    # ---- 指标计算 ----
    total = len(y_true)
    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    accuracy = correct / total if total else 0.0

    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    confusion: Dict[str, Dict[str, int]] = {c: {p: 0 for p in CLASSES} for c in CLASSES}
    for t, p in zip(y_true, y_pred):
        confusion[t][p] += 1
        if t == p:
            tp[t] += 1
        else:
            fp[p] += 1
            fn[t] += 1

    per_class = {}
    for c in CLASSES:
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) else 0.0
        r = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        per_class[c] = {
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(f1, 4),
            "support": tp[c] + fn[c],
        }

    # 宏平均
    macro_f1 = sum(v["f1"] for v in per_class.values()) / len(CLASSES)
    macro_p = sum(v["precision"] for v in per_class.values()) / len(CLASSES)
    macro_r = sum(v["recall"] for v in per_class.values()) / len(CLASSES)

    errors = []
    for c, t, p, reason in zip(cases, y_true, y_pred, reasons):
        if t != p:
            errors.append({
                "text": c["text"],
                "expected": t,
                "predicted": p,
                "reason": reason,
                "note": c.get("note", ""),
                "history": bool(c.get("history")),
            })

    fast_count = sum(1 for x in is_fast if x)
    report = {
        "backend": backend_name,
        "total": total,
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "macro_precision": round(macro_p, 4),
        "macro_recall": round(macro_r, 4),
        "macro_f1": round(macro_f1, 4),
        "fast_path_ratio": round(fast_count / total, 4) if total else 0.0,
        "fast_path_count": fast_count,
        "llm_path_count": total - fast_count,
        "per_class": per_class,
        "confusion_matrix": confusion,
        "errors": errors,
    }
    return report


def print_report(report: Dict[str, Any]) -> None:
    print("=" * 64)
    print(f"路由意图评测报告  (backend={report['backend']})")
    print("=" * 64)
    print(f"样本总数      : {report['total']}")
    print(f"路由准确率    : {report['accuracy'] * 100:.2f}%  ({report['correct']}/{report['total']})")
    print(f"宏平均 P/R/F1 : {report['macro_precision']:.4f} / "
          f"{report['macro_recall']:.4f} / {report['macro_f1']:.4f}")
    print(f"快路径占比    : {report['fast_path_ratio'] * 100:.1f}% "
          f"({report['fast_path_count']} 关键词 / {report['llm_path_count']} LLM)")
    print("-" * 64)
    print("各类指标 (Precision / Recall / F1 / Support):")
    for c in CLASSES:
        v = report["per_class"][c]
        print(f"  {c:<13} {v['precision']:.3f}   {v['recall']:.3f}   "
              f"{v['f1']:.3f}   {v['support']}")
    print("-" * 64)
    print("混淆矩阵 (行=真实 / 列=预测):")
    header = "        " + "".join(f"{c[:6]:>8}" for c in CLASSES)
    print(header)
    for t in CLASSES:
        row = f"  {t[:6]:<6}" + "".join(
            f"{report['confusion_matrix'][t][p]:>8}" for p in CLASSES)
        print(row)
    print("-" * 64)
    if report["errors"]:
        print(f"错误样本 ({len(report['errors'])} 条):")
        for e in report["errors"][:20]:
            tag = "[多轮]" if e["history"] else "[单轮]"
            print(f"  {tag} 期望={e['expected']:<12} 预测={e['predicted']:<12} "
                  f"| {e['text']}  ({e['note']})")
        if len(report["errors"]) > 20:
            print(f"  ... 其余 {len(report['errors']) - 20} 条见 report JSON")
    else:
        print("✓ 全部样本路由正确，无错误。")
    print("=" * 64)


def main():
    parser = argparse.ArgumentParser(description="RouterAgent 意图路由评测")
    parser.add_argument("--real", action="store_true", help="使用真实 QwenLLM（需 DASHSCOPE_API_KEY）")
    parser.add_argument("--async", dest="use_async", action="store_true", help="走 aroute 异步路径")
    args = parser.parse_args()

    report = evaluate(use_real=args.real, use_async=args.use_async)
    print_report(report)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"✓ 报告已写入: {REPORT_PATH}")


if __name__ == "__main__":
    main()
