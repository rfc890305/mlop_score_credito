# Deploy no Google Cloud com versionamento automático (GitHub Actions + Cloud Run)

## Visão geral

```text
push / PR ──► GitHub Actions: job "testes"
                 instala dependências → treino local (registry temporário) → pytest
                         │ (somente push na main, ou "Run workflow", e testes verdes)
                         ▼
              job "treino-e-deploy"
                 autentica no GCP por Workload Identity Federation (sem chave JSON)
                 baixa o registry de produção:  gs://consultorfinanceiroai-mlflow/mlflow.db
                 treina e compara o candidato com o champion REAL (mesma validação de agosto)
                     aprovado  → nova versão vira champion      rejeitado → champion não muda
                 envia o registry de volta ao bucket (+ cópia histórica por release)
                 docker build da API (sem treino) → Artifact Registry :v1.0.<n> e :<sha>
                 deploy no Cloud Run (a API baixa o registry e carrega o champion ao iniciar)
                 smoke test em /health → tag git + GitHub Release com a decisão e as métricas
```

| Peça | Serviço | Papel |
|---|---|---|
| Testes e orquestração | GitHub Actions (`.github/workflows/ci-cd.yml`) | Roda em todo push e PR; bloqueia o deploy se algum teste falhar |
| Registry do modelo | Cloud Storage (`gs://consultorfinanceiroai-mlflow`) | `mlflow.db` (versões, aliases, runs, métricas) + artefatos dos modelos; persiste entre deploys, com versionamento de objetos |
| Imagens versionadas | Artifact Registry (`credit-score`) | Guarda cada imagem da API com a tag `v1.0.<n>` e o SHA do commit |
| Servidor da API | Cloud Run (serviço `api-score-credito`) | HTTPS automático, escala a zero, uma revisão por deploy |
| Segredos | Secret Manager (`qf-api-keys`, `qf-admin-keys`, `qf-codigo-convite`) | As chaves da API nunca ficam no GitHub nem na imagem |
| Autenticação GitHub → GCP | Workload Identity Federation | Token OIDC temporário, restrito a este repositório |

### Como fica o versionamento

- **Modelo:** versões `v1`, `v2`, ... no MLflow Model Registry, que agora é persistente. Cada execução
  registra um candidato; o critério de promoção (F1 macro ≥ 0,60, recall de `Poor` ≥ 0,60 e ganho mínimo de
  0,005 sobre o champion reavaliado na mesma validação) decide se ele vira `champion` ou fica `rejeitado`.
- **Código e imagem:** cada deploy bem-sucedido cria a tag git e a imagem `v1.0.<número da execução>`.
- **Release:** cada deploy publica uma GitHub Release com o resumo do treino, a decisão de promoção e a
  lista de versões do registry. `GET /v1/modelo` informa a versão do modelo, o F1 e a release em uso.
- **Rollback do modelo:** Actions → ci-cd → **Run workflow**, informando a versão em `promover_versao`
  (detalhes em [Operações do dia a dia](#operações-do-dia-a-dia)).
- **Rollback da imagem:** `gcloud run services update-traffic api-score-credito --region=southamerica-east1 --to-revisions=<revisão>=100`.

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
   git clone https://github.com/rfc890305/mlop_score_credito.git
   cd mlop_score_credito
   ```
   Se o repositório já foi clonado antes, atualize com `cd mlop_score_credito && git pull`.
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
| Segredos | `qf-api-keys`, `qf-admin-keys`, `qf-codigo-convite` | Chaves da API e código de convite do autocadastro, gerados aleatoriamente |
| Bucket | `consultorfinanceiroai-mlflow` | Registry persistente do MLflow; o GitHub grava e a API só lê |
| Bucket | `consultorfinanceiroai-api-chaves` | Hash das chaves geradas pelo autocadastro (`POST /v1/chaves`); a API só cria e lê |

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

> **Esses valores podem aparecer na documentação?** Sim. São identificadores, não credenciais: o ID do projeto,
> a região, o e-mail das contas de serviço e o caminho do provider não dão acesso a nada sozinhos. Para usar a
> conta `github-deploy`, é preciso um token OIDC que o GitHub só emite para workflows deste repositório (o provider
> exige `assertion.repository == 'rfc890305/mlop_score_credito'`), e só quem tem permissão de escrita no repositório
> altera os workflows. Por isso eles ficam em *Variables*, não em *Secrets*. O que é segredo de fato (as chaves
> da API) fica no Secret Manager e nunca aparece no repositório, nos logs nem na imagem. Os cuidados que importam são:
> não dar permissão de escrita no repositório a quem não deve fazer deploy, proteger a branch `main` e nunca
> criar chave JSON para as contas de serviço.

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

## Operações do dia a dia

| Quero... | Como |
|---|---|
| Publicar uma melhora do modelo | PR alterando `config/config.yaml` (ou os dados em `data/raw`) → testes → merge. O pipeline treina, compara com o champion e só promove se passar no critério |
| Retreinar sem mudar código | Actions → ci-cd → **Run workflow** (branch `main`, campos vazios) |
| Fazer rollback do modelo | Actions → ci-cd → **Run workflow** com `promover_versao` = versão desejada e um `motivo`. O pipeline move o alias `champion`, grava o registry e faz um novo deploy |
| Ver o histórico de versões | Aba **Releases** do GitHub, resumo de cada execução do Actions, ou `GET /v1/modelo` |
| Abrir o registry de produção no MLflow | `gcloud storage cp gs://consultorfinanceiroai-mlflow/mlflow.db .` e `gcloud auth application-default login`; depois `mlflow ui --backend-store-uri sqlite:///mlflow.db` |
| Dar uma chave a um parceiro ou avaliador | `bash deploy/gcp/gerenciar_chaves.sh criar` no Cloud Shell (a chave aparece entre marcadores, sem o prompt colado) |
| Revogar uma chave | `bash deploy/gcp/gerenciar_chaves.sh revogar <primeiros caracteres>` |
| Ver o código de convite do autocadastro | `bash deploy/gcp/gerenciar_chaves.sh convite` (envie junto com a URL da API) |
| Trocar o código de convite | `bash deploy/gcp/gerenciar_chaves.sh novo-convite` (as chaves já emitidas continuam valendo) |
| Ver / revogar chaves do autocadastro | `bash deploy/gcp/gerenciar_chaves.sh listar-emitidas` e `revogar-emitida <prefixo>` (vale em até 1 minuto, sem deploy) |

O roteiro completo para demonstrar o ciclo (baseline, melhora, rejeição e rollback) está em
[`CICLO_NOVA_VERSAO.md`](CICLO_NOVA_VERSAO.md).

## Limitações desta arquitetura de teste e evolução

- O registry usa SQLite num bucket: o `concurrency` do workflow garante um treino por vez, e o bucket
  guarda versões anteriores do arquivo. Com vários times treinando ao mesmo tempo, o próximo passo é um
  **servidor MLflow** (Cloud Run + Cloud SQL), apontando `MLFLOW_TRACKING_URI` para ele.
- `POST /v1/modelo/recarregar` recarrega só a instância que recebeu a chamada. Por isso, promoções e
  rollbacks em produção passam pelo workflow, que cria uma revisão nova e recarrega todas as instâncias.
- Throttling: com `--max-instances=3`, cada instância conta o limite separadamente. Para um limite
  global, use o Memorystore (Redis) em `QF_RATE_LIMIT_STORAGE`.
