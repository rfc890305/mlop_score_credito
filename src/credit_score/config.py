"""Leitura da configuração central (config/config.yaml)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]


def carregar_config(caminho: str | os.PathLike | None = None) -> dict:
    caminho = Path(caminho or os.getenv("QF_CONFIG", RAIZ / "config" / "config.yaml"))
    with open(caminho, encoding="utf-8") as f:
        return yaml.safe_load(f)


def caminho_absoluto(relativo: str) -> Path:
    p = Path(relativo)
    return p if p.is_absolute() else RAIZ / p


def tracking_uri(cfg: dict) -> str:
    """Resolve a URI do MLflow. A variável MLFLOW_TRACKING_URI tem prioridade
    (permite apontar para um servidor MLflow remoto sem mudar código)."""
    uri = os.getenv("MLFLOW_TRACKING_URI", cfg["mlflow"]["tracking_uri"])
    if uri.startswith("sqlite:///") and not uri.startswith("sqlite:////"):
        uri = f"sqlite:///{caminho_absoluto(uri.removeprefix('sqlite:///'))}"
    return uri


def artifact_root(cfg: dict) -> str:
    """Local onde o MLflow grava os artefatos de novos experimentos.
    QF_ARTIFACT_ROOT tem prioridade (ex.: gs://<bucket>/mlartifacts no CI de produção)."""
    raiz = os.getenv("QF_ARTIFACT_ROOT")
    if raiz:
        return raiz
    return caminho_absoluto(cfg["mlflow"]["artifact_root"]).as_uri()
