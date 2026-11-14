from pathlib import Path

from solar_leads import config, pastas

from conftest import DADOS_TMP


def test_config_aponta_para_o_temporario():
    assert config.DADOS == DADOS_TMP
    assert Path(config.SAIDA) != config.RAIZ / "saida"


def test_pastas_nao_usa_o_arquivo_real():
    assert pastas.ARQUIVO == DADOS_TMP / "pastas.sqlite"
    assert pastas.ARQUIVO != config.RAIZ / "dados" / "pastas.sqlite"


def test_servidor_usa_pastas_temporarias():
    from solar_leads.web import servidor

    assert servidor.LISTAS.parent == DADOS_TMP
