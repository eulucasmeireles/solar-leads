"""Regras de aptidão para energia solar, de encaixe no perfil e de pontuação do contato.

Tudo aqui é ajustável: são pesos iniciais, não medidos em campanha.
"""

from __future__ import annotations

from datetime import date

# Prefixo de CNAE (classe, 4 ou 5 dígitos) -> peso de consumo diurno (0 a 3).
CNAE_PESO: dict[str, int] = {
    "1091": 3,  # panificação industrial
    "4721": 3,  # padaria e confeitaria (varejo)
    "4711": 3,  # hipermercados e supermercados
    "4712": 3,  # minimercados, mercearias
    "4722": 3,  # açougues e peixarias
    "4731": 3,  # postos de combustíveis
    "9313": 3,  # academias
    "5510": 3,  # hotéis
    "9601": 3,  # lavanderias
    "1011": 3,  # frigoríficos (abate de bovinos)
    "1012": 3,  # abate de suínos e aves
    "1013": 3,  # produtos de carne
    "1053": 3,  # sorvetes
    "5611": 2,  # restaurantes e lanchonetes
    "4771": 2,  # farmácias
    "8610": 2,  # hospitais
    "8630": 2,  # clínicas e consultórios
    "8511": 2,  # educação infantil
    "8513": 2,  # ensino fundamental
    "8520": 2,  # ensino médio
    "4520": 2,  # oficinas mecânicas
    "4744": 2,  # materiais de construção
    "4530": 2,  # autopeças
    "4741": 1,
    "4751": 1,
    "4753": 1,
    "4754": 1,
    "4781": 1,
    "4789": 1,
    "9602": 1,  # cabeleireiros
}

# Quando a classe não está na lista acima, vale o peso da divisão (2 primeiros dígitos).
CNAE_DIVISAO_PESO: dict[str, int] = {
    **{f"{d:02d}": 2 for d in (1, 2, 3)},  # agropecuária
    **{f"{d:02d}": 3 for d in range(5, 10)},  # extração mineral
    **{f"{d:02d}": 3 for d in range(10, 34)},  # indústria
    **{f"{d:02d}": 1 for d in range(35, 40)},  # energia, água, resíduos
    "45": 2,  # veículos: comércio e oficinas
    "46": 2,  # atacado
    "47": 1,  # varejo
    "49": 1, "50": 1, "51": 1, "52": 2, "53": 1,  # transporte e armazenagem
    "55": 3,  # hospedagem
    "56": 2,  # alimentação
    **{f"{d:02d}": 1 for d in range(58, 64)},  # informação e comunicação
    **{f"{d:02d}": 1 for d in range(69, 75)},  # escritórios profissionais
    "75": 2,  # veterinária
    "85": 2,  # educação
    "86": 2, "87": 2, "88": 2,  # saúde e assistência
    "90": 1, "91": 1, "92": 1, "93": 2,  # cultura, esporte e lazer
    "94": 1, "95": 1, "96": 1,  # associações, reparos, serviços pessoais
}

# Qualificações de sócio que indicam o dono/decisor, em ordem de prioridade.
QUALIF_DONO: dict[str, int] = {
    "49": 0,  # sócio-administrador
    "65": 0,  # titular pessoa física residente
    "50": 0,  # empresário
    "16": 1,  # presidente
    "10": 1,  # diretor
    "05": 1,  # administrador
    "28": 0,  # sócio-gerente
    "59": 0,  # produtor rural
    "22": 2,  # sócio
    "52": 2,  # sócio com capital
    "54": 2,  # fundador
}

COMPLEMENTO_SEM_TELHADO = ("SALA", "SHOPPING", "GALERIA", "CONJ", "ANDAR", "APT", "QUIOSQUE", "BOX")

# Um telefone que aparece em mais empresas do que isso é tratado como contador.
LIMITE_CONTADOR = 8


def peso_cnae(cnae: str | None) -> int:
    cnae = (cnae or "").strip()
    return CNAE_PESO.get(cnae[:4], CNAE_DIVISAO_PESO.get(cnae[:2], 0))


def aptidao_solar(
    cnae: str | None,
    porte: str | None,
    capital_social: float | None,
    data_inicio: date | None,
    complemento: str | None,
    avaliacoes: int | None,
    hoje: date | None = None,
) -> tuple[int, list[str]]:
    """Nota de 0 a 10 de aptidão do estabelecimento para solar, com os motivos."""
    hoje = hoje or date.today()
    nota = 0
    motivos: list[str] = []

    p = peso_cnae(cnae)
    if p:
        nota += p + 1
        motivos.append(f"atividade de consumo diurno (peso {p})")

    if data_inicio:
        anos = (hoje - data_inicio).days / 365.25
        if anos >= 5:
            nota += 2
            motivos.append("mais de 5 anos de operação")
        elif anos >= 2:
            nota += 1
            motivos.append("mais de 2 anos de operação")

    # Porte da Receita: 01 micro, 03 pequeno, 05 demais.
    if porte in ("03", "05"):
        nota += 1
        motivos.append("porte pequeno ou maior")
    if capital_social and capital_social >= 100_000:
        nota += 1
        motivos.append("capital social acima de R$ 100 mil")

    if avaliacoes and avaliacoes >= 50:
        nota += 1
        motivos.append("movimento alto no Google Maps")

    comp = (complemento or "").upper()
    if any(t in comp for t in COMPLEMENTO_SEM_TELHADO):
        nota -= 2
        motivos.append("endereço sugere imóvel sem telhado próprio")

    return max(0, min(10, nota)), motivos


def pontuar_contato(
    *,
    celular: bool,
    no_maps: bool,
    no_cadastro: bool,
    empresas_com_o_numero: int,
    empresas_do_dono_com_o_numero: int,
    de_outra_empresa_do_dono: bool,
    dono_unico: bool,
    email_contabilidade: bool,
    anos_encerrada: float | None = None,
) -> tuple[int, str, list[str]]:
    """Chance (0 a 100) de o número ser do dono, a faixa (A/B/C) e os motivos."""
    motivos: list[str] = []
    if not celular:
        return 0, "C", ["telefone fixo"]
    if empresas_com_o_numero > LIMITE_CONTADOR and empresas_do_dono_com_o_numero < empresas_com_o_numero / 2:
        return 5, "C", [f"número em {empresas_com_o_numero} empresas (provável contador)"]

    pontos = 30
    motivos.append("celular")
    if no_maps:
        pontos += 15
        motivos.append("em uso no Google Maps")
    if no_cadastro:
        pontos += 10
        motivos.append("no cadastro da Receita")
    if no_maps and no_cadastro:
        pontos += 10
        motivos.append("mesmo número nas duas fontes")
    if empresas_do_dono_com_o_numero >= 2:
        pontos += 25
        motivos.append(f"mesmo número em {empresas_do_dono_com_o_numero} empresas do dono")
    if de_outra_empresa_do_dono and not (no_maps or no_cadastro):
        pontos += 35
        motivos.append("celular cadastrado em outra empresa do dono")
    if dono_unico:
        pontos += 10
        motivos.append("empresa de dono único")
    if email_contabilidade and not no_maps:
        pontos -= 20
        motivos.append("e-mail do cadastro é de contabilidade")
    # Telefone que só aparece numa empresa fechada há muito tempo tem boa chance de já ser de outra pessoa.
    if anos_encerrada is not None and anos_encerrada >= 10 and not no_maps and empresas_do_dono_com_o_numero < 2 and not de_outra_empresa_do_dono:
        pontos -= 15
        motivos.append(f"empresa encerrada há {int(anos_encerrada)} anos (o número pode ter mudado de dono)")

    pontos = max(0, min(100, pontos))
    faixa = "A" if pontos >= 65 else "B" if pontos >= 40 else "C"
    return pontos, faixa, motivos


# ---- Potencial da pessoa (a casa do dono) ----
# O lead é a pessoa: quem tem empresa mora em algum lugar e paga conta de luz.
# A atividade da empresa revela o ofício e, com ele, a renda provável.
PROFISSAO: dict[str, tuple[int, str]] = {
    "8610": (3, "dono de hospital ou clínica com internação"),
    "8630": (3, "médico ou dentista"),
    "8640": (3, "dono de laboratório ou clínica de diagnóstico"),
    "8650": (2, "profissional de saúde"),
    "7500": (2, "veterinário"),
    "6911": (3, "advogado"),
    "6912": (3, "titular de cartório"),
    "6920": (2, "contador"),
    "7111": (2, "arquiteto"),
    "7112": (3, "engenheiro"),
    "7020": (2, "consultor de gestão"),
    "6201": (2, "profissional de tecnologia"), "6202": (2, "profissional de tecnologia"),
    "6203": (2, "profissional de tecnologia"), "6204": (2, "profissional de tecnologia"),
    "6209": (2, "profissional de tecnologia"),
    "6462": (3, "dono de holding"), "6463": (3, "dono de holding"),
    "6622": (2, "corretor de seguros"),
    "6810": (2, "dono de imóveis"), "6821": (2, "corretor de imóveis"), "6822": (2, "administrador de imóveis"),
    "4110": (3, "incorporador imobiliário"), "4120": (2, "construtor"),
    "4731": (3, "dono de posto de combustíveis"),
    "4511": (3, "dono de concessionária"),
    "4771": (2, "dono de farmácia"),
    "4711": (2, "dono de supermercado"),
}
_APARTAMENTO = ("APTO", "APT ", "APT.", "APARTAMENTO", " AP ", "BLOCO")
JA_COMPROU_SOLAR = ("empresa já tem", "outra unidade da empresa tem", "outra empresa do dono tem")


def potencial_da_pessoa(
    *,
    cnae: str | None,
    porte: str | None,
    capital_do_dono: float | None,
    empresas_ativas: int | None,
    empresas_total: int | None,
    socio_desde: date | None,
    idade: str | None,
    complemento: str | None,
    cargo: str | None,
    empresa_ativa: bool,
    solar: str,
    regiao_solar: bool,
    hoje: date | None = None,
) -> tuple[int, list[str]]:
    """Nota de 0 a 10 do potencial de o dono comprar energia solar para a própria casa, com os motivos."""
    hoje = hoje or date.today()
    nota, motivos = 2, []
    cnae = (cnae or "").strip()

    if cnae[:4] in PROFISSAO:
        peso, oficio = PROFISSAO[cnae[:4]]
        nota += peso
        motivos.append(f"{oficio} (renda provável alta)" if peso == 3 else oficio)
    elif porte == "05":
        nota += 2
        motivos.append("dono de empresa de médio ou grande porte")
    elif porte == "03":
        nota += 1
        motivos.append("dono de empresa de pequeno porte")
    if cnae[:2] == "01" or "PRODUTOR RURAL" in (cargo or "").upper():
        nota += 2
        motivos.append("produtor rural")

    if capital_do_dono:
        if capital_do_dono >= 1_000_000:
            nota += 3
            motivos.append("empresas ativas somam mais de R$ 1 milhão de capital")
        elif capital_do_dono >= 200_000:
            nota += 2
            motivos.append("empresas ativas somam mais de R$ 200 mil de capital")
        elif capital_do_dono >= 50_000:
            nota += 1
            motivos.append("empresas ativas somam mais de R$ 50 mil de capital")
    if empresas_ativas:
        if empresas_ativas >= 4:
            nota += 2
            motivos.append(f"dono de {empresas_ativas} empresas ativas")
        elif empresas_ativas >= 2:
            nota += 1
            motivos.append(f"dono de {empresas_ativas} empresas ativas")
    if socio_desde and (hoje - socio_desde).days >= 5 * 365:
        nota += 1
        motivos.append("sócio há mais de 5 anos")

    if idade in ("31 a 40", "41 a 50", "51 a 60"):
        nota += 1
        motivos.append("idade em que mais se investe na casa")
    elif idade in ("71 a 80", "mais de 80"):
        nota -= 1
        motivos.append("idade acima de 70 anos")

    if empresas_ativas == 0 and (empresas_total or 0) >= 2:
        nota -= 2
        motivos.append("teve empresas, mas nenhuma está ativa")
    elif not empresa_ativa and not empresas_ativas:
        nota -= 1
        motivos.append("empresa encerrada, sem outra ativa conhecida")

    comp = f" {(complemento or '').upper()} "
    if any(t in comp for t in _APARTAMENTO):
        nota -= 1
        motivos.append("endereço em apartamento ou bloco")
    if regiao_solar:
        nota += 1
        motivos.append("região da cidade com muita energia solar em casas")

    if solar in JA_COMPROU_SOLAR:
        nota += 1
        motivos.append("já comprou energia solar para empresa")
    elif solar == "dono: indício fraco":
        nota -= 2
        motivos.append("pode já ter energia solar em casa (indício fraco)")

    return max(0, min(10, nota)), motivos


def oportunidade(casa: int, empresa: int, solar: str) -> str:
    """Onde está a venda: na casa do dono, no ponto da empresa, nos dois, ou em ampliar o que já existe."""
    if solar == "empresa já tem":
        return "Ampliação"
    if casa >= 5 and empresa >= 5:
        return "Casa e empresa"
    if empresa >= 5:
        return "Empresa"
    if casa >= 5:
        return "Casa"
    return "Baixo potencial"


# ---------- encaixe no perfil (qualquer nicho) ----------

PORTES_PEQUENOS = ("03", "Pequeno porte")
PORTES_MAIORES = ("05", "Médio ou grande")


def _numero(valor) -> float | None:
    if valor in (None, ""):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def peso_da_atividade(cnae: str | None, atividade: str | None, pesos: dict[str, int]) -> int:
    """Peso (0 a 3) que o perfil dá à atividade do contato.

    `pesos` vem do perfil: código CNAE de 7 dígitos (ou prefixo) -> peso, e também a descrição
    da atividade -> peso, para listas antigas que não guardaram o código.
    """
    cnae = "".join(c for c in (cnae or "") if c.isdigit())
    if cnae:
        for n in (7, 5, 4, 2):
            p = pesos.get(cnae[:n])
            if p:
                return int(p)
    if atividade:
        return int(pesos.get(atividade.strip().lower(), 0))
    return 0


def encaixe(linha: dict, pesos: dict[str, int] | None = None) -> tuple[int, list[str]]:
    """Nota de 0 a 10 de quanto a empresa se encaixa no perfil, com os motivos.

    Usa só o que a Receita informa: atividade (com o peso dado pelo perfil), anos de empresa,
    porte, capital social e número de unidades. Serve para qualquer nicho; a nota de energia
    solar continua sendo a `aptidao_solar`.
    """
    nota, motivos = 0, []
    p = peso_da_atividade(linha.get("cnae"), linha.get("atividade"), pesos or {})
    if p:
        nota += p + 1
        motivos.append({3: "atividade principal do perfil", 2: "atividade importante do perfil", 1: "atividade aceita pelo perfil"}.get(p, "atividade do perfil"))
    anos = _numero(linha.get("idade_empresa"))
    if anos is not None:
        if anos >= 5:
            nota += 2
            motivos.append("mais de 5 anos de empresa")
        elif anos >= 2:
            nota += 1
            motivos.append("mais de 2 anos de empresa")
    porte = str(linha.get("porte") or "").strip()
    if porte in PORTES_MAIORES:
        nota += 2
        motivos.append("porte médio ou grande")
    elif porte in PORTES_PEQUENOS:
        nota += 1
        motivos.append("pequeno porte")
    capital = _numero(linha.get("capital_social"))
    if capital and capital >= 100_000:
        nota += 1
        motivos.append("capital social acima de R$ 100 mil")
    unidades = _numero(linha.get("unidades_ativas"))
    if unidades and unidades >= 2:
        nota += 1
        motivos.append("mais de uma unidade ativa")
    return max(0, min(10, nota)), motivos
