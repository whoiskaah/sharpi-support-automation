"""
main.py
Sharpi Support Automation — Orchestrator + FastAPI Webhook

Wires together:
  WhatsApp Webhook → Triage Agent → Context Enricher → Linear → Slack

Run: uvicorn main:app --reload
"""

import asyncio
import os
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from pydantic import BaseModel
import httpx

from triage_agent import (
    TriageAgent, IncomingMessage, ClientProfile,
    triage_message,
)
from context_enricher import ContextEnricher, SentryClient, BraintrustClient, LinearClient
from slack_notifier import SlackNotifier, ResolutionNotifier, ResolutionEvent

app = FastAPI(title="Sharpi Support Automation", version="1.0.0")

# ─── Clients (initialized from env vars) ─────────────────────────────────────

def get_clients():
    return {
        "triage_agent": TriageAgent(),
        "sentry": SentryClient(
            auth_token=os.environ["SENTRY_AUTH_TOKEN"],
            org_slug=os.environ["SENTRY_ORG"],
            project_slug=os.environ["SENTRY_PROJECT"],
        ),
        "braintrust": BraintrustClient(
            api_key=os.environ["BRAINTRUST_API_KEY"],
        ),
        "linear": LinearClient(
            api_key=os.environ["LINEAR_API_KEY"],
            team_id=os.environ["LINEAR_TEAM_ID"],
        ),
        "slack": SlackNotifier(
            webhook_url=os.environ["SLACK_WEBHOOK_URL"],
        ),
    }


# ─── Client Profile Fetcher (replace with your DB call) ──────────────────────

async def fetch_client_profile(phone: str) -> ClientProfile:
    """
    In production: query your DB by phone number or group name.
    Returns a ClientProfile with client metadata.
    """
    # Mock — replace with real DB query
    return ClientProfile(
        client_id="c_001",
        company_name="ACME Distribuidora",
        plan_tier="growth",
        days_since_onboarding=45,
        open_tickets=2,
        resolved_tickets_30d=8,
        is_in_production=True,
    )


# ─── Core Pipeline ───────────────────────────────────────────────────────────

async def process_support_message(message: IncomingMessage) -> dict:
    """Full triage pipeline. Returns ticket URL and priority."""
    clients = get_clients()

    enricher = ContextEnricher(
        sentry=clients["sentry"],
        braintrust=clients["braintrust"],
        linear=clients["linear"],
    )

    # 1. Fetch client profile
    client_profile = await fetch_client_profile(message.sender_phone)

    # 2. AI triage + deterministic scoring
    scored_ticket = triage_message(
        message=message,
        client=client_profile,
        agent=clients["triage_agent"],
    )

    # 3. Enrich with observability context (parallel queries)
    enrichment = await enricher.enrich(scored_ticket)

    slack = clients["slack"]
    linear = clients["linear"]

    # 4a. Duplicate detected → comment on existing ticket, notify Slack
    if enrichment.is_duplicate and enrichment.duplicate_ticket_id:
        comment = (
            f"Nova ocorrência reportada por **{message.sender_name}**:\n"
            f"> {message.text}\n\n"
            f"Grupo: {message.group_name} · {message.timestamp}"
        )
        await linear.add_comment(enrichment.duplicate_ticket_id, comment)

        existing_url = next(
            (t.url for t in enrichment.similar_tickets
             if t.ticket_id == enrichment.duplicate_ticket_id),
            "#"
        )
        await slack.notify_duplicate(scored_ticket, existing_url)

        return {
            "action": "duplicate_detected",
            "existing_ticket_id": enrichment.duplicate_ticket_id,
            "existing_ticket_url": existing_url,
        }

    # 4b. New issue → create Linear ticket, notify Slack
    ticket_url = await linear.create_ticket(scored_ticket, enrichment)
    await slack.notify_new_ticket(scored_ticket, enrichment, ticket_url)

    return {
        "action": "ticket_created",
        "ticket_url": ticket_url,
        "priority": scored_ticket.priority.value,
        "priority_score": scored_ticket.priority_score,
        "component": scored_ticket.triage.component.value,
        "issue_type": scored_ticket.triage.issue_type.value,
    }


# ─── Webhook Endpoints ───────────────────────────────────────────────────────

class WhatsAppWebhookPayload(BaseModel):
    """Shape of an incoming WhatsApp Business API webhook event."""
    message_id: str
    sender_name: str
    sender_phone: str
    group_name: str
    text: str
    has_image: bool = False
    image_description: str | None = None
    timestamp: str = ""


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(payload: WhatsAppWebhookPayload,
                           background_tasks: BackgroundTasks):
    """
    Receive WhatsApp messages from support groups.
    Processes asynchronously so webhook returns immediately.
    """
    message = IncomingMessage(**payload.model_dump())

    # Run pipeline in background so we can return 200 quickly
    background_tasks.add_task(process_support_message, message)

    return {"status": "accepted", "message_id": payload.message_id}


class LinearWebhookPayload(BaseModel):
    action: str
    data: dict


@app.post("/webhook/linear")
async def linear_webhook(payload: LinearWebhookPayload,
                         background_tasks: BackgroundTasks):
    """
    Receive Linear issue state change webhooks.
    Triggers resolution notification + CSAT when ticket moves to Done.
    """
    if payload.action != "update":
        return {"status": "ignored"}

    issue = payload.data.get("issue", {})
    state = issue.get("state", {}).get("name", "")

    if state != "Done":
        return {"status": "not_resolved_yet"}

    # Extract reporter info from issue description metadata
    # (stored when ticket was created)
    metadata = issue.get("metadata", {})
    event = ResolutionEvent(
        ticket_id=issue["id"],
        ticket_title=issue["title"],
        ticket_url=issue["url"],
        resolved_at=issue.get("completedAt", ""),
        reporter_name=metadata.get("reporter_name", ""),
        reporter_phone=metadata.get("reporter_phone", ""),
        client_company=metadata.get("client_company", ""),
    )

    # Mock whatsapp sender and csat store for this example
    async def mock_whatsapp(phone, message):
        print(f"[WhatsApp → {phone}]: {message[:100]}...")

    async def mock_csat_store(ticket_id, phone, rating):
        print(f"[CSAT] ticket={ticket_id} phone={phone} rating={rating}")

    clients = get_clients()
    notifier = ResolutionNotifier(
        whatsapp_sender=mock_whatsapp,
        csat_store=mock_csat_store,
        slack=clients["slack"],
    )

    background_tasks.add_task(notifier.on_ticket_resolved, event)
    return {"status": "resolution_notifier_triggered"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "sharpi-support-automation"}


# ─── Demo Runner (no server needed) ─────────────────────────────────────────

if __name__ == "__main__":
    """
    Demo mode: run the triage pipeline on realistic mock messages
    extracted from the case study screenshots.
    """
    from triage_agent import TriageAgent, IncomingMessage, ClientProfile, triage_message

    demo_messages = [
        IncomingMessage(
            message_id="msg_001",
            sender_name="Giovanna Pontes",
            sender_phone="+5521999990001",
            group_name="SADDI CENTER — Canal Digital Backoffice",
            text="Esta demorando demais para atualizar:",
            has_image=True,
            image_description="Screenshot showing product search for '4125' with empty results list and loading state",
            timestamp="15:31",
        ),
        IncomingMessage(
            message_id="msg_002",
            sender_name="Vendedora Bianca",
            sender_phone="+5521987199647",
            group_name="CAKE E CO — Suporte Sharpi",
            text="Olá, boa tarde! Deu esse erro aqui no meu pedido ... Alguém pode por favor me orientar ????",
            has_image=True,
            image_description="Order #385287 stuck in 'Solicitação de Aprovação Pendente' state. Client: CAKE E CO 1191. Payment: 7 DD. Address: Botafogo, RJ.",
            timestamp="15:26",
        ),
        IncomingMessage(
            message_id="msg_003",
            sender_name="vendedormultiplas",
            sender_phone="+5521987464195",
            group_name="BCO CUCINA ITA — Suporte Sharpi",
            text="olha aqui, novamente acontecendo",
            has_image=True,
            image_description="Error dialog on order #384985: 'Múltiplos erros ocorreram. Falha ao lançar pedido. Por favor entre em contato com o suporte. Erro interno.' Multiple errors on both launch and file sending.",
            timestamp="15:14",
        ),
        IncomingMessage(
            message_id="msg_004",
            sender_name="Bárbara Tavares | BOLD",
            sender_phone="+553799982175",
            group_name="BOLD — Canal Digital Backoffice",
            text="não esta identificando",
            has_image=True,
            image_description="Order creation from message showing items 7, 8, 9 (bold tube avelã, paçoca, trufa - 12 unid.) all flagged as 'não identificado' with DEFAULT unit type.",
            timestamp="17:14",
        ),
    ]

    demo_clients = {
        "+5521999990001": ClientProfile("c1", "SADDI CENTER", "growth", 90, 1, 5, True),
        "+5521987199647": ClientProfile("c2", "CAKE E CO", "starter", 10, 0, 1, True),
        "+5521987464195": ClientProfile("c3", "BCO CUCINA ITA", "enterprise", 120, 3, 20, True),
        "+553799982175":  ClientProfile("c4", "BOLD", "growth", 30, 1, 7, True),
    }

    agent = TriageAgent()

    print("\n" + "="*60)
    print("SHARPI SUPPORT AUTOMATION — DEMO")
    print("="*60 + "\n")

    for msg in demo_messages:
        client = demo_clients[msg.sender_phone]
        print(f"📩 Processando mensagem de {msg.sender_name}...")
        print(f"   Grupo: {msg.group_name}")
        print(f"   Texto: {msg.text[:80]}")

        scored = triage_message(msg, client, agent)

        priority_emoji = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
        emoji = priority_emoji.get(scored.priority.value, "⚪")

        print(f"\n   ┌─ Resultado da Triagem ─────────────────────────────")
        print(f"   │ {emoji} Prioridade: {scored.priority.value.upper()} (score: {scored.priority_score}/100)")
        print(f"   │ 🏷️  Tipo: {scored.triage.issue_type.value}")
        print(f"   │ ⚙️  Componente: {scored.triage.component.value}")
        print(f"   │ 📌 Título: {scored.triage.title}")
        print(f"   │ 👤 Atribuir para: {scored.triage.suggested_assignee_type} team")
        if scored.triage.urgency_signals:
            print(f"   │ ⚠️  Sinais: {', '.join(scored.triage.urgency_signals)}")
        print(f"   └─────────────────────────────────────────────────────")
        print()

    print("\nDemo concluído. Em produção, cada mensagem geraria:")
    print("  → 1 ticket no Linear com descrição estruturada")
    print("  → 1 notificação no Slack com links de observabilidade")
    print("  → WhatsApp de confirmação quando o ticket fechar\n")
