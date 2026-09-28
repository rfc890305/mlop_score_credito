#!/usr/bin/env bash
# Exemplos de chamadas à API (usados para gerar docs/evidencias/07_chamadas_api.txt).
# Uso: API_KEY=<sua-chave> ADMIN_KEY=<chave-admin> ./scripts/exemplos_chamadas.sh
URL=${URL:-http://localhost:8000}
API_KEY=${API_KEY:?defina API_KEY}
ADMIN_KEY=${ADMIN_KEY:-}
CLIENTE='{"cliente_id":"CUS_0xd40","Age":23,"Annual_Income":19114.12,"Monthly_Inhand_Salary":1824.84,"Num_Bank_Accounts":3,"Num_Credit_Card":4,"Interest_Rate":3,"Num_of_Loan":4,"Delay_from_due_date":3,"Num_of_Delayed_Payment":7,"Changed_Credit_Limit":11.27,"Num_Credit_Inquiries":4,"Outstanding_Debt":809.98,"Credit_Utilization_Ratio":26.82,"Credit_History_Age":265,"Total_EMI_per_month":49.57,"Amount_invested_monthly":80.42,"Monthly_Balance":312.49,"Credit_Mix":"Good","Payment_of_Min_Amount":"No","Payment_Behaviour":"High_spent_Small_value_payments"}'
RUIM='{"cliente_id":"CUS_0x21b1","Age":28,"Annual_Income":30625.94,"Monthly_Inhand_Salary":2706.16,"Num_Bank_Accounts":6,"Num_Credit_Card":5,"Interest_Rate":27,"Num_of_Loan":5,"Delay_from_due_date":45,"Num_of_Delayed_Payment":20,"Changed_Credit_Limit":16.2,"Num_Credit_Inquiries":9,"Outstanding_Debt":3500.5,"Credit_Utilization_Ratio":34.1,"Credit_History_Age":90,"Total_EMI_per_month":180.3,"Amount_invested_monthly":60.2,"Monthly_Balance":210.4,"Credit_Mix":"Bad","Payment_of_Min_Amount":"Yes","Payment_Behaviour":"Low_spent_Small_value_payments"}'
j() { python3 -m json.tool --no-ensure-ascii 2>/dev/null || cat; }
titulo() { echo; echo "=== $1"; echo "\$ $2"; }

titulo "1. Health check (sem autenticação)" "curl $URL/health"
curl -s "$URL/health" | j

titulo "2. Score de um cliente (200)" "curl -X POST $URL/v1/score -H 'X-API-Key: ***' -H 'Content-Type: application/json' -d '<cliente>'"
curl -s -D /dev/stderr -X POST "$URL/v1/score" -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' -d "$CLIENTE" 2> >(grep -i -E "^HTTP|x-ratelimit|x-request-id" >&2) | j
sleep 0.2

titulo "3. Score em lote (200)" "curl -X POST $URL/v1/score/lote -H 'X-API-Key: ***' -d '{\"clientes\": [<cliente1>, <cliente2>]}'"
curl -s -X POST "$URL/v1/score/lote" -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' -d "{\"clientes\":[$CLIENTE,$RUIM]}" | j

titulo "4. Versão do modelo em produção (200)" "curl $URL/v1/modelo -H 'X-API-Key: ***'"
curl -s "$URL/v1/modelo" -H "X-API-Key: $API_KEY" | j

titulo "5. Sem chave (401)" "curl -X POST $URL/v1/score -d '<cliente>'"
curl -s -w "\nHTTP %{http_code}\n" -X POST "$URL/v1/score" -H 'Content-Type: application/json' -d "$CLIENTE"

titulo "6. Chave inválida (401)" "curl -X POST $URL/v1/score -H 'X-API-Key: chave-errada' -d '<cliente>'"
curl -s -w "\nHTTP %{http_code}\n" -X POST "$URL/v1/score" -H 'X-API-Key: chave-errada' -H 'Content-Type: application/json' -d "$CLIENTE"

titulo "7. Payload inválido (422)" "curl -X POST $URL/v1/score -H 'X-API-Key: ***' -d '{\"Age\": 500, \"Credit_Mix\": \"Ótimo\"}'"
curl -s -w "\nHTTP %{http_code}\n" -X POST "$URL/v1/score" -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' -d '{"Age": 500, "Credit_Mix": "Ótimo"}'

titulo "8. JSON malformado (422)" "curl -X POST $URL/v1/score -H 'X-API-Key: ***' -d '{Age: 23'"
curl -s -w "\nHTTP %{http_code}\n" -X POST "$URL/v1/score" -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' -d '{Age: 23'

titulo "9. Recarregar modelo com chave comum (403)" "curl -X POST $URL/v1/modelo/recarregar -H 'X-API-Key: ***'"
curl -s -w "\nHTTP %{http_code}\n" -X POST "$URL/v1/modelo/recarregar" -H "X-API-Key: $API_KEY"

if [ -n "$ADMIN_KEY" ]; then
titulo "10. Recarregar modelo com chave admin (200)" "curl -X POST $URL/v1/modelo/recarregar -H 'X-API-Key: ***admin***'"
curl -s -w "\nHTTP %{http_code}\n" -X POST "$URL/v1/modelo/recarregar" -H "X-API-Key: $ADMIN_KEY"
fi

titulo "11. Método errado (405) e rota inexistente (404)" "curl $URL/v1/score ; curl $URL/v2/score"
curl -s -w "\nHTTP %{http_code}\n" "$URL/v1/score" -H "X-API-Key: $API_KEY"
curl -s -w "\nHTTP %{http_code}\n" "$URL/v2/score" -H "X-API-Key: $API_KEY"

titulo "12. Throttling: 35 chamadas seguidas (limite 30/minuto)" "for i in \$(seq 35); do curl -s -o /dev/null -w '%{http_code} ' $URL/v1/modelo -H 'X-API-Key: ***'; done"
for i in $(seq 35); do curl -s -o /dev/null -w '%{http_code} ' "$URL/v1/modelo" -H "X-API-Key: $API_KEY"; done; echo
echo "Resposta da chamada bloqueada:"
curl -s -i "$URL/v1/modelo" -H "X-API-Key: $API_KEY" | grep -i -E "^HTTP|retry-after|x-ratelimit|^\{"
