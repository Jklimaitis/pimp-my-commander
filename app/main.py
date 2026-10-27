import os
import logging
import re
import urllib.parse
from pathlib import Path
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi import FastAPI, HTTPException, Request
from app.schemas import (
    DeckTextInput, 
    FullAnalysisResponse, 
    CardRecommendation,
    CollectionImportInput,
    BuiltDeckInput,
    DeckUrlInput,
    DeckFromUrlResponse,
)
from app.services import collection as collection_service
from app.services import deck_finder
from app.services.collection import CollectionError
from app.services.edhrec import fetch_edhrec_recommendations, fetch_edhrec_dual_recommendations
from app.services.parser import count_deck_cards, parse_decklist_text
from app.services.scryfall import normalize_deck_to_english, search_commander_candidates
from app.services.agent import generate_tactical_advice, AgentError
from app.services.manabox import fetch_manabox_deck

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

app = FastAPI(
    title="Pimp My Commander API",
    description="API para diagnóstico e sugestão de melhorias em decks de Commander",
    version="1.0.0"
)

@app.exception_handler(AgentError)
async def agent_error_handler(request: Request, exc: AgentError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

@app.exception_handler(CollectionError)
async def collection_error_handler(request: Request, exc: CollectionError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})

@app.get("/", include_in_schema=False)
def serve_frontend():
    return FileResponse("app/static/index.html")

@app.get("/collection")
def get_collection():
    return collection_service.summarize(collection_service.load_state())

@app.post("/collection/import")
async def import_collection(body: CollectionImportInput):
    return await collection_service.import_collection(body.raw_text, body.mode)

@app.delete("/collection")
async def clear_collection():
    return await collection_service.clear_collection()

@app.post("/collection/decks")
async def save_built_deck(body: BuiltDeckInput):
    raw = body.raw_decklist.strip()
    commander = body.commander
    if "manabox.app/decks/" in raw:
        mb = await fetch_manabox_deck(raw)
        raw = mb["raw_decklist"]
        commander = commander or mb["commander"]
    return await collection_service.save_built_deck(body.name, raw, commander)

@app.get("/collection/decks/{name}")
def get_built_deck(name: str):
    state = collection_service.load_state()
    deck = collection_service.get_built_deck(state, name)
    if not deck:
        raise HTTPException(status_code=404, detail=f"Deck '{name}' não encontrado.")
    return deck

@app.delete("/collection/decks/{name}")
async def delete_built_deck(name: str):
    return await collection_service.delete_built_deck(name)

@app.post("/deck/from-url", response_model=DeckFromUrlResponse)
async def load_deck_from_url(body: DeckUrlInput):
    try:
        return await fetch_manabox_deck(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/collection/recommend-decks")
async def get_recommended_decks(limit: int = 8):
    try:
        return await deck_finder.recommend_decks_from_collection(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao analisar decks recomendados: {exc}")

@app.post("/collection/draft-deck/{commander_name}")
async def draft_deck(commander_name: str):
    try:
        return await deck_finder.draft_deck_for_commander(commander_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao montar deck rascunho: {exc}")


IMAGE_CACHE_DIR = Path(os.getenv("PMC_IMAGE_CACHE_DIR", "/tmp/pmc_image_cache"))
try:
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    IMAGE_CACHE_DIR = None

IMAGE_MEMORY_CACHE = {}


def generate_svg_card_placeholder(card_name: str) -> str:
    safe_title = (card_name or "Carta").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="240" height="335" viewBox="0 0 240 335">
  <rect width="240" height="335" rx="12" fill="#0f172a" stroke="#334155" stroke-width="2"/>
  <rect x="10" y="10" width="220" height="315" rx="8" fill="#1e293b" stroke="#475569" stroke-width="1"/>
  <circle cx="120" cy="115" r="32" fill="#334155"/>
  <text x="120" y="125" font-size="30" text-anchor="middle" fill="#f59e0b" font-family="sans-serif">🧙‍♂️</text>
  <text x="120" y="185" font-size="13" font-weight="bold" text-anchor="middle" fill="#f1f5f9" font-family="sans-serif">{safe_title}</text>
  <text x="120" y="210" font-size="11" text-anchor="middle" fill="#94a3b8" font-family="sans-serif">Imagem indisponível</text>
</svg>"""


@app.get("/card-image")
async def get_card_image(name: str):
    clean = (name or "").strip()
    if not clean:
        return Response(content=generate_svg_card_placeholder(""), media_type="image/svg+xml")

    # Extrai face frontal se for split/dupla-face
    clean_front = clean.split(" // ")[0].strip()
    # Remove prefixos de quantidade e aspas
    clean_front = re.sub(r"^(?:SB:\s*)?(?:\d+x?\s+)?", "", clean_front)
    clean_front = clean_front.strip('"\'')

    safe_slug = re.sub(r"[^\w-]", "_", clean_front.lower()).strip("_")

    # 1. Verifica cache em memória (ultrarrápido)
    if safe_slug in IMAGE_MEMORY_CACHE:
        return Response(
            content=IMAGE_MEMORY_CACHE[safe_slug],
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=604800"}
        )

    # 2. Verifica cache em disco
    if IMAGE_CACHE_DIR:
        cached_file = IMAGE_CACHE_DIR / f"{safe_slug}.jpg"
        if cached_file.exists() and cached_file.stat().st_size > 1000:
            content = cached_file.read_bytes()
            IMAGE_MEMORY_CACHE[safe_slug] = content
            return Response(
                content=content,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=604800"}
            )

    # 3. Busca no Scryfall com headers bem-formados
    url = f"https://api.scryfall.com/cards/named?fuzzy={urllib.parse.quote(clean_front)}&format=image&version=normal"
    headers = {"User-Agent": "PimpMyCommander/1.0", "Accept": "*/*"}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            if resp.status_code == 200 and len(resp.content) > 1000:
                IMAGE_MEMORY_CACHE[safe_slug] = resp.content
                if IMAGE_CACHE_DIR:
                    try:
                        (IMAGE_CACHE_DIR / f"{safe_slug}.jpg").write_bytes(resp.content)
                    except Exception:
                        pass
                return Response(
                    content=resp.content,
                    media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=604800"}
                )
    except Exception as exc:
        logging.getLogger("pimp.image").warning(f"Erro ao buscar imagem para '{clean_front}': {exc}")

    # Fallback garantido: SVG gerado localmente (nunca quebra no navegador)
    return Response(
        content=generate_svg_card_placeholder(clean_front),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=3600"}
    )


@app.get("/health")
def health_check():
    return {"status": "ok", "app": "Pimp My Commander"}

@app.post("/analyze/tactical", response_model=FullAnalysisResponse)
async def analyze_deck_tactical(input_data: DeckTextInput):
    # Se o usuário colou um link do ManaBox diretamente no campo da lista:
    raw_text = input_data.raw_decklist.strip()
    if raw_text.startswith("http") and "manabox.app/decks" in raw_text:
        try:
            deck_data = await fetch_manabox_deck(raw_text)
            input_data.raw_decklist = deck_data["raw_decklist"]
            if not input_data.commander:
                input_data.commander = deck_data["commander"]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Erro ao carregar link do ManaBox: {exc}")

    parsed_commander, parsed_cards = parse_decklist_text(input_data.raw_decklist)
    raw_commander = input_data.commander or parsed_commander
    
    if not raw_commander:
        raise HTTPException(
            status_code=400,
            detail="Comandante não identificado. Forneça o nome do comandante."
        )

    print("-> [SCRYFALL] Normalizando nomes de cartas para inglês...")
    commander_info, card_infos = await normalize_deck_to_english(raw_commander, parsed_cards)
    commander = commander_info["name"] if commander_info else None
    normalized_cards = [c["name"] for c in card_infos]

    # Se não localizou o comandante exato, busca opções para o usuário escolher!
    if not commander:
        candidates = await search_commander_candidates(raw_commander)
        return JSONResponse(
            status_code=422,
            content={
                "detail": f"Não localizamos o comandante '{raw_commander}' com esse nome exato. Selecione uma das opções abaixo ou digite o nome em inglês:",
                "candidates": candidates
            }
        )

    print(f"-> [SCRYFALL] Comandante resolvido: {commander} ({len(normalized_cards)} cartas)")

    general_staples, budget_staples = await fetch_edhrec_dual_recommendations(commander)
    if not general_staples and not budget_staples:
        raise HTTPException(
            status_code=404, 
            detail=f"Comandante '{commander}' não encontrado no EDHREC."
        )

    deck_cards_normalized = {c.strip().lower() for c in normalized_cards}
    coll_state = collection_service.load_state()
    has_collection = bool(coll_state and coll_state.get("collection"))

    def process_staples(staple_list):
        pres = []
        miss = []
        for staple in staple_list:
            name = staple["name"]
            if name.lower() in deck_cards_normalized:
                pres.append(name)
            else:
                status = None
                in_decks = []
                if has_collection:
                    status, in_decks = collection_service.availability(
                        coll_state, name, exclude_deck=input_data.current_deck_name
                    )

                miss.append(CardRecommendation(
                    name=name,
                    synergy=staple.get("synergy"),
                    inclusion_rate=staple.get("inclusion_rate"),
                    status=status,
                    in_decks=in_decks
                ))
        miss_sorted = sorted(miss, key=lambda x: x.synergy or 0, reverse=True)
        return pres, miss_sorted

    present, missing_sorted = process_staples(general_staples)
    _, budget_missing_sorted = process_staples(budget_staples)

    top_missing_slice = missing_sorted[:15]
    top_budget_slice = budget_missing_sorted[:15]

    # Se o usuário marcou a opção e possui coleção, o agente foca estritamente nas cartas que ele tem livres
    # (reunindo opções disponíveis tanto do pool geral quanto do pool budget)
    if input_data.use_collection and has_collection:
        seen_names = set()
        available_in_collection = []
        for c in (missing_sorted + budget_missing_sorted):
            if c.status == "available" and c.name not in seen_names:
                seen_names.add(c.name)
                available_in_collection.append(c)
        available_in_collection = available_in_collection[:20]

        advice = await generate_tactical_advice(
            commander=commander_info,
            deck_cards=card_infos,
            top_missing=available_in_collection,
            collection_mode=True
        )
    else:
        # Padrão: recomenda as melhores cartas gerais do EDHREC
        advice = await generate_tactical_advice(
            commander=commander_info,
            deck_cards=card_infos,
            top_missing=top_missing_slice,
            collection_mode=False
        )

    return FullAnalysisResponse(
        commander=commander,
        total_deck_cards=count_deck_cards(commander, parsed_cards),
        missing_top_staples=top_missing_slice,
        missing_budget_staples=top_budget_slice,
        present_top_staples=present,
        agent_advice=advice,
        collection_used=bool(input_data.use_collection and has_collection)
    )