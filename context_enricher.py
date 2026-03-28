"""
context_enricher.py
Sharpi Support Automation — Context Enrichment

Queries observability tools (Sentry, Braintrust, Temporal) and Linear
to enrich a triage result with real technical context before creating a ticket.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional
import httpx

from triage_agent import Component, IssueType, ScoredTicket

# ─── Enrichment Types ────────────────────────────────────────────────────────

@dataclass
class SentryEvent:
    event_id: str
    title: str
    culprit: str
    count: int          # How many times in last 24h
    url: str
    first_seen: str
    last_seen: str

@dataclass
class BraintrustTrace:
    trace_id: str
    span_name: str
    error: str
    model: str
    input_preview: str
    url: str
    timestamp: str

@dataclass
class SimilarLinearTicket:
    ticket_id: str
    title: str
    status: str         # "triage" | "in_progress" | "done" | "cancelled"
    created_at: str
    url: str

@dataclass
class EnrichmentResult:
    sentry_events: list[SentryEvent] = field(default_factory=list)
    braintrust_traces: list[BraintrustTrace] = field(default_factory=list)
    similar_tickets: list[SimilarLinearTicket] = field(default_factory=list)
    is_duplicate: bool = False
    duplicate_ticket_id: Optional[str] = None
    enriched_description: str = ""  # Markdown with all context assembled


# ─── Individual Fetchers ─────────────────────────────────────────────────────

class SentryClient:
    """Queries Sentry Issues API for recent errors matching a component."""

    COMPONENT_TO_TAGS = {
        Component.PRODUCT_SEARCH: ["product-search", "catalog"],
        Component.ORDER_LAUNCH: ["order-launch", "erp-dispatch"],
        Component.APPROVAL_FLOW: ["approval", "order-approval"],
        Component.AI_CAPTURE: ["ai-capture", "message-parser"],
        Component.ERP_SYNC: ["erp-sync", "temporal-worker"],
        Component.AUTH: ["auth", "clerk"],
    }

    def __init__(self, auth_token: str, org_slug: str, project_slug: str):
        self.auth_token = auth_token
        self.org_slug = org_slug
        self.project_slug = project_slug
        self.base_url = f"https://sentry.io/api/0/projects/{org_slug}/{project_slug}"

    async def get_recent_errors(self, component: Component, limit: int = 3) -> list[SentryEvent]:
        tags = self.COMPONENT_TO_TAGS.get(component, [])
        if not tags:
            return []

        query = " OR ".join(f"tags[component]:{t}" for t in tags)
        params = {
            "query": query,
            "limit": limit,
            "sort": "date",
            "statsPeriod": "24h",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/issues/",
                headers={"Authorization": f"Bearer {self.auth_token}"},
                params=params,
                timeout=5.0,
            )
            resp.raise_for_status()
            issues = resp.json()

        return [
            SentryEvent(
                event_id=i["id"],
                title=i["title"],
                culprit=i.get("culprit", ""),
                count=i.get("count", 0),
                url=i.get("permalink", ""),
                first_seen=i.get("firstSeen", ""),
                last_seen=i.get("lastSeen", ""),
            )
            for i in issues
        ]


class BraintrustClient:
    """Queries Braintrust for recent AI trace failures."""

    def __init__(self, api_key: str, project_name: str = "sharpi-ai"):
        self.api_key = api_key
        self.project_name = project_name
        self.base_url = "https://api.braintrust.dev/v1"

    async def get_recent_failures(self, component: Component, limit: int = 3) -> list[BraintrustTrace]:
        # Only relevant for AI components
        if component not in (Component.AI_CAPTURE,):
            return []

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/experiment",
                headers={"Authorization": f"Bearer {self.api_key}"},
                params={
                    "project_name": self.project_name,
                    "filter": "error != null",
                    "limit": limit,
                },
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()

        traces = []
        for row in data.get("objects", []):
            traces.append(BraintrustTrace(
                trace_id=row.get("id", ""),
                span_name=row.get("name", ""),
                error=str(row.get("error", "")),
                model=row.get("metadata", {}).get("model", ""),
                input_preview=str(row.get("input", ""))[:200],
                url=f"https://www.braintrust.dev/app/{self.project_name}/logs/{row.get('id', '')}",
                timestamp=row.get("created", ""),
            ))

        return traces


class LinearClient:
    """Queries Linear for duplicate/similar tickets and creates new ones."""

    def __init__(self, api_key: str, team_id: str):
        self.api_key = api_key
        self.team_id = team_id
        self.graphql_url = "https://api.linear.app/graphql"

    async def find_similar_tickets(self, title: str, component: str,
                                   client_company: str) -> list[SimilarLinearTicket]:
        """Search Linear for tickets with similar title or component label."""
        query = f"""
        query SearchIssues($query: String!) {{
          issueSearch(query: $query, first: 5) {{
            nodes {{
              id
              title
              state {{ name }}
              createdAt
              url
            }}
          }}
        }}
        """
        # Search by component keyword + client name
        search_term = f"{component} {client_company}"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.graphql_url,
                headers={"Authorization": self.api_key},
                json={"query": query, "variables": {"query": search_term}},
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()

        tickets = []
        for node in data.get("data", {}).get("issueSearch", {}).get("nodes", []):
            tickets.append(SimilarLinearTicket(
                ticket_id=node["id"],
                title=node["title"],
                status=node.get("state", {}).get("name", "unknown"),
                created_at=node.get("createdAt", ""),
                url=node.get("url", ""),
            ))

        return tickets

    async def create_ticket(self, scored_ticket: ScoredTicket,
                            enrichment: "EnrichmentResult") -> str:
        """Create a Linear issue. Returns the new issue URL."""

        priority_map = {"urgent": 1, "high": 2, "medium": 3, "low": 4}

        description = _build_linear_description(scored_ticket, enrichment)

        mutation = """
        mutation CreateIssue($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id url identifier }
          }
        }
        """

        variables = {
            "input": {
                "teamId": self.team_id,
                "title": scored_ticket.triage.title,
                "description": description,
                "priority": priority_map.get(scored_ticket.priority.value, 3),
                "labelIds": [],  # Map issue_type to Linear labels in production
            }
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.graphql_url,
                headers={"Authorization": self.api_key},
                json={"query": mutation, "variables": variables},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()

        issue = data["data"]["issueCreate"]["issue"]
        return issue["url"]

    async def add_comment(self, ticket_id: str, comment: str) -> None:
        """Add a comment to an existing ticket (for duplicate detection)."""
        mutation = """
        mutation CreateComment($input: CommentCreateInput!) {
          commentCreate(input: $input) { success }
        }
        """
        async with httpx.AsyncClient() as client:
            await client.post(
                self.graphql_url,
                headers={"Authorization": self.api_key},
                json={"query": mutation, "variables": {
                    "input": {"issueId": ticket_id, "body": comment}
                }},
                timeout=5.0,
            )


# ─── Enrichment Orchestrator ─────────────────────────────────────────────────

class ContextEnricher:
    def __init__(self, sentry: SentryClient, braintrust: BraintrustClient,
                 linear: LinearClient):
        self.sentry = sentry
        self.braintrust = braintrust
        self.linear = linear

    async def enrich(self, scored_ticket: ScoredTicket) -> EnrichmentResult:
        """Run all enrichment queries in parallel."""
        triage = scored_ticket.triage
        client = scored_ticket.client

        # Fan out: all queries run concurrently
        sentry_task = self.sentry.get_recent_errors(triage.component)
        braintrust_task = self.braintrust.get_recent_failures(triage.component)
        similar_task = self.linear.find_similar_tickets(
            triage.title, triage.component.value, client.company_name
        )

        sentry_events, braintrust_traces, similar_tickets = await asyncio.gather(
            sentry_task, braintrust_task, similar_task,
            return_exceptions=True,
        )

        # Handle partial failures gracefully (don't break triage if Sentry is down)
        sentry_events = sentry_events if isinstance(sentry_events, list) else []
        braintrust_traces = braintrust_traces if isinstance(braintrust_traces, list) else []
        similar_tickets = similar_tickets if isinstance(similar_tickets, list) else []

        # Duplicate detection: open ticket with same component + client?
        is_duplicate = False
        duplicate_id = None
        for t in similar_tickets:
            if t.status in ("triage", "in_progress") and client.company_name.lower() in t.title.lower():
                is_duplicate = True
                duplicate_id = t.ticket_id
                break

        result = EnrichmentResult(
            sentry_events=sentry_events,
            braintrust_traces=braintrust_traces,
            similar_tickets=similar_tickets,
            is_duplicate=is_duplicate,
            duplicate_ticket_id=duplicate_id,
        )

        result.enriched_description = _build_linear_description(scored_ticket, result)
        return result


def _build_linear_description(scored_ticket: ScoredTicket, enrichment: EnrichmentResult) -> str:
    """Assemble a rich Markdown description for the Linear ticket."""
    t = scored_ticket.triage
    c = scored_ticket.client
    m = scored_ticket.message

    lines = [
        f"## Reportado por",
        f"**{m.sender_name}** · {m.group_name} · {m.timestamp}",
        "",
        f"## Mensagem original",
        f"> {m.text}",
        "",
        f"## Classificação",
        f"- **Tipo:** {t.issue_type.value}",
        f"- **Componente:** {t.component.value}",
        f"- **Prioridade score:** {scored_ticket.priority_score}/100",
        f"- **Sinais de urgência:** {', '.join(t.urgency_signals) if t.urgency_signals else 'nenhum'}",
        "",
        f"## Cliente",
        f"- **Empresa:** {c.company_name}",
        f"- **Plano:** {c.plan_tier}",
        f"- **Dias desde onboarding:** {c.days_since_onboarding}",
        f"- **Tickets abertos:** {c.open_tickets}",
        "",
    ]

    if enrichment.sentry_events:
        lines += ["## Erros Recentes no Sentry", ""]
        for e in enrichment.sentry_events:
            lines.append(f"- [{e.title}]({e.url}) — {e.count}x nas últimas 24h · último: {e.last_seen}")
        lines.append("")

    if enrichment.braintrust_traces:
        lines += ["## Traces de IA (Braintrust)", ""]
        for tr in enrichment.braintrust_traces:
            lines.append(f"- [{tr.span_name}]({tr.url}) — `{tr.error[:100]}`")
        lines.append("")

    if enrichment.similar_tickets:
        lines += ["## Tickets Similares", ""]
        for st in enrichment.similar_tickets:
            lines.append(f"- [{st.title}]({st.url}) — status: {st.status}")
        lines.append("")

    score_parts = [f"{k}: {v}" for k, v in scored_ticket.score_breakdown.items()]
    lines += [
        "## Score Breakdown (auditoria)",
        "```",
        "\n".join(score_parts),
        "```",
    ]

    return "\n".join(lines)
