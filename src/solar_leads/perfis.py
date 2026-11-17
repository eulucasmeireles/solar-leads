"""Perfis de busca: a receita de uma lista, guardada para reaproveitar em qualquer cidade.

Um perfil diz o que se vende, quem é o cliente ideal, quais atividades entram (com um peso de
1 a 3 para cada), quais filtros valem e qual critério a IA usa para avaliar as empresas.
Dois perfis vêm prontos e não mudam: "Energia solar" (o funcionamento original, com ANEEL e
potencial da casa e da empresa) e "Sem perfil" (lista comum, sem nada de solar).
Os perfis criados ficam no mesmo SQLite das pastas, que já entra na cópia de segurança.
"""

from __future__ import annotations

import json
import re
from contextlib import closing

from . import pastas

SOLAR, GERAL = "solar", "geral"
PORTES = ("01", "03", "05")
MAX_ATIVIDADES = 60

CRITERIO_SOLAR = (
    "Vendo sistemas de energia solar fotovoltaica para empresas e para a casa dos donos. "
    "O cliente ideal tem conta de luz alta: comércio, indústria e serviços que funcionam de dia, "
    "com imóvel próprio ou ponto fixo, alguns anos de operação e porte para investir."
)

PRONTOS = {
    SOLAR: {
        "id": SOLAR, "nome": "Energia solar", "pronto": True, "solar": True,
        "receita": {
            "oferta": "Energia solar", "cliente_ideal": "Donos de empresas e de casas que ainda não têm energia solar.",
            "criterio_ia": CRITERIO_SOLAR, "explicacao": "", "atividades": [], "palavras": [],
            "filtros": {"so_ativas": False, "incluir_mei": False, "so_locais": True, "idade_min": 0, "portes": []},
        },
    },
    GERAL: {
        "id": GERAL, "nome": "Sem perfil", "pronto": True, "solar": False,
        "receita": {
            "oferta": "", "cliente_ideal": "", "criterio_ia": "", "explicacao": "", "atividades": [], "palavras": [],
            "filtros": {"so_ativas": False, "incluir_mei": False, "so_locais": True, "idade_min": 0, "portes": []},
        },
    },
}


class PerfilInvalido(ValueError):
    pass


def _texto(valor, limite: int) -> str:
    texto = " ".join(str(valor or "").split()) if limite <= 200 else str(valor or "").strip()
    return texto[:limite]


def _sim(valor, padrao: bool) -> bool:
    return padrao if valor is None else bool(valor)


def receita_valida(bruta: dict | None) -> dict:
    """Confere e normaliza a receita que veio da tela, da API ou da IA."""
    bruta = bruta or {}
    atividades, vistos = [], set()
    for a in bruta.get("atividades") or []:
        codigo = "".join(c for c in str((a or {}).get("codigo") or "") if c.isdigit())
        if len(codigo) != 7 or codigo in vistos:
            continue
        vistos.add(codigo)
        try:
            peso = int((a or {}).get("peso") or 2)
        except (TypeError, ValueError):
            peso = 2
        atividades.append({"codigo": codigo, "descricao": _texto(a.get("descricao"), 200), "peso": max(1, min(3, peso))})
    if len(atividades) > MAX_ATIVIDADES:
        raise PerfilInvalido(f"Um perfil pode ter até {MAX_ATIVIDADES} atividades.")
    palavras = sorted({_texto(p, 40).upper() for p in bruta.get("palavras") or []} - {""})
    if len(palavras) > 10 or any(len(p) < 3 for p in palavras):
        raise PerfilInvalido("Use até 10 palavras, cada uma com pelo menos 3 letras.")
    f = bruta.get("filtros") or {}
    try:
        idade_min = max(0, min(50, int(f.get("idade_min") or 0)))
    except (TypeError, ValueError):
        idade_min = 0
    return {
        "oferta": _texto(bruta.get("oferta"), 500),
        "cliente_ideal": _texto(bruta.get("cliente_ideal"), 1000),
        "criterio_ia": _texto(bruta.get("criterio_ia"), 2000),
        "explicacao": _texto(bruta.get("explicacao"), 2000),
        "atividades": atividades,
        "palavras": palavras,
        "filtros": {
            "so_ativas": _sim(f.get("so_ativas"), True),
            "incluir_mei": _sim(f.get("incluir_mei"), False),
            "so_locais": _sim(f.get("so_locais"), True),
            "idade_min": idade_min,
            "portes": sorted({str(p).zfill(2) for p in f.get("portes") or []} & set(PORTES)),
        },
    }


def _nome(nome: str) -> str:
    nome = _texto(nome, 80)
    if not nome:
        raise PerfilInvalido("Dê um nome para o perfil.")
    return nome


def _do_banco(linha) -> dict:
    return {
        "id": str(linha["id"]), "nome": linha["nome"], "pronto": False, "solar": False,
        "criado_em": linha["criado_em"], "atualizado_em": linha["atualizado_em"], "receita": json.loads(linha["receita"]),
    }


def listar() -> list[dict]:
    with closing(pastas._con()) as con:
        linhas = con.execute("SELECT * FROM perfis ORDER BY atualizado_em DESC, id DESC").fetchall()
    return [PRONTOS[SOLAR], PRONTOS[GERAL], *(_do_banco(l) for l in linhas)]


def um(perfil_id: str | int | None) -> dict | None:
    perfil_id = str(perfil_id or "").strip()
    if perfil_id in PRONTOS:
        return PRONTOS[perfil_id]
    if not perfil_id.isdigit():
        return None
    with closing(pastas._con()) as con:
        linha = con.execute("SELECT * FROM perfis WHERE id = ?", [int(perfil_id)]).fetchone()
    return _do_banco(linha) if linha else None


def criar(nome: str, receita: dict) -> dict:
    nome, receita = _nome(nome), receita_valida(receita)
    agora = pastas._agora()
    with closing(pastas._con()) as con, con:
        if con.execute("SELECT 1 FROM perfis WHERE nome = ? COLLATE NOCASE", [nome]).fetchone() or nome.lower() in {
            p["nome"].lower() for p in PRONTOS.values()
        }:
            raise PerfilInvalido("Já existe um perfil com esse nome.")
        novo = con.execute(
            "INSERT INTO perfis (nome, receita, criado_em, atualizado_em) VALUES (?, ?, ?, ?)",
            [nome, json.dumps(receita, ensure_ascii=False), agora, agora],
        ).lastrowid
    return um(novo)


def atualizar(perfil_id: str, nome: str, receita: dict) -> dict:
    if str(perfil_id) in PRONTOS:
        raise PerfilInvalido("Os perfis prontos não podem ser mudados. Salve uma cópia com outro nome.")
    if not um(perfil_id):
        raise PerfilInvalido("Perfil não encontrado.")
    nome, receita = _nome(nome), receita_valida(receita)
    with closing(pastas._con()) as con, con:
        if con.execute("SELECT 1 FROM perfis WHERE nome = ? COLLATE NOCASE AND id <> ?", [nome, int(perfil_id)]).fetchone():
            raise PerfilInvalido("Já existe um perfil com esse nome.")
        con.execute(
            "UPDATE perfis SET nome = ?, receita = ?, atualizado_em = ? WHERE id = ?",
            [nome, json.dumps(receita, ensure_ascii=False), pastas._agora(), int(perfil_id)],
        )
    return um(perfil_id)


def excluir(perfil_id: str) -> None:
    if str(perfil_id) in PRONTOS:
        raise PerfilInvalido("Os perfis prontos não podem ser excluídos.")
    if not str(perfil_id).isdigit():
        raise PerfilInvalido("Perfil não encontrado.")
    with closing(pastas._con()) as con, con:
        con.execute("DELETE FROM perfis WHERE id = ?", [int(perfil_id)])


def retrato(perfil: dict) -> dict:
    """O que a lista guarda do perfil no momento em que foi gerada (mudar o perfil depois não a altera)."""
    r = perfil["receita"]
    return {
        "id": perfil["id"], "nome": perfil["nome"], "solar": perfil["solar"],
        "oferta": r["oferta"], "cliente_ideal": r["cliente_ideal"], "criterio_ia": r["criterio_ia"],
        "atividades": [{"codigo": a["codigo"], "descricao": a["descricao"], "peso": a["peso"]} for a in r["atividades"]],
        "filtros": r["filtros"],
    }


def do_meta(meta: dict) -> dict:
    """O perfil de uma lista. Listas de antes dos perfis: público "dono" é solar, "empresa" é sem perfil."""
    if meta.get("perfil"):
        return meta["perfil"]
    return retrato(PRONTOS[SOLAR if (meta.get("publico") or "dono") == "dono" else GERAL])


def pesos(retrato_do_perfil: dict, atividades_da_lista: list[dict] | None = None) -> dict[str, int]:
    """Peso de cada atividade, pelo código e pela descrição.

    Sem atividades no perfil, as atividades escolhidas para a lista valem peso 2.
    """
    atividades = retrato_do_perfil.get("atividades") or [{**a, "peso": 2} for a in atividades_da_lista or []]
    saida: dict[str, int] = {}
    for a in atividades:
        peso = int(a.get("peso") or 2)
        if a.get("codigo"):
            saida[str(a["codigo"])] = peso
        if a.get("descricao"):
            saida[str(a["descricao"]).strip().lower()] = peso
    return saida


def criterio(retrato_do_perfil: dict) -> str:
    """O texto que a IA usa para avaliar: o critério escrito ou, sem ele, oferta e cliente ideal."""
    if retrato_do_perfil.get("criterio_ia"):
        return retrato_do_perfil["criterio_ia"]
    partes = [
        f"Vendo: {retrato_do_perfil['oferta']}." if retrato_do_perfil.get("oferta") else "",
        f"Cliente ideal: {retrato_do_perfil['cliente_ideal']}" if retrato_do_perfil.get("cliente_ideal") else "",
    ]
    return re.sub(r"\s+", " ", " ".join(p for p in partes if p)).strip()
