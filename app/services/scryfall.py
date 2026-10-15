import asyncio
import re
import httpx
from typing import Any, Dict, List, Optional

# Informações de uma carta usadas pelo agente:
# {"name", "cmc", "type_line", "color_identity", "tags"}
CardInfo = Dict[str, Any]

CARD_CACHE: Dict[str, CardInfo] = {}
SEM = asyncio.Semaphore(5)
HEADERS = {"User-Agent": "PimpMyCommander/1.0"}

# Heurísticas simples sobre o texto de regras (aproximadas, apenas para estatística).
_RAMP_RE = re.compile(r"\badd \{|search your library for [^.]*\bland\b", re.IGNORECASE)
_DRAW_RE = re.compile(r"\bdraws?\b[^.]*\bcards?\b", re.IGNORECASE)
_INTERACTION_RE = re.compile(
    r"\b(destroy|exile|counter|return) (target|all|each)\b|\bdeals? [\w{}]+ damage to (any target|target)",
    re.IGNORECASE,
)


def _clean_double_face(name: str) -> str:
    return name.split(" // ")[0] if " // " in name else name


def _oracle_text(card: Dict[str, Any]) -> str:
    if card.get("oracle_text"):
        return card["oracle_text"]
    return "\n".join(face.get("oracle_text", "") for face in card.get("card_faces", []))


def build_card_info(card: Dict[str, Any], fallback_name: str) -> CardInfo:
    """Converte o objeto de carta do Scryfall no formato enxuto usado pelo agente."""
    type_line = card.get("type_line") or ""
    front_type = type_line.split(" // ")[0]
    text = _oracle_text(card)
    tags: List[str] = []

    if "Land" not in front_type:
        if _RAMP_RE.search(text):
            tags.append("ramp")
        if _DRAW_RE.search(text):
            tags.append("draw")
        if _INTERACTION_RE.search(text):
            tags.append("interaction")

    return {
        "name": _clean_double_face(card.get("name", fallback_name)),
        "cmc": card.get("cmc"),
        "type_line": type_line,
        "color_identity": card.get("color_identity", []),
        "tags": tags,
    }


def unresolved_card_info(name: str) -> CardInfo:
    return {"name": name, "cmc": None, "type_line": "", "color_identity": [], "tags": []}


async def resolve_single_card(card_name: str, client: httpx.AsyncClient) -> Optional[CardInfo]:
    clean_name = card_name.strip()
    if "Ã" in clean_name:
        try:
            clean_name = clean_name.encode("latin1").decode("utf-8")
        except Exception:
            pass
    cache_key = clean_name.lower()

    if cache_key in CARD_CACHE:
        return CARD_CACHE[cache_key]

    async with SEM:
        # 1. Tenta match EXATO em Inglês
        url_named = "https://api.scryfall.com/cards/named"
        resp_exact = await client.get(url_named, params={"exact": clean_name}, headers=HEADERS)
        
        if resp_exact.status_code == 200:
            info = build_card_info(resp_exact.json(), clean_name)
            CARD_CACHE[cache_key] = info
            return info

        await asyncio.sleep(0.06)

        # 2. Busca em Português com unique=prints
        url_search = "https://api.scryfall.com/cards/search"
        resp_pt = await client.get(
            url_search,
            params={
                "q": f"lang:pt {clean_name}",
                "include_multilingual": "true",
                "unique": "prints"
            },
            headers=HEADERS
        )

        if resp_pt.status_code == 200:
            cards = resp_pt.json().get("data", [])
            if cards:
                clean_lower = clean_name.lower()
                chosen_card = None
                
                for c in cards:
                    printed = (c.get("printed_name") or "").lower()
                    if printed == clean_lower:
                        chosen_card = c
                        break
                
                if not chosen_card:
                    for c in cards:
                        printed = (c.get("printed_name") or "").lower()
                        if clean_lower in printed:
                            chosen_card = c
                            break

                if not chosen_card:
                    chosen_card = cards[0]

                info = build_card_info(chosen_card, clean_name)
                print(f"   [Traduzido PT->EN] '{clean_name}' -> '{info['name']}'")
                CARD_CACHE[cache_key] = info
                return info

        await asyncio.sleep(0.06)

        # 3. Fallback fuzzy em Inglês
        resp_fuzzy = await client.get(url_named, params={"fuzzy": clean_name}, headers=HEADERS)
        if resp_fuzzy.status_code == 200:
            info = build_card_info(resp_fuzzy.json(), clean_name)
            print(f"   [Fuzzy EN] '{clean_name}' -> '{info['name']}'")
            CARD_CACHE[cache_key] = info
            return info

    # Se não encontrou de jeito nenhum, retorna None para podermos tratar!
    print(f"   [Não Encontrado] '{clean_name}'")
    return None


async def search_commander_candidates(commander_query: str) -> List[str]:
    """
    Busca comandantes candidatos a partir da primeira palavra (em EN ou PT).
    """
    first_word = commander_query.strip().split(",")[0].split()[0]
    url_search = "https://api.scryfall.com/cards/search"
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Busca criaturas lendárias ou comandantes válidos em qualquer idioma
        resp = await client.get(
            url_search,
            params={
                "q": f"(is:commander or t:legendary) {first_word}",
                "include_multilingual": "true",
                "order": "edhrec"
            },
            headers=HEADERS
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            candidates = []
            for c in data:
                name = _clean_double_face(c.get("name", ""))
                if name and name not in candidates:
                    candidates.append(name)
                if len(candidates) >= 8:
                    break
            print(f"   [Opções de Comandante Encontradas]: {candidates}")
            return candidates
    return []


async def normalize_deck_to_english(
    commander: str, cards: List[str]
) -> tuple[Optional[CardInfo], List[CardInfo]]:
    async with httpx.AsyncClient(timeout=25.0) as client:
        en_commander = await resolve_single_card(commander, client)
        if not en_commander:
            return None, []

        # Resolve cada nome único uma única vez (o parser repete cartas com quantidade > 1,
        # como terrenos básicos) para não disparar requisições duplicadas ao Scryfall.
        unique_names = list(dict.fromkeys(c.strip() for c in cards))
        resolved = await asyncio.gather(*(resolve_single_card(n, client) for n in unique_names))
        by_name = dict(zip(unique_names, resolved))

        # Se alguma carta das 99 não traduzir, mantém o nome original dela
        en_cards = [
            by_name[card.strip()] or unresolved_card_info(card)
            for card in cards
        ]
        return en_commander, en_cards


COLLECTION_URL = "https://api.scryfall.com/cards/collection"
COLLECTION_CHUNK = 75  # limite do endpoint /cards/collection


async def resolve_many(names: List[str]) -> Dict[str, Optional[CardInfo]]:
    """
    Resolve muitos nomes de carta de uma vez (coleções grandes).

    Usa o endpoint em lote /cards/collection (75 nomes por requisição) para nomes
    em inglês e cai no resolvedor individual (tradução PT->EN e fuzzy) para o resto.
    Retorna {nome_em_minúsculas: CardInfo | None}.
    """
    unique = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    result: Dict[str, Optional[CardInfo]] = {}
    todo: List[str] = []

    for name in unique:
        cached = CARD_CACHE.get(name.lower())
        if cached:
            result[name.lower()] = cached
        else:
            todo.append(name)

    if not todo:
        return result

    async with httpx.AsyncClient(timeout=25.0) as client:
        for i in range(0, len(todo), COLLECTION_CHUNK):
            chunk = todo[i:i + COLLECTION_CHUNK]
            try:
                resp = await client.post(
                    COLLECTION_URL,
                    json={"identifiers": [{"name": n} for n in chunk]},
                    headers=HEADERS,
                )
            except httpx.HTTPError as exc:
                print(f"   [Scryfall] Falha no lote: {type(exc).__name__}")
                continue

            if resp.status_code == 200:
                by_name: Dict[str, CardInfo] = {}
                for card in resp.json().get("data", []):
                    info = build_card_info(card, card.get("name", ""))
                    by_name[card.get("name", "").lower()] = info
                    by_name[info["name"].lower()] = info  # nome da face da frente
                for name in chunk:
                    info = by_name.get(name.lower())
                    if info:
                        CARD_CACHE[name.lower()] = info
                        result[name.lower()] = info
            await asyncio.sleep(0.1)  # gentileza com o rate limit do Scryfall

        # O que o lote não achou (nomes em português, typos, etc.) vai pelo resolvedor individual
        missing = [n for n in todo if n.lower() not in result]
        if missing:
            print(f"   [Scryfall] {len(missing)} nomes via resolvedor individual...")
            resolved = await asyncio.gather(*(resolve_single_card(n, client) for n in missing))
            for name, info in zip(missing, resolved):
                result[name.lower()] = info

    return result