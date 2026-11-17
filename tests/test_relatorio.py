"""PDF da lista e da pasta: conteúdo, limites e rotas."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from conftest import SENHA_TESTE
from solar_leads import pastas, pipeline, relatorio
from solar_leads.web import servidor


def contato(i: int, **extra) -> dict:
    return {
        "primeiro_nome": "MARIA", "sobrenome": f"DE SOUZA {i}", "telefone": f"+55389999900{i:02d}", "telefone_alternativo": "",
        "email": f"maria{i}@exemplo.com", "faixa": "A" if i % 2 else "B", "chance_dono": 90, "whatsapp": "sim" if i % 3 == 0 else "não verificado",
        "tipo_whatsapp": "pessoal" if i % 3 == 0 else "", "empresa": "Padaria Central", "qualificacao_dono": "Sócio-Administrador",
        "atividade": "Padaria e confeitaria com predominância de revenda", "bairro": "CENTRO", "cidade": "Montes Claros", "uf": "MG",
        "potencial_casa": 8, "potencial_empresa": 5, "oportunidade": "Casa e empresa", "solar": "sem registro",
        "idade_empresa": 6, "numero_socios": 2, **extra,
    }


def paginas(pdf: bytes) -> int:
    return len(re.findall(rb"/Type /Page\b", pdf))


@pytest.mark.parametrize(
    "entrada,esperado",
    [("+5538991211266", "(38) 99121-1266"), ("+553832221234", "(38) 3222-1234"), ("38991211266", "(38) 99121-1266"), ("", ""), (None, "")],
)
def test_telefone_formatado(entrada, esperado):
    assert relatorio.telefone(entrada) == esperado


@pytest.mark.parametrize(
    "entrada,esperado",
    [("RIO DE JANEIRO", "Rio de Janeiro"), ("maria DA silva E souza", "Maria da Silva e Souza"), ("", ""), (None, "")],
)
def test_titulo(entrada, esperado):
    assert relatorio.titulo(entrada) == esperado


def test_texto_longo_e_cortado_com_reticencias():
    relatorio._fontes()
    curto = relatorio._cabe("Comércio varejista de produtos alimentícios em geral", relatorio.REGULAR, 8, 100)
    assert curto.endswith("…") and len(curto) < 40
    assert relatorio._cabe("Curto", relatorio.REGULAR, 8, 100) == "Curto"


def test_pdf_tem_capa_e_sete_contatos_por_pagina():
    pdf = relatorio.gerar_pdf([contato(i) for i in range(15)], nome="Montes Claros", regiao="MG")
    assert pdf.startswith(b"%PDF")
    assert paginas(pdf) == 1 + 3  # capa + 7 + 7 + 1
    assert b"wa.me/5538999990000" in pdf  # o telefone abre a conversa no WhatsApp
    assert b"Inter-SemiBold" in pdf and b"Inter-Regular" in pdf


@pytest.mark.parametrize("publico", ["dono", "empresa"])
def test_pdf_aguenta_contato_quase_vazio(publico):
    vazio = {"telefone": "+5511999990001", "faixa": "B"}
    sem_nome = contato(1, primeiro_nome="", sobrenome="", email="", bairro="", idade_empresa="", numero_socios="", potencial_casa=None)
    pdf = relatorio.gerar_pdf([vazio, sem_nome], nome="Teste " * 30, publico=publico, filtro="Com WhatsApp confirmado")
    assert paginas(pdf) == 2


def test_pdf_recusa_lista_grande_demais(monkeypatch):
    monkeypatch.setattr(relatorio, "MAX_CONTATOS", 3)
    with pytest.raises(ValueError, match="divida em pastas"):
        relatorio.gerar_pdf([contato(i) for i in range(4)], nome="X")


# ---------- rotas ----------

@pytest.fixture
def logado():
    with TestClient(servidor.app, base_url="https://testserver", follow_redirects=False) as c:
        c.headers["X-Forwarded-For"] = "203.0.113.240"
        assert c.post("/api/login", json={"senha": SENHA_TESTE}).status_code == 200
        yield c


@pytest.fixture
def lista():
    base = {c: "" for c in pipeline.CHAVES + pipeline.INTERNAS}
    linhas = [{**base, **contato(i), "aptidao_solar": 5, "situacao": "ativa", "fonte": "Receita", "cnpj": f"{i:014d}"} for i in range(9)]
    pipeline.ordenar(linhas, "Montes Claros")
    id_ = "20260102-000000-montes-claros-mg"
    pipeline.salvar_parquet(linhas, servidor.LISTAS / f"{id_}.parquet")
    (servidor.LISTAS / f"{id_}.json").write_text(json.dumps({
        "id": id_, "criada_em": "2026-01-02T00:00:00", "cidade": "MONTES CLAROS", "uf": "MG", "atividades": [],
        "parametros": {}, "status": "pronta", "etapa": None, "erro": None, "aviso": None, "resumo": {},
    }))
    yield id_
    for sufixo in ("parquet", "json"):
        (servidor.LISTAS / f"{id_}.{sufixo}").unlink(missing_ok=True)


def test_pdf_da_lista_baixa_e_fica_no_historico(logado, lista):
    r = logado.get(f"/api/listas/{lista}/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith('attachment; filename="garimpo-montes-claros-mg-') and r.headers["content-disposition"].endswith('.pdf"')
    assert r.content.startswith(b"%PDF") and paginas(r.content) == 1 + 2
    ultima = logado.get("/api/exportacoes").json()[0]
    assert ultima["descricao"] == "PDF da lista de Montes Claros/MG" and ultima["contatos"] == 9


def test_pdf_da_lista_respeita_o_filtro(logado, lista):
    r = logado.get(f"/api/listas/{lista}/pdf", params={"tipo_whatsapp": ["pessoal", "business"]})
    assert r.status_code == 200 and paginas(r.content) == 2  # 3 contatos confirmados: capa + 1 página
    assert logado.get(f"/api/listas/{lista}/pdf", params={"busca": "ninguem-com-esse-nome"}).status_code == 400


def test_pdf_acima_do_limite_da_400_com_explicacao(logado, lista, monkeypatch):
    monkeypatch.setattr(relatorio, "MAX_CONTATOS", 5)
    r = logado.get(f"/api/listas/{lista}/pdf")
    assert r.status_code == 400 and "divida em pastas" in r.json()["detail"]


def test_pdf_da_pasta(logado, lista):
    pasta = pastas.criar("Entrega do cliente")
    logado.post(f"/api/pastas/{pasta}/adicionar", params={"lista_id": lista, "quantidade": 4})
    r = logado.get(f"/api/pastas/{pasta}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and paginas(r.content) == 2
    assert "pasta-entrega-cliente" in r.headers["content-disposition"]
    assert logado.get(f"/api/pastas/{pastas.criar('Vazia')}/pdf").status_code == 400
    assert logado.get("/api/pastas/99999/pdf").status_code == 404


def test_pdf_exige_sessao_e_vale_com_chave_na_api(logado, lista):
    with TestClient(servidor.app, base_url="https://testserver", follow_redirects=False) as anonimo:
        anonimo.headers["X-Forwarded-For"] = "203.0.113.241"
        assert anonimo.get(f"/api/listas/{lista}/pdf").status_code == 401
        chave = logado.post("/api/chaves", json={"nome": "pdf"}).json()["chave"]
        r = anonimo.get(f"/api/v1/listas/{lista}/pdf", headers={"Authorization": f"Bearer {chave}"})
        assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_filtro_em_palavras():
    f = servidor.Filtros(tipo_whatsapp=["pessoal", "business"], uf=["MG"], busca=" solar ")
    assert servidor._descricao_do_filtro(f) == "Com WhatsApp confirmado  ·  MG  ·  Busca “solar”"
    assert servidor._descricao_do_filtro(servidor.Filtros()) == ""
