"""Additional authored DEVELOPMENT cases; no independent annotation or holdout."""
from cases import CASES, case

EXTRA_CASES = [
    case("refused_handover", "发送方案", ["销售陈琪：虽然提议让我接手发送方案，但我不接手，仍由王宁按原约定发送。"],
         "王宁", "2026-11-16", "todo", ["h1", "s1"], previous="此前已确认：王宁负责2026-11-16发送方案，状态todo。"),
    case("condition_removed", "发送通知", ["销售陈琪：我原来要等安全评审通过后发送通知。",
         "客户：这项通知的评审前置条件已正式取消，不需要再等。", "销售陈琪：确认，我明天发送通知。"],
         "陈琪", "2026-11-13", "todo", ["s2", "s3"]),
    case("cancel_retracted", "发送报价", ["销售王宁：刚才取消发送报价的说法撤回，恢复原安排，还是我明天发送。"],
         "王宁", "2026-11-13", "todo", ["h1", "s1"], previous="此前已确认：王宁负责2026-11-13发送报价，状态todo。"),
    case("unrelated_customer_deadline", "发送需求清单", ["客户：如果采购同意，我周五给预算。",
         "销售陈琪：我只承诺明天发送需求清单，没有承诺发送报价。"], "陈琪", "2026-11-13", "todo", ["s2"]),
    case("two_distinct_targets", "发送合同草案", ["销售何悦：我周五发送报价。",
         "销售陈琪：我下周一发送合同草案，报价还是何悦负责。"], "陈琪", "2026-11-16", "todo", ["s2"]),
    case("deadline_revoked", "发送方案", ["销售王宁：方案先暂停，暂停时原来的11月13日截止日期也作废，新的日期尚未确定。"],
         "王宁", None, "paused", ["h1", "s1"], previous="此前已确认：王宁负责2026-11-13发送方案，状态todo。"),
    case("conditional_suggestion", "发送方案", ["客户：如果我领导同意，你们要不要下周发方案？",
         "销售王宁：只是讨论可能性，我没有接受这项任务。"], None, None, "none", ["s1", "s2"]),
    case("not_yet_sent", "发送报价", ["销售陈琪：我正在准备报价，还没有发出去。", "客户：那就等你发送。"],
         "陈琪", None, "todo", ["s1"]),
]
INTERNAL_CASES = [*CASES, *EXTRA_CASES]
