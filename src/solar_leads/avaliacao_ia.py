"""Avaliação dos contatos pela IA: uma nota de 0 a 10 e o motivo, para cada empresa da lista.

As empresas vão em grupos de 25 para o modelo barato (`ia.MODELO_AVALIACAO`), com o critério do
perfil. Só dados públicos da empresa saem do servidor: nome, atividade, porte, capital, anos,
bairro e cidade, sócios, natureza. Nunca telefone, e-mail, nome do dono ou CPF; em empresário
individual, cuja razão social é o nome da pessoa, o nome só vai se for um nome fantasia.
As notas ficam guardadas por critério e CNPJ: a mesma empresa nunca é paga duas vezes.
Acima de `LIMITE_DIRETO` empresas, usa o processamento em lote da API (metade do preço, até 1 hora).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import ia, pastas

GRUPO = 25
EM_PARALELO = 4
LIMITE_DIRETO = 5000
CUSTO_POR_EMPRESA_USD = 0.00006  # medido em 17/11/2026: 30 empresas por US$ 0,0018 com o Haiku 5.5
VERSAO_DO_CRITERIO = "v1"

INSTRUCOES = """Você avalia empresas brasileiras como possíveis clientes, para uma equipe de vendas B2B.
Para cada empresa da lista, dê uma nota inteira de 0 a 10 de quanto ela se encaixa no critério abaixo,
e um motivo curto (até 15 palavras, em português do Brasil, sem repetir o nome da empresa).
10 = cliente ideal; 7 a 9 = bom cliente; 4 a 6 = talvez; 1 a 3 = improvável; 0 = fora do perfil.
Use só os dados informados. Quando faltar informação, seja moderado e diga isso no motivo.
Devolva exatamente uma nota para cada id recebido.

Critério:
"""

ESQUEMA = {
    "type": "object",
    "properties": {
        "notas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "nota": {"type": "integer"}, "motivo": {"type": "string"}},
                "required": ["id", "nota", "motivo"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["notas"],
    "additionalProperties": False,
}


def chave(criterio: str) -> str:
    """Identifica o critério (e a versão das instruções) nas notas guardadas."""
    return hashlib.sha256(f"{VERSAO_DO_CRITERIO}|{(criterio or '').strip()}".encode()).hexdigest()[:20]


def estimar_usd(empresas: int) -> float:
    custo = empresas * CUSTO_POR_EMPRESA_USD
    return round(custo * (0.5 if empresas > LIMITE_DIRETO else 1.0), 4)


def _limpo(texto) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\d.\-/]{6,}", " ", str(texto or ""))).strip()


def _sem_acento_maiusculo(texto: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", texto.upper()) if unicodedata.category(c) != "Mn")


def dados_publicos(linha: dict, ident: int) -> dict:
    """O que vai para a IA sobre uma empresa. Nada de telefone, e-mail, dono ou CPF."""
    natureza = str(linha.get("natureza") or "")
    individual = "individual" in natureza.lower() or "empresário" in natureza.lower()
    nome = _limpo(linha.get("empresa"))
    razao = _limpo(linha.get("razao_social"))
    if individual and _sem_acento_maiusculo(nome) == _sem_acento_maiusculo(razao):
        nome = ""  # a "empresa" é o próprio nome da pessoa
    saida = {
        "id": ident,
        "nome": nome,
        "razao_social": "" if individual else razao,
        "atividade": linha.get("atividade") or "",
        "porte": linha.get("porte") or "",
        "capital_social": linha.get("capital_social") or "",
        "anos_de_empresa": linha.get("idade_empresa"),
        "natureza": natureza,
        "simples": linha.get("simples") or "",
        "situacao": linha.get("situacao") or "",
        "unidades_ativas": linha.get("unidades_ativas"),
        "socios": linha.get("numero_socios"),
        "atividades_secundarias": linha.get("atividades_secundarias"),
        "bairro": linha.get("bairro") or "",
        "cidade": linha.get("cidade") or "",
        "uf": linha.get("uf") or "",
    }
    return {k: v for k, v in saida.items() if v not in (None, "")}


def _pedido(grupo: list[dict], criterio: str) -> dict:
    """Os parâmetros de uma chamada (servem para a chamada direta e para o lote)."""
    return {
        "model": ia.MODELO_AVALIACAO,
        "max_tokens": 8000,
        "system": [{"type": "text", "text": INSTRUCOES + criterio.strip(), "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": json.dumps([dados_publicos(l, i) for i, l in enumerate(grupo)], ensure_ascii=False)}],
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA}},
    }


def _ler_notas(texto: str, grupo: list[dict]) -> dict[str, tuple[int, str]]:
    try:
        notas = json.loads(texto).get("notas") or []
    except (ValueError, AttributeError):
        return {}
    saida = {}
    for n in notas:
        try:
            i, nota = int(n["id"]), int(n["nota"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < len(grupo) and grupo[i].get("cnpj"):
            saida[grupo[i]["cnpj"]] = (max(0, min(10, nota)), " ".join(str(n.get("motivo") or "").split())[:200])
    return saida


def avaliar(empresas: list[dict], criterio: str, *, parar=None, avisar=None, ref: str = "", cliente=None) -> dict:
    """Avalia as empresas (linhas da lista, uma por CNPJ). Pula as que já têm nota com este critério.

    Devolve o resumo: avaliadas, já tinham nota, recusadas, custo e o motivo da parada.
    `avisar(texto, feitos, total)` recebe o andamento.
    """
    criterio = (criterio or "").strip()
    if len(criterio) < 15:
        raise ia.IaIndisponivel("O perfil desta lista não tem critério para a IA. Escreva o que você vende e quem é o cliente ideal.")
    k = chave(criterio)
    unicas = list({e["cnpj"]: e for e in empresas if e.get("cnpj")}.values())
    ja = pastas.notas_da_ia(k, [e["cnpj"] for e in unicas])
    fila = [e for e in unicas if e["cnpj"] not in ja]
    resumo = {"chave": k, "empresas": len(unicas), "ja_tinham": len(ja), "avaliadas": 0, "recusadas": 0, "custo_usd": 0.0}
    aviso = avisar or (lambda texto, feitos, total: None)
    if not fila:
        return {**resumo, "motivo": "todas já tinham nota"}
    ia.conferir_teto(estimar_usd(len(fila)))
    cliente = cliente or ia.cliente()
    if len(fila) > LIMITE_DIRETO:
        return _em_lote(cliente, fila, criterio, k, resumo, aviso, parar, ref)
    grupos = [fila[i : i + GRUPO] for i in range(0, len(fila), GRUPO)]
    total = len(fila)
    aviso(f"Avaliando 0 de {total} empresas", 0, total)
    parada = {"motivo": "concluída"}
    trava = threading.Lock()

    def um(grupo: list[dict]) -> None:
        if parar is not None and parar.is_set():
            parada["motivo"] = "parada a pedido"
            return
        if ia.gasto_do_mes() > ia.teto_usd():
            parada["motivo"] = "teto de gasto do mês atingido"
            return
        try:
            resposta = cliente.messages.create(**_pedido(grupo, criterio))
        except Exception as e:  # noqa: BLE001 - o motivo vai para a tela; os grupos já feitos ficam guardados
            parada["motivo"] = str(ia.erro_da_api(e))
            return
        valor = ia.registrar("avaliacao", getattr(resposta, "model", "") or ia.MODELO_AVALIACAO, resposta.usage, ref)
        notas = _ler_notas(ia.texto_da_resposta(resposta), grupo) if resposta.stop_reason != "refusal" else {}
        if notas:
            pastas.gravar_notas_da_ia(k, notas, ia.MODELO_AVALIACAO)
        with trava:
            resumo["custo_usd"] += valor
            resumo["avaliadas"] += len(notas)
            resumo["recusadas"] += len(grupo) - len(notas)
            feitos = resumo["avaliadas"] + resumo["recusadas"]
        aviso(f"Avaliando {feitos} de {total} empresas", feitos, total)

    with ThreadPoolExecutor(EM_PARALELO) as execucao:
        list(execucao.map(um, grupos))
    resumo["custo_usd"] = round(resumo["custo_usd"], 4)
    return {**resumo, "motivo": parada["motivo"]}


def _em_lote(cliente, fila, criterio, k, resumo, aviso, parar, ref) -> dict:
    """Muitas empresas: manda tudo de uma vez pela API de lotes (metade do preço) e espera o resultado."""
    grupos = [fila[i : i + GRUPO] for i in range(0, len(fila), GRUPO)]
    total = len(fila)
    try:
        lote = cliente.messages.batches.create(requests=[{"custom_id": f"g{i}", "params": _pedido(g, criterio)} for i, g in enumerate(grupos)])
    except Exception as e:  # noqa: BLE001
        raise ia.erro_da_api(e) from e
    inicio = time.monotonic()
    while True:
        aviso(f"Avaliando {total} empresas em lote (até 1 hora; metade do preço)", 0, total)
        if parar is not None and parar.is_set():
            try:
                cliente.messages.batches.cancel(lote.id)
            except Exception:  # noqa: BLE001 - cancelar é melhor esforço
                pass
            return {**resumo, "motivo": "parada a pedido", "lote": lote.id}
        lote = cliente.messages.batches.retrieve(lote.id)
        if lote.processing_status == "ended":
            break
        if time.monotonic() - inicio > 25 * 3600:
            return {**resumo, "motivo": "o lote passou de 24 horas", "lote": lote.id}
        time.sleep(30)
    for item in cliente.messages.batches.results(lote.id):
        grupo = grupos[int(item.custom_id[1:])]
        if item.result.type != "succeeded":
            resumo["recusadas"] += len(grupo)
            continue
        resposta = item.result.message
        resumo["custo_usd"] += ia.registrar("avaliacao-lote", getattr(resposta, "model", "") or ia.MODELO_AVALIACAO, resposta.usage, ref, lote=True)
        notas = _ler_notas(ia.texto_da_resposta(resposta), grupo) if resposta.stop_reason != "refusal" else {}
        if notas:
            pastas.gravar_notas_da_ia(k, notas, ia.MODELO_AVALIACAO)
        resumo["avaliadas"] += len(notas)
        resumo["recusadas"] += len(grupo) - len(notas)
    aviso(f"Avaliadas {resumo['avaliadas']} de {total} empresas", total, total)
    resumo["custo_usd"] = round(resumo["custo_usd"], 4)
    return {**resumo, "motivo": "concluída", "lote": lote.id}
