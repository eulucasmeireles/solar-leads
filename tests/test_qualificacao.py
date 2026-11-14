from datetime import date

import pytest

from solar_leads.qualificacao import (
    aptidao_solar, oportunidade, peso_cnae, pontuar_contato, potencial_da_pessoa,
)

HOJE = date(2026, 1, 1)


def contato(**mudancas):
    base = dict(
        celular=True, no_maps=False, no_cadastro=False, empresas_com_o_numero=1,
        empresas_do_dono_com_o_numero=0, de_outra_empresa_do_dono=False, dono_unico=False,
        email_contabilidade=False, anos_encerrada=None,
    )
    base.update(mudancas)
    return pontuar_contato(**base)


# ---------- pontuar_contato ----------

def test_telefone_fixo_e_sempre_faixa_c_com_zero_pontos():
    assert contato(celular=False, no_maps=True, no_cadastro=True) == (0, "C", ["telefone fixo"])


def test_numero_em_muitas_empresas_e_tratado_como_contador():
    pontos, faixa, motivos = contato(empresas_com_o_numero=9, empresas_do_dono_com_o_numero=1, no_maps=True)
    assert (pontos, faixa) == (5, "C")
    assert "contador" in motivos[0]


def test_numero_em_oito_empresas_ainda_nao_e_contador():
    assert contato(empresas_com_o_numero=8, no_cadastro=True)[0] == 40


def test_numero_em_muitas_empresas_mas_a_maioria_do_dono_nao_e_contador():
    pontos, _, _ = contato(empresas_com_o_numero=10, empresas_do_dono_com_o_numero=5, no_cadastro=True)
    assert pontos > 5


def test_celular_sem_nenhum_sinal_fica_com_30_na_faixa_c():
    assert contato() == (30, "C", ["celular"])


def test_celular_so_no_cadastro_fica_na_faixa_b():
    pontos, faixa, motivos = contato(no_cadastro=True)
    assert (pontos, faixa) == (40, "B")
    assert "no cadastro da Receita" in motivos


def test_celular_so_no_maps_fica_na_faixa_b():
    assert contato(no_maps=True)[:2] == (45, "B")


def test_mesmo_numero_no_maps_e_no_cadastro_chega_exatamente_a_65_faixa_a():
    assert contato(no_maps=True, no_cadastro=True)[:2] == (65, "A")


def test_celular_em_duas_empresas_do_dono_soma_25():
    assert contato(empresas_do_dono_com_o_numero=2)[0] == 55


def test_celular_so_de_outra_empresa_do_dono_fica_na_faixa_a():
    pontos, faixa, motivos = contato(de_outra_empresa_do_dono=True)
    assert (pontos, faixa) == (65, "A")
    assert "celular cadastrado em outra empresa do dono" in motivos


@pytest.mark.parametrize("campo", ["no_maps", "no_cadastro"])
def test_bonus_de_outra_empresa_do_dono_nao_vale_se_o_numero_ja_esta_na_propria_empresa(campo):
    sem = contato(**{campo: True})[0]
    com = contato(**{campo: True, "de_outra_empresa_do_dono": True})[0]
    assert sem == com


def test_dono_unico_soma_10():
    assert contato(no_cadastro=True, dono_unico=True)[0] == 50


def test_email_de_contabilidade_tira_20_quando_nao_esta_no_maps():
    assert contato(no_cadastro=True, email_contabilidade=True)[:2] == (20, "C")


def test_email_de_contabilidade_nao_pune_quem_esta_no_maps():
    assert contato(no_maps=True, email_contabilidade=True)[0] == 45


def test_empresa_encerrada_ha_10_anos_tira_15():
    assert contato(no_cadastro=True, anos_encerrada=10)[:2] == (25, "C")


def test_empresa_encerrada_ha_menos_de_10_anos_nao_tira_nada():
    assert contato(no_cadastro=True, anos_encerrada=9.9)[0] == 40


@pytest.mark.parametrize(
    "extra",
    [{"no_maps": True}, {"empresas_do_dono_com_o_numero": 2}, {"de_outra_empresa_do_dono": True}],
)
def test_encerrada_ha_muito_tempo_nao_pune_com_sinal_forte(extra):
    com = contato(no_cadastro=True, anos_encerrada=12, **extra)[0]
    sem = contato(no_cadastro=True, **extra)[0]
    assert com == sem


def test_pontos_nunca_ficam_negativos():
    assert contato(email_contabilidade=True, anos_encerrada=20)[0] == 0


def test_pontos_nunca_passam_de_100():
    pontos, faixa, _ = contato(no_maps=True, no_cadastro=True, empresas_do_dono_com_o_numero=3, dono_unico=True)
    assert (pontos, faixa) == (100, "A")


@pytest.mark.parametrize(
    "pontos_alvo,faixa",
    [(30, "C"), (40, "B"), (55, "B"), (65, "A"), (75, "A")],
)
def test_limites_das_faixas(pontos_alvo, faixa):
    casos = {
        30: dict(),
        40: dict(no_cadastro=True),
        55: dict(no_maps=True, dono_unico=True),
        65: dict(no_maps=True, no_cadastro=True),
        75: dict(no_maps=True, no_cadastro=True, dono_unico=True),
    }
    pontos, f, _ = contato(**casos[pontos_alvo])
    assert (pontos, f) == (pontos_alvo, faixa)


# ---------- peso_cnae ----------

@pytest.mark.parametrize(
    "cnae,peso",
    [("4721102", 3), ("4741500", 1), ("4799999", 1), ("0111301", 2), ("1091101", 3), ("0000000", 0), ("", 0), (None, 0)],
)
def test_peso_do_cnae_usa_a_classe_e_cai_para_a_divisao(cnae, peso):
    assert peso_cnae(cnae) == peso


# ---------- aptidao_solar ----------

def apt(**mudancas):
    base = dict(cnae=None, porte=None, capital_social=None, data_inicio=None, complemento=None, avaliacoes=None, hoje=HOJE)
    base.update(mudancas)
    return aptidao_solar(**base)


def test_aptidao_sem_nenhum_dado_e_zero():
    assert apt() == (0, [])


def test_aptidao_atividade_de_consumo_diurno_soma_peso_mais_um():
    assert apt(cnae="4721102")[0] == 4


def test_aptidao_completa_soma_todos_os_criterios():
    nota, motivos = apt(
        cnae="4721102", porte="03", capital_social=100_000, data_inicio=date(2020, 12, 1), avaliacoes=50,
    )
    assert nota == 4 + 2 + 1 + 1 + 1
    assert len(motivos) == 5


@pytest.mark.parametrize(
    "inicio,esperado",
    [(date(2025, 6, 1), 0), (date(2024, 1, 1), 1), (date(2020, 12, 1), 2)],
)
def test_aptidao_anos_de_operacao(inicio, esperado):
    assert apt(data_inicio=inicio)[0] == esperado


@pytest.mark.parametrize("porte,esperado", [("01", 0), ("03", 1), ("05", 1), (None, 0)])
def test_aptidao_porte(porte, esperado):
    assert apt(porte=porte)[0] == esperado


@pytest.mark.parametrize("capital,esperado", [(99_999, 0), (100_000, 1), (None, 0)])
def test_aptidao_capital_social(capital, esperado):
    assert apt(capital_social=capital)[0] == esperado


@pytest.mark.parametrize("avaliacoes,esperado", [(49, 0), (50, 1), (None, 0)])
def test_aptidao_avaliacoes_no_maps(avaliacoes, esperado):
    assert apt(avaliacoes=avaliacoes)[0] == esperado


@pytest.mark.parametrize("complemento", ["Sala 3", "galeria x", "SHOPPING CENTER", "Apt 12", "Box 4"])
def test_aptidao_complemento_sem_telhado_tira_2(complemento):
    assert apt(cnae="4721102", complemento=complemento)[0] == 2


def test_aptidao_nunca_fica_negativa():
    nota, motivos = apt(complemento="SALA 1")
    assert nota == 0
    assert any("sem telhado" in m for m in motivos)


def test_aptidao_nunca_passa_de_10():
    nota, _ = apt(cnae="5510", porte="05", capital_social=1e9, data_inicio=date(2000, 1, 1), avaliacoes=999)
    assert 0 <= nota <= 10


# ---------- potencial_da_pessoa ----------

def pot(**mudancas):
    base = dict(
        cnae=None, porte=None, capital_do_dono=None, empresas_ativas=None, empresas_total=None,
        socio_desde=None, idade=None, complemento=None, cargo=None, empresa_ativa=True,
        solar="sem registro", regiao_solar=False, hoje=HOJE,
    )
    base.update(mudancas)
    return potencial_da_pessoa(**base)


def test_potencial_base_e_2():
    assert pot() == (2, [])


def test_potencial_medico_soma_3_e_diz_renda_alta():
    nota, motivos = pot(cnae="8630503")
    assert nota == 5
    assert motivos == ["médico ou dentista (renda provável alta)"]


def test_potencial_profissao_de_peso_2_nao_menciona_renda_alta():
    nota, motivos = pot(cnae="6920601")
    assert nota == 4
    assert motivos == ["contador"]


def test_potencial_profissao_vale_mais_que_o_porte():
    assert pot(cnae="8630503", porte="05")[0] == 5


@pytest.mark.parametrize("porte,esperado", [("05", 4), ("03", 3), ("01", 2)])
def test_potencial_porte_sem_profissao(porte, esperado):
    assert pot(porte=porte)[0] == esperado


@pytest.mark.parametrize("extra", [dict(cnae="0111301"), dict(cargo="Produtor Rural")])
def test_potencial_produtor_rural_soma_2(extra):
    nota, motivos = pot(**extra)
    assert nota == 4
    assert "produtor rural" in motivos


def test_potencial_produtor_rural_por_cnae_e_cargo_conta_uma_vez():
    assert pot(cnae="0111301", cargo="PRODUTOR RURAL")[0] == 4


@pytest.mark.parametrize(
    "capital,esperado",
    [(49_999, 2), (50_000, 3), (199_999, 3), (200_000, 4), (999_999, 4), (1_000_000, 5), (None, 2)],
)
def test_potencial_capital_do_dono(capital, esperado):
    assert pot(capital_do_dono=capital)[0] == esperado


@pytest.mark.parametrize("ativas,esperado", [(None, 2), (0, 2), (1, 2), (2, 3), (3, 3), (4, 4), (10, 4)])
def test_potencial_varias_empresas_ativas(ativas, esperado):
    assert pot(empresas_ativas=ativas, empresas_total=ativas)[0] == esperado


def test_potencial_socio_ha_mais_de_5_anos():
    assert pot(socio_desde=date(2020, 12, 1))[0] == 3
    assert pot(socio_desde=date(2023, 1, 1))[0] == 2


@pytest.mark.parametrize(
    "idade,esperado",
    [("31 a 40", 3), ("41 a 50", 3), ("51 a 60", 3), ("21 a 30", 2), ("61 a 70", 2), ("71 a 80", 1), ("mais de 80", 1), (None, 2)],
)
def test_potencial_idade(idade, esperado):
    assert pot(idade=idade)[0] == esperado


def test_potencial_teve_empresas_mas_nenhuma_ativa_tira_2():
    nota, motivos = pot(empresas_ativas=0, empresas_total=3, empresa_ativa=False)
    assert nota == 0
    assert any("nenhuma está ativa" in m for m in motivos)


def test_potencial_empresa_encerrada_sem_outra_ativa_tira_1():
    assert pot(empresa_ativa=False, empresas_ativas=0, empresas_total=1)[0] == 1
    assert pot(empresa_ativa=False)[0] == 1


def test_potencial_empresa_encerrada_mas_com_outra_ativa_nao_tira():
    assert pot(empresa_ativa=False, empresas_ativas=1, empresas_total=2)[0] == 2


@pytest.mark.parametrize("comp", ["APTO 12", "Bloco B", "AP 301", "apartamento 2"])
def test_potencial_apartamento_tira_1(comp):
    assert pot(cnae="8630503", complemento=comp)[0] == 4


def test_potencial_complemento_comum_nao_e_apartamento():
    assert pot(complemento="CASA 2")[0] == 2


def test_potencial_regiao_com_muita_energia_solar_soma_1():
    assert pot(regiao_solar=True)[0] == 3


@pytest.mark.parametrize("solar", ["empresa já tem", "outra unidade da empresa tem", "outra empresa do dono tem"])
def test_potencial_quem_ja_comprou_solar_para_a_empresa_soma_1(solar):
    assert pot(solar=solar)[0] == 3


def test_potencial_indicio_fraco_de_solar_em_casa_tira_2():
    assert pot(cnae="8630503", solar="dono: indício fraco")[0] == 3


def test_potencial_nao_consultado_e_sem_registro_nao_mexem_na_nota():
    assert pot(solar="não consultado")[0] == pot(solar="sem registro")[0] == 2


def test_potencial_nunca_passa_de_10():
    nota, _ = pot(
        cnae="8630503", capital_do_dono=5e6, empresas_ativas=6, empresas_total=6, socio_desde=date(2000, 1, 1),
        idade="41 a 50", regiao_solar=True, solar="empresa já tem",
    )
    assert nota == 10


def test_potencial_nunca_fica_negativo():
    nota, _ = pot(
        empresas_ativas=0, empresas_total=3, empresa_ativa=False, complemento="APTO 1",
        solar="dono: indício fraco", idade="mais de 80",
    )
    assert nota == 0


# ---------- oportunidade ----------

@pytest.mark.parametrize(
    "casa,empresa,solar,esperado",
    [
        (9, 9, "empresa já tem", "Ampliação"),
        (0, 0, "empresa já tem", "Ampliação"),
        (5, 5, "sem registro", "Casa e empresa"),
        (10, 10, "sem registro", "Casa e empresa"),
        (4, 5, "sem registro", "Empresa"),
        (0, 9, "sem registro", "Empresa"),
        (5, 4, "sem registro", "Casa"),
        (9, 0, "sem registro", "Casa"),
        (4, 4, "sem registro", "Baixo potencial"),
        (0, 0, "sem registro", "Baixo potencial"),
        (5, 5, "outra unidade da empresa tem", "Casa e empresa"),
        (5, 5, "outra empresa do dono tem", "Casa e empresa"),
        (6, 0, "dono: indício fraco", "Casa"),
    ],
)
def test_oportunidade(casa, empresa, solar, esperado):
    assert oportunidade(casa, empresa, solar) == esperado
