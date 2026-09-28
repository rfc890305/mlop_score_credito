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
| Servidor da API | Cloud Run (serviço `api-score-credito`, criado automaticamente no primeiro deploy) | HTTPS automático, escala a zero, uma revisão por deploy (permite rollback) |
| Segredos | Secret Manager (`qf-api-keys`, `qf-admin-keys`) | As chaves da API nunca ficam no GitHub nem na imagem |
| Autenticação GitHub → GCP | Workload Identity Federation | Token OIDC temporário, restrito a este repositório |

### Como fica o versionamento

- **Código:** cada deploy bem-sucedido cria a tag git `v1.0.<número da execução>`.
- **Imagem:** a mesma versão vira tag no Artifact Registry, junto com o SHA do commit.
- **Modelo:** o build roda `src/treinamento.py` com os critérios de promoção. A imagem só é
  gerada se existir um `champion`, e `GET /v1/modelo` informa a versão e o run do modelo servido.
- **Rollback:** no Cloud Run, basta direcionar o tráfego para a revisão anterior:
  `gcloud run services update-traffic api-score-credito --region=southamerica-east1 --to-revisions=<revisão>=100`.

## Passo a passo (projeto `consultorfinanceiroai`)

A configuração é feita uma única vez. Depois dela, todo merge na `main` faz o deploy sozinho.

### Passo 1: conferir o projeto e o faturamento
1. Acesse https://console.cloud.google.com e selecione o projeto **ConsultorFinanceiroAI** no seletor do topo (ID `consultorfinanceiroai`).
2. Em **Faturamento**, confirme que o projeto está vinculado a uma conta de faturamento. O Cloud Run e o Artifact Registry exigem isso, mas um teste pequeno fica dentro da cota gratuita.
3. Sua conta precisa ser **Proprietário (Owner)** do projeto, em **IAM e administrador → IAM**.

### Passo 2: abrir o Cloud Shell com o repositório
1. No console, clique no ícone **>_ Ativar o Cloud Shell**, no canto superior direito. Ele já vem com `gcloud`, `git` e `python3`.
2. No terminal do Cloud Shell, rode:
   ```bash
   git clone -b claude/estrutura-mlops https://github.com/rfc890305/mlop_score_credito.git
   cd mlop_score_credito
   ```
   Se o repositório for privado, o `git clone` pede usuário e senha. Use seu usuário do GitHub e, como senha, um *Personal Access Token*. Outra opção é enviar só o arquivo `deploy/gcp/configurar_gcp.sh` pelo menu ⋮ → **Upload** do Cloud Shell.

### Passo 3: rodar o script de configuração
```bash
bash deploy/gcp/configurar_gcp.sh
```
O `PROJECT_ID` já está definido como `consultorfinanceiroai`, e a região como `southamerica-east1` (São Paulo). Se aparecer uma janela pedindo **Autorizar** o Cloud Shell, aceite. O script leva de 2 a 4 minutos e cria:

| Recurso | Nome | Para quê |
|---|---|---|
| APIs ativadas | Cloud Run, Artifact Registry, Secret Manager, IAM Credentials, STS | Serviços usados pelo pipeline |
| Artifact Registry | `credit-score` | Guarda as imagens `v1.0.<n>` |
| Conta de serviço | `github-deploy@...` | Usada pelo GitHub Actions para publicar e fazer o deploy |
| Conta de serviço | `api-score-runtime@...` | Identidade com que a API roda (só lê os segredos) |
| Workload Identity Pool + Provider | `github-pool` / `github-provider` | Permite que **somente** o repositório `rfc890305/mlop_score_credito` se autentique, sem chave JSON |
| Segredos | `qf-api-keys`, `qf-admin-keys` | Chaves da API, geradas aleatoriamente |

Rodar o script de novo não causa problema: recursos que já existem são mantidos.
No final, ele imprime 5 valores. Deixe essa tela aberta para o próximo passo.

### Passo 4: conectar o GitHub ao Google Cloud (variáveis do Actions)
1. No GitHub, abra **rfc890305/mlop_score_credito → Settings → Secrets and variables → Actions**.
2. Selecione a aba **Variables** (não Secrets) e clique em **New repository variable** para cada linha:

| Nome | Valor |
|---|---|
| `GCP_PROJECT_ID` | `consultorfinanceiroai` |
| `GCP_REGION` | `southamerica-east1` |
| `GCP_SERVICE_ACCOUNT` | `github-deploy@consultorfinanceiroai.iam.gserviceaccount.com` |
| `GCP_RUNTIME_SA` | `api-score-runtime@consultorfinanceiroai.iam.gserviceaccount.com` |
| `GCP_WIF_PROVIDER` | `projects/<NÚMERO_DO_PROJETO>/locations/global/workloadIdentityPools/github-pool/providers/github-provider` (copie exatamente como o script mostrar) |

Essa é toda a "conexão": o GitHub apresenta um token temporário assinado para este repositório, e o Google
troca esse token por um acesso da conta `github-deploy`. Nenhuma senha ou chave fica guardada no GitHub.

### Passo 5: fazer o merge e acompanhar o primeiro deploy
1. Faça o merge do PR na `main`.
2. Em **Actions → ci-cd**, acompanhe os jobs `testes`, com cerca de 1 min, e depois `deploy`, com 5 a 8 min, já que o build treina o modelo.
3. Com o job verde, o serviço **`api-score-credito`** aparece em **Cloud Run** no console, com uma URL do tipo
   `https://api-score-credito-xxxxxxxx-rj.a.run.app`. A imagem aparece em **Artifact Registry → credit-score**, e a tag
   `v1.0.<n>` aparece em **Tags** no GitHub.

### Passo 6: testar a API na nuvem (no Cloud Shell)
```bash
URL=$(gcloud run services describe api-score-credito --region=southamerica-east1 --format='value(status.url)')
CHAVE=$(gcloud secrets versions access latest --secret=qf-api-keys)
curl "$URL/health"
URL=$URL API_KEY=$CHAVE ./scripts/exemplos_chamadas.sh
```
A documentação interativa fica em `$URL/docs`: clique em **Authorize** e cole a chave.

### Problemas comuns no primeiro deploy

| Erro no job `deploy` | Causa | Solução |
|---|---|---|
| `google-github-actions/auth failed ... workload_identity_provider` vazio | Variáveis não cadastradas ou cadastradas como *Secrets* | Cadastre na aba **Variables**, com os nomes exatos |
| `Permission 'iam.serviceAccounts.getAccessToken' denied` | O provider não aceita o repositório (nome diferente) | Confira `GITHUB_REPO` no script e rode-o de novo |
| `denied: Permission "artifactregistry.repositories.uploadArtifacts"` | Papel de escrita ausente ou região diferente | Rode o script de novo e confira se `GCP_REGION` é `southamerica-east1` |
| `Secret ... not found` ou `Permission denied on secret` | Segredos não criados ou sem acesso para `api-score-runtime` | Rode o passo 5 do script de novo |
| `One or more users named in the policy do not belong to a permitted customer` | Política da organização impede serviço público (`allUsers`) | Peça ao administrador para liberar ou remova `--allow-unauthenticated` e use autenticação IAM |
| Smoke test falha em `/health` | A API subiu sem modelo | Veja os logs em **Cloud Run → api-score-credito → Registros** |

### Custos e como remover
Com `--max-instances=3` e escala a zero, um teste custa centavos. Para apagar tudo:
`gcloud run services delete api-score-credito --region=southamerica-east1` e
`gcloud artifacts repositories delete credit-score --location=southamerica-east1`.

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
