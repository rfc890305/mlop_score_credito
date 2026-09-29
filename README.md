# QuantumFinance: Score de Crédito para Empresas Parceiras (MLOps)

Modelo simplificado de score de crédito (`Poor` / `Standard` / `Good`), treinado com as
**transações mais recentes** dos clientes. O projeto cobre o ciclo completo de MLOps:
template de repositório, rastreamento de experimentos, versionamento do modelo e API
segura com autenticação e throttling, tudo documentado.

## Onde está cada entregável

| # | Entregável pedido | Arquivo |
|---|---|---|
| 1 | Esquema de organização dos arquivos e diretórios | [`docs/ESTRUTURA_REPOSITORIO.md`](docs/ESTRUTURA_REPOSITORIO.md) + [`docs/imagens/00_ciclo_de_vida.png`](docs/imagens/00_ciclo_de_vida.png) (e o próprio repositório, que segue o template) |
| 2 | Script de treino: rastreio, critério de avaliação e promoção | [`src/treinamento.py`](src/treinamento.py) (o cabeçalho explica os três pontos) |
| 3 | Script de inferência com a última versão promovida | [`src/inferencia.py`](src/inferencia.py) |
| 4 | Documentação da API (endpoint, chamada, respostas, FAQ, deploy) | [`docs/API.md`](docs/API.md) |
| + | Deploy no Google Cloud com CI/CD e versionamento automático | [`docs/DEPLOY_GCP.md`](docs/DEPLOY_GCP.md), [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml), [`deploy/gcp/`](deploy/gcp) |
| + | Aplicação cliente no Colab: pedido de empréstimo → API → SIM/NÃO | [`notebooks/03_aplicacao_emprestimo_colab.ipynb`](notebooks/03_aplicacao_emprestimo_colab.ipynb) ([abrir no Colab](https://colab.research.google.com/github/rfc890305/mlop_score_credito/blob/main/notebooks/03_aplicacao_emprestimo_colab.ipynb)) |
| + | Avaliação da API publicada com dados rotulados, no Colab | [`notebooks/02_avaliacao_api_colab.ipynb`](notebooks/02_avaliacao_api_colab.ipynb) ([abrir no Colab](https://colab.research.google.com/github/rfc890305/mlop_score_credito/blob/main/notebooks/02_avaliacao_api_colab.ipynb)) |
| + | Ciclo de nova versão (melhora, rejeição e rollback) com evidências | [`docs/CICLO_NOVA_VERSAO.md`](docs/CICLO_NOVA_VERSAO.md) |
| + | **Evidências de Versão**: ciclo real em produção (v1 → v2 aprovada → v3 rejeitada → rollback), com releases e prints da aplicação | [`docs/EVIDENCIAS_CICLO.md`](docs/EVIDENCIAS_CICLO.md) |
| + | Evidências reais de execução (logs e telas) | [`docs/evidencias/`](docs/evidencias) e [`docs/imagens/`](docs/imagens) |

## Como testar a API publicada

1. Peça uma chave ao responsável pelo projeto. Ele gera a chave com `bash deploy/gcp/gerenciar_chaves.sh criar`.
2. Abra um dos notebooks pelo link "abrir no Colab" da tabela acima. No Colab, clique na chave 🔑 (Secrets),
   crie o segredo `QF_API_KEY` com a chave e ative o acesso do notebook.
3. Rode as células. Outra opção é abrir `https://api-score-credito-czkhbhag2q-rj.a.run.app/docs`, clicar em
   **Authorize**, colar a chave e usar **Try it out**.

## Resumo da solução

| Tema | Decisão |
|---|---|
| Dados | Dataset de Credit Score da disciplina (`train.csv`: jan–ago, 100 mil linhas; `test.csv`: set–dez, 50 mil linhas, sem rótulo) |
| "Transações mais recentes" | Treino só com os **4 últimos meses** (mai–jul para treino e **agosto como validação out-of-time**) |
| Modelo mais simples | 20 variáveis financeiras e comportamentais, sem dados pessoais (Name, SSN, IDs); pipeline scikit-learn único (limpeza, pré-processamento e classificador) |
| Candidatos | Regressão logística, árvore de decisão e gradient boosting (histogram-based) |
| Rastreamento | **MLflow**: run pai + um run filho por candidato, com params, métricas, matriz de confusão, relatório, importância das variáveis, dataset (hash e linhagem), config e modelo com assinatura |
| Versionamento | **MLflow Model Registry**: versões v1, v2, ... com aliases `challenger` (último candidato) e `champion` (produção) e tags de status |
| Critério de promoção | F1 macro ≥ 0,60 **e** recall de `Poor` ≥ 0,60 **e** F1 macro ≥ F1 do champion (reavaliado na mesma validação) + 0,005 |
| Inferência | Sempre `models:/quantumfinance-credit-score@champion`; cada predição registra a versão usada |
| API | **FastAPI** + **Uvicorn**; `X-API-Key`; throttling com **slowapi** (30/min por chave); validação estrita; erros padronizados; Swagger em `/docs` |

### Resultado das execuções registradas em `docs/evidencias/`

| Execução | Treino | F1 macro (validação agosto) | Decisão |
|---|---|---|---|
| 01 | 4 meses, config padrão | 0,6847 | **v1 promovida** (primeira versão) |
| 02 | mesmo treino | 0,6847 | v2 **rejeitada** (sem ganho sobre v1) |
| 03 | janela de 7 meses | 0,6875 | v3 **rejeitada** (ganho de 0,003, abaixo do mínimo de 0,005) |
| 04 | 4 meses, gradient boosting ajustado | **0,7009** | **v4 promovida** (v1 arquivada) |

Modelo em produção (v4): acurácia 0,706, F1 macro 0,701, ROC AUC 0,868, recall `Poor` 0,756.
A execução 03 mostra que usar mais meses de histórico não compensou, o que reforça a
decisão de treinar com os dados mais recentes.

## Guia rápido

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

python src/treinamento.py                  # treina, rastreia, versiona e promove
python src/promover_modelo.py --listar     # versões e aliases
python src/inferencia.py                   # lote: data/raw/test.csv -> data/predictions/

cp .env.example .env                       # defina QF_API_KEYS e QF_ADMIN_KEYS
make api                                   # API em http://localhost:8000 (Swagger em /docs)
make mlflow-ui                             # MLflow em http://localhost:5000
python -m pytest -q tests                  # 15 testes (exigem um modelo champion)
```

> O MLflow grava caminhos absolutos dos artefatos. Por isso `mlflow.db` e `mlartifacts/` **não**
> fazem parte do pacote: depois de descompactar, rode `python src/treinamento.py` para recriá-los
> (cerca de 20 s). A saída das execuções originais está em `docs/evidencias/` e nas telas em `docs/imagens/`.

## Ambiente validado

Python 3.11, mlflow 3.16.1, scikit-learn 1.9.1, pandas 3.0.6, FastAPI 0.141.1, slowapi 0.1.10.
Treino, inferência, API e testes foram executados de fato (15 testes passando). O `Dockerfile`
e o `docker-compose.yml` estão incluídos, mas não foram executados neste ambiente, porque não
havia daemon Docker disponível.
