"""Busca por descrição: a pessoa escreve o que vende e quem quer achar; a IA monta o perfil de busca.

A IA (modelo forte, `ia.MODELO_BUSCA`) trabalha com ferramentas que consultam a base da Receita
deste servidor: acha a cidade, procura atividades (CNAE), conta empresas com os filtros, vê
exemplos de nomes e procura palavras no nome. No fim chama `entregar_perfil`, e o sistema confere
a proposta (códigos que existem, contagens de verdade) antes de mostrar na tela.
O que vai para a Anthropic: o texto da pessoa e dados públicos de empresas (atividade, contagens,
nome fantasia). Nunca telefone, e-mail, nome de dono ou CPF.
"""

from __future__ import annotations

import json
from datetime import date

import duckdb

from . import cnpj_db, config, ia, perfis

MAX_VOLTAS = 14
MUNICIPIOS = config.DADOS / "municipios_uf.parquet"

SISTEMA = """Você monta perfis de busca de clientes para um sistema brasileiro de prospecção B2B.
O sistema tem a base completa de empresas da Receita Federal (CNPJ): atividade principal (CNAE),
município, data de abertura, porte (01 micro, 03 pequeno, 05 médio ou grande), situação, MEI.
Depois de pronto, o perfil gera uma lista com os donos dessas empresas e o telefone deles.

A pessoa vai descrever o que vende e quem quer encontrar. Sua tarefa:
1. Se ela citou uma cidade, ache-a com `achar_cidade`. Sem cidade, trabalhe sem ela (cidade nula).
2. Ache as atividades (CNAE) em que o cliente ideal está, com `buscar_atividades`. Procure por vários
   termos: o nome do ramo, sinônimos e ramos vizinhos que também compram. Use só códigos que as
   ferramentas devolveram.
3. Confira com `contar_empresas` (e, se ajudar, `exemplos_de_empresas`) se cada atividade tem
   empresas e se elas são mesmo o cliente descrito. Corte atividades vazias ou fora do alvo.
4. Dê peso a cada atividade: 3 = cliente ideal, 2 = bom cliente, 1 = vale a pena incluir.
5. Escolha filtros só quando a pessoa pediu ou quando o produto claramente exige (ex.: produto caro
   pede porte pequeno ou maior; "com mais de 2 anos" vira idade mínima 2).
6. Chame `entregar_perfil` uma única vez, com a proposta final. Escreva tudo em português do Brasil.

`criterio_ia` é o texto que outra IA vai usar para dar nota de 0 a 10 a cada empresa da lista, só com
dados públicos (nome, atividade, porte, capital, idade, bairro). Escreva 2 a 4 frases: o que é vendido,
quem é o cliente ideal e o que torna uma empresa melhor ou pior para essa venda.
`explicacao` é para a pessoa: 2 a 4 frases simples dizendo por que essas atividades e filtros.
Seja econômico: em geral bastam de 4 a 10 chamadas de ferramenta."""


def _texto(v) -> str:
    return " ".join(str(v or "").split())


def _curto(texto: str, limite: int) -> str:
    """Corta no fim de uma palavra, para o nome não ficar pela metade."""
    if len(texto) <= limite:
        return texto
    return texto[:limite].rsplit(" ", 1)[0].rstrip(" –-,·")


# ---------- ferramentas (consultas na base) ----------

def achar_cidade(con: duckdb.DuckDBPyConnection, texto: str) -> list[dict]:
    termo = _texto(texto)
    if len(termo) < 2 or not MUNICIPIOS.exists():
        return []
    linhas = con.execute(
        f"""
        SELECT exibicao, uf, empresas, nome FROM read_parquet('{MUNICIPIOS}')
        WHERE nome = strip_accents(upper(?)) OR contains(nome, strip_accents(upper(?)))
        ORDER BY nome <> strip_accents(upper(?)), empresas DESC LIMIT 6
        """,
        [termo, termo, termo],
    ).fetchall()
    return [{"cidade": e.title(), "uf": uf, "empresas": n} for e, uf, n, _ in linhas]


def _codigo_municipio(con, cidade: str, uf: str) -> tuple[str, str] | None:
    if not (cidade and uf) or not MUNICIPIOS.exists():
        return None
    achado = con.execute(
        f"SELECT codigo, exibicao FROM read_parquet('{MUNICIPIOS}') WHERE uf = ? AND nome = strip_accents(upper(?))",
        [uf.upper(), _texto(cidade)],
    ).fetchone()
    return (achado[0], achado[1]) if achado else None


def buscar_atividades(con: duckdb.DuckDBPyConnection, termo: str) -> list[dict]:
    termo = _texto(termo)
    if len(termo) < 3:
        return []
    linhas = con.execute(
        f"""
        SELECT trim(codigo), descricao FROM read_parquet('{config.CNPJ_PARQUET}/cnaes_*.parquet')
        WHERE strip_accents(lower(descricao)) LIKE '%' || strip_accents(lower(?)) || '%' OR trim(codigo) LIKE ? || '%'
        ORDER BY descricao LIMIT 25
        """,
        [termo, "".join(c for c in termo if c.isdigit()) or "x"],
    ).fetchall()
    return [{"codigo": c, "descricao": d} for c, d in linhas]


def _onde(cidade_uf: tuple[str, str] | None, so_ativas: bool, incluir_mei: bool, idade_min: int, portes: list[str]) -> tuple[str, list]:
    cond, params = ["matriz_filial = '1'", "coalesce(natureza_juridica, '') NOT LIKE '1%'"], []
    if cidade_uf:
        cond.append("municipio = ? AND uf = ?")
        params += list(cidade_uf)
    if so_ativas:
        cond.append("situacao = '02'")
    if not incluir_mei:
        cond.append("NOT coalesce(mei, false)")
    if idade_min:
        limite = date.today().replace(year=date.today().year - int(idade_min)).strftime("%Y%m%d")
        cond.append("data_inicio_atividade <= ?")
        params.append(limite)
    portes = [p for p in portes if p in perfis.PORTES]
    if portes:
        cond.append("trim(porte) IN (SELECT unnest(?))")
        params.append(portes)
    return " AND ".join(cond), params


def contar_empresas(
    con: duckdb.DuckDBPyConnection, *, cidade: str = "", uf: str = "", codigos: list[str] | None = None,
    so_ativas: bool = True, incluir_mei: bool = False, idade_min: int = 0, portes: list[str] | None = None,
) -> dict:
    """Sedes de empresas por atividade, na cidade (ou no estado, ou no país) e com os filtros."""
    municipio = _codigo_municipio(con, cidade, uf)
    if cidade and not municipio:
        return {"erro": f"Cidade não encontrada: {cidade}/{uf}. Use achar_cidade."}
    onde, params = _onde((municipio[0], uf.upper()) if municipio else None, so_ativas, incluir_mei, idade_min, portes or [])
    if not municipio and uf:
        onde += " AND uf = ?"
        params.append(uf.upper())
    codigos = sorted({"".join(c for c in x if c.isdigit()) for x in codigos or []} - {""})
    if codigos:
        onde += " AND cnae_fiscal IN (SELECT unnest(?))"
        params.append(codigos)
    linhas = con.execute(
        f"SELECT cnae_fiscal, any_value(cnae_descricao), count(*) FROM estab_cidade WHERE {onde} GROUP BY 1 ORDER BY 3 DESC LIMIT 40",
        params,
    ).fetchall()
    por = [{"codigo": c, "descricao": d, "empresas": n} for c, d, n in linhas]
    vistos = {p["codigo"] for p in por}
    por += [{"codigo": c, "descricao": "", "empresas": 0} for c in codigos if c not in vistos]
    return {
        "onde": f"{municipio[1].title()}/{uf.upper()}" if municipio else (uf.upper() or "Brasil"),
        "total": sum(p["empresas"] for p in por), "por_atividade": por,
    }


def exemplos_de_empresas(con: duckdb.DuckDBPyConnection, *, codigo: str, cidade: str = "", uf: str = "", quantidade: int = 8) -> list[dict]:
    """Nomes fantasia (públicos) de empresas ativas da atividade; nunca razão social de pessoa física."""
    municipio = _codigo_municipio(con, cidade, uf)
    onde, params = _onde((municipio[0], uf.upper()) if municipio else None, True, False, 0, [])
    if not municipio and uf:
        onde += " AND uf = ?"
        params.append(uf.upper())
    linhas = con.execute(
        f"""
        SELECT nome_fantasia, data_inicio_atividade, trim(porte) FROM estab_cidade
        WHERE {onde} AND cnae_fiscal = ? AND coalesce(trim(nome_fantasia), '') <> ''
        ORDER BY hash(cnpj) LIMIT ?
        """,
        params + ["".join(c for c in codigo if c.isdigit()), max(1, min(12, int(quantidade or 8)))],
    ).fetchall()
    hoje = date.today().year
    return [
        {"nome": _texto(n).title(), "anos": hoje - int(d[:4]) if d and d[:4].isdigit() else None, "porte": {"01": "micro", "03": "pequeno", "05": "médio ou grande"}.get(p, "")}
        for n, d, p in linhas
    ]


def buscar_por_nome(con: duckdb.DuckDBPyConnection, *, palavras: list[str], uf: str = "") -> dict:
    palavras = [p for p in (_texto(x).upper() for x in palavras or []) if len(p) >= 3][:10]
    if not palavras:
        return {"erro": "Informe palavras com pelo menos 3 letras."}
    nome = "strip_accents(upper(coalesce(razao_social, '') || ' ' || coalesce(nome_fantasia, '')))"
    cond = " OR ".join(f"contains({nome}, strip_accents(?))" for _ in palavras)
    onde, params = _onde(None, True, False, 0, [])
    if uf:
        onde += " AND uf = ?"
        params.append(uf.upper())
    linhas = con.execute(
        f"SELECT cnae_fiscal, any_value(cnae_descricao), count(*) FROM estab_cidade WHERE ({cond}) AND {onde} GROUP BY 1 ORDER BY 3 DESC LIMIT 20",
        palavras + params,
    ).fetchall()
    return {"total": sum(n for *_, n in linhas), "por_atividade": [{"codigo": c, "descricao": d, "empresas": n} for c, d, n in linhas]}


# ---------- definição das ferramentas para a IA ----------

def _obj(propriedades: dict) -> dict:
    return {"type": "object", "properties": propriedades, "required": list(propriedades), "additionalProperties": False}


_PORTES = {"type": "array", "items": {"type": "string", "enum": list(perfis.PORTES)}}
FERRAMENTAS = [
    {"name": "achar_cidade", "description": "Acha um município brasileiro pelo nome. Devolve cidade, UF e número de empresas na base.",
     "strict": True, "input_schema": _obj({"texto": {"type": "string", "description": "Nome da cidade, com ou sem acento"}})},
    {"name": "buscar_atividades", "description": "Procura atividades econômicas (CNAE) pela descrição ou pelo começo do código. Até 25 resultados.",
     "strict": True, "input_schema": _obj({"termo": {"type": "string", "description": "Palavra da descrição, como 'clínica' ou 'academia'"}})},
    {"name": "contar_empresas", "description": "Conta sedes de empresas por atividade, com os filtros. Sem códigos, mostra as 40 atividades mais comuns do lugar. Cidade vazia conta o estado (ou o país, sem UF).",
     "strict": True, "input_schema": _obj({
         "cidade": {"type": "string"}, "uf": {"type": "string"},
         "codigos": {"type": "array", "items": {"type": "string"}, "description": "Códigos CNAE de 7 dígitos"},
         "so_ativas": {"type": "boolean"}, "incluir_mei": {"type": "boolean"},
         "idade_min": {"type": "integer", "description": "Anos mínimos de empresa (0 = qualquer)"}, "portes": _PORTES})},
    {"name": "exemplos_de_empresas", "description": "Nomes fantasia de algumas empresas ativas de uma atividade, para conferir se são mesmo o cliente.",
     "strict": True, "input_schema": _obj({"codigo": {"type": "string"}, "cidade": {"type": "string"}, "uf": {"type": "string"},
                                            "quantidade": {"type": "integer"}})},
    {"name": "buscar_por_nome", "description": "Conta empresas que têm certas palavras no nome, por atividade. Útil quando o nicho não tem CNAE próprio.",
     "strict": True, "input_schema": _obj({"palavras": {"type": "array", "items": {"type": "string"}}, "uf": {"type": "string"}})},
    {"name": "entregar_perfil", "description": "Entrega a proposta final de perfil de busca. Chame uma única vez, no fim.",
     "strict": True, "input_schema": _obj({
         "nome_sugerido": {"type": "string", "description": "Nome curto do perfil, até 40 letras"},
         "oferta": {"type": "string", "description": "O que é vendido, numa frase"},
         "cliente_ideal": {"type": "string", "description": "Quem é o cliente ideal, numa ou duas frases"},
         "criterio_ia": {"type": "string"}, "explicacao": {"type": "string"},
         "cidade": {"type": "string", "description": "Cidade achada com achar_cidade, ou vazio"},
         "uf": {"type": "string", "description": "UF da cidade, ou vazio"},
         "atividades": {"type": "array", "items": _obj({
             "codigo": {"type": "string"}, "peso": {"type": "integer", "enum": [1, 2, 3]},
             "motivo": {"type": "string", "description": "Por que esta atividade, em poucas palavras"}})},
         "palavras": {"type": "array", "items": {"type": "string"}, "description": "Palavras no nome da empresa; em geral vazio"},
         "so_ativas": {"type": "boolean"}, "incluir_mei": {"type": "boolean"}, "idade_min": {"type": "integer"}, "portes": _PORTES})},
]

_ETAPAS = {
    "achar_cidade": lambda e: f"Procurando a cidade “{e.get('texto', '')}”",
    "buscar_atividades": lambda e: f"Procurando atividades: “{e.get('termo', '')}”",
    "contar_empresas": lambda e: f"Contando empresas{' em ' + e['cidade'] if e.get('cidade') else ''}",
    "exemplos_de_empresas": lambda e: "Olhando exemplos de empresas",
    "buscar_por_nome": lambda e: "Procurando palavras no nome das empresas",
    "entregar_perfil": lambda e: "Conferindo a proposta",
}


def _executar(con, nome: str, entrada: dict):
    if nome == "achar_cidade":
        return achar_cidade(con, entrada.get("texto", ""))
    if nome == "buscar_atividades":
        return buscar_atividades(con, entrada.get("termo", ""))
    if nome == "contar_empresas":
        return contar_empresas(con, **{k: entrada.get(k) for k in ("cidade", "uf", "codigos", "so_ativas", "incluir_mei", "idade_min", "portes") if entrada.get(k) is not None})
    if nome == "exemplos_de_empresas":
        return exemplos_de_empresas(con, **{k: entrada.get(k) for k in ("codigo", "cidade", "uf", "quantidade") if entrada.get(k) is not None})
    if nome == "buscar_por_nome":
        return buscar_por_nome(con, palavras=entrada.get("palavras") or [], uf=entrada.get("uf") or "")
    return {"erro": f"Ferramenta desconhecida: {nome}"}


# ---------- a conversa com a IA ----------

def _chamar(cliente, mensagens: list, ref: str):
    resposta = cliente.beta.messages.create(
        model=ia.MODELO_BUSCA, max_tokens=16000, system=SISTEMA, tools=FERRAMENTAS, messages=mensagens,
        output_config={"effort": "medium"}, cache_control={"type": "ephemeral"},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    )
    ia.registrar("busca", getattr(resposta, "model", "") or ia.MODELO_BUSCA, resposta.usage, ref)
    return resposta


def montar_perfil(descricao: str, *, etapa=None, ref: str = "", cliente=None) -> dict:
    """Conversa com a IA até ela entregar o perfil. Devolve a proposta conferida (ver `conferir`)."""
    descricao = (descricao or "").strip()
    if len(descricao) < 10:
        raise ia.IaIndisponivel("Descreva com um pouco mais de detalhe o que você vende e quem quer encontrar.")
    if len(descricao) > 2000:
        raise ia.IaIndisponivel("A descrição pode ter até 2.000 letras.")
    ia.conferir_teto(0.30)
    aviso = etapa or (lambda texto: None)
    cliente = cliente or ia.cliente()
    mensagens: list = [{"role": "user", "content": descricao}]
    lembrou = False
    # Mesma configuração das outras conexões do servidor: o DuckDB recusa abrir o arquivo de dois jeitos no mesmo processo.
    con = cnpj_db.conectar(tentativas=10)
    try:
        for _ in range(MAX_VOLTAS):
            aviso("Pensando na busca")
            try:
                resposta = _chamar(cliente, mensagens, ref)
            except ia.IaIndisponivel:
                raise
            except Exception as e:  # noqa: BLE001 - erros do SDK viram frase para a tela
                raise ia.erro_da_api(e) from e
            if resposta.stop_reason == "refusal":
                raise ia.IaIndisponivel("A IA não quis atender este pedido. Reescreva a descrição com outras palavras.")
            if resposta.stop_reason == "max_tokens":
                raise ia.IaIndisponivel("A resposta da IA ficou longa demais. Tente uma descrição mais curta.")
            usos = [b for b in resposta.content if getattr(b, "type", "") == "tool_use"]
            final = next((b for b in usos if b.name == "entregar_perfil"), None)
            if final is not None:
                aviso("Conferindo a proposta")
                return conferir(con, dict(final.input))
            mensagens.append({"role": "assistant", "content": resposta.content})
            if not usos:
                if lembrou:
                    raise ia.IaIndisponivel("A IA não conseguiu montar o perfil. Tente descrever de outro jeito.")
                lembrou = True
                mensagens.append({"role": "user", "content": "Entregue a proposta chamando entregar_perfil."})
                continue
            resultados = []
            for b in usos:
                aviso(_ETAPAS.get(b.name, lambda e: "Consultando a base")(dict(b.input)))
                try:
                    saida = _executar(con, b.name, dict(b.input))
                    resultados.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(saida, ensure_ascii=False)})
                except Exception as e:  # noqa: BLE001 - a IA vê o erro e pode tentar de outro jeito
                    resultados.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True, "content": f"{type(e).__name__}: {e}"})
            mensagens.append({"role": "user", "content": resultados})
        raise ia.IaIndisponivel("A IA demorou demais para montar o perfil. Tente uma descrição mais direta.")
    finally:
        con.close()


def conferir(con: duckdb.DuckDBPyConnection, proposta: dict) -> dict:
    """Confere o que a IA entregou com a base: só códigos que existem, contagens de verdade, cidade achada."""
    pedidos = {}
    for a in proposta.get("atividades") or []:
        codigo = "".join(c for c in str(a.get("codigo") or "") if c.isdigit())
        if len(codigo) == 7 and codigo not in pedidos:
            pedidos[codigo] = a
    descricoes = dict(
        con.execute(
            f"SELECT trim(codigo), descricao FROM read_parquet('{config.CNPJ_PARQUET}/cnaes_*.parquet') WHERE trim(codigo) IN (SELECT unnest(?))",
            [sorted(pedidos)],
        ).fetchall()
    ) if pedidos else {}
    filtros = {
        "so_ativas": bool(proposta.get("so_ativas", True)), "incluir_mei": bool(proposta.get("incluir_mei", False)),
        "so_locais": True, "idade_min": max(0, min(50, int(proposta.get("idade_min") or 0))),
        "portes": sorted({p for p in proposta.get("portes") or []} & set(perfis.PORTES)),
    }
    cidade, uf = _texto(proposta.get("cidade")), _texto(proposta.get("uf")).upper()
    achada = _codigo_municipio(con, cidade, uf) if cidade and uf else None
    contagem = contar_empresas(
        con, cidade=cidade if achada else "", uf=uf if achada else "", codigos=list(descricoes),
        so_ativas=filtros["so_ativas"], incluir_mei=filtros["incluir_mei"], idade_min=filtros["idade_min"], portes=filtros["portes"],
    ) if descricoes else {"total": 0, "por_atividade": []}
    por_codigo = {p["codigo"]: p["empresas"] for p in contagem.get("por_atividade", [])}
    atividades = sorted(
        (
            {"codigo": c, "descricao": descricoes[c], "peso": max(1, min(3, int(pedidos[c].get("peso") or 2))),
             "motivo": _texto(pedidos[c].get("motivo"))[:200], "empresas": por_codigo.get(c, 0)}
            for c in descricoes
        ),
        key=lambda a: (-a["peso"], -a["empresas"]),
    )
    empresas_cidade = None
    if achada:
        empresas_cidade = con.execute(f"SELECT empresas FROM read_parquet('{MUNICIPIOS}') WHERE codigo = ? AND uf = ?", [achada[0], uf]).fetchone()[0]
    return {
        "nome": _curto(_texto(proposta.get("nome_sugerido")), 60) or "Novo perfil",
        "oferta": _texto(proposta.get("oferta"))[:500],
        "cliente_ideal": _texto(proposta.get("cliente_ideal"))[:1000],
        "criterio_ia": str(proposta.get("criterio_ia") or "").strip()[:2000],
        "explicacao": str(proposta.get("explicacao") or "").strip()[:2000],
        "cidade": {"nome": achada[1].title(), "uf": uf, "empresas": empresas_cidade} if achada else None,
        "atividades": atividades,
        "palavras": [p for p in (_texto(x).upper() for x in proposta.get("palavras") or []) if len(p) >= 3][:10],
        "filtros": filtros,
        "total": sum(a["empresas"] for a in atividades),
    }
