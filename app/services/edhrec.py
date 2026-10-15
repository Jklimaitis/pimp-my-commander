import asyncio
import re
import unicodedata
import httpx
from typing import List, Dict, Tuple


def slugify(text: str) -> str:
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8')
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[-\s]+", "-", text).strip("-")


def _parse_cardlists(cardlists: list) -> List[Dict]:
    recommendations = []
    seen_cards = set()
    for section in cardlists:
        for card in section.get("cardviews", []):
            name = card.get("name")
            if name and name not in seen_cards:
                seen_cards.add(name)
                recommendations.append({
                    "name": name,
                    "synergy": card.get("synergy", 0.0),
                    "inclusion_rate": card.get("num_decks", 0)
                })
    return recommendations


async def _fetch_single_url(client: httpx.AsyncClient, url: str) -> List[Dict]:
    try:
        response = await client.get(url)
        if response.status_code != 200:
            return []
        data = response.json()
        cardlists = data.get("container", {}).get("json_dict", {}).get("cardlists", [])
        return _parse_cardlists(cardlists)
    except Exception:
        return []


async def fetch_edhrec_recommendations(commander: str) -> List[Dict]:
    """Busca as recomendações gerais do EDHREC para o comandante."""
    slug = slugify(commander)
    url = f"https://json.edhrec.com/pages/commanders/{slug}.json"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PimpMyCommander/1.0"}
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
        return await _fetch_single_url(client, url)


async def fetch_edhrec_dual_recommendations(commander: str) -> Tuple[List[Dict], List[Dict]]:
    """
    Busca simultaneamente as recomendações gerais e a versão budget do EDHREC.
    Retorna (geral, budget).
    """
    slug = slugify(commander)
    general_url = f"https://json.edhrec.com/pages/commanders/{slug}.json"
    budget_url = f"https://json.edhrec.com/pages/commanders/{slug}/budget.json"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PimpMyCommander/1.0"}

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
        general, budget = await asyncio.gather(
            _fetch_single_url(client, general_url),
            _fetch_single_url(client, budget_url),
        )
    return general, budget