# Sharpi — Support Automation via IA
**Katri Hakulinen · Case Técnico**

---

## TL;DR

O problema não é falta de informação — é falta de estrutura. As mensagens existem, os logs existem, o contexto existe. O que falta é um agente que conecte tudo isso e libere o time de fazer triagem manual.

A proposta é um pipeline em duas fases:

- **Fase 1** — Triagem automática: captura mensagens do WhatsApp, classifica com IA, enriquece com logs observabilidade, cria ticket no Linear e notifica no Slack. Time para de garimpar mensagens e começa a só resolver.
- **Fase 2** — Resolução autônoma: agente aprende com as correções documentadas na Fase 1, detecta padrões nos logs, propõe fix, abre PR. Humano só revisa.

Bônus que vai fazer diferença: quando o ticket fecha, o próprio agente manda WhatsApp pro cliente confirmando que está resolvido e coleta feedback.

---

## O Problema Real

Olhando os prints, os problemas reportados são bem concretos:

| # | Problema | Componente |
|---|----------|------------|
| 1 | Busca de produto demorando pra atualizar | Product Search |
| 2 | Pedido travado em aprovação pendente, vendedora sem entender | Approval Flow |
| 3 | Erro recorrente ao lançar pedido no ERP (mesmo cliente) | ERP Integration |
| 4 | Produtos não identificados pela IA na captura | AI Capture |
| 5 | Criação de pedido por mensagem não funciona, sem erro claro | AI Capture |

Cinco problemas distintos, todos reportados em diferentes grupos do WhatsApp, provavelmente sem ninguém sabendo que o #3 já aconteceu 3x antes. Esse é o custo real do processo manual.

---

## Arquitetura — Fase 1: Triagem e Ticketing

```
Mensagem no WhatsApp (texto, print, print + descrição vaga)
        │
        ▼
┌─────────────────────────────────┐
│  Ingestion Layer                │
│  Webhook WhatsApp Business API  │
│  Extração: sender, group, mídia │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│  AI Triage Agent (Claude)       │
│                                 │
│  → Classifica tipo de issue:    │
│    bug | uso | config | erp |   │
│    ai_failure                   │
│                                 │
│  → Extrai componente afetado    │
│  → Detecta urgência linguística │
│  → Gera título e descrição      │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│  Context Enricher               │
│                                 │
│  → Sentry: erros recentes       │
│    no componente identificado   │
│  → Braintrust: traces de IA     │
│    com falha nas últimas 2h     │
│  → Temporal: workflows com erro │
│  → Linear: tickets similares    │
│    (deduplicação)               │
│  → Client profile: maturidade,  │
│    plano, histórico de tickets  │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│  Priority Scoring (Determinístico)│
│                                 │
│  score = base_severity          │
│        + recurrence_penalty     │
│        + client_tier_weight     │
│        + time_sensitivity       │
│                                 │
│  P0 → sistema fora / bloqueio   │
│  P1 → bug recorrente / crítico  │
│  P2 → problema intermitente     │
│  P3 → dúvida de uso             │
└────────────────┬────────────────┘
                 │
          ┌──────┴──────┐
          │             │
     Duplicata?       Novo
          │             │
          ▼             ▼
   Commenta no    Cria ticket
   ticket atual   no Linear
          │             │
          └──────┬──────┘
                 │
                 ▼
┌─────────────────────────────────┐
│  Slack Notification             │
│                                 │
│  Canal: #support-triage         │
│  → Embed rico com:              │
│    título, prioridade, cliente, │
│    link do ticket Linear,       │
│    link do trace relevante      │
│  → @mention do responsável      │
│    por tipo de issue            │
└────────────────┬────────────────┘
                 │
                 ▼
        Time resolve no Linear
                 │
                 ▼
┌─────────────────────────────────┐
│  Resolution Notifier            │
│                                 │
│  Ticket fechado no Linear       │
│  → Webhook dispara              │
│  → Agente manda WhatsApp pro    │
│    cliente que reportou:        │
│    "Olá [nome], seu problema    │
│    [X] foi resolvido! Como foi  │
│    seu atendimento? (1-5)"      │
│  → Resposta salva no banco      │
│  → CSAT atualizado no dashboard │
└─────────────────────────────────┘
```

---

## Arquitetura — Fase 2: Resolução Autônoma

A Fase 2 só faz sentido depois que a Fase 1 rodou por algumas semanas e o time documentou como resolveu cada tipo de problema. O aprendizado vem dos humanos — o agente lê os tickets resolvidos e aprende os padrões.

```
Ticket criado (Fase 1)
        │
        ▼
┌─────────────────────────────────┐
│  Pattern Matcher                │
│                                 │
│  → Busca tickets similares      │
│    resolvidos (embedding)       │
│  → Identifica padrão conhecido  │
│  → Confiança > threshold?       │
└────────────────┬────────────────┘
                 │
         Alta confiança?
          ┌──────┴──────┐
         Sim            Não
          │              │
          ▼              ▼
   Tenta resolver    Vai pra fila
   automaticamente   humana normal
          │
          ▼
┌─────────────────────────────────┐
│  Root Cause Agent               │
│                                 │
│  → Lê logs completos (Sentry,   │
│    Encore, Temporal, Braintrust)│
│  → Cruza com código no GitHub   │
│  → Propõe hipótese de causa     │
│  → Gera fix proposto            │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│  PR Agent                       │
│                                 │
│  → Abre draft PR no GitHub      │
│  → Descrição: causa, solução,   │
│    tickets relacionados,        │
│    traces de evidência          │
│  → Posta no Slack para revisão  │
│    humana                       │
└────────────────┬────────────────┘
                 │
          Humano aprova?
          ┌──────┴──────┐
         Sim            Não
          │              │
          ▼              ▼
   Merge + Deploy   Feedback pro
          │         agente (loop)
          ▼
   Notifica cliente
   (mesmo fluxo Fase 1)
```

---

## Decisões de Design

### Por que scoring determinístico pra prioridade?

Prioridade tem consequências reais: define quem acorda às 3h da manhã se o sistema cai. Deixar isso completamente pro LLM cria risco de inconsistência e dificulta auditoria. O scoring determinístico garante que todo P0 pode ser explicado com um número — e o time pode calibrar a fórmula conforme aprende.

O LLM faz o que LLM faz bem: interpretar linguagem ambígua, extrair componente de um print sem contexto, gerar título humano-readable de um "tá quebrando".

### Por que deduplicação via Linear e não só embedding?

Embedding captura similaridade semântica — útil. Mas Linear search por título + componente + cliente captura o caso mais comum: o mesmo erro acontecendo de novo no mesmo cliente. É mais rápido, mais barato, e mais preciso pra esse caso específico. Os dois juntos cobrem bem.

### Por que separar Fase 1 e Fase 2?

A Fase 2 depende de dados de alta qualidade sobre como os bugs foram resolvidos. Esses dados não existem ainda — precisam ser criados pelos humanos na Fase 1. Pular direto pra autonomia de resolução sem esse histórico seria um agente alucinando fixes.

### O que ficou de fora (e por quê)

- **Auto-resposta no WhatsApp pra clientes durante triagem**: útil no futuro, mas cria expectativas de SLA que o time precisa estar pronto pra cumprir. Fase posterior.
- **Dashboard de métricas**: Linear + Slack já têm bom tracking nativo. Não vale a complexidade agora.
- **Integração direta com ERP**: fora do escopo — o Temporal já cuida disso.

---

## Stack do PoC

| Componente | Tecnologia |
|------------|-----------|
| Triage Agent | Claude Sonnet via Anthropic SDK |
| Orquestração | Python async (asyncio) |
| Linear API | GraphQL via `linear-sdk` |
| Slack | `slack-sdk` Webhook |
| Observabilidade mock | Estrutura pronta pra plugar Sentry SDK |
| Deduplicação | Linear search + embeddings (text-embedding-3-small) |
| WhatsApp trigger | Webhook handler (FastAPI) |

---

## O que entregamos como PoC

1. **`triage_agent.py`** — núcleo do agente: classifica, extrai, pontua
2. **`context_enricher.py`** — consulta Sentry/Braintrust/Linear pra enriquecer ticket
3. **`linear_client.py`** — criação e atualização de tickets
4. **`slack_notifier.py`** — notificações ricas no Slack
5. **`resolution_notifier.py`** — loop de feedback pós-resolução
6. **`main.py`** — orquestrador completo com FastAPI webhook
7. **`demo.py`** — demo rodável com mensagens dos prints reais

---

## Impacto Esperado

| Métrica | Hoje | Com Fase 1 |
|---------|------|------------|
| Tempo médio pra abrir ticket | 10–20min | < 30s |
| Mensagens perdidas/sem ticket | ~30% | ~0% |
| Duplicatas não detectadas | frequente | raro |
| Visibilidade de padrões recorrentes | zero | automática |
| CSAT coletado sistematicamente | não | sim |

---

*Katri Hakulinen · Março 2026*
