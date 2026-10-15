import os
import re
import json
import uuid
import time
import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from dotenv import load_dotenv
from json_repair import repair_json
from pydantic import ValidationError
from app.schemas import StrategyReport, CardRecommendation

load_dotenv()

logger = logging.getLogger("pimp.agent")

# --- Configuração do OpenCode Go -------------------------------------------------
# Docs: https://opencode.ai/docs/go/
# - Endpoint OpenAI-compatível: https://opencode.ai/inference/go/v1/chat/completions
#   (modelos como deepseek-v4.1-flash, glm-5.3-flash, kimi-k3, ... usam este endpoint).
# - O cliente deve se identificar com um User-Agent PRÓPRIO (não o da SDK genérica
#   nem fingindo ser outro cliente) e enviar um ID de sessão estável por conversa
#   no header `x-opencode-session` (roteamento e cache de prompt).
# - O Go tem limites de uso em dólar (5h / semanal / mensal) e o DeepSeek tem
#   preço diferente em horário de pico (01-04h e 06-10h UTC, seg-sex).
DEFAULT_BASE_URL = "https://opencode.ai/inference/go/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"
USER_AGENT = "PimpMyCommander/1.0 (personal deck analysis tool)"
SESSION_HEADER = "x-opencode-session"

REQUEST_TIMEOUT = 120.0   # modelos que raciocinam podem demorar
SDK_MAX_RETRIES = 2       # retry automático da SDK: 429, 5xx e falhas de conexão
PARSE_ATTEMPTS = 2        # re-tenta se a resposta vier com JSON inválido
DEFAULT_MAX_TOKENS = 32000 # teto suportado pelo OpenCode Go

SYSTEM_PROMPT = """
Você é um especialista analítico de Magic: The Gathering focado em Commander (EDH).
Analise o baralho com base EXCLUSIVAMENTE nos dados fornecidos e devolva APENAS um objeto JSON
(sem markdown, sem texto fora do JSON) com exatamente esta estrutura:
{
  "tactical_summary": "resumo curto em português",
  "recommended_cuts": [
    {"card_name": "Nome da Carta", "reason": "Motivo curto"}
  ],
  "priority_additions": ["Carta 1", "Carta 2", "Carta 3"],
  "mana_curve_feedback": "análise curta de curva"
}

Regras:
- IMPORTANTE: Responda diretamente com o objeto JSON. Não gere monólogos nem raciocínio preliminar longo (<think>). Produza o JSON final imediatamente.
- Escreva os textos em português, mas mantenha TODOS os nomes de cartas em inglês, exatamente como aparecem nos dados.
- "recommended_cuts": de 3 a 8 cartas, escolhidas SOMENTE entre as cartas da lista do deck (nunca o comandante).
- "priority_additions": de 3 a 8 cartas, escolhidas SOMENTE entre as "Sugestões de Cartas" fornecidas.__COLLECTION_RULE__
- "mana_curve_feedback": baseie-se nas estatísticas fornecidas (curva, terrenos, ramp, compra, interação). Não invente números.
- As tags ramp/compra/interação são heurísticas aproximadas.
"""

_COLLECTION_RULE = " (Atenção: estas sugestões são cartas que o jogador JÁ POSSUI livres na coleção física dele; escolha exclusivamente entre elas. Se a lista estiver vazia, retorne lista vazia)."


def _build_system_prompt(collection_mode: bool) -> str:
    return SYSTEM_PROMPT.replace("__COLLECTION_RULE__", _COLLECTION_RULE if collection_mode else "")





class AgentError(Exception):
    """Falha ao obter a análise do LLM. `status_code` é o HTTP a devolver ao cliente."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


# --- Cliente reutilizável --------------------------------------------------------
_clients: Dict[tuple, AsyncOpenAI] = {}


def _get_client(api_key: str, base_url: str) -> AsyncOpenAI:
    key = (api_key, base_url)
    client = _clients.get(key)
    if client is None:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=REQUEST_TIMEOUT,
            max_retries=SDK_MAX_RETRIES,
            default_headers={"User-Agent": USER_AGENT},
        )
        _clients[key] = client
    return client


# --- Estatísticas do deck --------------------------------------------------------
def _front_type(card: Dict[str, Any]) -> str:
    return (card.get("type_line") or "").split(" // ")[0]


def _is_land(card: Dict[str, Any]) -> bool:
    return "Land" in _front_type(card)


def compute_deck_stats(deck_cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    nonland = [c for c in deck_cards if not _is_land(c)]
    known = [c for c in nonland if c.get("cmc") is not None]

    curve: Counter = Counter()
    for c in known:
        curve[min(int(c["cmc"]), 7)] += 1

    types: Counter = Counter()
    for c in nonland:
        front = _front_type(c)
        for t in ("Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Battle"):
            if t in front:
                types[t] += 1
                break

    tag_counts: Counter = Counter(tag for c in nonland for tag in c.get("tags", []))

    return {
        "total_cards": len(deck_cards),
        "lands": len(deck_cards) - len(nonland),
        "nonland": len(nonland),
        "avg_cmc": round(sum(c["cmc"] for c in known) / len(known), 2) if known else None,
        "curve": {("7+" if k == 7 else str(k)): curve[k] for k in sorted(curve)},
        "types": dict(types),
        "ramp": tag_counts["ramp"],
        "draw": tag_counts["draw"],
        "interaction": tag_counts["interaction"],
        "unresolved": sum(1 for c in deck_cards if not c.get("type_line")),
    }


def _build_user_prompt(
    commander: Dict[str, Any],
    deck_cards: List[Dict[str, Any]],
    top_missing: List[CardRecommendation],
    stats: Dict[str, Any],
    collection_mode: bool = False,
) -> str:
    colors = "".join(commander.get("color_identity", [])) or "Incolor"

    nonland = sorted(
        (c for c in deck_cards if not _is_land(c)),
        key=lambda c: (c["cmc"] if c.get("cmc") is not None else 99, c["name"]),
    )
    nonland_lines = ", ".join(
        f"{c['name']} [{int(c['cmc']) if c.get('cmc') is not None else '?'}]" for c in nonland
    )
    land_counts = Counter(c["name"] for c in deck_cards if _is_land(c))
    land_lines = ", ".join(f"{n} x{q}" if q > 1 else n for n, q in land_counts.items())

    def _fmt(cards: List[CardRecommendation]) -> str:
        return "; ".join(
            f"{c.name} (sinergia: {c.synergy}, decks: {c.inclusion_rate})" for c in cards
        ) or "(nenhuma carta disponível)"

    if collection_mode:
        label = "Sugestões de Cartas (Disponíveis na Minha Coleção, com sinergia EDHREC para incluir já):"
    else:
        label = "Sugestões de Cartas (Top Staples Gerais do EDHREC fora do deck):"

    suggestions_block = f"{label} {_fmt(top_missing)}"

    curve = ", ".join(f"CMC {k}: {v}" for k, v in stats["curve"].items()) or "sem dados"
    types = ", ".join(f"{k}: {v}" for k, v in stats["types"].items()) or "sem dados"

    return f"""
Comandante: {commander['name']} (identidade de cores: {colors})

Estatísticas do deck:
- Total de cartas (sem o comandante): {stats['total_cards']} | Terrenos: {stats['lands']} | Não-terrenos: {stats['nonland']}
- CMC médio (não-terrenos): {stats['avg_cmc']}
- Curva: {curve}
- Tipos: {types}
- Ramp: {stats['ramp']} | Compra de cartas: {stats['draw']} | Interação/remoção: {stats['interaction']}
- Cartas não resolvidas no Scryfall (sem dados): {stats['unresolved']}

Cartas não-terreno [CMC]: {nonland_lines}
Terrenos: {land_lines}

{suggestions_block}

Gere a análise tática estruturada.
"""



# --- Parsing e validação da resposta ---------------------------------------------
def _parse_report(raw_content: str) -> StrategyReport:
    # Remove tags <think>...</think> do raciocínio, mesmo se a tag não foi fechada
    raw_content = re.sub(r"<think>.*?(?:</think>|$)", "", raw_content, flags=re.DOTALL).strip()
    if not raw_content:
        raise ValueError("resposta vazia ou consumida inteiramente pelo raciocínio")

    start_idx = raw_content.find("{")
    end_idx = raw_content.rfind("}")
    json_str = raw_content[start_idx:end_idx + 1] if end_idx != -1 and end_idx >= start_idx else (raw_content[start_idx:] if start_idx != -1 else raw_content)

    # Repara qualquer JSON que venha com pequenos desvios de sintaxe ou truncado
    parsed = json.loads(repair_json(json_str))
    if not isinstance(parsed, dict):
        raise ValueError("a resposta não é um objeto JSON")
    return StrategyReport(**parsed)


def _sanitize_report(
    report: StrategyReport,
    commander_name: str,
    deck_names: List[str],
    top_missing: List[CardRecommendation],
) -> StrategyReport:
    """Descarta alucinações: cortes só do deck, adições só das sugestões fornecidas."""
    deck_map = {n.lower(): n for n in deck_names}
    suggestion_map = {c.name.lower(): c.name for c in top_missing}

    cuts, seen_cuts = [], set()
    for cut in report.recommended_cuts:
        key = cut.card_name.strip().lower()
        if key in deck_map and key != commander_name.lower() and key not in seen_cuts:
            seen_cuts.add(key)
            cuts.append(cut.model_copy(update={"card_name": deck_map[key]}))
        else:
            logger.warning("Corte descartado (não está no deck): %s", cut.card_name)

    additions, seen_adds = [], set()
    for name in report.priority_additions:
        key = name.strip().lower()
        if key in suggestion_map and key not in deck_map and key not in seen_adds:
            seen_adds.add(key)
            additions.append(suggestion_map[key])
        else:
            logger.warning("Adição descartada (fora das sugestões permitidas ou já no deck): %s", name)

    return report.model_copy(update={
        "recommended_cuts": cuts,
        "priority_additions": additions,
    })




def _translate_api_error(exc: Exception, base_url: str, model: str) -> AgentError:
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return AgentError(
            "OpenCode Go recusou a chave (OPENCODE_API_KEY inválida ou assinatura Go/Go Plus inativa).",
            502,
        )
    if isinstance(exc, RateLimitError):
        return AgentError(
            "Limite de uso do OpenCode Go atingido (limites de 5h, semanal ou mensal). Tente mais tarde ou troque o modelo.",
            429,
        )
    if isinstance(exc, NotFoundError):
        return AgentError(
            f"Endpoint ou modelo não encontrado (base_url={base_url}, model={model}). "
            f"Confira OPENCODE_BASE_URL/OPENCODE_MODEL; os IDs válidos estão em {base_url}/models.",
            502,
        )
    if isinstance(exc, APITimeoutError):
        return AgentError(f"Timeout de {REQUEST_TIMEOUT:.0f}s aguardando o modelo {model}.", 504)
    if isinstance(exc, APIConnectionError):
        return AgentError(f"Não foi possível conectar ao OpenCode Go: {exc}", 502)
    if isinstance(exc, APIStatusError):
        return AgentError(f"OpenCode Go retornou HTTP {exc.status_code}: {exc.message}", 502)
    return AgentError(f"Falha inesperada no agente ({type(exc).__name__}): {exc}", 500)


async def generate_tactical_advice(
    commander: Dict[str, Any],
    deck_cards: List[Dict[str, Any]],
    top_missing: List[CardRecommendation],
    collection_mode: bool = False,
) -> StrategyReport:
    """
    `commander` e `deck_cards` seguem o formato CardInfo do módulo scryfall
    (name, cmc, type_line, color_identity, tags).

    Se `collection_mode` for True, `top_missing` contém as cartas da coleção disponíveis para inclusão.
    Se for False, contém as top sinergias gerais do EDHREC.
    """
    load_dotenv(override=True)

    api_key = (os.getenv("OPENCODE_API_KEY") or "").strip()
    base_url = (os.getenv("OPENCODE_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    model_name = (os.getenv("OPENCODE_MODEL") or DEFAULT_MODEL).strip()
    max_tokens = int(os.getenv("OPENCODE_MAX_TOKENS") or DEFAULT_MAX_TOKENS)

    if not api_key:
        raise AgentError("OPENCODE_API_KEY não configurada no .env", 500)

    if "/zen/go/" in base_url:
        logger.warning(
            "OPENCODE_BASE_URL usa o caminho antigo /zen/go/v1; a documentação atual indica %s",
            DEFAULT_BASE_URL,
        )

    client = _get_client(api_key, base_url)

    # Um ID de sessão estável por conversa (esta análise e suas eventuais re-tentativas)
    session_id = str(uuid.uuid4())

    stats = compute_deck_stats(deck_cards)
    user_prompt = _build_user_prompt(
        commander, deck_cards, top_missing, stats,
        collection_mode=collection_mode,
    )
    system_prompt = _build_system_prompt(collection_mode)
    deck_names = [c["name"] for c in deck_cards]

    last_error = "resposta inválida"
    for attempt in range(1, PARSE_ATTEMPTS + 1):
        start_time = time.time()
        logger.info("Enviando requisição para %s (tentativa %d/%d)...", model_name, attempt, PARSE_ATTEMPTS)

        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=max_tokens,
                extra_headers={SESSION_HEADER: session_id},
            )
        except Exception as exc:
            logger.error("Erro na API do OpenCode Go: %s: %s", type(exc).__name__, exc)
            raise _translate_api_error(exc, base_url, model_name) from exc

        elapsed = time.time() - start_time
        usage = getattr(response, "usage", None)
        logger.info(
            "Resposta em %.2fs%s",
            elapsed,
            f" | tokens: {usage.total_tokens}" if usage else "",
        )

        choice = response.choices[0]
        finish_reason = choice.finish_reason

        try:
            report = _parse_report(choice.message.content or "")
        except (ValueError, ValidationError, TypeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Resposta não pôde ser interpretada (%s, finish_reason=%s)", last_error, finish_reason)
            if finish_reason == "length":
                # Re-tentar com o mesmo limite não adianta: o raciocínio consumiu os tokens.
                raise AgentError(
                    f"O modelo estourou o limite de {max_tokens} tokens antes de terminar a resposta. "
                    "Aumente OPENCODE_MAX_TOKENS no .env.",
                    502,
                ) from exc
            continue

        return _sanitize_report(report, commander["name"], deck_names, top_missing)

    raise AgentError(f"O modelo não devolveu um JSON válido após {PARSE_ATTEMPTS} tentativas ({last_error}).", 502)