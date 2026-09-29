"""
Script de INFERÊNCIA do modelo de score de crédito - QuantumFinance
======================================================================

Como o modelo é lido:
  - O modelo NÃO é lido de um arquivo fixo. Ele é resolvido pelo MLflow Model Registry
    através do alias de produção:

        models:/quantumfinance-credit-score@champion

  - O alias "champion" é movido pelo script de treinamento (src/treinamento.py) somente
    quando uma nova versão passa nos critérios de promoção. Assim, este script sempre
    usa a ÚLTIMA VERSÃO PROMOVIDA, sem nenhuma alteração de código.
  - Antes de carregar, o script consulta o registry para saber QUAL versão o alias
    aponta (número, algoritmo, run de origem) e grava essa informação em cada predição,
    garantindo rastreabilidade (qual versão gerou qual score).
  - Opcionalmente é possível fixar uma versão (--versao N) para auditoria ou rollback.

Uso:
  python src/inferencia.py                                   # data/raw/test.csv -> data/predictions/
  python src/inferencia.py --entrada outro.csv --saida out.csv
  python src/inferencia.py --versao 1                        # usa uma versão específica
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import mlflow
import mlflow.sklearn
import pandas as pd
from mlflow import MlflowClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from credit_score.config import caminho_absoluto, carregar_config, tracking_uri  # noqa: E402


def carregar_modelo_producao(cfg: dict, versao: str | None = None):
    """Resolve e carrega o modelo em produção (alias champion) ou uma versão fixa.

    Retorna (modelo, metadados) onde metadados contém versão, alias e run de origem.
    """
    uri_tracking = tracking_uri(cfg)
    mlflow.set_tracking_uri(uri_tracking)
    mlflow.set_registry_uri(uri_tracking)
    client = MlflowClient()
    nome = cfg["mlflow"]["nome_modelo_registrado"]
    alias = cfg["mlflow"]["alias_producao"]

    if versao:
        mv = client.get_model_version(nome, str(versao))
        uri_modelo = f"models:/{nome}/{mv.version}"
    else:
        mv = client.get_model_version_by_alias(nome, alias)   # última versão promovida
        uri_modelo = f"models:/{nome}@{alias}"

    modelo = mlflow.sklearn.load_model(uri_modelo)
    meta = {
        "nome_modelo": nome,
        "versao": mv.version,
        "alias": alias if not versao else None,
        "uri": uri_modelo,
        "algoritmo": mv.tags.get("algoritmo"),
        "run_id": mv.run_id,
        "status": mv.tags.get("status"),
        "f1_macro_validacao": mv.tags.get("f1_macro_validacao"),
    }
    return modelo, meta


def prever(modelo, dados: pd.DataFrame) -> pd.DataFrame:
    """Aplica o pipeline (limpeza + pré-processamento + classificador) e devolve
    a classe prevista e a probabilidade de cada classe."""
    proba = modelo.predict_proba(dados)
    classes = list(modelo.classes_)
    saida = pd.DataFrame(proba, columns=[f"prob_{c}" for c in classes], index=dados.index)
    saida.insert(0, "Credit_Score_previsto", [classes[i] for i in proba.argmax(axis=1)])
    return saida


def main() -> None:
    parser = argparse.ArgumentParser(description="Inferência em lote com o modelo em produção.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--entrada", default=None, help="CSV de entrada (padrão: dados.teste)")
    parser.add_argument("--saida", default=None, help="CSV de saída")
    parser.add_argument("--versao", default=None, help="força uma versão específica do registry")
    args = parser.parse_args()

    cfg = carregar_config(args.config)
    modelo, meta = carregar_modelo_producao(cfg, args.versao)
    print(f"[modelo] {meta['uri']} -> versão {meta['versao']} ({meta['algoritmo']}), "
          f"run {meta['run_id']}")

    entrada = caminho_absoluto(args.entrada or cfg["dados"]["teste"])
    dados = pd.read_csv(entrada, low_memory=False)
    print(f"[dados] {len(dados)} linhas lidas de {entrada}")

    predicoes = prever(modelo, dados)
    chaves = [c for c in ("ID", "Customer_ID", "Month") if c in dados]
    resultado = pd.concat([dados[chaves], predicoes], axis=1)
    resultado["versao_modelo"] = meta["versao"]
    resultado["data_inferencia"] = datetime.now().isoformat(timespec="seconds")

    if args.saida:
        saida = caminho_absoluto(args.saida)
    else:
        pasta = caminho_absoluto(cfg["dados"]["predicoes"])
        saida = pasta / f"predicoes_v{meta['versao']}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    saida.parent.mkdir(parents=True, exist_ok=True)
    resultado.to_csv(saida, index=False)

    print(f"[saída] {len(resultado)} predições gravadas em {saida}")
    print("[distribuição]", resultado["Credit_Score_previsto"].value_counts().to_dict())


if __name__ == "__main__":
    main()
