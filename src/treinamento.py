"""
Script de TREINAMENTO do modelo de score de crédito - QuantumFinance
======================================================================

Fluxo:
  1. Carrega o dataset e mantém apenas os meses MAIS RECENTES (requisito de negócio).
  2. Separa o último mês como validação temporal (out-of-time).
  3. Treina vários modelos candidatos. Cada um é um run filho no MLflow.
  4. Escolhe o melhor candidato pela métrica principal (F1 macro).
  5. Registra o melhor como NOVA VERSÃO no MLflow Model Registry (alias "challenger").
  6. Compara o candidato com o modelo em produção (alias "champion") nos MESMOS dados
     de validação e, se passar nos critérios, PROMOVE-o (move o alias "champion").

------------------------------------------------------------------------------
O QUE É RASTREADO NO MLFLOW (e como)
------------------------------------------------------------------------------
Run pai ("treino-AAAAMMDD-HHMMSS"):
  - params : janela de meses, meses de treino/validação, nº de linhas, critérios de promoção
  - tags   : hash SHA-256 do dataset, usuário, commit git (se houver), decisão de promoção
  - inputs : dataset de treino e validação (mlflow.log_input -> linhagem dos dados)
  - artefatos: config.yaml usado, decisao_promocao.json, comparativo_candidatos.csv
Run filho (um por candidato):
  - params : todos os hiperparâmetros do estimador (mlflow.log_params)
  - métricas: accuracy, f1_macro, f1_weighted, roc_auc_ovr, precision/recall/f1 por classe,
              tempo de treino
  - artefatos: matriz de confusão (PNG), classification_report.json,
              importância das variáveis (CSV, quando o modelo expõe)
  - modelo : pipeline scikit-learn completo (limpeza + pré-processamento + classificador)
             com assinatura (schema de entrada/saída), exemplo de entrada,
             requirements e o código do pacote credit_score (code_paths),
             tornando o artefato autossuficiente para a inferência.

------------------------------------------------------------------------------
CRITÉRIO DE AVALIAÇÃO (vira o novo modelo em produção?)
------------------------------------------------------------------------------
O candidato só é promovido se TODAS as condições forem verdadeiras:
  a) f1_macro >= limiar_minimo              (piso absoluto de qualidade; config: 0.60)
  b) recall da classe "Poor" >= recall_minimo_poor (controle de risco;   config: 0.60)
  c) f1_macro >= f1_macro_producao + ganho_minimo (supera o modelo atual; config: +0.005)
     - o modelo em produção é REAVALIADO nos mesmos dados de validação do candidato,
       garantindo comparação justa.
     - se ainda não existe modelo em produção, a condição (c) é considerada atendida.

------------------------------------------------------------------------------
PROMOÇÃO PARA PRODUÇÃO (mudança de versão)
------------------------------------------------------------------------------
  - Todo melhor candidato vira uma nova versão numerada (v1, v2, v3, ...) do modelo
    registrado "quantumfinance-credit-score" e recebe o alias "challenger".
  - Se aprovado, o alias "champion" passa a apontar para a nova versão; a versão
    anterior recebe a tag status=arquivado (continua disponível para rollback).
  - Se reprovado, a versão recebe a tag status=rejeitado e o motivo.
  - A inferência e a API SEMPRE carregam "models:/quantumfinance-credit-score@champion",
    portanto a troca de versão em produção não exige mudança de código.
  - Rollback / promoção manual: scripts/promover_modelo.py --versao N

Uso:
  python src/treinamento.py                       # usa config/config.yaml
  python src/treinamento.py --janela-meses 8      # sobrescreve a janela temporal
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException
from mlflow.models import infer_signature
from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (ConfusionMatrixDisplay, accuracy_score, classification_report,
                             f1_score, roc_auc_score)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer
from sklearn.tree import DecisionTreeClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
from credit_score.config import RAIZ, artifact_root, caminho_absoluto, carregar_config, tracking_uri  # noqa: E402
from credit_score.processamento import (CLASSES, MESES, criar_preprocessador,  # noqa: E402
                                        filtrar_meses_recentes, limpar_dados)



# Tipos que o próprio treino gera e que podem ser desserializados com segurança
# pelo formato skops (padrão do MLflow 3, mais seguro que pickle).
TIPOS_CONFIAVEIS = [
    "credit_score.processamento.limpar_dados",
    "numpy.dtype",
    "sklearn.tree._tree.Tree",
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
]


# -----------------------------------------------------------------------------
# Utilitários
# -----------------------------------------------------------------------------
def hash_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def commit_git() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=RAIZ,
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "sem-git"


def construir_candidatos(cfg: dict) -> dict:
    semente = cfg["treino"]["semente"]
    p = cfg["treino"]["candidatos"]
    return {
        "regressao_logistica": LogisticRegression(**p["regressao_logistica"], class_weight="balanced",
                                                  random_state=semente),
        "arvore_decisao": DecisionTreeClassifier(**p["arvore_decisao"], class_weight="balanced",
                                                 random_state=semente),
        "gradient_boosting": HistGradientBoostingClassifier(**p["gradient_boosting"],
                                                            class_weight="balanced",
                                                            random_state=semente),
    }


def montar_pipeline(estimador) -> Pipeline:
    """Pipeline completo: dados BRUTOS -> limpeza -> pré-processamento -> classificador.
    Como a limpeza está dentro do pipeline, o modelo registrado aceita o mesmo formato
    do CSV original / payload da API."""
    return Pipeline([
        ("limpeza", FunctionTransformer(limpar_dados, validate=False)),
        ("preprocessamento", criar_preprocessador()),
        ("classificador", estimador),
    ])


def avaliar(modelo, X: pd.DataFrame, y: pd.Series) -> tuple[dict, np.ndarray]:
    pred = modelo.predict(X)
    proba = modelo.predict_proba(X)
    rel = classification_report(y, pred, labels=CLASSES, output_dict=True, zero_division=0)
    metricas = {
        "accuracy": accuracy_score(y, pred),
        "f1_macro": f1_score(y, pred, average="macro"),
        "f1_weighted": f1_score(y, pred, average="weighted"),
        "roc_auc_ovr": roc_auc_score(y, proba, multi_class="ovr", labels=list(modelo.classes_)),
    }
    for classe in CLASSES:
        metricas[f"precision_{classe}"] = rel[classe]["precision"]
        metricas[f"recall_{classe}"] = rel[classe]["recall"]
        metricas[f"f1_{classe}"] = rel[classe]["f1-score"]
    return metricas, pred


def registrar_artefatos_avaliacao(nome: str, modelo, y, pred, dir_tmp: Path) -> None:
    # Matriz de confusão
    fig, ax = plt.subplots(figsize=(5, 4))
    ConfusionMatrixDisplay.from_predictions(y, pred, labels=CLASSES, ax=ax, cmap="Blues",
                                            colorbar=False)
    ax.set_title(f"Matriz de confusão - {nome}")
    fig.tight_layout()
    caminho_cm = dir_tmp / f"matriz_confusao_{nome}.png"
    fig.savefig(caminho_cm, dpi=120)
    plt.close(fig)
    mlflow.log_artifact(str(caminho_cm), artifact_path="avaliacao")

    # Relatório de classificação
    rel = classification_report(y, pred, labels=CLASSES, output_dict=True, zero_division=0)
    mlflow.log_dict(rel, "avaliacao/classification_report.json")

    # Importância das variáveis (quando o estimador expõe)
    clf = modelo.named_steps["classificador"]
    nomes = modelo.named_steps["preprocessamento"].get_feature_names_out()
    importancia = None
    if hasattr(clf, "feature_importances_"):
        importancia = clf.feature_importances_
    elif hasattr(clf, "coef_"):
        importancia = np.abs(clf.coef_).mean(axis=0)
    if importancia is not None:
        df_imp = (pd.DataFrame({"feature": nomes, "importancia": importancia})
                  .sort_values("importancia", ascending=False))
        caminho_imp = dir_tmp / f"importancia_{nome}.csv"
        df_imp.to_csv(caminho_imp, index=False)
        mlflow.log_artifact(str(caminho_imp), artifact_path="avaliacao")


def metricas_producao(client: MlflowClient, nome_modelo: str, alias: str,
                      X_val: pd.DataFrame, y_val: pd.Series) -> tuple[str | None, dict | None]:
    """Reavalia o modelo atualmente em produção nos dados de validação atuais."""
    try:
        versao = client.get_model_version_by_alias(nome_modelo, alias)
    except MlflowException:
        return None, None
    modelo_prod = mlflow.sklearn.load_model(f"models:/{nome_modelo}@{alias}")
    metricas, _ = avaliar(modelo_prod, X_val, y_val)
    return versao.version, metricas


def decidir_promocao(cfg: dict, m_cand: dict, m_prod: dict | None) -> tuple[bool, list[str]]:
    crit = cfg["promocao"]
    metrica = crit["metrica_principal"]
    motivos, aprovado = [], True
    if m_cand[metrica] < crit["limiar_minimo"]:
        aprovado = False
        motivos.append(f"{metrica}={m_cand[metrica]:.4f} abaixo do limiar mínimo {crit['limiar_minimo']}")
    if m_cand["recall_Poor"] < crit["recall_minimo_poor"]:
        aprovado = False
        motivos.append(f"recall_Poor={m_cand['recall_Poor']:.4f} abaixo de {crit['recall_minimo_poor']}")
    if m_prod is None:
        motivos.append("não há modelo em produção: candidato elegível como primeira versão")
    else:
        alvo = m_prod[metrica] + crit["ganho_minimo"]
        if m_cand[metrica] < alvo:
            aprovado = False
            motivos.append(f"{metrica}={m_cand[metrica]:.4f} não supera produção "
                           f"({m_prod[metrica]:.4f}) + ganho mínimo {crit['ganho_minimo']}")
        else:
            motivos.append(f"{metrica}={m_cand[metrica]:.4f} supera produção "
                           f"({m_prod[metrica]:.4f}) + ganho mínimo {crit['ganho_minimo']}")
    return aprovado, motivos


# -----------------------------------------------------------------------------
# Fluxo principal
# -----------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Treina, rastreia e promove o modelo de score.")
    parser.add_argument("--config", default=None, help="caminho alternativo do config.yaml")
    parser.add_argument("--janela-meses", type=int, default=None,
                        help="sobrescreve dados.janela_meses_recentes")
    parser.add_argument("--saida-decisao", default=None,
                        help="grava a decisão de promoção em JSON (usado pelo CI no resumo da execução)")
    args = parser.parse_args()

    cfg = carregar_config(args.config)
    if args.janela_meses:
        cfg["dados"]["janela_meses_recentes"] = args.janela_meses
    cfg_dados, cfg_mlflow = cfg["dados"], cfg["mlflow"]

    # --- MLflow -------------------------------------------------------------
    mlflow.set_tracking_uri(tracking_uri(cfg))
    mlflow.set_registry_uri(tracking_uri(cfg))
    if mlflow.get_experiment_by_name(cfg_mlflow["experimento"]) is None:
        mlflow.create_experiment(cfg_mlflow["experimento"],
                                 artifact_location=artifact_root(cfg))
    mlflow.set_experiment(cfg_mlflow["experimento"])
    client = MlflowClient()

    # --- Dados --------------------------------------------------------------
    caminho_treino = caminho_absoluto(cfg_dados["treino"])
    df = pd.read_csv(caminho_treino, low_memory=False)
    col_mes, alvo = cfg_dados["coluna_mes"], cfg_dados["coluna_alvo"]
    df = filtrar_meses_recentes(df, col_mes, cfg_dados["janela_meses_recentes"])
    ordem = df[col_mes].map({m: i for i, m in enumerate(MESES)})
    meses_ordenados = sorted(ordem.unique())
    meses_val = meses_ordenados[-cfg_dados["meses_validacao"]:]
    df_treino, df_val = df[~ordem.isin(meses_val)], df[ordem.isin(meses_val)]

    colunas_entrada = [c for c in df.columns if c != alvo]
    X_treino, y_treino = df_treino[colunas_entrada], df_treino[alvo]
    X_val, y_val = df_val[colunas_entrada], df_val[alvo]
    nomes_meses = lambda idx: ",".join(MESES[i] for i in sorted(set(idx)))  # noqa: E731
    print(f"[dados] treino: {len(df_treino)} linhas ({nomes_meses(ordem[~ordem.isin(meses_val)])})")
    print(f"[dados] validação: {len(df_val)} linhas ({nomes_meses(meses_val)})")

    nome_run = datetime.now().strftime("treino-%Y%m%d-%H%M%S")
    with mlflow.start_run(run_name=nome_run) as run_pai, tempfile.TemporaryDirectory() as tmp:
        dir_tmp = Path(tmp)
        mlflow.set_tags({
            "dataset_sha256": hash_arquivo(caminho_treino),
            "git_commit": commit_git(),
            "usuario": getpass.getuser(),
            "tipo_run": "treino_completo",
        })
        mlflow.log_params({
            "janela_meses_recentes": cfg_dados["janela_meses_recentes"],
            "meses_treino": nomes_meses(ordem[~ordem.isin(meses_val)]),
            "meses_validacao": nomes_meses(meses_val),
            "linhas_treino": len(df_treino),
            "linhas_validacao": len(df_val),
            **{f"promocao_{k}": v for k, v in cfg["promocao"].items()},
        })
        mlflow.log_dict(cfg, "config/config_utilizada.yaml")
        mlflow.log_input(mlflow.data.from_pandas(df_treino, source=str(caminho_treino),
                                                 targets=alvo, name="treino"), context="treino")
        mlflow.log_input(mlflow.data.from_pandas(df_val, source=str(caminho_treino),
                                                 targets=alvo, name="validacao"), context="validacao")

        # --- Treino dos candidatos (runs filhos) ----------------------------
        resultados = []
        exemplo_entrada = X_val.head(3)
        for nome, estimador in construir_candidatos(cfg).items():
            with mlflow.start_run(run_name=nome, nested=True) as run_filho:
                modelo = montar_pipeline(clone(estimador))
                inicio = time.time()
                modelo.fit(X_treino, y_treino)
                duracao = time.time() - inicio

                metricas, pred = avaliar(modelo, X_val, y_val)
                metricas["tempo_treino_s"] = duracao
                mlflow.set_tag("algoritmo", type(estimador).__name__)
                mlflow.log_params({f"modelo_{k}": v for k, v in estimador.get_params().items()})
                mlflow.log_metrics(metricas)
                registrar_artefatos_avaliacao(nome, modelo, y_val, pred, dir_tmp)

                assinatura = infer_signature(exemplo_entrada, modelo.predict(exemplo_entrada))
                mlflow.sklearn.log_model(
                    modelo, name="modelo", signature=assinatura, input_example=exemplo_entrada,
                    code_paths=[str(RAIZ / "src" / "credit_score")],
                    # serialização segura (skops): só os tipos listados podem ser carregados
                    skops_trusted_types=TIPOS_CONFIAVEIS,
                    pip_requirements=[f"scikit-learn=={__import__('sklearn').__version__}",
                                      f"pandas=={pd.__version__}", f"numpy=={np.__version__}"],
                )
                resultados.append({"candidato": nome, "run_id": run_filho.info.run_id, **metricas})
                print(f"[candidato] {nome:<20} f1_macro={metricas['f1_macro']:.4f} "
                      f"accuracy={metricas['accuracy']:.4f} recall_Poor={metricas['recall_Poor']:.4f}")

        comparativo = pd.DataFrame(resultados).sort_values("f1_macro", ascending=False)
        caminho_comp = dir_tmp / "comparativo_candidatos.csv"
        comparativo.to_csv(caminho_comp, index=False)
        mlflow.log_artifact(str(caminho_comp))

        # --- Melhor candidato -> nova versão no Model Registry -------------
        melhor = comparativo.iloc[0].to_dict()
        nome_modelo = cfg_mlflow["nome_modelo_registrado"]
        m_cand = {k: melhor[k] for k in melhor if k not in ("candidato", "run_id")}
        mlflow.log_metrics({f"melhor_{k}": v for k, v in m_cand.items()})

        versao_prod, m_prod = metricas_producao(client, nome_modelo, cfg_mlflow["alias_producao"],
                                                X_val, y_val)
        nova = mlflow.register_model(f"runs:/{melhor['run_id']}/modelo", nome_modelo,
                                     tags={"algoritmo": melhor["candidato"],
                                           "run_treino": run_pai.info.run_id,
                                           "f1_macro_validacao": f"{m_cand['f1_macro']:.4f}"})
        client.update_registered_model(nome_modelo, description=cfg["projeto"]["descricao"])
        client.set_registered_model_alias(nome_modelo, cfg_mlflow["alias_candidato"], nova.version)
        print(f"[registry] {nome_modelo} v{nova.version} registrada ({melhor['candidato']}) "
              f"-> alias '{cfg_mlflow['alias_candidato']}'")

        # --- Critério de avaliação e promoção -------------------------------
        aprovado, motivos = decidir_promocao(cfg, m_cand, m_prod)
        decisao = {
            "data": datetime.now().isoformat(timespec="seconds"),
            "versao_candidata": nova.version,
            "algoritmo_candidato": melhor["candidato"],
            "metricas_candidato": m_cand,
            "versao_producao_anterior": versao_prod,
            "metricas_producao_mesma_validacao": m_prod,
            "criterios": cfg["promocao"],
            "promovido": aprovado,
            "motivos": motivos,
        }
        mlflow.log_dict(decisao, "decisao_promocao.json")
        mlflow.set_tags({"promovido": str(aprovado), "versao_registrada": nova.version})
        client.set_model_version_tag(nome_modelo, nova.version, "motivo", " | ".join(motivos))

        if aprovado:
            client.set_registered_model_alias(nome_modelo, cfg_mlflow["alias_producao"], nova.version)
            client.set_model_version_tag(nome_modelo, nova.version, "status", "producao")
            client.set_model_version_tag(nome_modelo, nova.version, "promovido_em", decisao["data"])
            if versao_prod is not None:
                client.set_model_version_tag(nome_modelo, versao_prod, "status", "arquivado")
            print(f"[promoção] APROVADA: v{nova.version} agora é '{cfg_mlflow['alias_producao']}' "
                  f"(anterior: {f'v{versao_prod}' if versao_prod else 'nenhuma'})")
        else:
            client.set_model_version_tag(nome_modelo, nova.version, "status", "rejeitado")
            print(f"[promoção] REJEITADA: produção continua em v{versao_prod}")
        for m in motivos:
            print(f"           - {m}")
        print(json.dumps({"run_id": run_pai.info.run_id, "versao": nova.version,
                          "promovido": aprovado}, ensure_ascii=False))
        if args.saida_decisao:
            Path(args.saida_decisao).write_text(
                json.dumps({"run_id": run_pai.info.run_id, **decisao}, ensure_ascii=False, indent=2,
                           default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
