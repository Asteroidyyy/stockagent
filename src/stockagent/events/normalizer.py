from __future__ import annotations


RISK_KEYWORDS = {
    "风险提示": "风险提示公告",
    "减持": "减持风险",
    "问询": "交易所问询",
    "监管": "监管关注",
    "立案": "立案调查风险",
    "处罚": "监管处罚风险",
    "诉讼": "诉讼仲裁风险",
    "冻结": "股权冻结风险",
    "质押": "股权质押风险",
    "停牌": "停牌风险",
    "终止": "终止事项风险",
    "亏损": "业绩承压",
    "下滑": "业绩承压",
    "承压": "行业景气承压",
    "解禁": "解禁压力",
}

POSITIVE_KEYWORDS = {
    "预增": "业绩预增预告",
    "增长": "业绩增长",
    "回购": "股份回购",
    "中标": "订单中标",
    "增持": "增持计划",
    "分红": "分红预案",
    "激励": "股权激励",
    "调研": "机构调研活跃",
}

NEUTRAL_KEYWORDS = {
    "年报": "定期报告披露",
    "季报": "定期报告披露",
    "半年报": "定期报告披露",
    "财务报告": "定期报告披露",
    "更名": "名称或信息变更",
    "变更": "信息变更",
    "重组": "资产重组进展",
    "融资": "融资事项公告",
}


def normalize_notice(title: str, notice_type: str) -> str:
    text = f"{notice_type} {title}".strip()

    for keyword, label in RISK_KEYWORDS.items():
        if keyword in text:
            return label

    for keyword, label in POSITIVE_KEYWORDS.items():
        if keyword in text:
            return label

    for keyword, label in NEUTRAL_KEYWORDS.items():
        if keyword in text:
            return label

    if notice_type:
        return f"{notice_type}公告"
    return "一般公告"


def is_risk_label(label: str) -> bool:
    if label.startswith("无重大风险"):
        return False
    return any(
        keyword in label
        for keyword in ["风险", "问询", "承压", "立案", "处罚", "诉讼", "冻结", "质押", "解禁"]
    )
