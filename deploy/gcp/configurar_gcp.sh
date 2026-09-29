#!/usr/bin/env bash
# Configuração única do Google Cloud para o pipeline de CI/CD.
# Execute no Cloud Shell (ou com gcloud autenticado) e ajuste as 3 variáveis abaixo.
set -euo pipefail

PROJECT_ID="consultorfinanceiroai"      # ID do projeto no Google Cloud
REGION="southamerica-east1"             # São Paulo
GITHUB_REPO="rfc890305/mlop_score_credito"

SA_NAME="github-deploy"
SA="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
POOL="github-pool"
PROVIDER="github-provider"

# Cria o recurso só se ele ainda não existir; qualquer outro erro interrompe o script.
garantir() {  # uso: garantir "<comando describe>" "<comando create>"
  if eval "$1" >/dev/null 2>&1; then echo "   já existe"; else eval "$2"; fi
}

gcloud config set project "$PROJECT_ID"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

echo "1) Ativando APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com sts.googleapis.com storage.googleapis.com

echo "2) Repositório de imagens (Artifact Registry)"
garantir "gcloud artifacts repositories describe credit-score --location=$REGION" \
  "gcloud artifacts repositories create credit-score --repository-format=docker --location=$REGION --description='Imagens da API de score'"

echo "3) Conta de serviço usada pelo GitHub Actions"
garantir "gcloud iam service-accounts describe $SA" \
  "gcloud iam service-accounts create $SA_NAME --display-name='GitHub Actions deploy'"
for ROLE in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="$ROLE" >/dev/null
done

echo "4) Workload Identity Federation (GitHub -> GCP sem chave JSON)"
garantir "gcloud iam workload-identity-pools describe $POOL --location=global" \
  "gcloud iam workload-identity-pools create $POOL --location=global --display-name=GitHub"
garantir "gcloud iam workload-identity-pools providers describe $PROVIDER --location=global --workload-identity-pool=$POOL" \
  "gcloud iam workload-identity-pools providers create-oidc $PROVIDER --location=global \
    --workload-identity-pool=$POOL --display-name='GitHub OIDC' \
    --issuer-uri=https://token.actions.githubusercontent.com \
    --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref \
    --attribute-condition=\"assertion.repository=='$GITHUB_REPO'\""
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository/$GITHUB_REPO"

echo "5) Chaves da API e código de convite no Secret Manager (gerados aqui, nunca vão para o GitHub)"
for S in qf-api-keys qf-admin-keys; do
  garantir "gcloud secrets describe $S" \
    "python3 -c 'import secrets;print(secrets.token_urlsafe(32), end=\"\")' | gcloud secrets create $S --data-file=-"
done
garantir "gcloud secrets describe qf-codigo-convite" \
  "python3 -c 'import secrets;print(secrets.token_urlsafe(9), end=\"\")' | gcloud secrets create qf-codigo-convite --data-file=-"
echo "6) Conta de serviço com que a API roda no Cloud Run (só lê os segredos)"
RUNTIME_SA="api-score-runtime@$PROJECT_ID.iam.gserviceaccount.com"
garantir "gcloud iam service-accounts describe $RUNTIME_SA" \
  "gcloud iam service-accounts create api-score-runtime --display-name='API score (Cloud Run)'"
for S in qf-api-keys qf-admin-keys qf-codigo-convite; do
  gcloud secrets add-iam-policy-binding "$S" --member="serviceAccount:$RUNTIME_SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done

echo "7) Bucket do registry persistente do MLflow (mlflow.db + artefatos dos modelos)"
BUCKET="$PROJECT_ID-mlflow"
garantir "gcloud storage buckets describe gs://$BUCKET" \
  "gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access"
gcloud storage buckets update "gs://$BUCKET" --versioning >/dev/null   # guarda versões anteriores do mlflow.db
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA" --role=roles/storage.objectAdmin >/dev/null      # CI: lê e grava o registry
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/storage.objectViewer >/dev/null   # API: só lê
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA" --role=roles/storage.legacyBucketReader >/dev/null  # CI: conferir o bucket

echo "8) Bucket das chaves geradas pelo autocadastro (POST /v1/chaves; só o hash de cada chave)"
BUCKET_CHAVES="$PROJECT_ID-api-chaves"
garantir "gcloud storage buckets describe gs://$BUCKET_CHAVES" \
  "gcloud storage buckets create gs://$BUCKET_CHAVES --location=$REGION --uniform-bucket-level-access"
for ROLE in roles/storage.objectCreator roles/storage.objectViewer; do   # API: cria e lê; não apaga nem altera
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_CHAVES" \
    --member="serviceAccount:$RUNTIME_SA" --role="$ROLE" >/dev/null
done
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_CHAVES" \
  --member="serviceAccount:$SA" --role=roles/storage.legacyBucketReader >/dev/null  # CI: conferir o bucket

echo "9) Conferência final"
gcloud storage buckets describe "gs://$BUCKET" --format="value(name)"
gcloud storage buckets describe "gs://$BUCKET_CHAVES" --format="value(name)"
gcloud artifacts repositories describe credit-score --location="$REGION" --format="value(name)"
gcloud iam workload-identity-pools providers describe "$PROVIDER" --location=global --workload-identity-pool="$POOL" --format="value(name)"

echo
echo "Pronto. Cadastre estas VARIÁVEIS no GitHub (Settings > Secrets and variables > Actions > Variables):"
echo "  GCP_PROJECT_ID      = $PROJECT_ID"
echo "  GCP_REGION          = $REGION"
echo "  GCP_SERVICE_ACCOUNT = $SA"
echo "  GCP_RUNTIME_SA      = $RUNTIME_SA"
echo "  GCP_WIF_PROVIDER    = projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER"
echo
echo "Bucket do registry do MLflow: gs://$BUCKET (o workflow usa esse nome por padrão)"
echo "Bucket das chaves do autocadastro: gs://$BUCKET_CHAVES (idem)"
echo "Código de convite para o autocadastro: bash deploy/gcp/gerenciar_chaves.sh convite"
echo
echo "Chaves da API: bash deploy/gcp/gerenciar_chaves.sh listar | criar | revogar <início da chave>"
