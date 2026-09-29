#!/usr/bin/env bash
# Gestão das chaves de parceiro da API (segredo qf-api-keys no Secret Manager).
# Execute no Cloud Shell. Cada parceiro (ou avaliador) recebe uma chave própria,
# que pode ser revogada sem afetar as demais.
#
#   bash deploy/gcp/gerenciar_chaves.sh listar
#   bash deploy/gcp/gerenciar_chaves.sh criar
#   bash deploy/gcp/gerenciar_chaves.sh revogar <primeiros caracteres da chave>
#
# Autocadastro (POST /v1/chaves): quem tem o código de convite gera a própria chave.
#   bash deploy/gcp/gerenciar_chaves.sh convite                  # mostra o código atual
#   bash deploy/gcp/gerenciar_chaves.sh novo-convite             # troca o código (o antigo para de valer)
#   bash deploy/gcp/gerenciar_chaves.sh listar-emitidas          # chaves geradas pelo autocadastro
#   bash deploy/gcp/gerenciar_chaves.sh revogar-emitida <prefixo>
#
# Depois de criar/revogar chaves fixas ou trocar o convite, o script atualiza o Cloud Run
# para ler a nova versão do segredo (o Cloud Run só lê segredos ao criar uma revisão).
# Revogar uma chave emitida não exige nova revisão: vale em até 1 minuto.
set -euo pipefail

PROJECT_ID="consultorfinanceiroai"
REGION="southamerica-east1"
SERVICO="api-score-credito"
SEGREDO="qf-api-keys"
SEGREDO_CONVITE="qf-codigo-convite"
BUCKET_CHAVES="gs://$PROJECT_ID-api-chaves"

chaves_atuais() {  # uma chave por linha (o segredo é gravado sem quebra de linha no final)
  { gcloud secrets versions access latest --secret="$SEGREDO" --project="$PROJECT_ID"; echo; } \
    | tr ',' '\n' | sed 's/[[:space:]]//g; /^$/d'
}

gravar() {  # recebe as chaves (uma por linha) e grava como nova versão do segredo
  paste -sd, - | tr -d '\n' | gcloud secrets versions add "$SEGREDO" --data-file=- --project="$PROJECT_ID" >/dev/null
  echo "Atualizando o Cloud Run para usar a nova versão do segredo..."
  gcloud run services update "$SERVICO" --region="$REGION" --project="$PROJECT_ID" \
    --update-secrets=QF_API_KEYS="$SEGREDO":latest --quiet >/dev/null
  echo "Pronto: a nova revisão já está atendendo."
}

case "${1:-}" in
  listar)
    echo "Chaves ativas (mostradas só as pontas):"
    chaves_atuais | awk '{ printf "  %d) %s...%s (%d caracteres)\n", NR, substr($0,1,6), substr($0,length($0)-3), length($0) }'
    ;;
  criar)
    NOVA=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
    TODAS=$(chaves_atuais; echo "$NOVA")
    echo "$TODAS" | gravar
    echo
    echo "Nova chave (copie apenas a linha entre os marcadores e envie por um canal privado):"
    echo "----------------------------------------------------------------"
    echo "$NOVA"
    echo "----------------------------------------------------------------"
    echo "Tamanho: ${#NOVA} caracteres. Para revogar depois: bash $0 revogar ${NOVA:0:6}"
    ;;
  revogar)
    PREFIXO="${2:?informe os primeiros caracteres da chave a revogar}"
    ENCONTRADAS=$(chaves_atuais | grep -c "^$PREFIXO" || true)
    [ "$ENCONTRADAS" -eq 0 ] && { echo "Nenhuma chave começa com '$PREFIXO'."; exit 1; }
    RESTANTES=$(chaves_atuais | grep -v "^$PREFIXO" || true)
    [ -z "$RESTANTES" ] && { echo "Não é possível revogar a última chave ativa. Crie outra antes."; exit 1; }
    echo "$RESTANTES" | gravar
    echo "Chave(s) iniciada(s) por '$PREFIXO' revogada(s)."
    ;;
  convite)
    echo "Código de convite atual (envie junto com a URL da API):"
    gcloud secrets versions access latest --secret="$SEGREDO_CONVITE" --project="$PROJECT_ID"; echo
    ;;
  novo-convite)
    python3 -c 'import secrets;print(secrets.token_urlsafe(9), end="")' \
      | gcloud secrets versions add "$SEGREDO_CONVITE" --data-file=- --project="$PROJECT_ID" >/dev/null
    echo "Atualizando o Cloud Run para usar o novo código..."
    gcloud run services update "$SERVICO" --region="$REGION" --project="$PROJECT_ID" \
      --update-secrets=QF_CODIGO_CONVITE="$SEGREDO_CONVITE":latest --quiet >/dev/null
    echo "Novo código (chaves já emitidas continuam valendo):"
    gcloud secrets versions access latest --secret="$SEGREDO_CONVITE" --project="$PROJECT_ID"; echo
    ;;
  listar-emitidas)
    OBJETOS=$(gcloud storage ls "$BUCKET_CHAVES/chaves/" 2>/dev/null || true)
    [ -z "$OBJETOS" ] && { echo "Nenhuma chave emitida pelo autocadastro."; exit 0; }
    echo "Chaves emitidas pelo autocadastro:"
    for O in $OBJETOS; do gcloud storage cat "$O"; echo; done | python3 -c '
import json, sys
from datetime import datetime, timezone
agora = datetime.now(timezone.utc)
for linha in filter(None, map(str.strip, sys.stdin)):
    r = json.loads(linha)
    status = "ativa" if datetime.fromisoformat(r["expira_em"]) > agora else "expirada"
    print("  {prefixo}...  {nome} <{email}>  criada {c}  expira {e} ({s})".format(
        c=r["criada_em"][:10], e=r["expira_em"][:10], s=status, **r))'
    ;;
  revogar-emitida)
    PREFIXO="${2:?informe o prefixo mostrado em listar-emitidas}"
    ALVOS=""
    for O in $(gcloud storage ls "$BUCKET_CHAVES/chaves/" 2>/dev/null || true); do
      gcloud storage cat "$O" | grep -q "\"prefixo\": \"$PREFIXO" && ALVOS="$ALVOS $O"
    done
    [ -z "$ALVOS" ] && { echo "Nenhuma chave emitida começa com '$PREFIXO'."; exit 1; }
    gcloud storage rm $ALVOS --quiet
    echo "Chave(s) emitida(s) iniciada(s) por '$PREFIXO' revogada(s); vale em até 1 minuto."
    ;;
  *)
    sed -n '2,19p' "$0"; exit 1 ;;
esac
