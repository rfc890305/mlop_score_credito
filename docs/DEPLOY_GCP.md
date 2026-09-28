# Deploy no Google Cloud com versionamento automático (GitHub Actions + Cloud Run)

## Visão geral

```text
push / PR ──► GitHub Actions: job "testes"
                 instala dependências → treina e promove (MLflow) → pytest
                         │ (somente push na main e testes verdes)
                         ▼
              job "deploy"
                 autentica no GCP por Workload Identity Federation (sem chave JSON)
                 docker build (deploy/gcp/Dockerfile treina o modelo dentro da imagem)
                 push no Artifact Registry  :v1.0.<n>  e  :<sha do commit>
                 deploy no Cloud Run (chaves da API vindas do Secret Manager)
                 smoke test em /health
                 cria a tag git v1.0.<n>
```

| Peça | Serviço | Papel |
|---|---|---|
| Testes e orquestração | GitHub Actions (`.github/workflows/ci-cd.yml`) | Roda em todo push e PR; bloqueia o deploy se algum teste falhar |
| Imagens versionadas | Artifact Registry (`credit-score`) | Guarda cada imagem com a tag `v1.0.<n>` e o SHA do commit |
| Servidor da API | Cloud Run (`api-score-credito`) | HTTPS automático, escala a zero, uma revisão por deploy (permite rollback) |
| Segredos | Secret Manager (`qf-api-keys`, `qf-admin-keys`) | As chaves da API nunca ficam no GitHub nem na imagem |
| Autenticação GitHub → GCP | Workload Identity Federation | Token OIDC temporário, restrito a este repositório |

### Como fica o versionamento

- **Código:** cada deploy bem-sucedido cria a tag git `v1.0.<número da execução>`.
- **Imagem:** a mesma versão vira tag no Artifact Registry, junto com o SHA do commit.
- **Modelo:** o build roda `src/treinamento.py` com os critérios de promoção. A imagem só é
  gerada se existir um `champion`, e `GET /v1/modelo` informa a versão e o run do modelo servido.
- **Rollback:** no Cloud Run, basta direcionar o tráfego para a revisão anterior:
  `gcloud run services update-traffic api-score-credito --region=southamerica-east1 --to-revisions=<revisão>=100`.

## Passo a passo (configuração feita uma única vez)

### 1. Pré-requisitos
- Um projeto no Google Cloud com faturamento ativo. O free tier do Cloud Run cobre um teste pequeno.
- Permissão de *Owner* (ou equivalente) no projeto, para criar a conta de serviço e o WIF.

### 2. Rodar o script de configuração no Cloud Shell
1. Abra o [Cloud Shell](https://shell.cloud.google.com) no projeto.
2. Envie o arquivo `deploy/gcp/configurar_gcp.sh` (ou clone o repositório) e edite `PROJECT_ID` no topo.
3. Execute: `bash deploy/gcp/configurar_gcp.sh`

O script ativa as APIs, cria o Artifact Registry, a conta de serviço `github-deploy`
(com os papéis `run.admin`, `artifactregistry.writer` e `iam.serviceAccountUser`), o pool e o
provedor de Workload Identity (restrito ao repositório `rfc890305/mlop_score_credito`) e as duas
chaves da API no Secret Manager. No final, ele imprime os quatro valores do próximo passo.

### 3. Cadastrar as variáveis no GitHub
Em **Settings → Secrets and variables → Actions → aba Variables**, crie:

| Variável | Exemplo |
|---|---|
| `GCP_PROJECT_ID` | `meu-projeto-123` |
| `GCP_REGION` | `southamerica-east1` |
| `GCP_SERVICE_ACCOUNT` | `github-deploy@meu-projeto-123.iam.gserviceaccount.com` |
| `GCP_WIF_PROVIDER` | `projects/123456789/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |

Essas variáveis não são segredos: sem o token OIDC emitido pelo GitHub para este repositório, elas não dão acesso a nada.

### 4. Disparar o pipeline
Faça merge do PR na `main` (ou um push). Em **Actions → ci-cd** aparecem os jobs `testes` e `deploy`.
A URL do serviço fica no passo *deploy* e também em **Cloud Run → api-score-credito**.

### 5. Testar a API na nuvem
```bash
URL=$(gcloud run services describe api-score-credito --region=southamerica-east1 --format='value(status.url)')
CHAVE=$(gcloud secrets versions access latest --secret=qf-api-keys)
curl "$URL/health"
URL=$URL API_KEY=$CHAVE ./scripts/exemplos_chamadas.sh
```
A documentação interativa fica em `$URL/docs`.

## Limitações desta arquitetura de teste e evolução

- O MLflow fica **dentro da imagem**: cada build tem um registry novo, então o número de versão
  do modelo reinicia a cada deploy (a versão rastreável é a tag `v1.0.<n>` da imagem). O histórico
  de treino de cada execução fica salvo como artefato `mlflow-<n>` no GitHub Actions.
- Em produção, o próximo passo é um **servidor MLflow compartilhado**: MLflow em Cloud Run com Cloud
  SQL (PostgreSQL) como backend e um bucket no Cloud Storage para artefatos. Basta apontar a variável
  `MLFLOW_TRACKING_URI` para ele no treino e na API. Com isso, a numeração das versões e o critério de
  promoção passam a comparar com o champion real, e a API pode recarregar o modelo sem novo deploy.
- Throttling: com `--max-instances=3`, cada instância conta o limite separadamente. Para um limite
  global, use o Memorystore (Redis) em `QF_RATE_LIMIT_STORAGE`.
