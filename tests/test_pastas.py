import sqlite3
import stat

import pytest

from solar_leads import pastas
from solar_leads.pastas import PastaInvalida


def c(telefone, **extra):
    return {"telefone": telefone, "primeiro_nome": "Ana", "sobrenome": "Souza", "empresa": "Padaria", **extra}


# ---------- criar / renomear / excluir ----------

def test_criar_pasta_e_listar():
    pid = pastas.criar("  Padarias   Centro ")
    lista = pastas.listar()
    assert [p["nome"] for p in lista] == ["Padarias Centro"]  # espaços normalizados
    assert lista[0]["id"] == pid and lista[0]["contatos"] == 0 and lista[0]["cidades"] == []


def test_listar_mais_recente_primeiro():
    a, b = pastas.criar("A"), pastas.criar("B")
    assert [p["id"] for p in pastas.listar()] == [b, a]


def test_nome_repetido_e_recusado_sem_diferenciar_maiusculas():
    pastas.criar("Padarias")
    with pytest.raises(PastaInvalida, match="Já existe"):
        pastas.criar("padarias")


@pytest.mark.parametrize("nome", ["", "   ", None])
def test_nome_vazio_e_recusado(nome):
    with pytest.raises(PastaInvalida, match="nome"):
        pastas.criar(nome)


def test_nome_com_mais_de_80_letras_e_recusado_e_com_80_passa():
    with pytest.raises(PastaInvalida):
        pastas.criar("x" * 81)
    assert pastas.criar("x" * 80)


def test_renomear():
    pid = pastas.criar("Velha")
    pastas.renomear(pid, "Nova")
    assert pastas.uma(pid)["nome"] == "Nova"


def test_renomear_para_nome_de_outra_pasta_e_recusado():
    pastas.criar("A")
    b = pastas.criar("B")
    with pytest.raises(PastaInvalida, match="Já existe"):
        pastas.renomear(b, "a")


def test_renomear_para_o_mesmo_nome_funciona():
    pid = pastas.criar("A")
    pastas.renomear(pid, "A")
    assert pastas.uma(pid)["nome"] == "A"


def test_renomear_para_vazio_e_recusado():
    pid = pastas.criar("A")
    with pytest.raises(PastaInvalida):
        pastas.renomear(pid, " ")


def test_excluir_apaga_a_pasta_e_seus_contatos():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c("+5511999990001")], "L1", "Uberlândia")
    pastas.excluir(pid)
    assert pastas.uma(pid) is None
    assert pastas.telefones() == []


def test_uma_pasta_inexistente_devolve_none():
    assert pastas.uma(999) is None


def test_arquivo_das_pastas_so_o_dono_le():
    pastas.criar("A")
    assert stat.S_IMODE(pastas.ARQUIVO.stat().st_mode) == 0o600


# ---------- adicionar / remover / contatos ----------

def test_adicionar_separa_novos_dos_que_ja_estavam():
    pid = pastas.criar("A")
    assert pastas.adicionar(pid, [c("+551"), c("+552")], "L1", "X") == (2, 0)
    assert pastas.adicionar(pid, [c("+552"), c("+553")], "L1", "X") == (1, 1)
    assert sorted(pastas.telefones(pid)) == ["+551", "+552", "+553"]


def test_adicionar_nao_sobrescreve_contato_que_ja_estava():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c("+551", empresa="Original")], "L1", "X")
    pastas.adicionar(pid, [c("+551", empresa="Outra")], "L2", "Y")
    _, linhas = pastas.contatos(pid)
    assert linhas[0]["empresa"] == "Original" and linhas[0]["lista_id"] == "L1"


def test_mesmo_telefone_pode_estar_em_pastas_diferentes():
    a, b = pastas.criar("A"), pastas.criar("B")
    pastas.adicionar(a, [c("+551")], "L", "X")
    assert pastas.adicionar(b, [c("+551")], "L", "X") == (1, 0)
    assert sorted(pastas.telefones()) == ["+551"]  # DISTINCT


def test_adicionar_ignora_linha_sem_telefone():
    pid = pastas.criar("A")
    novos, _ = pastas.adicionar(pid, [c(""), {"primeiro_nome": "Sem"}], "L", "X")
    assert novos == 0 and pastas.telefones(pid) == []


def test_listar_mostra_contagem_e_cidades_ordenadas():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c("+551")], "L1", "Uberaba")
    pastas.adicionar(pid, [c("+552")], "L2", "Araguari")
    p = pastas.uma(pid)
    assert p["contatos"] == 2 and p["cidades"] == ["Araguari", "Uberaba"]


def test_remover_devolve_quantos_foram_removidos():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c("+551"), c("+552"), c("+553")], "L", "X")
    assert pastas.remover(pid, ["+551", "+553", "+999"]) == 2
    assert pastas.telefones(pid) == ["+552"]


def test_remover_so_mexe_na_pasta_indicada():
    a, b = pastas.criar("A"), pastas.criar("B")
    pastas.adicionar(a, [c("+551")], "L", "X")
    pastas.adicionar(b, [c("+551")], "L", "X")
    pastas.remover(a, ["+551"])
    assert pastas.telefones(b) == ["+551"]


def test_contatos_na_ordem_de_entrada_com_paginacao():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c(f"+55{i}") for i in range(5)], "L", "X")
    total, p1 = pastas.contatos(pid, por_pagina=2)
    _, p2 = pastas.contatos(pid, pagina=2, por_pagina=2)
    _, p3 = pastas.contatos(pid, pagina=3, por_pagina=2)
    _, p4 = pastas.contatos(pid, pagina=4, por_pagina=2)
    assert total == 5
    assert [l["telefone"] for l in p1 + p2 + p3] == [f"+55{i}" for i in range(5)]
    assert [len(p1), len(p2), len(p3), len(p4)] == [2, 2, 1, 0]


def test_contatos_pagina_zero_ou_negativa_vale_como_primeira():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c("+551")], "L", "X")
    assert pastas.contatos(pid, pagina=0)[1] == pastas.contatos(pid, pagina=1)[1]
    assert pastas.contatos(pid, pagina=-3)[1] == pastas.contatos(pid, pagina=1)[1]


@pytest.mark.parametrize(
    "busca,esperados",
    [
        ("maria", ["+552"]),
        ("  MARIA ", ["+552"]),
        ("padaria", ["+551", "+552"]),
        ("12345678000190", ["+551"]),
        ("centro", ["+551"]),
        ("+552", ["+552"]),
        ("nada", []),
        ("", ["+551", "+552"]),
    ],
)
def test_contatos_busca_em_nome_empresa_cnpj_bairro_e_telefone(busca, esperados):
    pid = pastas.criar("A")
    pastas.adicionar(
        pid,
        [
            c("+551", cnpj="12345678000190", bairro="Centro"),
            c("+552", primeiro_nome="Maria", bairro="Jardim"),
        ],
        "L", "X",
    )
    total, linhas = pastas.contatos(pid, busca=busca)
    assert [l["telefone"] for l in linhas] == esperados and total == len(esperados)


def test_contatos_total_conta_o_filtrado_nao_a_pagina():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c(f"+55{i}") for i in range(4)], "L", "X")
    total, linhas = pastas.contatos(pid, busca="ana", por_pagina=1)
    assert (total, len(linhas)) == (4, 1)


def test_contatos_trazem_resultado_e_data():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c("+551")], "L", "X")
    _, [l] = pastas.contatos(pid)
    assert l["resultado"] == "" and l["adicionado_em"]


def test_pastas_dos_telefones():
    a, b = pastas.criar("A"), pastas.criar("B")
    pastas.adicionar(a, [c("+551"), c("+552")], "L", "X")
    pastas.adicionar(b, [c("+551")], "L", "X")
    r = pastas.pastas_dos(["+551", "+552", "+999"])
    assert sorted(p["nome"] for p in r["+551"]) == ["A", "B"]
    assert [p["nome"] for p in r["+552"]] == ["A"]
    assert "+999" not in r


def test_pastas_dos_lista_vazia():
    assert pastas.pastas_dos([]) == {}


def test_pastas_dos_com_mais_de_500_telefones():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c(f"+55{i}") for i in range(1200)], "L", "X")
    r = pastas.pastas_dos([f"+55{i}" for i in range(1200)])
    assert len(r) == 1200


# ---------- resultados ----------

def test_marcar_resultado_so_dos_telefones_da_pasta():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c("+551"), c("+552")], "L", "X")
    marcados, fora, alvo = pastas.marcar_resultado(pid, "respondeu", ["+551", "+551", "+999"])
    assert (marcados, fora, alvo) == (1, 1, ["+551"])
    resultados = {l["telefone"]: l["resultado"] for l in pastas.contatos(pid)[1]}
    assert resultados == {"+551": "respondeu", "+552": ""}


def test_marcar_resultado_todos_sem_resultado_nao_sobrescreve_os_ja_marcados():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c("+551"), c("+552"), c("+553")], "L", "X")
    pastas.marcar_resultado(pid, "interessado", ["+551"])
    marcados, fora, alvo = pastas.marcar_resultado(pid, "sem_resposta", todos_sem_resultado=True)
    assert (marcados, fora) == (2, 0) and sorted(alvo) == ["+552", "+553"]
    resultados = {l["telefone"]: l["resultado"] for l in pastas.contatos(pid)[1]}
    assert resultados["+551"] == "interessado"


def test_marcar_resultado_vazio_limpa_a_marca():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c("+551")], "L", "X")
    pastas.marcar_resultado(pid, "respondeu", ["+551"])
    pastas.marcar_resultado(pid, "", ["+551"])
    assert pastas.contatos(pid)[1][0]["resultado"] == ""


def test_marcar_resultado_desconhecido_e_recusado():
    pid = pastas.criar("A")
    with pytest.raises(PastaInvalida, match="Resultado"):
        pastas.marcar_resultado(pid, "inventado", ["+551"])


def test_resumo_de_resultados_sem_nenhum_resultado():
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c("+551")], "L", "X")
    r = pastas.resumo_resultados(pid)
    assert r["total"] == 1 and r["com_resultado"] == 0 and r["taxa_resposta"] is None
    assert r["grupos"] == {"forca": [], "oportunidade": [], "whatsapp": []}


def test_resumo_de_resultados_taxas_por_grupo():
    pid = pastas.criar("A")
    pastas.adicionar(
        pid,
        [
            c("+551", faixa="A", oportunidade="Casa", tipo_whatsapp="pessoal"),
            c("+552", faixa="A", oportunidade="Casa", tipo_whatsapp="pessoal"),
            c("+553", faixa="B", oportunidade="Empresa", tipo_whatsapp=""),
            c("+554", faixa="B", oportunidade="Empresa", tipo_whatsapp="business"),
            c("+555", faixa="B"),  # sem resultado: fora das taxas
        ],
        "L", "X",
    )
    pastas.marcar_resultado(pid, "interessado", ["+551"])
    pastas.marcar_resultado(pid, "sem_resposta", ["+552", "+553"])
    pastas.marcar_resultado(pid, "nao_quer", ["+554"])
    r = pastas.resumo_resultados(pid)
    assert (r["total"], r["com_resultado"], r["responderam"], r["interessados"]) == (5, 4, 2, 1)
    assert r["taxa_resposta"] == 50.0
    forca = {g["valor"]: g for g in r["grupos"]["forca"]}
    assert forca["A"]["enviados"] == 2 and forca["A"]["responderam"] == 1 and forca["A"]["taxa"] == 50.0
    assert forca["A"]["rotulo"] == "Contato forte" and forca["A"]["interessados"] == 1
    assert forca["B"]["enviados"] == 2 and forca["B"]["responderam"] == 1
    whats = {g["valor"]: g["rotulo"] for g in r["grupos"]["whatsapp"]}
    assert whats == {"pessoal": "WhatsApp pessoal", "": "Não verificado", "business": "WhatsApp comercial"}
    assert [x["valor"] for x in r["por_resultado"]] == ["interessado", "nao_quer", "sem_resposta"]


def test_resumo_geral_soma_todas_as_pastas():
    a, b = pastas.criar("A"), pastas.criar("B")
    pastas.adicionar(a, [c("+551", faixa="A")], "L", "X")
    pastas.adicionar(b, [c("+551", faixa="A"), c("+552", faixa="A")], "L", "X")
    pastas.marcar_resultado(a, "respondeu", ["+551"])
    pastas.marcar_resultado(b, "sem_resposta", ["+552"])
    r = pastas.resumo_resultados()
    assert r["total"] == 3 and r["com_resultado"] == 2 and r["taxa_resposta"] == 50.0


def test_nao_entregue_e_sem_resposta_nao_contam_como_resposta():
    assert not ({"nao_entregue", "sem_resposta"} & pastas.RESPONDEU)


# ---------- não contatar ----------

def test_bloquear_conta_so_os_novos_e_tira_das_pastas():
    a, b = pastas.criar("A"), pastas.criar("B")
    pastas.adicionar(a, [c("+551"), c("+552")], "L", "X")
    pastas.adicionar(b, [c("+551")], "L", "X")
    assert pastas.bloquear(["+551", "+999"], "pediu para sair") == 2
    assert pastas.bloquear(["+551"]) == 0
    assert pastas.telefones() == ["+552"]
    assert sorted(pastas.bloqueados()) == ["+551", "+999"]


def test_bloquear_corta_o_motivo_em_120_caracteres():
    pastas.bloquear(["+551"], "m" * 500)
    _, itens = pastas.bloqueados_recentes()
    assert len(itens[0]["motivo"]) == 120


def test_bloqueados_recentes_total_e_limite():
    pastas.bloquear([f"+55{i}" for i in range(5)])
    total, itens = pastas.bloqueados_recentes(limite=2)
    assert total == 5 and len(itens) == 2


def test_desbloquear():
    pastas.bloquear(["+551"])
    assert pastas.desbloquear("+551") == 1
    assert pastas.desbloquear("+551") == 0
    assert pastas.bloqueados() == []


# ---------- exportações ----------

def test_registrar_exportacao_e_listar_mais_recente_primeiro():
    pastas.registrar_exportacao("lista", "L1", "primeira", 10, "1.2.3.4")
    pastas.registrar_exportacao("pasta", "7", "segunda", 3)
    r = pastas.exportacoes_recentes()
    assert [e["descricao"] for e in r] == ["segunda", "primeira"]
    assert r[0]["contatos"] == 3 and r[0]["origem"] == "pasta"


def test_registrar_exportacao_corta_descricao_e_ip():
    pastas.registrar_exportacao("lista", "L", "d" * 500, 1, "i" * 500)
    assert len(pastas.exportacoes_recentes()[0]["descricao"]) == 200
    con = sqlite3.connect(pastas.ARQUIVO)
    assert len(con.execute("SELECT ip FROM exportacoes").fetchone()[0]) == 60
    con.close()


def test_exportacao_da_pasta_aparece_na_listagem_da_pasta():
    pid = pastas.criar("A")
    pastas.registrar_exportacao("pasta", str(pid), "x", 1)
    pastas.registrar_exportacao("lista", str(pid), "nao conta", 1)
    p = pastas.uma(pid)
    assert p["exportacoes"] == 1 and p["ultima_exportacao"]


# ---------- cópia de segurança ----------

def test_copia_de_seguranca_gera_arquivo_legivel(tmp_path):
    pid = pastas.criar("A")
    pastas.adicionar(pid, [c("+551")], "L", "X")
    caminho = pastas.copia_de_seguranca(tmp_path / "bk")
    assert caminho.startswith(str(tmp_path / "bk"))
    con = sqlite3.connect(caminho)
    assert con.execute("SELECT count(*) FROM pasta_contatos").fetchone()[0] == 1
    con.close()
    assert stat.S_IMODE(__import__("os").stat(caminho).st_mode) == 0o600


def test_copia_de_seguranca_guarda_so_as_ultimas(tmp_path):
    pastas.criar("A")
    destino = tmp_path / "bk"
    destino.mkdir()
    for dia in range(1, 6):
        (destino / f"pastas-2020010{dia}.sqlite").write_bytes(b"velho")
    pastas.copia_de_seguranca(destino, manter=3)
    nomes = sorted(p.name for p in destino.glob("pastas-*.sqlite"))
    assert len(nomes) == 3
    assert nomes[:2] == ["pastas-20200104.sqlite", "pastas-20200105.sqlite"]


def test_copia_de_seguranca_duas_vezes_no_mesmo_dia_sobrescreve(tmp_path):
    pastas.criar("A")
    pastas.copia_de_seguranca(tmp_path)
    pastas.copia_de_seguranca(tmp_path)
    assert len(list(tmp_path.glob("pastas-*.sqlite"))) == 1


def test_adicionar_linha_sem_telefone_nao_conta_como_ja_estava():
    pid = pastas.criar("A")
    novos, ja = pastas.adicionar(pid, [c("+551"), c("")], "L", "X")
    assert (novos, ja) == (1, 0)
