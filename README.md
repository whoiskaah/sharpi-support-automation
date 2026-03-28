# Sharpi Support Automation

Pipeline de triagem automática de mensagens de suporte via WhatsApp.

## Estrutura

```
sharpi-solution/
├── triage_agent.py       # Core: classificação AI + scoring determinístico
├── context_enricher.py   # Enriquecimento: Sentry, Braintrust, Linear
├── slack_notifier.py     # Notificações Slack + loop de feedback pós-resolução
├── main.py               # Orquestrador FastAPI + demo runner
├── SOLUTION.md           # Documento completo de solução
└── requirements.txt      # Dependências
```

## Setup

```bash
pip install -r requirements.txt
```

### Variáveis de ambiente necessárias

```env
# Anthropic (triage agent)
ANTHROPIC_API_KEY=sk-ant-...

# Sentry
SENTRY_AUTH_TOKEN=...
SENTRY_ORG=sharpi
SENTRY_PROJECT=sharpi-web

# Braintrust
BRAINTRUST_API_KEY=...

# Linear
LINEAR_API_KEY=lin_api_...
LINEAR_TEAM_ID=...

# Slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

## Rodar o demo (sem servidor)

```bash
python main.py
```

Processa as 4 mensagens reais dos prints do case e mostra a classificação + scoring de cada uma.

## Rodar o servidor

```bash
uvicorn main:app --reload
```

Endpoints:
- `POST /webhook/whatsapp` — recebe mensagem do WhatsApp
- `POST /webhook/linear` — recebe evento de resolução do Linear
- `GET /health` — healthcheck

## Fluxo completo

```
Mensagem WhatsApp
    → POST /webhook/whatsapp
    → triage_message() [Claude classificação + scoring determinístico]
    → ContextEnricher.enrich() [Sentry + Braintrust + Linear em paralelo]
    → Duplicata? → comenta no ticket existente + Slack
    → Novo? → cria ticket Linear + notifica Slack
    
Ticket resolvido no Linear
    → POST /webhook/linear  
    → ResolutionNotifier.on_ticket_resolved()
    → WhatsApp pro cliente confirmando resolução
    → Cliente responde rating 1-5
    → CSAT salvo no banco
```


## Notes

This repository contains the implementation-oriented version of the case solution, focused on production architecture rather than a visual demo.
