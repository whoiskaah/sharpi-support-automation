
import streamlit as st
import time
from datetime import datetime

st.set_page_config(
    page_title="Sharpi Support Automation",
    page_icon="🧠",
    layout="wide"
)

EXAMPLES = {
    "ERP recorrente": {
        "message": "Erro ao lançar pedido no ERP, acontecendo de forma recorrente no mesmo cliente",
        "client": "Giovanna Pontes",
        "reported_by": "Letícia",
        "group": "Canal Digital Backoffice",
        "stage": "produção",
        "repeated": True,
        "multi_group": True,
    },
    "Aprovação travada": {
        "message": "Pedido travado em aprovação pendente, vendedora sem entender o motivo",
        "client": "Bianca",
        "reported_by": "Bianca",
        "group": "Suporte Sharpi",
        "stage": "produção",
        "repeated": False,
        "multi_group": False,
    },
    "Busca lenta": {
        "message": "Busca de produto demorando demais pra atualizar",
        "client": "Giovanna Pontes",
        "reported_by": "Giovanna",
        "group": "Canal Digital Backoffice",
        "stage": "produção",
        "repeated": False,
        "multi_group": False,
    },
    "IA não identificando": {
        "message": "Produtos não estão sendo identificados pela IA durante a captura do pedido",
        "client": "Bárbara Tavares",
        "reported_by": "Bárbara",
        "group": "BOLD",
        "stage": "produção",
        "repeated": True,
        "multi_group": False,
    },
    "Pedido via mensagem": {
        "message": "Criação de pedido a partir de mensagem não funcionando, sem mensagem de erro clara",
        "client": "Letícia",
        "reported_by": "Letícia",
        "group": "Canal Digital Backoffice",
        "stage": "produção",
        "repeated": True,
        "multi_group": False,
    },
}

CATEGORY_LABEL = {
    "integracao_erp": "integração ERP",
    "fluxo_aprovacao": "fluxo de aprovação",
    "busca_produtos": "busca de produtos",
    "captura_ia": "captura por IA",
    "suporte_geral": "suporte geral",
}

if "result" not in st.session_state:
    st.session_state["result"] = None
if "history" not in st.session_state:
    st.session_state["history"] = []

def classify_message(text: str, client_stage: str = "produção"):
    text = text.lower()

    if "erp" in text or "lançar pedido" in text or "lancar pedido" in text:
        return {
            "category": "integracao_erp",
            "type": "integração",
            "priority": "crítica",
            "score": 95,
            "team": "Time de Integração",
            "summary": "Falha recorrente ao lançar pedidos no ERP.",
        }

    if "aprovação" in text or "aprovacao" in text or "pendente" in text:
        return {
            "category": "fluxo_aprovacao",
            "type": "bug" if client_stage == "produção" else "dúvida de uso",
            "priority": "alta",
            "score": 78,
            "team": "Time de Produto / Operações",
            "summary": "Pedido travado em aprovação sem feedback claro.",
        }

    if "produto" in text or "busca" in text or "demorando" in text:
        return {
            "category": "busca_produtos",
            "type": "bug" if client_stage == "produção" else "dúvida de uso",
            "priority": "média",
            "score": 52,
            "team": "Time de Produto / Frontend",
            "summary": "Busca de produto com lentidão ou atualização inconsistente.",
        }

    if (
        "ia" in text
        or "captura" in text
        or "não identificando" in text
        or "nao identificando" in text
        or "não está identificando" in text
        or "nao esta identificando" in text
    ):
        return {
            "category": "captura_ia",
            "type": "ia",
            "priority": "alta",
            "score": 82,
            "team": "Time de IA",
            "summary": "Fluxo de captura por IA não está identificando corretamente os itens do pedido.",
        }

    return {
        "category": "suporte_geral",
        "type": "suporte",
        "priority": "baixa",
        "score": 30,
        "team": "Suporte",
        "summary": "Solicitação geral de suporte sem classificação específica.",
    }

def get_context(category: str):
    contexts = {
        "integracao_erp": {
            "sources": ["Encore / AWS logs", "Temporal workflow traces"],
            "hint": "Validar workflow de sincronização com ERP, payload enviado e falhas no envio do arquivo do pedido.",
            "next_step": "Comparar payload enviado, status do workflow no Temporal e logs de sincronização no backend.",
        },
        "fluxo_aprovacao": {
            "sources": ["Encore / AWS logs", "Temporal workflow traces"],
            "hint": "Investigar regra de aprovação, status do pedido e possíveis falhas de condição.",
            "next_step": "Verificar regra de aprovação aplicada, status do pedido e último evento de transição.",
        },
        "busca_produtos": {
            "sources": ["Vercel frontend logs", "Sentry frontend issues"],
            "hint": "Verificar lentidão no frontend, consulta de busca e atualização de catálogo.",
            "next_step": "Analisar latência percebida pelo usuário e chamadas de busca no frontend.",
        },
        "captura_ia": {
            "sources": ["Braintrust AI traces", "Temporal workflow traces", "Encore / AWS logs"],
            "hint": "Analisar traces da IA, parsing da mensagem e pipeline de captura do pedido.",
            "next_step": "Validar como a mensagem foi interpretada e onde os itens deixaram de ser reconhecidos.",
        },
        "suporte_geral": {
            "sources": ["Histórico de tickets", "Notas internas"],
            "hint": "Reunir mais contexto antes de definir o próximo passo técnico.",
            "next_step": "Solicitar detalhes adicionais ou encaminhar para triagem humana.",
        },
    }
    return contexts.get(category, contexts["suporte_geral"])

def build_ticket(message, client, reported_by, group_name, triage, context, repeated, multi_group, stage):
    return {
        "title": f"[{triage['priority'].upper()}] {client} - {triage['summary']}",
        "description": f"""Cliente: {client}
Grupo: {group_name}
Reportado por: {reported_by}
Estágio do cliente: {stage}
Tipo: {triage['type']}
Categoria: {triage['category']}
Prioridade: {triage['priority']} ({triage['score']}/100)
Recorrente: {repeated}
Múltiplos grupos: {multi_group}

Resumo estruturado:
{triage['summary']}

Mensagem original:
{message}

Logs sugeridos:
{", ".join(context["sources"])}

Hipótese inicial:
{context["hint"]}

Próximo passo sugerido:
{context["next_step"]}
""",
    }

def build_slack_message(ticket, triage):
    return f"""Novo incidente triado automaticamente

Prioridade: {triage['priority']}
Categoria: {CATEGORY_LABEL.get(triage['category'], triage['category'])}
Owner sugerido: {triage['team']}
Resumo: {triage['summary']}

Ticket: {ticket['title']}
Próximo passo: iniciar investigação com os logs sugeridos.
"""

def build_client_message(reported_by, group_name, summary):
    return f"""Oi, {reported_by}! Identificamos e tratamos o problema que você reportou no grupo {group_name}.

Resumo da correção: {summary}

Se puder, nos avalie com uma nota de 1 a 5 para sabermos se a comunicação foi útil.
"""

def build_phase2_result(triage, context):
    return {
        "base": f"Baseado em tickets resolvidos de {CATEGORY_LABEL.get(triage['category'], triage['category'])}",
        "pattern": triage["summary"],
        "root_cause": context["hint"],
        "suggested_fix": context["next_step"],
    }

def build_phase3_article(client, triage, context):
    return {
        "title": f"Como resolver: {triage['summary']}",
        "client": client,
        "problem": triage["summary"],
        "probable_cause": context["hint"],
        "how_to_resolve": context["next_step"],
        "when_to_use": "Usar em casos recorrentes com o mesmo padrão de erro e solução validada pelo time.",
    }

def run_pipeline(message, client, reported_by, group_name, stage, repeated, multi_group):
    triage = classify_message(message, stage)
    context = get_context(triage["category"])
    ticket = build_ticket(message, client, reported_by, group_name, triage, context, repeated, multi_group, stage)
    slack = build_slack_message(ticket, triage)
    client_message = build_client_message(reported_by, group_name, triage["summary"])
    phase2 = build_phase2_result(triage, context)
    article = build_phase3_article(client, triage, context)
    return {
        "triage": triage,
        "context": context,
        "ticket": ticket,
        "slack": slack,
        "client_message": client_message,
        "phase2": phase2,
        "article": article,
    }

st.title("Sharpi Support Automation")
st.caption("Fase 1: triagem automática, ticket estruturado, alerta operacional, investigação guiada e fechamento com cliente.")

st.markdown("### Visão geral da solução")
st.write(
    "WhatsApp → triagem → contexto técnico → ticket → alerta interno → resolução → retorno ao cliente → aprendizado → autoatendimento"
)

overview1, overview2, overview3 = st.columns(3)
overview1.info("Fase 1\n\nOrganiza a operação e reduz a triagem manual.")
overview2.info("Fase 2\n\nAprende com tickets resolvidos e sugere causa e solução.")
overview3.info("Fase 3\n\nTransforma resoluções em conteúdo de autoatendimento.")

with st.sidebar:
    st.header("Entrada do incidente")
    selected = st.selectbox("Exemplo rápido", list(EXAMPLES.keys()), index=0)
    example = EXAMPLES[selected]

    client = st.text_input("Cliente", value=example["client"])
    reported_by = st.text_input("Quem reportou", value=example["reported_by"])
    group_name = st.text_input("Grupo de WhatsApp", value=example["group"])
    stage = st.selectbox("Estágio do cliente", ["novo", "produção"], index=1 if example["stage"] == "produção" else 0)
    repeated = st.checkbox("Problema recorrente", value=example["repeated"])
    multi_group = st.checkbox("Mesmo problema em múltiplos grupos", value=example["multi_group"])

message = st.text_area("Mensagem recebida no WhatsApp", value=example["message"], height=120)

st.markdown("### O que esta fase resolve")
st.write(
    "A fase 1 organiza a entrada, define prioridade, estrutura o ticket, sugere contexto técnico e aciona o fluxo interno. "
    "A resolução ainda é humana, mas já deixa de depender de leitura manual e repasse informal."
)

if st.button("Triar mensagem", type="primary", use_container_width=True):
    progress = st.progress(0, text="Iniciando triagem...")
    steps = [
        ("Lendo mensagem recebida", 15),
        ("Classificando tipo de incidente", 35),
        ("Avaliando prioridade", 55),
        ("Gerando contexto técnico", 70),
        ("Estruturando ticket", 85),
        ("Preparando alerta e fechamento", 100),
    ]
    timeline = []
    for label, pct in steps:
        time.sleep(0.3)
        progress.progress(pct, text=label)
        timeline.append({"horário": datetime.utcnow().strftime("%H:%M:%S"), "etapa": label})

    result = run_pipeline(message, client, reported_by, group_name, stage, repeated, multi_group)
    result["timeline"] = timeline
    st.session_state["result"] = result
    st.session_state["raw_message"] = message
    st.session_state["history"].insert(0, {
        "mensagem": message[:60] + ("..." if len(message) > 60 else ""),
        "categoria": CATEGORY_LABEL.get(result["triage"]["category"], result["triage"]["category"]),
        "prioridade": result["triage"]["priority"],
        "owner": result["triage"]["team"],
    })

if st.session_state["result"]:
    result = st.session_state["result"]
    triage = result["triage"]
    context = result["context"]

    st.markdown("## Antes e depois da triagem")
    left, right = st.columns(2)
    with left:
        st.markdown("**Antes**")
        st.code(st.session_state["raw_message"], language="text")
    with right:
        st.markdown("**Depois**")
        st.json({
            "tipo": triage["type"],
            "categoria": triage["category"],
            "prioridade": triage["priority"],
            "score": triage["score"],
            "resumo": triage["summary"],
            "owner_sugerido": triage["team"],
        })

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Tipo", triage["type"])
    m2.metric("Categoria", CATEGORY_LABEL.get(triage["category"], triage["category"]))
    m3.metric("Prioridade", triage["priority"])
    m4.metric("Score", triage["score"])

    st.markdown("## Processo acontecendo")
    for item in result["timeline"]:
        st.write(f"{item['horário']} — {item['etapa']}")

    tabs = st.tabs([
        "Contexto técnico",
        "Ticket",
        "Alerta interno",
        "Retorno ao cliente",
        "Fase 2",
        "Fase 3",
        "Histórico"
    ])

    with tabs[0]:
        st.subheader("Contexto técnico sugerido")
        st.write("**Fontes sugeridas:**")
        for source in context["sources"]:
            st.write(f"- {source}")
        st.write("**Hipótese inicial:**")
        st.info(context["hint"])
        st.write("**Próximo passo:**")
        st.write(context["next_step"])

    with tabs[1]:
        st.subheader("Ticket estruturado")
        st.code(result["ticket"]["description"], language="text")

    with tabs[2]:
        st.subheader("Alerta operacional")
        st.code(result["slack"], language="text")

    with tabs[3]:
        st.subheader("Fechamento com cliente")
        st.code(result["client_message"], language="text")

    with tabs[4]:
        st.subheader("Fase 2 — agente orientado por aprendizado")
        st.write(result["phase2"]["base"])
        st.write(f"**Padrão detectado:** {result['phase2']['pattern']}")
        st.write(f"**Causa raiz sugerida:** {result['phase2']['root_cause']}")
        st.write(f"**Solução sugerida:** {result['phase2']['suggested_fix']}")

    with tabs[5]:
        article = result["article"]
        st.subheader("Fase 3 — artigo de autoatendimento")
        st.write(f"**Título:** {article['title']}")
        st.write(f"**Cliente alvo:** {article['client']}")
        st.write(f"**Problema:** {article['problem']}")
        st.write(f"**Causa provável:** {article['probable_cause']}")
        st.write(f"**Como resolver:** {article['how_to_resolve']}")
        st.write(f"**Quando usar:** {article['when_to_use']}")

    with tabs[6]:
        st.subheader("Histórico de incidentes processados")
        if st.session_state["history"]:
            st.dataframe(st.session_state["history"], use_container_width=True)
        else:
            st.write("Nenhuma mensagem processada ainda.")
else:
    st.info("Selecione um exemplo ou escreva uma mensagem e clique em 'Triar mensagem'.")
