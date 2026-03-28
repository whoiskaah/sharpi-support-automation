"""
slack_notifier.py
Sharpi Support Automation — Slack Notifications

Posts rich, actionable notifications to Slack when:
- A new support ticket is created
- A duplicate is detected
- A ticket is resolved (triggers WhatsApp feedback to client)
"""

import httpx
from dataclasses import dataclass
from typing import Optional

from triage_agent import Priority, IssueType, Component, ScoredTicket
from context_enricher import EnrichmentResult

# ─── Config ──────────────────────────────────────────────────────────────────

PRIORITY_EMOJI = {
    Priority.P0: "🔴",
    Priority.P1: "🟠",
    Priority.P2: "🟡",
    Priority.P3: "🟢",
}

COMPONENT_EMOJI = {
    Component.PRODUCT_SEARCH: "🔍",
    Component.ORDER_LAUNCH: "🚀",
    Component.APPROVAL_FLOW: "⏳",
    Component.AI_CAPTURE: "🤖",
    Component.ERP_SYNC: "🔗",
    Component.AUTH: "🔑",
    Component.UNKNOWN: "❓",
}

ISSUE_TYPE_LABEL = {
    IssueType.BUG: "Bug",
    IssueType.ERP_INTEGRATION: "Integração ERP",
    IssueType.AI_FAILURE: "Falha de IA",
    IssueType.CONFIG: "Configuração",
    IssueType.USAGE_QUESTION: "Dúvida de uso",
    IssueType.UNKNOWN: "Desconhecido",
}

# Map issue type to Slack team handle
ASSIGNEE_HANDLES = {
    "backend": "@backend-team",
    "ai": "@ai-team",
    "erp": "@integrations-team",
    "support": "@support-team",
}


class SlackNotifier:
    def __init__(self, webhook_url: str, triage_channel: str = "#support-triage"):
        self.webhook_url = webhook_url
        self.channel = triage_channel

    async def notify_new_ticket(self, scored_ticket: ScoredTicket,
                                 enrichment: EnrichmentResult,
                                 linear_ticket_url: str) -> None:
        """Post a rich Block Kit message for a new ticket."""
        t = scored_ticket.triage
        c = scored_ticket.client
        m = scored_ticket.message

        p_emoji = PRIORITY_EMOJI[scored_ticket.priority]
        comp_emoji = COMPONENT_EMOJI.get(t.component, "❓")
        assignee = ASSIGNEE_HANDLES.get(t.suggested_assignee_type, "@support-team")
        issue_label = ISSUE_TYPE_LABEL.get(t.issue_type, "Desconhecido")

        # Build sentry context snippet if available
        sentry_note = ""
        if enrichment.sentry_events:
            top_event = enrichment.sentry_events[0]
            sentry_note = f"\n*Sentry:* <{top_event.url}|{top_event.title}> ({top_event.count}x/24h)"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{p_emoji} [{scored_ticket.priority.value.upper()}] {t.title}",
                    "emoji": True,
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Tipo:* {comp_emoji} {issue_label}"},
                    {"type": "mrkdwn", "text": f"*Componente:* `{t.component.value}`"},
                    {"type": "mrkdwn", "text": f"*Cliente:* {c.company_name} ({c.plan_tier})"},
                    {"type": "mrkdwn", "text": f"*Reportado por:* {m.sender_name}"},
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Mensagem:*\n_{m.text[:300]}_{sentry_note}"
                }
            },
        ]

        if t.urgency_signals:
            blocks.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": f"⚠️ Sinais de urgência: {' · '.join(t.urgency_signals)}"
                }]
            })

        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Ver no Linear", "emoji": True},
                    "url": linear_ticket_url,
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Atribuir a mim"},
                    "action_id": f"claim_ticket_{scored_ticket.message.message_id}",
                }
            ]
        })

        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"Atribuição sugerida: {assignee} · Score: {scored_ticket.priority_score}/100"
            }]
        })

        await self._post({"channel": self.channel, "blocks": blocks})

    async def notify_duplicate(self, scored_ticket: ScoredTicket,
                                existing_ticket_url: str) -> None:
        """Notify that a message maps to an existing open ticket."""
        t = scored_ticket.triage
        m = scored_ticket.message

        text = (
            f"🔁 *Nova mensagem mapeada para ticket existente*\n"
            f"Componente: `{t.component.value}` · Cliente: {scored_ticket.client.company_name}\n"
            f"Reportado por {m.sender_name}: _{m.text[:200]}_\n"
            f"→ <{existing_ticket_url}|Ver ticket aberto>"
        )

        await self._post({"channel": self.channel, "text": text})

    async def notify_resolution(self, ticket_title: str, client_name: str,
                                 reporter_phone: str, linear_url: str) -> None:
        """Notify that a ticket was resolved. Triggers WhatsApp feedback flow."""
        text = (
            f"✅ *Ticket resolvido:* {ticket_title}\n"
            f"Cliente: {client_name} · <{linear_url}|Ver ticket>\n"
            f"📲 Enviando confirmação no WhatsApp para {reporter_phone}..."
        )
        await self._post({"channel": self.channel, "text": text})

    async def _post(self, payload: dict) -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.webhook_url, json=payload, timeout=5.0)
            resp.raise_for_status()


# ─── Resolution Feedback Loop ────────────────────────────────────────────────

RESOLUTION_MESSAGE_TEMPLATE = """Olá {reporter_name}! 👋

Boa notícia: o problema que você reportou foi resolvido!

*Problema:* {ticket_title}
*Resolvido em:* {resolved_at}

Como você avalia o atendimento da Sharpi?

1️⃣ · 2️⃣ · 3️⃣ · 4️⃣ · 5️⃣

Responda com o número. Seu feedback nos ajuda a melhorar! 🙏"""


@dataclass
class ResolutionEvent:
    """Triggered by a Linear webhook when a ticket moves to 'Done'."""
    ticket_id: str
    ticket_title: str
    ticket_url: str
    resolved_at: str
    reporter_name: str
    reporter_phone: str
    client_company: str


class ResolutionNotifier:
    """
    Listens for Linear ticket resolution webhooks.
    Sends WhatsApp confirmation to the original reporter.
    Collects CSAT rating.
    """

    def __init__(self, whatsapp_sender, csat_store, slack: SlackNotifier):
        """
        whatsapp_sender: callable(phone, message) — your WhatsApp Business API wrapper
        csat_store: callable(ticket_id, phone, rating) — stores rating in DB
        slack: SlackNotifier instance
        """
        self.whatsapp = whatsapp_sender
        self.csat_store = csat_store
        self.slack = slack

    async def on_ticket_resolved(self, event: ResolutionEvent) -> None:
        """Handle a ticket resolution event end-to-end."""
        # 1. Notify Slack
        await self.slack.notify_resolution(
            ticket_title=event.ticket_title,
            client_name=event.client_company,
            reporter_phone=event.reporter_phone,
            linear_url=event.ticket_url,
        )

        # 2. Send WhatsApp to the original reporter
        message = RESOLUTION_MESSAGE_TEMPLATE.format(
            reporter_name=event.reporter_name,
            ticket_title=event.ticket_title,
            resolved_at=event.resolved_at,
        )
        await self.whatsapp(event.reporter_phone, message)

    async def on_csat_response(self, phone: str, ticket_id: str, rating: int) -> None:
        """Handle the reporter's rating reply (1–5)."""
        if not 1 <= rating <= 5:
            return  # Ignore invalid responses

        await self.csat_store(ticket_id, phone, rating)

        # Send a thank-you
        thank_you = f"Obrigado pelo feedback, {'⭐' * rating}! Estamos sempre melhorando. 🚀"
        await self.whatsapp(phone, thank_you)
