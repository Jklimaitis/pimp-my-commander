import html
import json
import re
import httpx
from typing import Any, Dict

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _unwrap(v: Any) -> Any:
    """Desempacota a serialização interna de props do Astro: [tipo, valor]."""
    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int):
        return _unwrap(v[1])
    if isinstance(v, dict):
        return {k: _unwrap(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_unwrap(item) for item in v]
    return v


def parse_manabox_html(html_text: str) -> Dict[str, Any]:
    """
    Extrai do HTML do ManaBox o JSON contido na tag <astro-island component-export="Main" ...>
    e monta a lista de cartas formatada com Comandante e Mainboard.
    """
    m = re.search(r'component-export=\"Main\"[^>]*props=\"([^\"]+)\"', html_text)
    if not m:
        raise ValueError(
            "Não foi possível extrair os dados do deck desta página do ManaBox. "
            "Verifique se o deck é público ou compartilhado."
        )

    raw_props = html.unescape(m.group(1))
    data = _unwrap(json.loads(raw_props))
    deck = data.get("deck", {})
    deck_name = deck.get("name", "ManaBox Deck")

    commanders = []
    mainboard = []

    for c in deck.get("cards", []):
        name = c.get("name")
        qty = int(c.get("quantity", 1))
        cat = c.get("boardCategory")
        if not name:
            continue
        # boardCategory 0 = Comandante / Parceiro
        if cat == 0:
            commanders.append((name, qty))
        else:
            mainboard.append((name, qty))

    lines = []
    commander_str = commanders[0][0] if commanders else ""
    if commanders:
        lines.append("// Commander")
        for name, qty in commanders:
            lines.append(f"{qty} {name}")
        lines.append("")

    lines.append("// Deck")
    for name, qty in mainboard:
        lines.append(f"{qty} {name}")

    total_cards = sum(q for _, q in commanders) + sum(q for _, q in mainboard)

    return {
        "name": deck_name,
        "commander": commander_str,
        "raw_decklist": "\n".join(lines),
        "total_cards": total_cards,
    }


async def fetch_manabox_deck(url_or_id: str) -> Dict[str, Any]:
    """
    Recebe a URL compartilhada do ManaBox (ex: https://manabox.app/decks/AaE7kKYAfmWP6L_nQwys4g)
    ou apenas o ID do deck, faz o download do HTML e extrai o decklist.
    """
    target = url_or_id.strip()
    if not target.startswith("http"):
        target = f"https://manabox.app/decks/{target}"

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            resp = await client.get(target, headers=HEADERS)
        except httpx.HTTPError as exc:
            raise ValueError(f"Falha de conexão ao acessar ManaBox: {exc}") from exc

        if resp.status_code == 404:
            raise ValueError("Deck não encontrado no ManaBox (erro 404).")
        if resp.status_code != 200:
            raise ValueError(f"ManaBox retornou status HTTP {resp.status_code}.")

        return parse_manabox_html(resp.text)

