# Ciclo de uma nova versão do modelo: roteiro e evidências

Este roteiro demonstra, no ambiente publicado (GitHub Actions + Cloud Storage + Cloud Run), o ciclo
completo de MLOps. Ele passa por uma versão inicial, uma melhora aprovada, um candidato rejeitado pelo
critério de promoção e um rollback, e mostra o efeito de cada passo numa aplicação cliente (o notebook
de empréstimo no Colab).

```text
 (0) preparação ─► (1) baseline v1 ─► (2) melhora v2 ─► (3) candidato rejeitado ─► (4) rollback v1 ─► volta à v2
                         │                  │                    │                        │
                         └──── notebook 03: mesma fila de pedidos avaliada por cada versão ─┘
```

Os valores "esperados" abaixo vêm da simulação local deste ciclo, com o mesmo código e os mesmos dados.
No GitHub Actions, o treino roda em outra máquina, então podem aparecer diferenças pequenas nas casas decimais.

## 0. Preparação (uma vez)

1. No Cloud Shell, atualize o repositório e rode o script de configuração, que agora também cria o bucket
   `gs://consultorfinanceiroai-mlflow` e dá as permissões de leitura e escrita:
   ```bash
   cd mlop_score_credito && git pull   # ou git clone, se ainda não tiver o repositório
   bash deploy/gcp/configurar_gcp.sh
   ```
2. Gere uma chave para cada pessoa que vai testar (por exemplo, o professor):
   ```bash
   bash deploy/gcp/gerenciar_chaves.sh criar
   ```
   Envie a linha entre os marcadores por um canal privado. Quem recebe cria o segredo `QF_API_KEY` no
   Colab (ícone 🔑) e abre os notebooks pelo link do README.

## 1. Baseline: primeira versão no registry persistente

| Ação | O que acontece | Evidência |
|---|---|---|
| Merge do PR que cria o registry persistente | `testes` verde → `treino-e-deploy` encontra o bucket vazio ("registry novo"), treina e promove **v1** | Execução no Actions (resumo com os candidatos e a decisão) |
| | `[promoção] APROVADA: v1 agora é 'champion'`, F1 macro ≈ 0,685 | GitHub Release `v1.0.<n>` com o mesmo resumo |
| | Imagem `v1.0.<n>` publicada e nova revisão no Cloud Run | Artifact Registry e Cloud Run → Revisões (console) |
| Notebook 03, seções 1 e 2 | Cliente D (caso limítrofe) → **NÃO** (score Poor); a fila de 30 pedidos fica com ≈ 63% de aprovação | Print das saídas; `historico_decisoes.csv` |
| Notebook 02 | F1 macro em agosto próximo de 0,685 | Print das métricas e da matriz de confusão |

## 2. Melhora: nova versão aprovada

| Ação | O que acontece | Evidência |
|---|---|---|
| PR "Melhora do modelo" alterando os hiperparâmetros do gradient boosting em `config/config.yaml` | `testes` roda no PR (treino local + 15 testes) | Aba *Checks* do PR |
| Merge do PR | O pipeline baixa o registry, registra **v2** e reavalia a v1 na mesma validação | Resumo da execução no Actions |
| | `[promoção] APROVADA: v2 agora é 'champion' (anterior: v1)`, F1 0,701 > 0,685 + 0,005; v1 fica `arquivado` | Release `v1.0.<n+1>` |
| `GET /v1/modelo` | `"versao": "2"`, `"f1_macro_validacao": 0.7009`, `"versao_release": "v1.0.<n+1>"` | Célula de configuração do notebook 03 |
| Notebook 03, seções 1, 2 e 3 (mesma sessão) | Cliente D → **SIM** (Standard, risco de Poor ≈ 25%); a comparação lista os pedidos que mudaram de decisão entre v1 e v2 | Print da tabela de comparação |
| Notebook 02 | F1 macro sobe para ≈ 0,70 | Print das métricas |

## 3. Critério de promoção barrando um candidato

| Ação | O que acontece | Evidência |
|---|---|---|
| Actions → ci-cd → **Run workflow** (main, campos vazios) | Novo treino com os mesmos dados e a mesma configuração registra **v3** | Resumo da execução |
| | `[promoção] REJEITADA: produção continua em v2`, porque não há ganho mínimo de 0,005 | Release, com a v3 como `rejeitado` na lista de versões |
| `GET /v1/modelo` | Continua `"versao": "2"` | Notebook 03 |

## 4. Rollback

| Ação | O que acontece | Evidência |
|---|---|---|
| **Run workflow** com `promover_versao = 1` e `motivo = demonstração de rollback` | `'champion' agora aponta para v1 (antes: v2)`; o registry é gravado e há um novo deploy | Resumo e Release |
| Notebook 03, seções 1, 2 e 3 | Cliente D volta a **NÃO**; o histórico mostra as decisões de v1, v2 e v1 de novo | Print da comparação |
| **Run workflow** com `promover_versao = 2` | Produção volta para a melhor versão | Release |

## 5. Onde fica cada artefato do ciclo

| Artefato | Onde |
|---|---|
| Código e configuração de cada versão | Commits, PRs e tags `v1.0.<n>` no GitHub |
| Testes automatizados | Job `testes` de cada PR e de cada push |
| Runs, métricas, parâmetros, gráficos e modelos | Registry MLflow em `gs://consultorfinanceiroai-mlflow` (`mlflow.db` + `mlartifacts/`) |
| Decisão de promoção de cada candidato | Artefato `decisao_promocao.json` do run, tags da versão (`status`, `motivo`) e resumo do Actions |
| Histórico do registry por release | `gs://consultorfinanceiroai-mlflow/historico/mlflow-v1.0.<n>.db` |
| Imagem da API por release | Artifact Registry `credit-score/api-score-credito:v1.0.<n>` |
| Versão servida | `GET /v1/modelo` e `X-Request-ID` de cada resposta; revisões do Cloud Run |
| Decisões da aplicação cliente | `historico_decisoes.csv` do notebook 03 (versão do modelo por decisão) |
