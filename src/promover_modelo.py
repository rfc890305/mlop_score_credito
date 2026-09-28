"""
Promoção MANUAL / ROLLBACK de versão do modelo.

O caminho normal de promoção é automático (src/treinamento.py). Este utilitário existe
para governança: permite que um responsável aponte o alias de produção para uma versão
específica (ex.: rollback após incidente) deixando registro nas tags da versão.

Uso:
  python src/promover_modelo.py --listar
  python src/promover_modelo.py --versao 1 --motivo "rollback: queda de desempenho em produção"
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime
from pathlib import Path

import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

sys.path.insert(0, str(Path(__file__).resolve().parent))
from credit_score.config import carregar_config, tracking_uri  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--versao", help="versão que passará a ser a de produção")
    parser.add_argument("--motivo", default="promoção manual")
    parser.add_argument("--listar", action="store_true", help="lista as versões registradas")
    args = parser.parse_args()

    cfg = carregar_config()
    mlflow.set_tracking_uri(tracking_uri(cfg))
    mlflow.set_registry_uri(tracking_uri(cfg))
    client = MlflowClient()
    nome, alias = cfg["mlflow"]["nome_modelo_registrado"], cfg["mlflow"]["alias_producao"]

    if args.listar or not args.versao:
        for resumo in sorted(client.search_model_versions(f"name='{nome}'"), key=lambda v: int(v.version)):
            mv = client.get_model_version(nome, resumo.version)  # traz os aliases da versão
            print(f"v{mv.version:<3} aliases={mv.aliases} status={mv.tags.get('status')} "
                  f"algoritmo={mv.tags.get('algoritmo')} f1={mv.tags.get('f1_macro_validacao')}")
        return

    try:
        anterior = client.get_model_version_by_alias(nome, alias).version
    except MlflowException:
        anterior = None
    client.set_registered_model_alias(nome, alias, args.versao)
    client.set_model_version_tag(nome, args.versao, "status", "producao")
    client.set_model_version_tag(nome, args.versao, "promocao_manual",
                                 f"{datetime.now():%Y-%m-%dT%H:%M:%S} por {getpass.getuser()}: {args.motivo}")
    if anterior and anterior != args.versao:
        client.set_model_version_tag(nome, anterior, "status", "arquivado")
    print(f"'{alias}' agora aponta para v{args.versao} (antes: v{anterior})")


if __name__ == "__main__":
    main()
