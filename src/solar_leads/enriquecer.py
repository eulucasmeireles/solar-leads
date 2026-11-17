"""Cruza lugares do Google Maps com a base de CNPJ, acha o dono e pontua o contato."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from datetime import date, datetime

import duckdb
from rapidfuzz import fuzz

from . import aneel, cnpj_db, config
from .qualificacao import (
    LIMITE_CONTADOR, QUALIF_DONO, aptidao_solar, oportunidade, pontuar_contato, potencial_da_pessoa,
)
from .telefone import DDDS_POR_UF, e164, e_celular, suspeito

_SUFIXOS = re.compile(r"\b(LTDA|ME|EPP|EIRELI|S\s?A|MEI|CIA|COMERCIO|COMERCIAL|E|DE|DA|DO|DAS|DOS)\b")
_NAO_ALFANUM = re.compile(r"[^A-Z0-9]+")
_NUMERO_PORTA = re.compile(r",\s*(?:n[ºo°.]*\s*)?(\d{1,6})\b", re.IGNORECASE)
_CONTABIL = ("CONTAB", "CONTADOR", "ASSESSORIA", "ESCRITORIO")
NATUREZA_INDIVIDUAL = ("2135",)  # empresário individual: a razão social é o nome da pessoa
MAX_SOCIOS_POR_EMPRESA = 5  # acima disso são diretorias grandes; ficam os cinco de maior cargo
MENORES = ("1", "2")  # faixas etárias até 20 anos: não viram contato
TETO_CAPITAL = 50_000_000  # capital declarado acima disso costuma ser de fachada; conta até o teto
MAX_EMPRESAS_DONO = 30  # acima disso o "sócio" é holding/administrador profissional
SITUACOES = {"01": "nula", "02": "ativa", "03": "suspensa", "04": "inapta", "08": "baixada"}
ENCERRADAS = ("01", "08")
FAIXA_ETARIA = {"2": "13 a 20", "3": "21 a 30", "4": "31 a 40", "5": "41 a 50", "6": "51 a 60", "7": "61 a 70", "8": "71 a 80", "9": "mais de 80"}
EMAIL_PESSOAL = (
    "gmail.com", "hotmail.com", "hotmail.com.br", "outlook.com", "outlook.com.br", "yahoo.com", "yahoo.com.br",
    "live.com", "icloud.com", "bol.com.br", "uol.com.br", "terra.com.br", "ig.com.br", "msn.com", "globo.com",
)
ENDERECO_COMPARTILHADO = 10  # a partir disso o endereço é galeria, prédio comercial ou escritório virtual
_EMAIL = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")
MAX_EMPRESAS_POR_NOME = 300_000  # acima disso a palavra é genérica demais ("LTDA", "COMERCIO")
DESCONTO_NAO_ATIVA = 0.15  # no cruzamento com o Maps, um CNPJ ativo equivalente ganha do encerrado


def norm(texto: str | None) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").upper())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = _NAO_ALFANUM.sub(" ", t)
    return " ".join(_SUFIXOS.sub(" ", t).split())


def _similaridade(nome_maps: str, fantasia: str | None, razao: str | None) -> float:
    a = norm(nome_maps)
    return max((fuzz.token_set_ratio(a, norm(b)) for b in (fantasia, razao) if b), default=0.0)


def _numero_porta(lugar: dict) -> str:
    m = _NUMERO_PORTA.search(lugar.get("rua") or "") or _NUMERO_PORTA.search(lugar.get("endereco") or "")
    return m.group(1) if m else ""


@lru_cache(maxsize=65536)
def _data(valor: str | None) -> date | None:
    try:
        return datetime.strptime((valor or "").strip(), "%Y%m%d").date()
    except ValueError:
        return None


@lru_cache(maxsize=65536)
def _capital(valor: str | None) -> float | None:
    try:
        return float((valor or "").replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _cpf6(dono: dict | None, est: dict | None) -> str:
    """Os seis dígitos do meio do CPF do dono, quando o cadastro os mostra."""
    if not dono:
        return ""
    m = re.search(r"\*{3}(\d{6})\*{2}", dono.get("chave") or "")
    if m:
        return m.group(1) if m.group(1) != "000000" else ""
    # Empresário individual: às vezes o CPF inteiro vem na razão social.
    m = re.search(r"(?<!\d)(\d{11})(?!\d)", (est or {}).get("razao_social") or "") if not dono.get("chave") else None
    return m.group(1)[3:9] if m else ""


def _nome_pessoa(nome: str) -> tuple[str, str]:
    partes = re.sub(r"[\d.\-/]+", " ", nome).title().split()
    return (partes[0], " ".join(partes[1:])) if partes else ("", "")


def carregar_municipio(con: duckdb.DuckDBPyConnection, codigo: str, uf: str, so_ativas: bool = False) -> None:
    """Materializa os estabelecimentos do município (todas as situações) com os dados da empresa.

    Usa a tabela nacional `estab_cidade` quando ela existe (rápido); senão lê os arquivos brutos.
    """
    no_endereco = (
        "CASE WHEN cep <> '' AND numero NOT IN ('', '0', 'S/N', 'SN', 'S N') "
        "THEN count(*) FILTER (WHERE situacao = '02') OVER (PARTITION BY cep, numero) END AS no_endereco"
    )
    if cnpj_db.tem_tabela(con, "estab_cidade"):
        filtro = "AND situacao = '02'" if so_ativas else ""
        con.execute(
            f"CREATE OR REPLACE TEMP TABLE estab_mun AS SELECT * EXCLUDE (uf, municipio), {no_endereco} "
            f"FROM estab_cidade WHERE municipio = ? AND uf = ? {filtro}",
            [codigo, uf.upper()],
        )
        return
    filtro = "AND lpad(trim(e.situacao_cadastral), 2, '0') = '02'" if so_ativas else ""
    tel1, tel2 = cnpj_db._tel("e.ddd_1", "e.telefone_1"), cnpj_db._tel("e.ddd_2", "e.telefone_2")
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE estab_mun AS
        SELECT *, {no_endereco}
        FROM (
        SELECT e.cnpj_basico, e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj, e.matriz_filial,
               e.nome_fantasia, em.razao_social, em.natureza_juridica, em.porte, em.capital_social,
               e.cnae_fiscal, c.descricao AS cnae_descricao, e.data_inicio_atividade,
               trim(coalesce(e.tipo_logradouro, '') || ' ' || coalesce(e.logradouro, '')) AS logradouro,
               trim(e.numero) AS numero, e.complemento, e.bairro, e.cep,
               lower(e.correio_eletronico) AS email, {tel1} AS tel1, {tel2} AS tel2,
               (coalesce(s.opcao_mei, 'N') = 'S' OR (
                  lpad(trim(e.situacao_cadastral), 2, '0') <> '02'
                  AND trim(coalesce(s.data_exclusao_mei, '')) NOT IN ('', '00000000')
                  AND trim(s.data_exclusao_mei) >= trim(e.data_situacao_cadastral))) AS mei,
               lpad(trim(e.situacao_cadastral), 2, '0') AS situacao, e.data_situacao_cadastral AS data_situacao,
               nat.descricao AS natureza, coalesce(s.opcao_simples, 'N') = 'S' AS simples,
               CASE WHEN trim(coalesce(e.cnae_fiscal_secundaria, '')) = '' THEN 0
                    ELSE len(string_split(e.cnae_fiscal_secundaria, ',')) END AS secundarias
        FROM estabelecimentos e
        LEFT JOIN empresas em USING (cnpj_basico)
        LEFT JOIN cnaes c ON c.codigo = e.cnae_fiscal
        LEFT JOIN naturezas nat ON nat.codigo = em.natureza_juridica
        LEFT JOIN (SELECT cnpj_basico, any_value(opcao_mei) AS opcao_mei, any_value(opcao_simples) AS opcao_simples,
                          any_value(data_exclusao_mei) AS data_exclusao_mei
                   FROM simples GROUP BY 1) s USING (cnpj_basico)
        WHERE e.municipio = ? AND e.uf = ? {filtro}
        )
        """,
        [codigo, uf.upper()],
    )


def _titulo_de_cidade(nome: str | None) -> str:
    """"RIO DE JANEIRO" -> "Rio de Janeiro"."""
    palavras = (nome or "").title().split()
    return " ".join(p.lower() if i and p in ("De", "Da", "Do", "Das", "Dos", "E", "D'") else p for i, p in enumerate(palavras))


def carregar_por_nome(con: duckdb.DuckDBPyConnection, palavras: list[str], uf: str = "", so_ativas: bool = False) -> int:
    """Materializa em `estab_mun` os estabelecimentos de um estado (ou do país, sem `uf`) cujo
    nome (razão social ou fantasia) contém alguma das palavras. Devolve quantos são.

    Além das colunas de sempre, cada linha traz `uf` e `cidade`, porque a lista cobre vários municípios.
    """
    if not cnpj_db.tem_tabela(con, "estab_cidade"):
        raise RuntimeError("A busca por nome usa a tabela nacional: rode `leads base cidades` antes.")
    termos = sorted({p.strip() for p in palavras if len(p.strip()) >= 3})
    if not termos:
        raise ValueError("Informe ao menos uma palavra com 3 letras ou mais.")
    nome = "strip_accents(upper(coalesce(e.razao_social, '') || ' ' || coalesce(e.nome_fantasia, '')))"
    cond = " OR ".join(f"contains({nome}, strip_accents(upper(?)))" for _ in termos)
    filtro, params = "", list(termos)
    if uf:
        filtro += " AND e.uf = ?"
        params.append(uf.upper())
    if so_ativas:
        filtro += " AND e.situacao = '02'"
    # Nome do município com acento, quando a tabela de apoio da página já existe.
    apoio = config.DADOS / "municipios_uf.parquet"
    if apoio.exists():
        cidade = "coalesce(x.exibicao, m.descricao)"
        juncao = f"LEFT JOIN read_parquet('{apoio}') x ON x.uf = e.uf AND x.codigo = e.municipio"
    else:
        cidade, juncao = "m.descricao", ""
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE estab_nome AS
        SELECT e.* EXCLUDE (municipio), {cidade} AS cidade
        FROM estab_cidade e LEFT JOIN municipios m ON m.codigo = e.municipio {juncao}
        WHERE ({cond}) {filtro}
        """,
        params,
    )
    n = con.execute("SELECT count(*) FROM estab_nome").fetchone()[0]
    if n > MAX_EMPRESAS_POR_NOME:
        con.execute("DROP TABLE estab_nome")
        raise ValueError(
            f"{n:,} empresas têm essas palavras no nome".replace(",", ".")
            + ": é muito para uma lista só. Use palavras mais específicas ou escolha um estado."
        )
    # Quantas empresas ativas dividem o endereço, contando todas as da base e não só as achadas pelo nome.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE estab_mun AS
        SELECT s.*, CASE WHEN s.cep <> '' AND s.numero NOT IN ('', '0', 'S/N', 'SN', 'S N') THEN v.n END AS no_endereco
        FROM estab_nome s
        LEFT JOIN (
          SELECT e.cep, e.numero, count(*) AS n
          FROM estab_cidade e JOIN (SELECT DISTINCT cep, numero FROM estab_nome WHERE cep <> '') k
            ON k.cep = e.cep AND k.numero = e.numero
          WHERE e.situacao = '02' {"AND e.uf = ?" if uf else ""}
          GROUP BY 1, 2
        ) v ON v.cep = s.cep AND v.numero = s.numero
        """,
        [uf.upper()] if uf else [],
    )
    con.execute("DROP TABLE estab_nome")
    return n


_COLS = (
    "cnpj_basico cnpj matriz_filial nome_fantasia razao_social natureza_juridica porte capital_social cnae_fiscal "
    "cnae_descricao data_inicio_atividade logradouro numero complemento bairro cep email tel1 tel2 mei "
    "situacao data_situacao natureza simples secundarias no_endereco"
).split()


def _casar(con: duckdb.DuckDBPyConnection, lugares: list[dict]) -> dict[int, tuple[dict, float, str]]:
    """Para cada lugar (pelo índice), o estabelecimento mais provável, a confiança e o motivo."""
    con.execute("CREATE OR REPLACE TEMP TABLE lug (i INTEGER, tel VARCHAR, cep VARCHAR, nome VARCHAR)")
    con.executemany(
        "INSERT INTO lug VALUES (?, ?, ?, ?)",
        [(i, l["telefone"], l["cep"] or None, norm(l["nome"])) for i, l in enumerate(lugares)],
    )
    sel = ", ".join(f"e.{c}" for c in _COLS)
    candidatos: dict[int, list[tuple[dict, str]]] = {}

    def juntar(sql: str, via: str) -> None:
        for linha in con.execute(sql).fetchall():
            candidatos.setdefault(linha[0], []).append((dict(zip(_COLS, linha[1:])), via))

    juntar(f"SELECT l.i, {sel} FROM lug l JOIN estab_mun e ON l.tel IN (e.tel1, e.tel2)", "telefone")
    juntar(f"SELECT l.i, {sel} FROM lug l JOIN estab_mun e ON l.cep = e.cep", "cep")
    # Último recurso: nome muito parecido em qualquer ponto do município.
    juntar(
        f"""
        SELECT l.i, {sel} FROM lug l JOIN estab_mun e
          ON length(l.nome) >= 6 AND jaro_winkler_similarity(
               l.nome, strip_accents(upper(coalesce(nullif(trim(e.nome_fantasia), ''), e.razao_social)))) >= 0.93
        """,
        "nome",
    )

    escolhidos: dict[int, tuple[dict, float, str]] = {}
    for i, lista in candidatos.items():
        lugar, porta = lugares[i], _numero_porta(lugares[i])
        melhor: tuple[float, float, dict, str] | None = None
        # Ordem fixa: candidatos empatados dão sempre o mesmo resultado.
        for est, via in sorted(lista, key=lambda c: c[0]["cnpj"]):
            sim = _similaridade(lugar["nome"], est["nome_fantasia"], est["razao_social"])
            mesma_porta = bool(porta) and porta == (est["numero"] or "").lstrip("0")
            if via == "telefone":
                conf, motivo = (1.0, "telefone e nome") if sim >= 60 else (0.85, "telefone")
            elif via == "cep" and mesma_porta and sim >= 55:
                conf, motivo = 0.9, "endereço e nome"
            elif via == "cep" and sim >= 85:
                conf, motivo = 0.8, "CEP e nome"
            elif via == "nome" and sim >= 92:
                conf, motivo = 0.6, "nome"
            else:
                continue
            if est["situacao"] != "02":
                conf = round(conf - DESCONTO_NAO_ATIVA, 2)
                motivo += f" (CNPJ {SITUACOES.get(est['situacao'], 'não ativo')})"
            if melhor is None or (conf, sim) > melhor[:2]:
                melhor = (conf, sim, est, motivo)
        if melhor:
            escolhidos[i] = (melhor[2], melhor[0], melhor[3])
    return escolhidos


def _ddds_da_regiao(con: duckdb.DuckDBPyConnection) -> set[str]:
    """DDDs que respondem por pelo menos 10% dos telefones das empresas do município."""
    linhas = con.execute(
        "SELECT substr(tel1, 1, 2) ddd, count(*) n FROM estab_mun WHERE tel1 IS NOT NULL GROUP BY 1 ORDER BY n DESC"
    ).fetchall()
    total = sum(n for _, n in linhas) or 1
    return {ddd for ddd, n in linhas if n / total >= 0.10}


def _da_base(
    con: duckdb.DuckDBPyConnection, cnaes: list[str], incluir_mei: bool, so_locais: bool = True, extras: tuple[str, ...] = ()
) -> list[dict]:
    """Estabelecimentos do município cuja atividade principal começa com um dos CNAEs.

    Um CNAE vazio pega todas as atividades. Órgãos públicos (natureza 1xxx) ficam fora.
    `extras`: colunas a mais de `estab_mun` (a busca por nome traz `uf` e `cidade` de cada empresa).
    """
    if not cnaes:
        return []
    colunas = [*_COLS, *extras]
    sel = ", ".join(f"e.{c}" for c in colunas)
    cond = " OR ".join("e.cnae_fiscal LIKE ?" for _ in cnaes)
    extra = "" if incluir_mei else "AND NOT e.mei"
    if so_locais:
        # Filial só entra se a matriz também for do município; filial de empresa de fora tem dono de fora.
        extra += (
            " AND (e.matriz_filial = '1' OR EXISTS (SELECT 1 FROM estab_mun m "
            "WHERE m.cnpj_basico = e.cnpj_basico AND m.matriz_filial = '1'))"
        )
    linhas = con.execute(
        f"SELECT {sel} FROM estab_mun e WHERE ({cond}) {extra} "
        "AND coalesce(e.natureza_juridica, '') NOT LIKE '1%' ORDER BY e.cnpj",
        [c + "%" for c in cnaes],
    )
    return [dict(zip(colunas, l)) for l in linhas.fetchall()]


def _lugar_da_base(est: dict, cidade: str) -> dict:
    """Monta, a partir do cadastro, os mesmos campos que um lugar do Maps tem."""
    nome = (est["nome_fantasia"] or "").strip() or re.sub(r"[\d.\-/]{6,}", " ", est["razao_social"] or "")
    return {
        "nome": " ".join(nome.split()).title(),
        "telefone": "",
        "endereco": ", ".join(p for p in (est["logradouro"], est["numero"]) if p).title(),
        "bairro": (est["bairro"] or "").title(),
        "cidade": cidade,
        "cep": est["cep"] or "",
        "site": "",
        "avaliacoes": None,
        "link": "",
        "categoria": "",
    }


def _donos(con: duckdb.DuckDBPyConnection, basicos: list[str]) -> dict[str, list[dict]]:
    """Sócios decisores de cada empresa, do cargo mais forte para o mais fraco (o primeiro é o principal)."""
    if not basicos:
        return {}
    linhas = con.execute(
        """
        SELECT s.cnpj_basico, s.nome, s.chave, s.qualificacao, s.data_entrada_sociedade, q.descricao, s.faixa_etaria
        FROM socio_pf s LEFT JOIN qualificacoes q ON lpad(trim(q.codigo), 2, '0') = s.qualificacao
        WHERE s.cnpj_basico IN (SELECT unnest(?))
        """,
        [basicos],
    ).fetchall()
    # Total de sócios de cada empresa, contando os que são outras empresas: "dono único" exige que não haja mais ninguém.
    total_socios = dict(
        con.execute(
            "SELECT cnpj_basico, count(*) FROM socios WHERE cnpj_basico IN (SELECT unnest(?)) GROUP BY 1", [basicos]
        ).fetchall()
    )
    por_empresa: dict[str, list[tuple]] = {}
    vistos: set[tuple[str, str]] = set()
    linhas_pf: dict[str, int] = {}
    for l in linhas:
        linhas_pf[l[0]] = linhas_pf.get(l[0], 0) + 1
        if (l[0], l[2]) in vistos:  # a mesma pessoa listada duas vezes na empresa
            continue
        vistos.add((l[0], l[2]))
        por_empresa.setdefault(l[0], []).append(l)
    donos: dict[str, list[dict]] = {}
    for basico, socios in por_empresa.items():
        socios.sort(key=lambda s: (QUALIF_DONO.get(s[3], 9), s[4] or "99999999", s[2]))
        # pessoas distintas + sócios que não são pessoa física (empresas, estrangeiros)
        n_socios = len(socios) + max(0, total_socios.get(basico, 0) - linhas_pf[basico])
        decisores = [
            {
                "nome": s[1],
                "chave": s[2],
                "unico": n_socios == 1,
                "qualificacao": s[5] or s[3],
                "socios": n_socios,
                "desde": s[4],
                "idade": FAIXA_ETARIA.get(s[6] or "", ""),
            }
            for s in socios
            if QUALIF_DONO.get(s[3], 9) < 9 and (s[6] or "") not in MENORES
        ]
        if decisores:
            donos[basico] = decisores[:MAX_SOCIOS_POR_EMPRESA]
    return donos


def _chave_identifica(chave: str) -> bool:
    """A chave só une a mesma pessoa em várias empresas se trouxer os seis dígitos do CPF (e não zeros)."""
    m = re.search(r"\*{3}(\d{6})\*{2}", (chave or "").split("|", 1)[-1])
    return bool(m) and m.group(1) != "000000"


def _perfil_do_dono(con: duckdb.DuckDBPyConnection, chaves: list[str]) -> dict[str, dict]:
    """Para cada dono (chave com CPF): empresas em que é sócio, quantas ativas, capital somado e as maiores."""
    chaves = [c for c in set(chaves) if _chave_identifica(c)]
    if not chaves:
        return {}
    if cnpj_db.tem_tabela(con, "empresa_resumo"):
        linhas = con.execute(
            """
            WITH emp AS (SELECT DISTINCT chave, cnpj_basico FROM socio_pf WHERE chave IN (SELECT unnest(?)))
            SELECT chave, count(*), count(*) FILTER (WHERE r.matriz_ativa),
                   coalesce(sum(least(try_cast(replace(r.capital_social, ',', '.') AS DOUBLE), ?)) FILTER (WHERE r.matriz_ativa), 0),
                   list({'b': r.cnpj_basico, 'n': r.razao_social}
                        ORDER BY try_cast(replace(r.capital_social, ',', '.') AS DOUBLE) DESC NULLS LAST, r.cnpj_basico)
                     FILTER (WHERE r.matriz_ativa)
            FROM emp LEFT JOIN empresa_resumo r USING (cnpj_basico) GROUP BY 1
            """,
            [chaves, TETO_CAPITAL],
        ).fetchall()
        # Quem aparece em dezenas de empresas é administrador profissional ou contador, não um dono rico:
        # fica só com a contagem, sem capital nem lista de empresas, para não inflar a nota.
        return {
            chave: {"total": total, "ativas": ativas, "capital": capital, "maiores": (maiores or [])[:6]}
            if total <= MAX_EMPRESAS_DONO
            else {"total": total, "ativas": None, "capital": 0, "maiores": [], "profissional": True}
            for chave, total, ativas, capital, maiores in linhas
        }
    linhas = con.execute(
        """
        WITH emp AS (SELECT DISTINCT chave, cnpj_basico FROM socio_pf WHERE chave IN (SELECT unnest(?))),
        ativas AS (
          SELECT DISTINCT cnpj_basico FROM estabelecimentos
          WHERE matriz_filial = '1' AND lpad(trim(situacao_cadastral), 2, '0') = '02'
            AND cnpj_basico IN (SELECT cnpj_basico FROM emp)
        ), info AS (
          SELECT cnpj_basico, razao_social, try_cast(replace(capital_social, ',', '.') AS DOUBLE) AS capital
          FROM empresas WHERE cnpj_basico IN (SELECT cnpj_basico FROM ativas)
        )
        SELECT chave, count(*), count(a.cnpj_basico), coalesce(sum(i.capital), 0),
               list({'b': i.cnpj_basico, 'n': i.razao_social} ORDER BY i.capital DESC NULLS LAST, i.cnpj_basico)
                 FILTER (WHERE i.cnpj_basico IS NOT NULL)
        FROM emp LEFT JOIN ativas a USING (cnpj_basico) LEFT JOIN info i USING (cnpj_basico) GROUP BY 1
        """,
        [chaves],
    ).fetchall()
    return {
        chave: {"total": total, "ativas": ativas, "capital": capital, "maiores": (maiores or [])[:6]}
        for chave, total, ativas, capital, maiores in linhas
    }


def _unidades(con: duckdb.DuckDBPyConnection, basicos: list[str]) -> dict[str, int]:
    """Quantos estabelecimentos ativos (matriz e filiais, no país todo) cada empresa tem."""
    if not basicos:
        return {}
    if cnpj_db.tem_tabela(con, "empresa_resumo"):
        return dict(
            con.execute(
                "SELECT cnpj_basico, unidades_ativas FROM empresa_resumo WHERE cnpj_basico IN (SELECT unnest(?))", [basicos]
            ).fetchall()
        )
    return dict(
        con.execute(
            """
            SELECT cnpj_basico, count(*) FROM estabelecimentos
            WHERE lpad(trim(situacao_cadastral), 2, '0') = '02' AND cnpj_basico IN (SELECT unnest(?))
            GROUP BY 1
            """,
            [basicos],
        ).fetchall()
    )


def _frequencias_email(con: duckdb.DuckDBPyConnection, emails: set[str]) -> dict[str, int]:
    if not emails:
        return {}
    return dict(
        con.execute("SELECT email, n_empresas FROM email_freq WHERE email IN (SELECT unnest(?))", [sorted(emails)]).fetchall()
    )


def _telefones_do_dono(con: duckdb.DuckDBPyConnection, chaves: list[str]) -> dict[str, dict[str, int]]:
    """Para cada dono, {telefone: em quantas empresas dele o número aparece}."""
    # Só cruza quando a chave tem os seis dígitos do CPF; nome sozinho confunde homônimos.
    chaves = [c for c in set(chaves) if _chave_identifica(c)]
    if not chaves:
        return {}
    linhas = con.execute(
        """
        WITH emp AS (
          SELECT chave, cnpj_basico FROM socio_pf WHERE chave IN (SELECT unnest(?))
        ), ok AS (
          SELECT chave FROM emp GROUP BY chave HAVING count(DISTINCT cnpj_basico) <= ?
        )
        SELECT e.chave, t.tel, count(DISTINCT t.cnpj_basico)
        FROM emp e JOIN ok USING (chave) JOIN tel_estab t USING (cnpj_basico)
        GROUP BY 1, 2
        """,
        [chaves, MAX_EMPRESAS_DONO],
    ).fetchall()
    resultado: dict[str, dict[str, int]] = {}
    for chave, tel, n in linhas:
        resultado.setdefault(chave, {})[tel] = n
    return resultado


def _frequencias(con: duckdb.DuckDBPyConnection, telefones: set[str]) -> dict[str, int]:
    if not telefones:
        return {}
    return dict(
        con.execute("SELECT tel, n_empresas FROM tel_freq WHERE tel IN (SELECT unnest(?))", [sorted(telefones)]).fetchall()
    )


def enriquecer(
    con: duckdb.DuckDBPyConnection,
    lugares: list[dict],
    codigo_municipio: str,
    uf: str,
    *,
    cidade: str = "",
    cnaes: list[str] | None = None,
    so_ativas: bool = False,
    incluir_mei: bool = False,
    so_locais: bool = True,
    palavras: list[str] | None = None,
    progresso=None,
    solar: bool = True,
) -> list[dict]:
    """Leads dos lugares do Maps e, com `cnaes`, também de todo estabelecimento do
    município com essa atividade na Receita (ativo ou não) que o Maps não trouxe.

    Com `palavras`, o recorte deixa de ser o município: entram as empresas do estado `uf`
    (ou do país, com `uf` vazia) que têm uma das palavras no nome, e cada linha leva a
    cidade e o estado da própria empresa. "Só locais" passa a valer por estado: sede da
    empresa dentro do recorte e telefone com DDD do estado dela.

    `solar=False` (listas de outros nichos) pula a consulta à ANEEL: as colunas de solar ficam vazias.
    """
    etapa = progresso or (lambda texto: None)
    amplo = bool(palavras)
    if amplo:
        etapa("Procurando as empresas pelo nome")
        carregar_por_nome(con, palavras, uf, so_ativas)
    else:
        etapa("Carregando as empresas da cidade")
        carregar_municipio(con, codigo_municipio, uf, so_ativas)
    casados = _casar(con, lugares) if lugares else {}
    itens = [(lugar, *casados.get(i, (None, 0.0, "")), "Google Maps") for i, lugar in enumerate(lugares)]
    ja = {est["cnpj"] for est, _, _ in casados.values()}
    ddds = _ddds_da_regiao(con) if so_locais and not amplo else set()
    for est in _da_base(con, cnaes or [], incluir_mei, so_locais, ("uf", "cidade") if amplo else ()):
        if est["cnpj"] not in ja:
            local = _titulo_de_cidade(est["cidade"]) if amplo else cidade
            itens.append((_lugar_da_base(est, local), est, 1.0, "direto da Receita", "Receita"))

    etapa("Identificando donos e sócios")
    basicos = sorted({est["cnpj_basico"] for _, est, _, _, _ in itens if est})
    donos = _donos(con, basicos)
    unidades = _unidades(con, basicos)
    freq_email = _frequencias_email(con, {est["email"].strip() for _, est, _, _, _ in itens if est and est["email"]})
    etapa("Cruzando telefones entre as empresas de cada dono")
    chaves = [d["chave"] for lista in donos.values() for d in lista]
    cruzados = _telefones_do_dono(con, chaves)
    perfis = _perfil_do_dono(con, chaves)

    # Candidatos de telefone por lugar, com a origem de cada um.
    pre: list[dict] = []
    todos: set[str] = set()
    for lugar, est, conf, motivo, fonte in itens:
        socios = donos.get(est["cnpj_basico"], []) if est else []
        if est and not socios and (est["natureza_juridica"] or "").strip() in NATUREZA_INDIVIDUAL:
            socios = [{"nome": est["razao_social"] or "", "chave": "", "unico": True, "qualificacao": "Empresário individual"}]
        por_socio = [cruzados.get(s["chave"], {}) for s in socios]
        cands: dict[str, dict] = {}
        if lugar["telefone"]:
            cands.setdefault(lugar["telefone"], {})["maps"] = True
        if est:
            for t in (est["tel1"], est["tel2"]):
                if t:
                    cands.setdefault(t, {})["cadastro"] = True
        for do_socio in por_socio:
            for t in do_socio:
                cands.setdefault(t, {})["dono"] = True
        # Números de enfeite (99999-9999, 12345678) não são de ninguém.
        cands = {t: o for t, o in cands.items() if not suspeito(t)}
        permitidos = DDDS_POR_UF.get((est or {}).get("uf") or "", set()) if amplo and so_locais else ddds
        if permitidos:
            # Só telefones da região: número de outro DDD costuma ser de sócio ou diretor que mora fora.
            cands = {t: o for t, o in cands.items() if t[:2] in permitidos}
        todos.update(cands)
        # Cada telefone vai para o sócio em cujas empresas ele mais aparece; no empate, para o principal.
        divisao: list[dict[str, dict]] = [{} for _ in socios] or [{}]
        for t, origem in cands.items():
            k = max(range(len(socios)), key=lambda i: (por_socio[i].get(t, 0), -i)) if socios else 0
            divisao[k][t] = origem
        for k, tels in enumerate(divisao):
            if k and not tels:
                continue  # sócio secundário sem telefone próprio não vira linha
            pre.append(
                {
                    "lugar": lugar, "est": est, "conf": conf, "motivo": motivo, "fonte": fonte,
                    "dono": socios[k] if socios else None,
                    "cands": tels,
                    "do_dono": por_socio[k] if socios else {},
                    "principal": k == 0,
                }
            )

    freq = _frequencias(con, todos)
    if solar:
        etapa("Conferindo quem já tem energia solar")
        solar_pj, solar_grupo, solar_pf = aneel.consultar(
            con,
            sorted({p["est"]["cnpj"] for p in pre if p["est"]}),
            # O CPF mascarado só se compara dentro de um município; numa lista de vários, fica de fora.
            [] if amplo else sorted({c for p in pre if (c := _cpf6(p["dono"], p["est"]))}),
            cidade,
            uf,
        )
    else:
        solar_pj = solar_grupo = solar_pf = None
    aneel_ok = solar_pj is not None
    solar_pj, solar_grupo, solar_pf = solar_pj or {}, solar_grupo or {}, solar_pf or {}
    # Donos que já têm solar em alguma empresa desta lista: as outras empresas deles herdam a informação,
    # e isso precisa valer antes de calcular as notas.
    donos_com_solar = {
        p["dono"]["chave"]
        for p in pre
        if p["est"] and p["dono"] and _chave_identifica(p["dono"].get("chave") or "")
        and (p["est"]["cnpj"] in solar_pj or p["est"]["cnpj_basico"] in solar_grupo)
    }
    regioes: set[str] = set()
    if solar and not amplo:  # "região com muita energia solar" é uma medida de bairros de uma cidade
        por_cep = dict(
            con.execute("SELECT substr(cep, 1, 5), count(*) FROM estab_mun WHERE situacao = '02' AND length(cep) = 8 GROUP BY 1").fetchall()
        )
        regioes = aneel.regioes_solares(con, cidade, uf, por_cep)
    etapa("Pontuando os contatos")
    leads: list[dict] = []
    for p in pre:
        lugar, est, dono = p["lugar"], p["est"], p["dono"]
        email = ((est or {}).get("email") or "").strip()
        fechou = _data((est or {}).get("data_situacao")) if (est or {}).get("situacao") in ENCERRADAS else None
        anos_encerrada = (date.today() - fechou).days / 365.25 if fechou else None
        vezes_email = freq_email.get(email, 1)
        # E-mail em muitas empresas é de contador, a menos que sejam as empresas do próprio dono.
        do_proprio_dono = bool(dono) and vezes_email <= (perfis.get(dono.get("chave") or "", {}).get("total") or 0)
        contabil = any(t in email.upper() for t in _CONTABIL) or (vezes_email > LIMITE_CONTADOR and not do_proprio_dono)
        avaliados = []
        for tel, origem in p["cands"].items():
            pontos, faixa, motivos = pontuar_contato(
                celular=e_celular(tel),
                no_maps=origem.get("maps", False),
                no_cadastro=origem.get("cadastro", False),
                empresas_com_o_numero=freq.get(tel, 0),
                empresas_do_dono_com_o_numero=p["do_dono"].get(tel, 0),
                de_outra_empresa_do_dono=origem.get("dono", False),
                dono_unico=bool(dono and (dono["unico"] or (est or {}).get("mei"))),
                email_contabilidade=contabil,
                anos_encerrada=anos_encerrada,
            )
            avaliados.append({"tel": tel, "pontos": pontos, "faixa": faixa, "motivos": motivos})
        avaliados.sort(key=lambda a: (-a["pontos"], a["tel"]))  # empate: sempre o mesmo número

        nota, motivos_solar = aptidao_solar(
            cnae=(est or {}).get("cnae_fiscal"),
            porte=((est or {}).get("porte") or "").strip().zfill(2) if est else None,
            capital_social=_capital((est or {}).get("capital_social")),
            data_inicio=_data((est or {}).get("data_inicio_atividade")),
            complemento=(est or {}).get("complemento"),
            avaliacoes=lugar["avaliacoes"],
        )
        situacao = (est or {}).get("situacao") or ""
        if situacao in ENCERRADAS:
            # Sem o negócio em funcionamento, a nota do estabelecimento não vale; o lead é a pessoa.
            nota, motivos_solar = 0, ["empresa encerrada: o lead é o antigo dono, não o ponto"]
        n_endereco = (est or {}).get("no_endereco") or 0
        if situacao == "02" and n_endereco >= ENDERECO_COMPARTILHADO:
            nota = max(0, nota - 2)
            motivos_solar.append(f"endereço dividido com {n_endereco} empresas (provável sala ou galeria)")
        n_unidades = unidades.get(est["cnpj_basico"], 0) if est else 0
        if situacao == "02" and n_unidades >= 3:
            nota = min(10, nota + 1)
            motivos_solar.append(f"empresa com {n_unidades} unidades ativas")
        # Quem já tem solar, pela ANEEL: a própria empresa (CNPJ exato) ou outra unidade dela.
        # Para o dono só há os 6 dígitos do meio do CPF, que se repetem muito dentro do estado:
        # medido contra outras cidades, o casamento acerta perto de 45%. Por isso é só um
        # indício fraco e não mexe na nota. Já a ausência de casamento é forte: o dono não
        # tem sistema no próprio CPF nesse município.
        cpf6 = _cpf6(dono, est)
        if est and est["cnpj"] in solar_pj:
            solar, reg = "empresa já tem", solar_pj[est["cnpj"]]
        elif est and est["cnpj_basico"] in solar_grupo:
            solar, reg = "outra unidade da empresa tem", solar_grupo[est["cnpj_basico"]]
        elif not aneel_ok:
            solar, reg = "não consultado", (None, "", "", "")
        elif dono and (dono.get("chave") or "") in donos_com_solar:
            solar, reg = "outra empresa do dono tem", (None, "", "", "")
        elif cpf6 in solar_pf:
            solar, reg = "dono: indício fraco", (*solar_pf[cpf6], cidade)
        else:
            solar, reg = "sem registro", (None, "", "", "")
        if solar == "empresa já tem":
            nota = 0
            motivos_solar = [f"já tem energia solar desde {reg[0].year if reg[0] else '?'} ({reg[1]} kW)"]
        abertura = _data((est or {}).get("data_inicio_atividade"))
        tipo_email = ""
        if _EMAIL.match(email):
            tipo_email = "contabilidade" if contabil else "pessoal" if email.split("@")[1] in EMAIL_PESSOAL else "corporativo"
        chave = (dono or {}).get("chave") or ""
        if dono and not chave and re.search(r"\d{6}", est["razao_social"] or ""):
            chave = norm(est["razao_social"])  # empresário individual com CPF na razão social
        primeiro, sobrenome = _nome_pessoa(dono["nome"]) if dono else ("", "")
        perfil = perfis.get((dono or {}).get("chave") or "", {})
        # Duas notas: a do ponto da empresa (acima) e a da casa do dono. A geral é a maior.
        nota_empresa = nota
        nota_casa, motivos_casa = potencial_da_pessoa(
            cnae=(est or {}).get("cnae_fiscal"),
            porte=((est or {}).get("porte") or "").strip().zfill(2) if est else None,
            capital_do_dono=perfil.get("capital"),
            empresas_ativas=perfil.get("ativas"),
            empresas_total=perfil.get("total"),
            socio_desde=_data((dono or {}).get("desde")),
            idade=(dono or {}).get("idade"),
            complemento=(est or {}).get("complemento"),
            cargo=(dono or {}).get("qualificacao"),
            empresa_ativa=situacao == "02",
            solar=solar,
            regiao_solar=bool(est) and (est["cep"] or "")[:5] in regioes,
        ) if dono else (0, ["dono não identificado"])
        nota = max(nota_empresa, nota_casa)
        outras = [m["n"] for m in perfil.get("maiores", []) if est and m["b"] != est["cnpj_basico"] and m["n"]][:3]
        leads.append(
            {
                "primeiro_nome": primeiro,
                "sobrenome": sobrenome,
                "qualificacao_dono": dono["qualificacao"] if dono else "",
                "empresa": lugar["nome"],
                "razao_social": (est or {}).get("razao_social") or "",
                "cnpj": (est or {}).get("cnpj") or "",
                "confianca_cnpj": p["conf"],
                "motivo_cnpj": p["motivo"],
                "cnae": (est or {}).get("cnae_fiscal") or "",
                "atividade": (est or {}).get("cnae_descricao") or lugar["categoria"],
                "porte": (est or {}).get("porte") or "",
                "capital_social": _capital((est or {}).get("capital_social")) or "",
                "abertura": str(_data((est or {}).get("data_inicio_atividade")) or ""),
                "mei": bool((est or {}).get("mei")),
                "endereco": lugar["endereco"],
                "bairro": lugar["bairro"] or (est or {}).get("bairro") or "",
                "cidade": lugar["cidade"],
                "uf": ((est or {}).get("uf") or uf).upper(),
                "cep": lugar["cep"] or (est or {}).get("cep") or "",
                "site": lugar["site"],
                "avaliacoes": lugar["avaliacoes"],
                "link_maps": lugar["link"],
                "aptidao_solar": nota,
                "motivos_solar": "; ".join(motivos_solar),
                "email": email if _EMAIL.match(email) and not contabil else "",
                "idade_dono": (dono or {}).get("idade", ""),
                "socio_desde": str(_data((dono or {}).get("desde")) or ""),
                "numero_socios": (dono or {}).get("socios", ""),
                "empresas_do_dono": perfil.get("total", ""),
                "empresas_ativas_do_dono": perfil.get("ativas", ""),
                "capital_do_dono": round(perfil["capital"]) if perfil.get("capital") else "",
                "outras_empresas_do_dono": "; ".join(o.title() for o in outras),
                "tipo_email": tipo_email,
                "potencial_casa": nota_casa,
                "motivos_casa": "; ".join(motivos_casa),
                "potencial_empresa": nota_empresa,
                "oportunidade": oportunidade(nota_casa, nota_empresa, solar),
                "solar": solar,
                "solar_desde": str(reg[0] or ""),
                "solar_kw": reg[1],
                "solar_sistemas": reg[2],
                "solar_onde": reg[3],
                "natureza": (est or {}).get("natureza") or "",
                "simples": ("sim" if est["simples"] else "não") if est else "",
                "idade_empresa": (date.today() - abertura).days // 365 if abertura else "",
                "atividades_secundarias": est["secundarias"] if est else "",
                "unidades_ativas": n_unidades if est else "",
                "empresas_no_endereco": n_endereco or "",
                "matriz_filial": {"1": "matriz", "2": "filial"}.get(((est or {}).get("matriz_filial") or "").strip(), ""),
                "papel": ("dono principal" if p["principal"] else "outro sócio") if dono else "",
                "fonte": p["fonte"],
                "situacao": SITUACOES.get(situacao, "") if est else "",
                "data_situacao": str(_data((est or {}).get("data_situacao")) or ""),
                "dono_chave": chave if re.search(r"\d{6}", chave) else "",
                "telefones": avaliados,
            }
        )
    return leads


def escolher_telefone(lead: dict, whatsapp: dict[str, dict] | None = None, so_pessoal: bool = False) -> dict:
    """Fixa o melhor telefone do lead.

    Com a verificação de WhatsApp, número sem conta sai; número ainda não
    verificado fica, marcado como tal. Conta pessoal ganha pontos (é mais provável
    que seja o aparelho do dono) e `so_pessoal` mantém só as contas pessoais.
    """
    opcoes = [dict(t) for t in lead["telefones"] if t["faixa"] != "C"]
    verificado = "não verificado"
    if whatsapp is not None:
        ativos = []
        for t in opcoes:
            info = whatsapp.get(t["tel"])
            if info is None:
                t["tipo"] = ""  # ainda não verificado
                ativos.append(t)
                continue
            if not info["tem"]:
                continue
            t["tipo"] = "business" if info["business"] else "pessoal"
            t["nome_business"] = info["nome_business"]
            if t["tipo"] == "pessoal":
                t["pontos"] = min(100, t["pontos"] + 10)
                t["motivos"] = t["motivos"] + ["conta pessoal de WhatsApp"]
                t["faixa"] = "A" if t["pontos"] >= 65 else "B" if t["pontos"] >= 40 else "C"
            ativos.append(t)
        sem_conta = bool(opcoes) and not ativos
        opcoes = [t for t in ativos if not so_pessoal or t["tipo"] == "pessoal"]
        # empate: WhatsApp confirmado na frente, depois sempre o mesmo número
        opcoes.sort(key=lambda t: (-t["pontos"], not t["tipo"], t["tel"]))
        verificado = "não" if sem_conta else "sim" if opcoes and opcoes[0]["tipo"] else "não verificado"
    escolhido = opcoes[0] if opcoes else None
    alternativo = opcoes[1] if len(opcoes) > 1 else None
    faixa = escolhido["faixa"] if escolhido else "C"
    if faixa == "A" and not lead["primeiro_nome"]:
        faixa = "B"  # sem nome do dono não dá para tratar como contato direto
    return {
        **{k: v for k, v in lead.items() if k != "telefones"},
        "telefone": e164(escolhido["tel"]) if escolhido else "",
        "telefone_alternativo": e164(alternativo["tel"]) if alternativo else "",
        "chance_dono": escolhido["pontos"] if escolhido else 0,
        "motivos_contato": "; ".join(escolhido["motivos"]) if escolhido else "sem celular válido",
        "faixa": faixa,
        "whatsapp": verificado,
        "tipo_whatsapp": (escolhido or {}).get("tipo", ""),
        "nome_whatsapp_business": (escolhido or {}).get("nome_business", ""),
    }
