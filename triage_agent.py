"""
triage_agent.py
Sharpi Support Automation — Core Triage Agent

Classifies incoming WhatsApp support messages using Claude,
then scores priority deterministically before creating a Linear ticket.
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import anthropic

# ─── Domain Types ───────────────────────────────────────────────────────────

class IssueType(str, Enum):
    BUG = "bug"
    USAGE_QUESTION = "usage_question"
    CONFIG = "config"
    ERP_INTEGRATION = "erp_integration"
    AI_FAILURE = "ai_failure"
    UNKNOWN = "unknown"

class Component(str, Enum):
    PRODUCT_SEARCH = "product_search"
    ORDER_LAUNCH = "order_launch"
    APPROVAL_FLOW = "approval_flow"
    AI_CAPTURE = "ai_capture"
    ERP_SYNC = "erp_sync"
    AUTH = "auth"
    UNKNOWN = "unknown"

class Priority(str, Enum):
    P0 = "urgent"    # System down / total blockage
    P1 = "high"      # Recurring bug / major impact
    P2 = "medium"    # Intermittent / partial impact
    P3 = "low"       # Usage question / minor issue

@dataclass
class IncomingMessage:
    """A WhatsApp message from a support group."""
    message_id: str
    sender_name: str
    sender_phone: str
    group_name: str
    text: str
    has_image: bool = False
    image_description: Optional[str] = None  # From vision model if image present
    timestamp: str = ""

@dataclass
class ClientProfile:
    """Enriched client context fetched from CRM/DB."""
    client_id: str
    company_name: str
    plan_tier: str          # "starter" | "growth" | "enterprise"
    days_since_onboarding: int
    open_tickets: int
    resolved_tickets_30d: int
    is_in_production: bool

@dataclass
class TriageResult:
    """Output of the AI triage step."""
    issue_type: IssueType
    component: Component
    title: str
    description: str        # Structured, ready for Linear
    urgency_signals: list[str]
    suggested_assignee_type: str  # "backend" | "ai" | "erp" | "support"
    raw_classification: dict = field(default_factory=dict)

@dataclass
class ScoredTicket:
    """Triage result + deterministic priority score."""
    triage: TriageResult
    priority: Priority
    priority_score: int     # 0–100 for debugging/audit
    score_breakdown: dict   # Auditable breakdown of each factor
    client: ClientProfile
    message: IncomingMessage

# ─── AI Triage Agent ────────────────────────────────────────────────────────

TRIAGE_SYSTEM_PROMPT = """You are a technical support triage agent for Sharpi, a B2B SaaS platform
that automates WhatsApp-based order processing for distributors using AI.

The platform has these main components:
- product_search: searching and browsing the product catalog
- order_launch: submitting orders to the ERP system
- approval_flow: order approval pipeline with rules/conditions
- ai_capture: AI parsing of WhatsApp messages to create orders
- erp_sync: synchronization between Sharpi and client ERP (via Temporal)
- auth: authentication via Clerk

Users are sales reps, supervisors, and backoffice staff. They report issues informally
in WhatsApp groups — often with screenshots, vague descriptions, or just "tá quebrando".

Your job: analyze the message and return a structured JSON classification.

RETURN ONLY VALID JSON. No markdown, no explanation. Schema:
{
  "issue_type": "bug" | "usage_question" | "config" | "erp_integration" | "ai_failure" | "unknown",
  "component": "product_search" | "order_launch" | "approval_flow" | "ai_capture" | "erp_sync" | "auth" | "unknown",
  "title": "<concise, specific title, max 80 chars>",
  "description": "<structured description: what happened, what was expected, context>",
  "urgency_signals": ["<signal1>", "<signal2>"],  // e.g. "recurring", "blocking_sales", "multiple_users"
  "suggested_assignee_type": "backend" | "ai" | "erp" | "support",
  "confidence": 0.0-1.0
}

For urgency_signals, look for: recurring issues, multiple affected users, sales being blocked,
production data at risk, integration failures, AI not identifying products."""

class TriageAgent:
    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(api_key=api_key)  # Uses ANTHROPIC_API_KEY env if not passed

    def classify(self, message: IncomingMessage) -> TriageResult:
        """Run AI classification on an incoming support message."""

        # Build the user message with all available context
        user_content = self._build_user_content(message)

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=TRIAGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}]
        )

        raw_text = response.content[0].text.strip()
        # Strip any accidental markdown fences
        raw_text = re.sub(r"```json\s*|\s*```", "", raw_text).strip()
        classification = json.loads(raw_text)

        return TriageResult(
            issue_type=IssueType(classification.get("issue_type", "unknown")),
            component=Component(classification.get("component", "unknown")),
            title=classification["title"],
            description=classification["description"],
            urgency_signals=classification.get("urgency_signals", []),
            suggested_assignee_type=classification.get("suggested_assignee_type", "support"),
            raw_classification=classification,
        )

    def _build_user_content(self, msg: IncomingMessage) -> str:
        parts = [
            f"**Support group:** {msg.group_name}",
            f"**Reporter:** {msg.sender_name} ({msg.sender_phone})",
            f"**Message:** {msg.text}",
        ]
        if msg.has_image and msg.image_description:
            parts.append(f"**Screenshot description:** {msg.image_description}")
        return "\n".join(parts)


# ─── Priority Scorer ─────────────────────────────────────────────────────────

# Base severity by issue type (0–40 points)
ISSUE_TYPE_BASE = {
    IssueType.BUG: 35,
    IssueType.ERP_INTEGRATION: 40,
    IssueType.AI_FAILURE: 30,
    IssueType.CONFIG: 20,
    IssueType.USAGE_QUESTION: 10,
    IssueType.UNKNOWN: 15,
}

# Component multiplier (some components are more critical)
COMPONENT_WEIGHT = {
    Component.ORDER_LAUNCH: 1.3,
    Component.ERP_SYNC: 1.3,
    Component.APPROVAL_FLOW: 1.1,
    Component.AI_CAPTURE: 1.1,
    Component.PRODUCT_SEARCH: 0.9,
    Component.AUTH: 1.2,
    Component.UNKNOWN: 1.0,
}

# Urgency signal bonuses (cumulative)
URGENCY_BONUSES = {
    "recurring": 20,
    "blocking_sales": 25,
    "multiple_users": 15,
    "production_data_at_risk": 30,
    "integration_failure": 20,
    "no_error_message": 5,  # Harder to debug, higher friction
}

def score_priority(triage: TriageResult, client: ClientProfile) -> tuple[Priority, int, dict]:
    """
    Deterministically compute priority. Returns (Priority, score, breakdown).
    Fully auditable — every point can be explained.
    """
    breakdown = {}

    # 1. Base severity from issue type
    base = ISSUE_TYPE_BASE.get(triage.issue_type, 15)
    breakdown["issue_type_base"] = base

    # 2. Component weight
    weight = COMPONENT_WEIGHT.get(triage.component, 1.0)
    weighted_base = int(base * weight)
    breakdown["component_weight"] = weight
    breakdown["weighted_base"] = weighted_base

    # 3. Urgency signal bonuses
    signal_bonus = 0
    matched_signals = []
    for signal in triage.urgency_signals:
        bonus = URGENCY_BONUSES.get(signal, 0)
        signal_bonus += bonus
        matched_signals.append(f"{signal}(+{bonus})")
    breakdown["urgency_signals"] = matched_signals
    breakdown["signal_bonus"] = signal_bonus

    # 4. Client tier weight
    tier_bonus = {"enterprise": 10, "growth": 5, "starter": 0}.get(client.plan_tier, 0)
    breakdown["client_tier_bonus"] = tier_bonus

    # 5. Recency of onboarding (new clients → likely usage question, reduce urgency)
    if client.days_since_onboarding < 14 and triage.issue_type == IssueType.USAGE_QUESTION:
        onboarding_adj = -10
    else:
        onboarding_adj = 0
    breakdown["onboarding_adjustment"] = onboarding_adj

    # 6. Already has open tickets? Recurring pattern gets higher priority
    recurrence_bonus = min(client.open_tickets * 3, 15)
    breakdown["recurrence_bonus"] = recurrence_bonus

    total = weighted_base + signal_bonus + tier_bonus + onboarding_adj + recurrence_bonus
    total = max(0, min(100, total))  # Clamp to 0–100
    breakdown["total_score"] = total

    # Map to Priority
    if total >= 75:
        priority = Priority.P0
    elif total >= 50:
        priority = Priority.P1
    elif total >= 25:
        priority = Priority.P2
    else:
        priority = Priority.P3

    return priority, total, breakdown


def triage_message(message: IncomingMessage, client: ClientProfile,
                   agent: Optional[TriageAgent] = None) -> ScoredTicket:
    """Full triage pipeline: classify → score → return ScoredTicket."""
    if agent is None:
        agent = TriageAgent()

    triage_result = agent.classify(message)
    priority, score, breakdown = score_priority(triage_result, client)

    return ScoredTicket(
        triage=triage_result,
        priority=priority,
        priority_score=score,
        score_breakdown=breakdown,
        client=client,
        message=message,
    )
