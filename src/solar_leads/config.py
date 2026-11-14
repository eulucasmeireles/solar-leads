from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ / ".env")

DADOS = Path(os.getenv("LEADS_DADOS", RAIZ / "dados"))
SAIDA = Path(os.getenv("LEADS_SAIDA", RAIZ / "saida"))
CNPJ_PARQUET = DADOS / "cnpj"
BANCO = DADOS / "leads.duckdb"

GMAPS_URL = os.getenv("GMAPS_URL", "http://127.0.0.1:8080")
EVOGO_URL = os.getenv("EVOGO_URL", "http://127.0.0.1:8081")
EVOGO_API_KEY = os.getenv("EVOGO_API_KEY", "")
EVOGO_INSTANCIA = os.getenv("EVOGO_INSTANCIA", "validador")
