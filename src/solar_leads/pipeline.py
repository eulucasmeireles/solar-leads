"""Da lista enriquecida até as linhas prontas: telefone escolhido, ordem, repetidos e resumo.

Usado pela linha de comando e pelo sistema web, para os dois darem o mesmo resultado.
"""

from __future__ import annotations

import csv
import re
import shutil
from functools import lru_cache
import tempfile
from pathlib import Path

import duckdb

from . import enriquecer, qualificacao

# chave, cabeçalho no CSV (os em inglês são os que o GHL reconhece sozinho), rótulo na tela, grupo, tipo
CAMPOS: list[tuple[str, str, str, str, str]] = [
    ("primeiro_nome", "First Name", "Nome", "Contato", "texto"),
    ("sobrenome", "Last Name", "Sobrenome", "Contato", "texto"),
    ("telefone", "Phone", "Telefone", "Contato", "telefone"),
    ("telefone_alternativo", "Telefone alternativo", "Telefone alternativo", "Contato", "telefone"),
    ("email", "Email", "E-mail", "Contato", "texto"),
    ("tipo_email", "Tipo de e-mail", "Tipo de e-mail", "Contato", "texto"),
    ("faixa", "Faixa", "Faixa", "Contato", "texto"),
    ("chance_dono", "Chance de ser o dono", "Chance de ser o dono (%)", "Contato", "numero"),
    ("motivos_contato", "Motivos do contato", "Por que este número", "Contato", "lista"),
    ("whatsapp", "WhatsApp verificado", "WhatsApp verificado", "Contato", "texto"),
    ("tipo_whatsapp", "Tipo de WhatsApp", "Tipo de WhatsApp", "Contato", "texto"),
    ("nome_whatsapp_business", "Nome no WhatsApp Business", "Nome no WhatsApp Business", "Contato", "texto"),
    ("empresa", "Business Name", "Empresa", "Empresa", "texto"),
    ("razao_social", "Razao social", "Razão social", "Empresa", "texto"),
    ("cnpj", "CNPJ", "CNPJ", "Empresa", "cnpj"),
    ("situacao", "Situacao do CNPJ", "Situação do CNPJ", "Empresa", "texto"),
    ("data_situacao", "Data da situacao", "Data da situação", "Empresa", "data"),
    ("atividade", "Atividade", "Atividade", "Empresa", "texto"),
    ("matriz_filial", "Matriz ou filial", "Matriz ou filial", "Empresa", "texto"),
    ("unidades_ativas", "Unidades ativas", "Unidades ativas (matriz e filiais)", "Empresa", "numero"),
    ("natureza", "Natureza juridica", "Natureza jurídica", "Empresa", "texto"),
    ("simples", "Simples Nacional", "Simples Nacional", "Empresa", "texto"),
    ("idade_empresa", "Anos de empresa", "Anos de empresa", "Empresa", "numero"),
    ("atividades_secundarias", "Atividades secundarias", "Atividades secundárias", "Empresa", "numero"),
    ("empresas_no_endereco", "Empresas no mesmo endereco", "Empresas ativas no mesmo endereço", "Empresa", "numero"),
    ("porte", "Porte", "Porte", "Empresa", "texto"),
    ("capital_social", "Capital social", "Capital social", "Empresa", "dinheiro"),
    ("abertura", "Abertura", "Abertura", "Empresa", "data"),
    ("mei", "MEI", "MEI", "Empresa", "simnao"),
    ("endereco", "Address", "Endereço", "Empresa", "texto"),
    ("bairro", "Bairro", "Bairro", "Empresa", "texto"),
    ("cidade", "City", "Cidade", "Empresa", "texto"),
    ("uf", "State", "Estado", "Empresa", "texto"),
    ("cep", "Postal Code", "CEP", "Empresa", "texto"),
    ("site", "Website", "Site", "Empresa", "link"),
    ("papel", "Papel do contato", "Papel na empresa", "Dono", "texto"),
    ("qualificacao_dono", "Cargo do dono", "Cargo", "Dono", "texto"),
    ("idade_dono", "Idade do dono", "Faixa de idade", "Dono", "texto"),
    ("socio_desde", "Socio desde", "Sócio desde", "Dono", "data"),
    ("numero_socios", "Numero de socios", "Sócios na empresa", "Dono", "numero"),
    ("empresas_do_dono", "Empresas do dono", "Empresas em que é ou foi sócio", "Dono", "numero"),
    ("empresas_ativas_do_dono", "Empresas ativas do dono", "Empresas ativas", "Dono", "numero"),
    ("capital_do_dono", "Capital das empresas do dono", "Capital somado das empresas ativas", "Dono", "dinheiro"),
    ("outras_empresas_do_dono", "Outras empresas do dono", "Outras empresas ativas", "Dono", "lista"),
    ("solar", "Ja tem solar", "Registro na ANEEL", "Energia solar", "texto"),
    ("solar_desde", "Solar desde", "Sistema desde", "Energia solar", "data"),
    ("solar_kw", "Solar kW", "Potência instalada (kW)", "Energia solar", "numero"),
    ("solar_sistemas", "Sistemas solares", "Sistemas registrados", "Energia solar", "numero"),
    ("solar_onde", "Onde fica o sistema", "Onde fica o sistema", "Energia solar", "texto"),
    ("nota_ia", "Nota da IA", "Nota da IA (0 a 10)", "Qualificação", "numero"),
    ("motivo_ia", "Motivo da IA", "Por que essa nota da IA", "Qualificação", "texto"),
    ("encaixe", "Encaixe no perfil", "Encaixe no perfil (0 a 10)", "Qualificação", "numero"),
    ("motivos_encaixe", "Motivos do encaixe", "Por que esse encaixe", "Qualificação", "lista"),
    ("oportunidade", "Oportunidade", "Onde está a oportunidade", "Qualificação", "texto"),
    ("aptidao_solar", "Aptidao solar", "Potencial geral (0 a 10)", "Qualificação", "numero"),
    ("potencial_casa", "Potencial da casa", "Potencial da casa do dono (0 a 10)", "Qualificação", "numero"),
    ("motivos_casa", "Motivos da casa", "Por que essa nota da casa", "Qualificação", "lista"),
    ("potencial_empresa", "Potencial da empresa", "Potencial da empresa (0 a 10)", "Qualificação", "numero"),
    ("motivos_solar", "Motivos solar", "Por que essa nota da empresa", "Qualificação", "lista"),
    ("fonte", "Fonte", "Fonte", "Qualificação", "texto"),
    ("confianca_cnpj", "Confianca do CNPJ", "Confiança do CNPJ", "Qualificação", "numero"),
    ("avaliacoes", "Avaliacoes no Maps", "Avaliações no Maps", "Qualificação", "numero"),
    ("link_maps", "Link do Maps", "Link do Maps", "Qualificação", "link"),
    ("tags", "Tags", "Tags", "Qualificação", "texto"),
]
CHAVES = [c[0] for c in CAMPOS]
CABECALHOS = [c[1] for c in CAMPOS]
# Colunas guardadas além das exibidas (controle interno).
INTERNAS = ["dono_chave", "ordem", "cnae"]
INTEIRAS = ("ordem", "aptidao_solar", "chance_dono", "potencial_casa", "potencial_empresa", "encaixe", "nota_ia")
# Versão do formato das listas gravadas. 2: encaixe no perfil, nota da IA e código da atividade.
VERSAO = 3  # 2: perfis, encaixe e nota da IA; 3: cada e-mail num contato só

PORTE = {"01": "Microempresa", "03": "Pequeno porte", "05": "Médio ou grande"}

# Para quem a lista vai ser usada. "dono": oferecer energia solar ao dono (casa ou empresa dele).
# "empresa": oferecer outra coisa para a empresa; aí as notas de solar não dizem nada.
PUBLICOS = ("dono", "empresa")
# Colunas que só fazem sentido no público "dono": ficam fora da tela e do CSV no público "empresa".
SO_DO_DONO = frozenset(
    c for c, _, _, grupo, _ in CAMPOS if grupo == "Energia solar"
) | {"oportunidade", "aptidao_solar", "potencial_casa", "motivos_casa", "potencial_empresa", "motivos_solar"}


# No público "dono" (energia solar) o encaixe é a própria nota solar: não aparece duas vezes.
SO_FORA_DO_SOLAR = frozenset({"encaixe", "motivos_encaixe"})


def _mostra(chave: str, publico: str) -> bool:
    return chave not in (SO_DO_DONO if publico == "empresa" else SO_FORA_DO_SOLAR)


def chaves_do_publico(publico: str) -> list[str]:
    return [c for c in CHAVES if _mostra(c, publico)]


def cabecalhos_do_publico(publico: str) -> list[str]:
    return [cab for c, cab, *_ in CAMPOS if _mostra(c, publico)]


def pontuar_encaixe(linhas: list[dict], publico: str, pesos: dict[str, int] | None = None) -> None:
    """Preenche `encaixe`: a nota solar no público "dono"; nos outros, o encaixe no perfil."""
    for l in linhas:
        if publico == "dono":
            l["encaixe"], l["motivos_encaixe"] = int(l.get("aptidao_solar") or 0), l.get("motivos_solar") or ""
        else:
            nota, motivos = qualificacao.encaixe(l, pesos)
            l["encaixe"], l["motivos_encaixe"] = nota, "; ".join(motivos)


def passa_nos_filtros(linha: dict, filtros: dict | None) -> bool:
    """Filtros do perfil que a base aplica depois de carregar: anos mínimos de empresa e porte."""
    if not filtros:
        return True
    idade_min = int(filtros.get("idade_min") or 0)
    if idade_min:
        try:
            if float(linha.get("idade_empresa") or -1) < idade_min:
                return False
        except (TypeError, ValueError):
            return False
    portes = filtros.get("portes") or []
    if portes:
        porte = str(linha.get("porte") or "").strip()
        if porte.zfill(2) not in portes and porte not in {PORTE[p] for p in portes if p in PORTE}:
            return False
    return True


def aplicar_notas_da_ia(linhas: list[dict], notas: dict[str, tuple[int, str]] | None) -> None:
    """Grava em cada linha a nota da IA da empresa dela (as notas são por CNPJ)."""
    for l in linhas:
        nota = (notas or {}).get(l.get("cnpj") or "")
        l["nota_ia"], l["motivo_ia"] = (nota[0], nota[1]) if nota else (None, "")


@lru_cache(maxsize=4096)
def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", enriquecer.norm(texto).lower()).strip("-")


def sem_repetidos(linhas: list[dict]) -> list[dict]:
    """Uma linha por telefone e por dono (a primeira, que é a mais bem colocada)."""
    telefones: set[str] = set()
    donos: set[str] = set()
    saida = []
    for l in linhas:
        if l["telefone"] in telefones or (l["dono_chave"] and l["dono_chave"] in donos):
            continue
        telefones.add(l["telefone"])
        if l["dono_chave"]:
            donos.add(l["dono_chave"])
        saida.append(l)
    return saida


def email_uma_vez(linhas: list[dict], disparo: list[dict]) -> int:
    """Cada e-mail fica só no melhor contato que o tem; nos outros, sai e o tipo vira "repetido".

    O e-mail vem do cadastro da empresa na Receita e vai igual para cada sócio: sem isso, a mesma caixa
    recebe o disparo várias vezes e o GHL junta sócios diferentes num contato só ao importar.
    Os contatos de disparo vêm primeiro, na ordem da lista; depois as demais linhas. Devolve quantos saíram.
    """
    do_disparo = {id(l) for l in disparo}
    vistos: set[str] = set()
    tirados = 0
    for l in [*disparo, *(l for l in linhas if id(l) not in do_disparo)]:
        email = (l.get("email") or "").strip().lower()
        if not email:
            continue
        if email in vistos:
            l["email"], l["tipo_email"] = "", "repetido"
            tirados += 1
        else:
            vistos.add(email)
    return tirados


def montar(
    leads: list[dict],
    cidade: str,
    tem_whatsapp: dict[str, dict] | None = None,
    so_pessoal: bool = False,
    min_aptidao: int = 0,
    publico: str = "dono",
    pesos: dict[str, int] | None = None,
    filtros: dict | None = None,
    notas: dict[str, tuple[int, str]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Devolve (todas as linhas, em ordem de prioridade; as linhas para disparo, sem repetidos).

    `pesos` e `filtros` vêm do perfil da lista; `notas`, das avaliações da IA já feitas (por CNPJ).
    """
    linhas = [enriquecer.escolher_telefone(l, tem_whatsapp, so_pessoal) for l in leads]
    linhas = [l for l in linhas if l["aptidao_solar"] >= min_aptidao]
    # Se alguma empresa da pessoa já tem solar, as outras linhas dela passam a dizer isso:
    # assim "sem registro" significa que nenhuma empresa do dono aparece na ANEEL.
    com_solar = {
        l["dono_chave"] for l in linhas if l["dono_chave"] and l["solar"] in ("empresa já tem", "outra unidade da empresa tem")
    }
    for l in linhas:
        if l["dono_chave"] in com_solar and l["solar"] in ("sem registro", "dono: indício fraco"):
            l["solar"] = "outra empresa do dono tem"
    for l in linhas:
        l["porte"] = PORTE.get((l["porte"] or "").strip().zfill(2), "") if l["porte"] else ""
    linhas = [l for l in linhas if passa_nos_filtros(l, filtros)]
    pontuar_encaixe(linhas, publico, pesos)
    aplicar_notas_da_ia(linhas, notas)
    ordenar(linhas, cidade, publico)
    disparo = sem_repetidos([l for l in linhas if l["faixa"] in ("A", "B") and l["telefone"]])
    email_uma_vez(linhas, disparo)
    return linhas, disparo


def ordenar(linhas: list[dict], cidade: str, publico: str = "dono") -> None:
    """Põe as linhas em ordem de prioridade e refaz as etiquetas, conforme o público da lista.

    Nos dois públicos vem primeiro quem tem WhatsApp confirmado (sem WhatsApp não há contato) e,
    dentro de cada faixa, quem está no Maps e o CNPJ ativo. O desempate seguinte é o encaixe no
    perfil (no público "dono", a própria nota solar); no público "empresa" as etiquetas não falam de solar.
    """
    if publico not in PUBLICOS:
        raise ValueError(f"Público desconhecido: {publico}")
    dono = publico == "dono"
    for l in linhas:
        l["tags"] = ",".join(
            ["solar" if dono else "empresas", f"faixa-{l['faixa'].lower()}", slug(cidade or l["cidade"]), f"fonte-{slug(l['fonte'])}"]
            + ([f"cnpj-{l['situacao']}"] if l["situacao"] else [])
            + (["ja-tem-solar"] if dono and l["solar"] not in ("sem registro", "dono: indício fraco") else [])
        )
    linhas.sort(
        key=lambda l: (
            l["faixa"] == "C",
            l["whatsapp"] != "sim",
            "ABC".index(l["faixa"]),
            l["fonte"] != "Google Maps",
            l["situacao"] not in ("ativa", ""),
            -int((l["aptidao_solar"] if dono else l.get("encaixe")) or 0),
            -int(l["chance_dono"] or 0),
            l["cnpj"],
        )
    )
    for i, l in enumerate(linhas):
        l["ordem"] = i


def ler_parquet(arquivo: Path | str) -> list[dict]:
    cur = duckdb.connect().execute(f"SELECT * FROM read_parquet('{arquivo}') ORDER BY ordem")
    nomes = [d[0] for d in cur.description]
    return [dict(zip(nomes, l)) for l in cur.fetchall()]


def trocar_publico(arquivo: Path | str, cidade: str, publico: str, pesos: dict[str, int] | None = None) -> None:
    """Reordena e reetiqueta uma lista já gravada. Não mexe em telefones; refaz só o encaixe."""
    linhas = ler_parquet(arquivo)
    pontuar_encaixe(linhas, publico, pesos)
    ordenar(linhas, cidade, publico)
    salvar_parquet(linhas, arquivo)


def trocar_notas_da_ia(arquivo: Path | str, notas: dict[str, tuple[int, str]]) -> None:
    """Grava as notas da IA numa lista pronta, sem mudar a ordem."""
    arquivo = Path(arquivo)
    linhas = ler_parquet(arquivo)
    aplicar_notas_da_ia(linhas, notas)
    provisorio = arquivo.with_suffix(".notas.parquet")
    salvar_parquet(linhas, provisorio)
    provisorio.replace(arquivo)  # quem estiver lendo a lista não pega o arquivo pela metade


def migrar(arquivo: Path | str, publico: str, pesos: dict[str, int] | None = None) -> bool:
    """Leva uma lista gravada ao formato atual, sem mudar a ordem nem os contatos.

    Antes dos perfis: acrescenta encaixe e nota da IA. Antes da versão 3: deixa cada e-mail num contato só.
    Guarda antes uma cópia em `antes-garimpo/`, ao lado da lista. Devolve se mudou algo.
    """
    arquivo = Path(arquivo)
    colunas = {d[0] for d in duckdb.connect().execute(f"SELECT * FROM read_parquet('{arquivo}') LIMIT 0").description}
    linhas = ler_parquet(arquivo)
    mudou = False
    if not set(CHAVES + INTERNAS) <= colunas:
        pontuar_encaixe(linhas, publico, pesos)
        for l in linhas:
            l.setdefault("nota_ia", None)
            l.setdefault("motivo_ia", "")
            l.setdefault("cnae", "")
        mudou = True
    disparo = sem_repetidos([l for l in linhas if l["faixa"] in ("A", "B") and l["telefone"]])
    mudou = bool(email_uma_vez(linhas, disparo)) or mudou
    if not mudou:
        return False
    copia = arquivo.parent / "antes-garimpo" / arquivo.name
    copia.parent.mkdir(exist_ok=True)
    if not copia.exists():
        shutil.copy2(arquivo, copia)
    provisorio = arquivo.with_suffix(".migrando.parquet")
    salvar_parquet(linhas, provisorio)
    provisorio.replace(arquivo)  # quem estiver lendo a lista não pega o arquivo pela metade
    return True


def _contar(conjunto: list[dict], campo: str) -> dict[str, int]:
    saida: dict[str, int] = {}
    for l in conjunto:
        saida[l[campo] or "não informado"] = saida.get(l[campo] or "não informado", 0) + 1
    return saida


def resumir(linhas: list[dict], disparo: list[dict], *, lugares: int = 0, whatsapp_verificado: bool = False) -> dict:
    return {
        "lugares_no_maps": lugares,
        "total_de_empresas": sum(1 for l in linhas if l["papel"] != "outro sócio"),
        "linhas_de_outros_socios": sum(1 for l in linhas if l["papel"] == "outro sócio"),
        "com_dono": sum(1 for l in linhas if l["primeiro_nome"]),
        "faixa_C_fora_do_disparo": sum(1 for l in linhas if l["faixa"] == "C"),
        "repetidos_removidos": sum(1 for l in linhas if l["faixa"] in ("A", "B") and l["telefone"]) - len(disparo),
        "contatos_para_disparo": len(disparo),
        "emails_repetidos_tirados": sum(1 for l in disparo if l.get("tipo_email") == "repetido"),
        "faixa_A": sum(1 for l in disparo if l["faixa"] == "A"),
        "faixa_B": sum(1 for l in disparo if l["faixa"] == "B"),
        "com_nome": sum(1 for l in disparo if l["primeiro_nome"]),
        "com_email": sum(1 for l in disparo if l["email"]),
        "disparo_por_fonte": _contar(disparo, "fonte"),
        "disparo_por_situacao": _contar(disparo, "situacao"),
        "disparo_por_papel": _contar(disparo, "papel"),
        "whatsapp_verificado": whatsapp_verificado,
    }


def celula_segura(valor):
    """Impede que um texto vindo do cadastro seja lido como fórmula ao abrir o CSV numa planilha.

    Telefones começam com "+55" e continuam valendo; só leva apóstrofo o que começa com
    = ou @, ou com + ou - seguido de algo que não é dígito.
    """
    if isinstance(valor, str) and valor and (valor[0] in "=@\t\r" or (valor[0] in "+-" and not valor[1:2].isdigit())):
        return "'" + valor
    return valor


def salvar_csv(linhas: list[dict], caminho: Path | str) -> None:
    """CSV com os cabeçalhos que o importador do GHL reconhece."""
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(CABECALHOS)
        w.writerows([[celula_segura(l.get(c, "")) for c in CHAVES] for l in linhas])


def salvar_parquet(linhas: list[dict], caminho: Path | str) -> None:
    """Guarda todas as linhas (com as colunas internas) para o sistema web filtrar depois."""
    colunas = CHAVES + INTERNAS
    with tempfile.NamedTemporaryFile("w", suffix=".csv", newline="", encoding="utf-8", delete=False) as f:
        w = csv.writer(f)
        w.writerow(colunas)
        w.writerows([["" if l.get(c) is None else l.get(c, "") for c in colunas] for l in linhas])
        temporario = f.name
    try:
        sel = ", ".join(f"TRY_CAST({c} AS INTEGER) AS {c}" if c in INTEIRAS else f"coalesce({c}, '') AS {c}" for c in colunas)
        con = duckdb.connect()
        con.execute(
            f"COPY (SELECT {sel} FROM read_csv('{temporario}', header=true, all_varchar=true, "
            f"quote='\"', escape='\"') ORDER BY ordem) TO '{caminho}' (FORMAT parquet, COMPRESSION zstd)"
        )
        con.close()
    finally:
        Path(temporario).unlink(missing_ok=True)
