"""Sales quality contracts; scoring is deterministic and separate from inference."""
import re
from typing import Literal

from pydantic import Field, model_validator

from .domain import Contract, DomainError

Judgment = Literal["met", "partial", "unmet", "not_applicable", "unknown"]
DIMENSIONS = ("discovery", "process", "product")
LABELS = {"met": "达标", "partial": "部分达标", "unmet": "未达标",
          "not_applicable": "不适用", "unknown": "无法判断"}
ROLE_LABELS = {"discovery": "需求与交易信息", "process": "流程与下一步",
               "product": "产品与承诺", "auditor": "独立证据复核"}


class QualityRule(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    title: str = Field(min_length=1, max_length=150)
    dimension: Literal["discovery", "process", "product"]
    weight: int = Field(ge=1, le=100)
    criterion: str = Field(min_length=1, max_length=1500)
    applicability: str = Field(min_length=1, max_length=1000)
    pass_anchor: str = Field(min_length=1, max_length=1500)
    partial_anchor: str = Field(min_length=1, max_length=1500)
    demo_terms: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def valid_terms(self):
        if any(not term.strip() or len(term) > 60 for term in self.demo_terms):
            raise ValueError("演示命中词须为 1—60 字的非空字面词")
        return self


class PlaybookInput(Contract):
    project_id: str
    base_id: str | None = None
    name: str = Field(min_length=1, max_length=150)
    stage: str = Field(min_length=1, max_length=80)
    product_notes: str = Field(default="", max_length=15000)
    rules: list[QualityRule] = Field(min_length=1, max_length=15)

    @model_validator(mode="after")
    def unique_rules(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("规则编号不能重复")
        return self


class SalesSpan(Contract):
    id: str = Field(pattern=r"^s[0-9]{1,5}$")
    text: str = Field(min_length=1, max_length=4000)
    speaker: Literal["sales", "customer", "unknown"] = "unknown"
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_time(self):
        if self.end is not None and (self.start is None or self.end < self.start):
            raise ValueError("片段结束时间必须不早于开始时间")
        return self


class ConversationInput(Contract):
    project_id: str
    title: str = Field(min_length=1, max_length=200)
    salesperson: str = Field(min_length=1, max_length=100)
    customer: str = Field(min_length=1, max_length=150)
    stage: str = Field(min_length=1, max_length=80)
    occurred_on: str | None = None
    complete: bool = False
    transcript: str = Field(default="", max_length=50000)
    spans: list[SalesSpan] = Field(default_factory=list, max_length=400)

    @model_validator(mode="after")
    def valid_content(self):
        from datetime import date
        if self.occurred_on:
            date.fromisoformat(self.occurred_on)
        if self.transcript and self.spans:
            raise ValueError("文字和结构化片段只能选择一种输入")
        if not self.spans and not self.transcript.strip():
            raise ValueError("请提供会话文字或片段")
        if len({span.id for span in self.spans}) != len(self.spans):
            raise ValueError("片段编号不能重复")
        if sum(len(span.text) for span in self.spans) > 50000:
            raise ValueError("首版会话文字总长度上限为 50000 字")
        return self


def parse_transcript(text):
    spans = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^(销售|客户|未知|sales|customer)\s*[:：]\s*(.+)$", line, re.IGNORECASE)
        speaker = {"销售": "sales", "客户": "customer", "sales": "sales", "customer": "customer"}.get(
            match[1].lower(), "unknown") if match else "unknown"
        spans.append(SalesSpan(id=f"s{len(spans) + 1}", speaker=speaker, text=match[2] if match else line))
    if not spans or len(spans) > 400:
        raise DomainError("请提供 1—400 个非空发言段落；长会话请按实际业务场景拆分")
    return [span.model_dump() for span in spans]


class AnalyzeInput(Contract):
    playbook_id: str
    mode: Literal["demo", "live"] = "demo"


class Decision(Contract):
    rule_id: str
    status: Judgment
    reason_title: str = Field(default="", max_length=60)
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    guidance: str = Field(default="", max_length=1500)


class Insight(Contract):
    topic: Literal["price", "feature", "competition", "procurement", "next_step", "other"]
    text: str = Field(min_length=1, max_length=1000)
    evidence: list[str] = Field(min_length=1, max_length=10)


class ExpertResult(Contract):
    decisions: list[Decision] = Field(default_factory=list, max_length=15)
    insights: list[Insight] = Field(default_factory=list, max_length=12)


class Challenge(Contract):
    rule_id: str
    reason: str = Field(min_length=1, max_length=1500)
    evidence: list[str] = Field(default_factory=list, max_length=10)


class AuditResult(Contract):
    challenges: list[Challenge] = Field(default_factory=list, max_length=15)


class ReviewDecision(Contract):
    rule_id: str
    status: Judgment
    reason: str = Field(default="", max_length=1500)


class SalesReviewInput(Contract):
    report_hash: str
    conversation_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=150)
    decisions: list[ReviewDecision] = Field(default_factory=list, max_length=15)
    followup_rule_ids: list[str] = Field(default_factory=list, max_length=15)
    note: str = Field(default="", max_length=3000)


class SpanEdit(Contract):
    version: int = Field(ge=1)
    spans: list[SalesSpan] = Field(min_length=1, max_length=400)
    complete: bool = False

    @model_validator(mode="after")
    def unique_spans(self):
        if len({span.id for span in self.spans}) != len(self.spans):
            raise ValueError("片段编号不能重复")
        if sum(len(span.text) for span in self.spans) > 50000:
            raise ValueError("会话文字总长度上限为 50000 字")
        return self


def score(rules, decisions):
    by_id = {item["rule_id"]: item for item in decisions}
    if len(by_id) != len(decisions) or set(by_id) != {rule["id"] for rule in rules}:
        raise DomainError("评分必须且只能覆盖当前规则，每项一次")
    total, possible, applicable, unknown = 0.0, 0, 0, 0
    for rule in rules:
        status = by_id[rule["id"]]["status"]
        if status not in LABELS:
            raise DomainError("无效评分状态")
        if status != "not_applicable":
            applicable += rule["weight"]
        if status == "unknown":
            unknown += 1
        if status in {"met", "partial", "unmet"}:
            possible += rule["weight"]
            total += rule["weight"] * {"met": 1, "partial": 0.5, "unmet": 0}[status]
    return {"score": round(100 * total / possible, 1) if possible else None,
            "coverage": round(100 * possible / applicable, 1) if applicable else None,
            "earned_weight": total, "evaluated_weight": possible, "applicable_weight": applicable,
            "unknown_count": unknown, "rule_count": len(rules)}


def scored_decisions(rules, decisions):
    weights = {rule["id"]: rule["weight"] for rule in rules}
    result = []
    for item in decisions:
        weight = weights[item["rule_id"]]
        factor = {"met": 1, "partial": 0.5, "unmet": 0}.get(item["status"])
        points = None if factor is None else weight * factor
        result.append({**item, "weight": weight, "points": points,
                       "deduction": None if points is None else weight - points})
    return result


def template():
    return {"name": "需求访谈基础卡", "stage": "需求访谈", "product_notes": "请替换为本企业已确认的产品能力和承诺边界。",
            "rules": [
                {"id": "needs", "title": "澄清客户问题", "dimension": "discovery", "weight": 30,
                 "criterion": "识别客户当前做法、具体困难并核对理解。", "applicability": "用于需求访谈。",
                 "pass_anchor": "了解现状和影响，并向客户核对理解。", "partial_anchor": "询问问题但未深入影响或核对。",
                 "demo_terms": ["现在怎么", "目前怎么", "影响", "理解对吗"]},
                {"id": "next", "title": "确认双方下一步", "dimension": "process", "weight": 40,
                 "criterion": "提出后续动作并获得客户回应，时间未知保留待确认。", "applicability": "适合讨论下一步的需求会话。",
                 "pass_anchor": "双方明确动作和时间安排。", "partial_anchor": "销售提出动作，但客户未确认或时间未定。",
                 "demo_terms": ["演示", "下周", "安排", "下一步"]},
                {"id": "promise", "title": "产品承诺有依据", "dimension": "product", "weight": 30,
                 "criterion": "具体产品和交付承诺与产品资料一致；资料不足无法判断。", "applicability": "会话包含具体产品承诺。",
                 "pass_anchor": "产品资料能支持承诺，且说明必要条件。", "partial_anchor": "承诺附有条件，但边界说明不完整。",
                 "demo_terms": []}]}
