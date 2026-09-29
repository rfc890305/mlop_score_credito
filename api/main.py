"""
API REST de Score de Crédito - QuantumFinance
=============================================

Segurança:
  - Autenticação: header X-API-Key (chaves em QF_API_KEYS / QF_ADMIN_KEYS ou emitidas
    por POST /v1/chaves mediante código de convite, guardadas só como hash)
  - Throttling: limite de requisições por chave (padrão 30/minuto; QF_RATE_LIMIT)
  - Validação estrita do payload (Pydantic): campos desconhecidos são rejeitados
  - Erros padronizados, sem vazamento de stack trace
  - Cada requisição recebe um X-Request-ID para rastreabilidade

Modelo:
  - Carregado do MLflow Model Registry pelo alias de produção (models:/<nome>@champion)
    na inicialização e recarregável via POST /v1/modelo/recarregar (chave admin).

Execução local:
  uvicorn api.main:app --host 0.0.0.0 --port 8000
Documentação interativa: http://localhost:8000/docs  (Swagger) e /redoc
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
from credit_score.config import carregar_config, tracking_uri  # noqa: E402
from credit_score.registro_remoto import baixar_registry, origem_registry  # noqa: E402
from inferencia import carregar_modelo_producao, prever  # noqa: E402

from . import chaves_emitidas  # noqa: E402
from .esquemas import (CadastroChave, ChaveEmitida, DadosCliente, InfoModelo,  # noqa: E402
                       RequisicaoLote, RespostaErro, RespostaLote, RespostaSaude, RespostaScore,
                       ResultadoScore)
from .seguranca import exigir_chave_admin, exigir_chave_api, ip_origem, limiter  # noqa: E402

logging.basicConfig(level=os.getenv("QF_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("qf.api")

CFG = carregar_config()
LIMITE = os.getenv("QF_RATE_LIMIT", CFG["api"]["limite_requisicoes"])
LIMITE_CADASTRO = os.getenv("QF_LIMITE_CADASTRO", "5/hour")
LIMITE_LOTE = int(os.getenv("QF_LIMITE_LOTE", CFG["api"]["limite_lote"]))


# -----------------------------------------------------------------------------
# Estado do modelo em memória
# -----------------------------------------------------------------------------
class GerenciadorModelo:
    def __init__(self) -> None:
        self.modelo = None
        self.info: InfoModelo | None = None
        self._trava = threading.Lock()

    def carregar(self) -> InfoModelo:
        origem = origem_registry()
        if origem:   # registry persistente (bucket): traz a versão mais recente do mlflow.db
            uri = tracking_uri(CFG)
            if not uri.startswith("sqlite:///"):
                raise RuntimeError("QF_REGISTRY_URI exige tracking_uri sqlite")
            baixar_registry(origem, Path(uri.removeprefix("sqlite:///")))
            log.info("registry sincronizado de %s", origem)
        modelo, meta = carregar_modelo_producao(CFG)
        info = InfoModelo(nome=meta["nome_modelo"], versao=str(meta["versao"]),
                          alias=meta["alias"], algoritmo=meta["algoritmo"], run_id=meta["run_id"],
                          carregado_em=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          f1_macro_validacao=meta.get("f1_macro_validacao"),
                          versao_release=os.getenv("QF_VERSAO_RELEASE"))
        with self._trava:            # troca atômica: requisições em andamento não são afetadas
            self.modelo, self.info = modelo, info
        log.info("modelo carregado: %s v%s (%s)", info.nome, info.versao, info.algoritmo)
        return info

    def obter(self):
        with self._trava:
            if self.modelo is None:
                raise HTTPException(503, detail={
                    "codigo": "modelo_indisponivel",
                    "mensagem": "Nenhum modelo em produção carregado. Execute o treinamento "
                                "ou recarregue o modelo."})
            return self.modelo, self.info


gerenciador = GerenciadorModelo()


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    try:
        gerenciador.carregar()
    except Exception as exc:  # a API sobe em modo degradado e informa no /health
        log.error("falha ao carregar o modelo de produção: %s", exc)
    yield


DESCRICAO = """
API para consulta do **score de crédito** (Poor / Standard / Good) de clientes de empresas
parceiras da QuantumFinance.

* **Autenticação**: envie a chave no header `X-API-Key`. Quem recebeu o **código de convite**
  gera a própria chave em `POST /v1/chaves` (limite {limite_cadastro} por IP; validade de {dias} dias).
* **Throttling**: {limite} por chave. Ao exceder, a API responde `429` com `Retry-After`.
* **Modelo**: sempre a última versão promovida para produção no MLflow (alias `champion`).
""".format(limite=LIMITE, limite_cadastro=LIMITE_CADASTRO,
           dias=os.getenv("QF_VALIDADE_CHAVE_DIAS", "30"))

app = FastAPI(
    title=CFG["api"]["titulo"],
    version=CFG["api"]["versao"],
    description=DESCRICAO,
    lifespan=ciclo_de_vida,
    responses={401: {"model": RespostaErro, "description": "Chave ausente ou inválida"},
               422: {"model": RespostaErro, "description": "Payload inválido"},
               429: {"model": RespostaErro, "description": "Limite de requisições excedido"},
               500: {"model": RespostaErro, "description": "Erro interno"}},
)
app.state.limiter = limiter


# -----------------------------------------------------------------------------
# Middleware e tratamento padronizado de erros
# -----------------------------------------------------------------------------
@app.middleware("http")
async def id_requisicao(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.id_requisicao = rid
    inicio = time.perf_counter()
    try:
        resposta = await call_next(request)
    except Exception:
        log.exception("erro não tratado id=%s", rid)
        resposta = JSONResponse(status_code=500, content={
            "erro": {"codigo": "erro_interno", "mensagem": "Erro interno. Informe o X-Request-ID ao suporte."},
            "id_requisicao": rid})
    resposta.headers["X-Request-ID"] = rid
    log.info("%s %s -> %s (%.1f ms) id=%s", request.method, request.url.path,
             resposta.status_code, (time.perf_counter() - inicio) * 1000, rid)
    return resposta


def _erro(request: Request, status: int, codigo: str, mensagem: str, detalhes=None, headers=None):
    return JSONResponse(status_code=status, headers=headers, content={
        "erro": {"codigo": codigo, "mensagem": mensagem, "detalhes": detalhes},
        "id_requisicao": getattr(request.state, "id_requisicao", None)})


@app.exception_handler(HTTPException)
async def tratar_http(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return _erro(request, exc.status_code, exc.detail.get("codigo", "erro"),
                     exc.detail.get("mensagem", ""), exc.detail.get("detalhes"), exc.headers)
    return _erro(request, exc.status_code, "erro_http", str(exc.detail), headers=exc.headers)


@app.exception_handler(404)
async def tratar_404(request: Request, exc):
    return _erro(request, 404, "rota_inexistente", f"Rota {request.url.path} não encontrada.")


@app.exception_handler(405)
async def tratar_405(request: Request, exc):
    return _erro(request, 405, "metodo_nao_permitido",
                 f"Método {request.method} não permitido em {request.url.path}.")


@app.exception_handler(RequestValidationError)
async def tratar_validacao(request: Request, exc: RequestValidationError):
    detalhes = [{"campo": "corpo" if e["type"] == "json_invalid" else ".".join(str(p) for p in e["loc"][1:]),
                 "problema": e["msg"]}
                for e in exc.errors()]
    return _erro(request, 422, "payload_invalido", "Um ou mais campos são inválidos.", detalhes)


@app.exception_handler(RateLimitExceeded)
async def tratar_limite(request: Request, exc: RateLimitExceeded):
    resposta = _erro(request, 429, "limite_excedido",
                     f"Limite de requisições excedido ({exc.detail}). Aguarde e tente novamente.")
    return request.app.state.limiter._inject_headers(resposta, request.state.view_rate_limit)


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
def _resultado(modelo, clientes: list[DadosCliente]) -> list[ResultadoScore]:
    dados = pd.DataFrame([c.model_dump(exclude={"cliente_id"}) for c in clientes])
    pred = prever(modelo, dados)
    classes = [c.removeprefix("prob_") for c in pred.columns if c.startswith("prob_")]
    return [ResultadoScore(cliente_id=cli.cliente_id, score=linha["Credit_Score_previsto"],
                           probabilidades={c: round(float(linha[f"prob_{c}"]), 4) for c in classes})
            for cli, (_, linha) in zip(clientes, pred.iterrows())]


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@app.get("/health", response_model=RespostaSaude, tags=["Monitoramento"],
         summary="Verifica se a API está no ar (não exige autenticação)")
def health():
    info = gerenciador.info
    return RespostaSaude(status="ok" if info else "degradado", modelo_carregado=info is not None,
                         versao_modelo=info.versao if info else None)


@app.get("/v1/modelo", response_model=InfoModelo, tags=["Modelo"],
         summary="Informações da versão do modelo em produção")
@limiter.limit(lambda: LIMITE)
def info_modelo(request: Request, response: Response, _: str = Depends(exigir_chave_api)):
    return gerenciador.obter()[1]


@app.post("/v1/score", response_model=RespostaScore, tags=["Score"],
          summary="Calcula o score de crédito de UM cliente")
@limiter.limit(lambda: LIMITE)
def score(request: Request, response: Response, cliente: DadosCliente,
          _: str = Depends(exigir_chave_api)):
    modelo, info = gerenciador.obter()
    r = _resultado(modelo, [cliente])[0]
    return RespostaScore(**r.model_dump(), id_requisicao=request.state.id_requisicao,
                         modelo=info, data_processamento=_agora())


@app.post("/v1/score/lote", response_model=RespostaLote, tags=["Score"],
          summary=f"Calcula o score de até {LIMITE_LOTE} clientes em uma chamada")
@limiter.limit(lambda: LIMITE)
def score_lote(request: Request, response: Response, corpo: RequisicaoLote,
               _: str = Depends(exigir_chave_api)):
    if len(corpo.clientes) > LIMITE_LOTE:
        raise HTTPException(413, detail={"codigo": "lote_muito_grande",
                                         "mensagem": f"Máximo de {LIMITE_LOTE} clientes por chamada; "
                                                     f"recebidos {len(corpo.clientes)}."})
    modelo, info = gerenciador.obter()
    resultados = _resultado(modelo, corpo.clientes)
    return RespostaLote(id_requisicao=request.state.id_requisicao, total=len(resultados),
                        resultados=resultados, modelo=info, data_processamento=_agora())


@app.post("/v1/modelo/recarregar", response_model=InfoModelo, tags=["Modelo"],
          summary="Recarrega o modelo após uma nova promoção (exige chave administrativa)",
          responses={403: {"model": RespostaErro}, 503: {"model": RespostaErro}})
@limiter.limit("5/minute")
def recarregar(request: Request, response: Response, _: str = Depends(exigir_chave_admin)):
    try:
        return gerenciador.carregar()
    except Exception as exc:
        log.error("falha ao recarregar: %s", exc)
        raise HTTPException(503, detail={"codigo": "modelo_indisponivel",
                                         "mensagem": "Não foi possível carregar o modelo de produção."})


@app.post("/v1/chaves", response_model=ChaveEmitida, status_code=201, tags=["Acesso"],
          summary="Gera uma chave de API pessoal (exige o código de convite do projeto)",
          responses={403: {"model": RespostaErro}, 503: {"model": RespostaErro}})
@limiter.limit(lambda: LIMITE_CADASTRO, key_func=lambda request: "cadastro:" + ip_origem(request))
def cadastrar_chave(request: Request, response: Response, corpo: CadastroChave):
    if not chaves_emitidas.cadastro_habilitado():
        raise HTTPException(503, detail={"codigo": "cadastro_indisponivel",
                                         "mensagem": "O autocadastro de chaves não está habilitado neste ambiente."})
    if not chaves_emitidas.convite_valido(corpo.codigo_convite):
        log.warning("cadastro recusado: convite inválido ip=%s", ip_origem(request))
        raise HTTPException(403, detail={"codigo": "convite_invalido",
                                         "mensagem": "Código de convite inválido. Peça o código ao responsável do projeto."})
    try:
        emitida = chaves_emitidas.emitir(corpo.nome.strip(), corpo.email.strip().lower())
    except Exception as exc:
        log.error("falha ao emitir chave: %s", exc)
        raise HTTPException(503, detail={"codigo": "cadastro_indisponivel",
                                         "mensagem": "Não foi possível emitir a chave agora. Tente novamente."})
    log.info("chave emitida prefixo=%s email=%s", emitida["prefixo"], emitida["email"])
    return ChaveEmitida(**emitida)
