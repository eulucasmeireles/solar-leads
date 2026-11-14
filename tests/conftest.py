"""Isolamento: nenhuma variável de ambiente nem pasta real é usada pelos testes.

As variáveis são definidas ANTES de qualquer import de `solar_leads`, porque o
`config.py` e o `pastas.py` leem o caminho dos dados no momento do import.
"""

import hashlib
import os
import secrets
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="solar-leads-testes-"))
SENHA_TESTE = "senha-de-teste-123"
_sal = os.urandom(16)
_hash = _sal.hex() + ":" + hashlib.scrypt(SENHA_TESTE.encode(), salt=_sal, n=2**14, r=8, p=1, dklen=32).hex()

os.environ["LEADS_DADOS"] = str(_TMP / "dados")
os.environ["LEADS_SAIDA"] = str(_TMP / "saida")
os.environ["LEADS_SENHA_HASH"] = _hash
os.environ["LEADS_SEGREDO"] = secrets.token_hex(32)
os.environ.pop("LEADS_SEM_SENHA", None)

import pytest  # noqa: E402

DADOS_TMP = _TMP / "dados"


@pytest.fixture(autouse=True)
def pastas_vazias():
    """Cada teste começa com um SQLite de pastas novo (no diretório temporário)."""
    from solar_leads import pastas

    assert pastas.ARQUIVO.parent == DADOS_TMP
    for sufixo in ("", "-wal", "-shm"):
        Path(str(pastas.ARQUIVO) + sufixo).unlink(missing_ok=True)
    yield


def pytest_sessionfinish(session, exitstatus):
    import shutil

    shutil.rmtree(_TMP, ignore_errors=True)
