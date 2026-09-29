"""Registry do MLflow persistente fora do container.

Em produção, o `mlflow.db` (metadados: runs, versões, aliases) fica num bucket do
Cloud Storage e os artefatos dos modelos em `gs://<bucket>/mlartifacts`. Assim o
histórico de versões sobrevive a cada deploy, e o critério de promoção compara o
candidato com o champion que está de fato em produção.

- O CI baixa o `mlflow.db`, treina, compara, promove (ou rejeita) e envia de volta.
- A API baixa o `mlflow.db` ao iniciar e em POST /v1/modelo/recarregar, e carrega
  `models:/<nome>@champion` direto do bucket.

QF_REGISTRY_URI aceita `gs://bucket[/prefixo]` ou um diretório local (útil em testes).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

ARQUIVO_DB = "mlflow.db"


def origem_registry() -> str | None:
    return os.getenv("QF_REGISTRY_URI") or None


def baixar_registry(origem: str, destino: Path) -> Path:
    """Copia o mlflow.db da origem (bucket ou diretório) para `destino`."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    if origem.startswith("gs://"):
        from google.cloud import storage  # dependência só necessária com bucket

        bucket, _, prefixo = origem.removeprefix("gs://").partition("/")
        nome_blob = f"{prefixo.strip('/')}/{ARQUIVO_DB}" if prefixo.strip("/") else ARQUIVO_DB
        storage.Client().bucket(bucket).blob(nome_blob).download_to_filename(str(destino))
    else:
        shutil.copyfile(Path(origem) / ARQUIVO_DB, destino)
    return destino
