"""Lista por nome da empresa (num estado ou no país): carga, recorte e validações.

A base real não entra aqui: as tabelas `estab_cidade` e `municipios` são montadas em memória.
"""

import duckdb
import pytest
from fastapi.testclient import TestClient

from conftest import SENHA_TESTE
from solar_leads import enriquecer
from solar_leads.enriquecer import _da_base, _titulo_de_cidade, carregar_por_nome
from solar_leads.telefone import DDDS_POR_UF
from solar_leads.web import servidor

COLUNAS = (
    "uf municipio cnpj_basico cnpj matriz_filial nome_fantasia razao_social natureza_juridica porte capital_social "
    "cnae_fiscal cnae_descricao data_inicio_atividade logradouro numero complemento bairro cep email tel1 tel2 mei "
    "situacao data_situacao natureza simples secundarias"
).split()


def empresa(n: int, razao: str, *, uf="MG", municipio="0001", fantasia="", situacao="02", filial=False, mei=False,
            cnae="4321500", cep="39400000", numero="10", basico: str | None = None) -> dict:
    basico = basico or f"{n:08d}"
    return {
        "uf": uf, "municipio": municipio, "cnpj_basico": basico, "cnpj": f"{basico}{'0002' if filial else '0001'}{n % 100:02d}",
        "matriz_filial": "2" if filial else "1", "nome_fantasia": fantasia, "razao_social": razao,
        "natureza_juridica": "2062", "porte": "01", "capital_social": "1000,00", "cnae_fiscal": cnae,
        "cnae_descricao": "Instalação e manutenção elétrica", "data_inicio_atividade": "20200101",
        "logradouro": "RUA A", "numero": numero, "complemento": "", "bairro": "CENTRO", "cep": cep, "email": "",
        "tel1": None, "tel2": None, "mei": mei, "situacao": situacao, "data_situacao": "20200101",
        "natureza": "Sociedade Empresária Limitada", "simples": True, "secundarias": 0,
    }


@pytest.fixture
def base():
    con = duckdb.connect()
    tipos = {"mei": "BOOLEAN", "simples": "BOOLEAN", "secundarias": "INTEGER"}
    con.execute(f"CREATE TABLE estab_cidade ({', '.join(f'{c} {tipos.get(c, "VARCHAR")}' for c in COLUNAS)})")
    con.execute("CREATE TABLE municipios (codigo VARCHAR, descricao VARCHAR)")
    con.executemany("INSERT INTO municipios VALUES (?, ?)", [("0001", "MONTES CLAROS"), ("0002", "RIO DE JANEIRO")])
    linhas = [
        empresa(1, "SOL NASCENTE ENERGIA SOLAR LTDA"),
        empresa(2, "JM ENGENHARIA LTDA", fantasia="Fotovoltáica Minas"),
        empresa(3, "PADARIA DO JOAO LTDA"),                                  # mesmo endereço da 1, sem a palavra
        empresa(4, "RIO SOLAR LTDA", uf="RJ", municipio="0002", cep="20000000"),
        empresa(5, "SOLAR ANTIGA LTDA", situacao="08", cep="39401000"),
        empresa(6, "FILIAL SOLAR DE FORA LTDA", filial=True, cep="39402000"),  # matriz fora do recorte
        empresa(7, "SOLARZINHO MEI", mei=True, cep="39403000"),
    ]
    con.executemany(f"INSERT INTO estab_cidade VALUES ({', '.join('?' * len(COLUNAS))})", [[l[c] for c in COLUNAS] for l in linhas])
    yield con
    con.close()


def cnpjs(con) -> set[str]:
    return {l[0][:8] for l in con.execute("SELECT cnpj FROM estab_mun").fetchall()}


def test_acha_pela_razao_social_e_pelo_nome_fantasia_sem_ligar_para_acento(base):
    n = carregar_por_nome(base, ["solar", "fotovoltaica"])
    assert cnpjs(base) == {f"{i:08d}" for i in (1, 2, 4, 5, 6, 7)}
    assert n == 6


def test_palavra_com_acento_e_minuscula_tambem_acha(base):
    carregar_por_nome(base, ["Fotovoltáica"])
    assert cnpjs(base) == {"00000002"}


def test_com_uf_fica_so_o_estado(base):
    carregar_por_nome(base, ["solar"], "rj")
    assert cnpjs(base) == {"00000004"}


def test_so_ativas_tira_as_encerradas(base):
    carregar_por_nome(base, ["solar"], "MG", so_ativas=True)
    assert "00000005" not in cnpjs(base)
    assert "00000001" in cnpjs(base)


def test_cada_linha_leva_a_cidade_e_o_estado_da_empresa(base):
    carregar_por_nome(base, ["solar"])
    onde = dict(base.execute("SELECT cnpj_basico, cidade || '/' || uf FROM estab_mun").fetchall())
    assert onde["00000001"] == "MONTES CLAROS/MG"
    assert onde["00000004"] == "RIO DE JANEIRO/RJ"


def test_empresas_no_endereco_conta_a_base_toda_e_nao_so_as_achadas(base):
    carregar_por_nome(base, ["solar"])
    no_endereco = dict(base.execute("SELECT cnpj_basico, no_endereco FROM estab_mun").fetchall())
    assert no_endereco["00000001"] == 3  # ela, a 2 e a padaria, que não tem a palavra no nome
    assert no_endereco["00000004"] == 1


@pytest.mark.parametrize("palavras", [[], ["ab"], ["  "], ["a", "  b "]])
def test_sem_palavra_de_tres_letras_recusa(base, palavras):
    with pytest.raises(ValueError):
        carregar_por_nome(base, palavras)


def test_palavra_generica_demais_recusa(base, monkeypatch):
    monkeypatch.setattr(enriquecer, "MAX_EMPRESAS_POR_NOME", 2)
    with pytest.raises(ValueError, match="mais específicas"):
        carregar_por_nome(base, ["ltda"])


def test_sem_a_tabela_nacional_avisa_o_que_rodar():
    with pytest.raises(RuntimeError, match="base cidades"):
        carregar_por_nome(duckdb.connect(), ["solar"])


def test_recorte_tira_mei_e_filial_de_empresa_de_fora(base):
    carregar_por_nome(base, ["solar"])
    achadas = _da_base(base, [""], incluir_mei=False, so_locais=True, extras=("uf", "cidade"))
    assert {e["cnpj_basico"] for e in achadas} == {"00000001", "00000004", "00000005"}
    assert {e["uf"] for e in achadas} == {"MG", "RJ"}
    assert all(e["cidade"] for e in achadas)


def test_recorte_por_atividade_continua_valendo(base):
    carregar_por_nome(base, ["solar"])
    assert _da_base(base, ["8112"], incluir_mei=False, so_locais=True, extras=("uf", "cidade")) == []


@pytest.mark.parametrize(
    "nome,esperado",
    [("RIO DE JANEIRO", "Rio de Janeiro"), ("SAO JOAO DEL REI", "Sao Joao Del Rei"), ("MONTES CLAROS", "Montes Claros"), (None, "")],
)
def test_titulo_de_cidade(nome, esperado):
    assert _titulo_de_cidade(nome) == esperado


# ---------- DDDs por estado ----------

def test_ddds_cobrem_os_27_estados_e_os_67_ddds_do_pais():
    assert len(DDDS_POR_UF) == 27
    todos = set().union(*DDDS_POR_UF.values())
    assert len(todos) == 67
    assert all(len(d) == 2 and d.isdigit() for d in todos)


def test_so_o_61_e_dividido_entre_dois_estados():
    donos: dict[str, list[str]] = {}
    for uf, ddds in DDDS_POR_UF.items():
        for d in ddds:
            donos.setdefault(d, []).append(uf)
    assert {d: sorted(u) for d, u in donos.items() if len(u) > 1} == {"61": ["DF", "GO"]}


def test_ddd_de_montes_claros_e_de_minas():
    assert "38" in DDDS_POR_UF["MG"]
    assert "38" not in DDDS_POR_UF["SP"]


# ---------- servidor ----------

@pytest.fixture
def logado():
    with TestClient(servidor.app, base_url="https://testserver", follow_redirects=False) as c:
        c.headers["X-Forwarded-For"] = "203.0.113.250"
        assert c.post("/api/login", json={"senha": SENHA_TESTE}).status_code == 200
        yield c


def test_previa_do_publico_exige_sessao():
    with TestClient(servidor.app, base_url="https://testserver", follow_redirects=False) as c:
        assert c.get("/api/publico?palavra=solar").status_code == 401


def test_previa_sem_palavra_da_400(logado):
    assert logado.get("/api/publico").status_code == 400


@pytest.mark.parametrize("palavra", ["ab", "x" * 41])
def test_previa_recusa_palavra_curta_ou_longa_demais(logado, palavra):
    r = logado.get("/api/publico", params={"palavra": palavra})
    assert r.status_code == 400
    assert "3 a 40" in r.json()["detail"]


def test_palavras_saem_limpas_sem_repeticao_e_em_ordem():
    assert servidor._palavras(["  solar ", "SOLAR", "foto   volt", ""]) == ["FOTO VOLT", "SOLAR"]


def test_mais_de_dez_palavras_da_400():
    with pytest.raises(servidor.HTTPException) as erro:
        servidor._palavras([f"palavra{i}" for i in range(11)])
    assert erro.value.status_code == 400


@pytest.mark.parametrize(
    "meta,esperado",
    [
        ({"cidade": "MONTES CLAROS", "uf": "MG"}, "Montes Claros/MG"),
        ({"cidade": "MONTES CLAROS", "uf": "MG", "palavras": []}, "Montes Claros/MG"),
        ({"cidade": "FOTOVOLT · SOLAR", "uf": "", "palavras": ["FOTOVOLT", "SOLAR"]}, "Fotovolt · Solar (Brasil)"),
        ({"cidade": "SOLAR", "uf": "MG", "palavras": ["SOLAR"]}, "Solar (MG)"),
    ],
)
def test_rotulo_da_lista(meta, esperado):
    assert servidor._rotulo(meta) == esperado


# ---------- público da lista ----------

from solar_leads import pipeline  # noqa: E402


def linha(cnpj, *, faixa="A", whatsapp="não verificado", aptidao=5, chance=70, solar="sem registro", situacao="ativa"):
    return {
        "cnpj": cnpj, "faixa": faixa, "whatsapp": whatsapp, "aptidao_solar": aptidao, "chance_dono": chance,
        "solar": solar, "situacao": situacao, "fonte": "Receita", "cidade": "Montes Claros", "telefone": "+55389" + cnpj,
        "dono_chave": "",
    }


def test_no_publico_dono_o_potencial_solar_desempata_antes_da_chance():
    linhas = [linha("1", aptidao=3, chance=95), linha("2", aptidao=9, chance=70)]
    pipeline.ordenar(linhas, "Montes Claros", "dono")
    assert [l["cnpj"] for l in linhas] == ["2", "1"]


def test_no_publico_empresa_vale_a_chance_e_o_potencial_solar_nao_conta():
    linhas = [linha("1", aptidao=3, chance=95), linha("2", aptidao=9, chance=70)]
    pipeline.ordenar(linhas, "Montes Claros", "empresa")
    assert [l["cnpj"] for l in linhas] == ["1", "2"]
    assert [l["ordem"] for l in linhas] == [0, 1]


@pytest.mark.parametrize("publico", pipeline.PUBLICOS)
def test_whatsapp_confirmado_vem_primeiro_nos_dois_publicos(publico):
    linhas = [linha("1", chance=100, aptidao=10), linha("2", whatsapp="sim", chance=40, aptidao=0, faixa="B")]
    pipeline.ordenar(linhas, "Montes Claros", publico)
    assert linhas[0]["cnpj"] == "2"


def test_etiquetas_do_publico_empresa_nao_falam_de_solar():
    linhas = [linha("1", solar="empresa já tem")]
    pipeline.ordenar(linhas, "Montes Claros", "empresa")
    assert linhas[0]["tags"] == "empresas,faixa-a,montes-claros,fonte-receita,cnpj-ativa"
    pipeline.ordenar(linhas, "Montes Claros", "dono")
    assert linhas[0]["tags"] == "solar,faixa-a,montes-claros,fonte-receita,cnpj-ativa,ja-tem-solar"


def test_publico_desconhecido_recusa():
    with pytest.raises(ValueError):
        pipeline.ordenar([linha("1")], "X", "outro")


def test_csv_do_publico_empresa_sai_sem_as_colunas_de_solar():
    chaves, cabecalhos = pipeline.chaves_do_publico("empresa"), pipeline.cabecalhos_do_publico("empresa")
    assert len(chaves) == len(cabecalhos) == len(pipeline.CHAVES) - len(pipeline.SO_DO_DONO)
    assert not {"solar", "solar_kw", "aptidao_solar", "potencial_casa", "oportunidade"} & set(chaves)
    assert {"telefone", "chance_dono", "whatsapp", "cidade", "uf", "tags"} <= set(chaves)
    assert pipeline.chaves_do_publico("dono") == pipeline.CHAVES
    assert pipeline.cabecalhos_do_publico("dono") == pipeline.CABECALHOS


def test_trocar_publico_reordena_a_lista_gravada_sem_perder_linhas(tmp_path):
    base = {c: "" for c in pipeline.CHAVES + pipeline.INTERNAS}
    linhas = [
        {**base, **linha("1", aptidao=3, chance=95), "primeiro_nome": "Ana"},
        {**base, **linha("2", aptidao=9, chance=70), "primeiro_nome": "Bia"},
    ]
    pipeline.ordenar(linhas, "Montes Claros", "dono")
    arquivo = tmp_path / "lista.parquet"
    pipeline.salvar_parquet(linhas, arquivo)
    ler = lambda: duckdb.execute(f"SELECT cnpj, ordem, tags, primeiro_nome, chance_dono FROM read_parquet('{arquivo}') ORDER BY ordem").fetchall()
    assert [l[0] for l in ler()] == ["2", "1"]
    pipeline.trocar_publico(arquivo, "Montes Claros", "empresa")
    depois = ler()
    assert [l[0] for l in depois] == ["1", "2"]
    assert depois[0][1:] == (0, "empresas,faixa-a,montes-claros,fonte-receita,cnpj-ativa", "Ana", 95)
    pipeline.trocar_publico(arquivo, "Montes Claros", "dono")
    assert [l[0] for l in ler()] == ["2", "1"]


def test_campos_marcam_o_que_e_so_do_publico_dono(logado):
    campos = {c["chave"]: c["so_dono"] for c in logado.get("/api/campos").json()}
    assert campos["solar"] and campos["potencial_casa"] and campos["aptidao_solar"]
    assert not campos["telefone"] and not campos["chance_dono"] and not campos["cidade"]


def test_mudar_publico_de_lista_inexistente_da_404(logado):
    assert logado.patch("/api/listas/nao-existe", json={"publico": "empresa"}).status_code in (404, 503)


def test_criar_lista_com_publico_desconhecido_da_400(logado):
    r = logado.post("/api/listas", json={"palavras": ["solar"], "publico": "qualquer"})
    assert r.status_code == 400
