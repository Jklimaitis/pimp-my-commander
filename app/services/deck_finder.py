import asyncio
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from app.services import collection, scryfall, edhrec

# Mapeamento amigável de identidades de cor para Commander
COLOR_NAMES = {
    "": ("Incolor", "⚙️"),
    "W": ("Mono Branco", "⚪"),
    "U": ("Mono Azul", "🔵"),
    "B": ("Mono Preto", "⚫"),
    "R": ("Mono Vermelho", "🔴"),
    "G": ("Mono Verde", "🟢"),
    "UW": ("Azorius", "🔵⚪"),
    "BU": ("Dimir", "⚫🔵"),
    "BR": ("Rakdos", "⚫🔴"),
    "GR": ("Gruul", "🟢🔴"),
    "GW": ("Selesnya", "🟢⚪"),
    "BW": ("Orzhov", "⚪⚫"),
    "BG": ("Golgari", "🟢⚫"),
    "GU": ("Simic", "🟢🔵"),
    "RU": ("Izzet", "🔴🔵"),
    "RW": ("Boros", "🔴⚪"),
    "BRW": ("Mardu", "🔴⚪⚫"),
    "GRU": ("Temur", "🟢🔴🔵"),
    "BGU": ("Sultai", "⚫🟢🔵"),
    "RUW": ("Jeskai", "🔴🔵⚪"),
    "BGW": ("Abzan", "⚪⚫🟢"),
    "BRU": ("Grixis", "🔵⚫🔴"),
    "BGR": ("Jund", "⚫🔴🟢"),
    "GRW": ("Naya", "🔴🟢⚪"),
    "GUW": ("Bant", "🟢⚪🔵"),
    "BUW": ("Esper", "⚪🔵⚫"),
    "BGRUW": ("5 Cores (WUBRG)", "🌈"),
}

# Tradução aproximada dos tags mais comuns do EDHREC para o Português
THEME_TRANSLATIONS = {
    "tokens": "Fichas de Criatura",
    "aggro": "Agressivo (Aggro)",
    "attack-triggers": "Gatilhos de Combate",
    "extra-combats": "Combates Adicionais",
    "equipment": "Equipamentos / Voltron",
    "samurai": "Samurais & Guerreiros",
    "legends": "Lendárias (Histórico)",
    "aristocrats": "Aristocratas & Sacrifício",
    "sacrifice": "Sacrifício & Drenagem",
    "reanimator": "Reanimação do Cemitério",
    "graveyard": "Cemitério & Recursão",
    "lifegain": "Ganho de Vida",
    "lifedrain": "Drenagem de Vida",
    "spellslinger": "Mágicas Instantâneas & Feitiços",
    "wraiths": "Espectros & Nazgûl",
    "plus-1-plus-1-counters": "Marcadores +1/+1",
    "burn": "Dano Direto (Burn)",
    "treasure": "Tesouros & Mana",
    "go-wide": "Enxame (Go Wide)",
    "voltron": "Voltron / Comandante Letal",
    "midrange": "Controle & Valor (Midrange)",
    "humans": "Tribal de Humanos",
    "vampires": "Tribal de Vampiros",
    "zombies": "Tribal de Zumbis",
    "angels": "Tribal de Anjos",
    "dragons": "Tribal de Dragões",
    "goblins": "Tribal de Goblins",
    "blink": "Blink / Entrar no Campo (ETB)",
    "etb": "Efeitos ao Entrar no Campo",
    "stax": "Controle & Travamento",
    "hatebears": "Hatebears / Taxas",
    "monarch": "Monarca & Política",
}


def get_color_label(colors: List[str]) -> Tuple[str, str]:
    key = "".join(sorted(colors))
    if key in COLOR_NAMES:
        return COLOR_NAMES[key]
    # Fallback se a ordem diferir
    for k, val in COLOR_NAMES.items():
        if set(k) == set(colors):
            return val
    symbols = " ".join(c for c in colors)
    return (f"{len(colors)} Cores", symbols)


def translate_tags(tags: List[Dict[str, Any]]) -> List[str]:
    translated = []
    for t in tags[:4]:
        slug = t.get("slug", "").lower()
        val = t.get("value", "")
        if slug in THEME_TRANSLATIONS:
            translated.append(THEME_TRANSLATIONS[slug])
        elif val:
            translated.append(val)
    return translated[:3]


async def recommend_decks_from_collection(limit: int = 8) -> Dict[str, Any]:
    """
    Analisa a coleção do usuário, identifica os comandantes presentes,
    calcula as cartas compatíveis na coleção e cruza com os dados do EDHREC
    para indicar os decks mais viáveis e fortes ("decks bons") que ele já consegue montar.
    """
    state = collection.load_state()
    coll = state.get("collection", {})
    if not coll:
        return {
            "total_collection_cards": 0,
            "total_commanders_found": 0,
            "decks": [],
            "message": "Sua coleção está vazia. Importe sua coleção primeiro para descobrir decks possíveis."
        }

    # 1. Resolve todas as cartas da coleção via Scryfall
    names = [c["name"] for c in coll.values()]
    resolved = await scryfall.resolve_many(names)
    all_cards = [info for info in resolved.values() if info]
    coll_names_lower = {c["name"].lower() for c in all_cards}

    # 2. Filtra candidatos a comandante presentes na coleção física
    candidates = []
    for card in all_cards:
        type_line = card.get("type_line", "")
        # Criaturas lendárias ou planeswalkers que podem ser comandante
        if "Legendary" in type_line and ("Creature" in type_line or "can be your commander" in card.get("oracle_text", "")):
            candidates.append(card)

    if not candidates:
        return {
            "total_collection_cards": sum(c.get("qty", 1) for c in coll.values()),
            "total_commanders_found": 0,
            "decks": [],
            "message": "Nenhuma criatura lendária encontrada na sua coleção para atuar como comandante."
        }

    # 3. Para cada candidato, calcula as cartas compatíveis na coleção
    scored_candidates = []
    for cmd in candidates:
        cmd_colors = set(cmd.get("color_identity", []))
        cmd_name = cmd["name"]
        
        # Cartas cuja identidade de cor cabe no deck desse comandante
        compat_cards = [
            c for c in all_cards 
            if set(c.get("color_identity", [])).issubset(cmd_colors) and c["name"].lower() != cmd_name.lower()
        ]
        
        # Só considera comandantes para os quais o jogador tenha pelo menos 30 cartas compatíveis na coleção
        if len(compat_cards) >= 30:
            scored_candidates.append({
                "cmd": cmd,
                "compat_cards": compat_cards,
                "compat_count": len(compat_cards),
                "colors": sorted(list(cmd_colors))
            })

    # Ordena pelo tamanho do acervo de cartas compatíveis
    scored_candidates.sort(key=lambda x: x["compat_count"], reverse=True)

    # 4. Seleciona candidatos diversificados por cor para não saturar com apenas uma combinação
    selected_for_edhrec = []
    color_distribution = {}
    
    for item in scored_candidates:
        c_tuple = tuple(item["colors"])
        current_count = color_distribution.get(c_tuple, 0)
        # Permite até 2 comandantes por combinação de cores para dar variedade
        if current_count < 2 and len(selected_for_edhrec) < 14:
            selected_for_edhrec.append(item)
            color_distribution[c_tuple] = current_count + 1

    # Se ainda sobrou espaço, completa com os melhores restantes
    if len(selected_for_edhrec) < 10:
        for item in scored_candidates:
            if item not in selected_for_edhrec and len(selected_for_edhrec) < 14:
                selected_for_edhrec.append(item)

    # 5. Consulta EDHREC em paralelo para os candidatos selecionados
    async def fetch_edhrec_profile(cand_item):
        cmd_name = cand_item["cmd"]["name"]
        slug = edhrec.slugify(cmd_name)
        url = f"https://json.edhrec.com/pages/commanders/{slug}.json"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PimpMyCommander/1.0"}
        
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                cardlists = data.get("container", {}).get("json_dict", {}).get("cardlists", [])
                staples = edhrec._parse_cardlists(cardlists)
                tags = data.get("panels", {}).get("taglinks", [])
                return {
                    "staples": staples,
                    "tags": translate_tags(tags),
                }
        except Exception:
            return None

    edhrec_results = await asyncio.gather(*(fetch_edhrec_profile(c) for c in selected_for_edhrec))

    # 6. Cruza staples do EDHREC com a coleção do jogador e calcula notas
    recommended_decks = []
    for cand_item, edh_data in zip(selected_for_edhrec, edhrec_results):
        cmd = cand_item["cmd"]
        cmd_name = cmd["name"]
        compat_count = cand_item["compat_count"]
        colors = cand_item["colors"]
        color_name, color_symbols = get_color_label(colors)

        if edh_data and edh_data.get("staples"):
            staples = edh_data["staples"]
            tags = edh_data.get("tags", [])

            # Quais staples o jogador já tem na coleção
            owned_staples = [s for s in staples if s["name"].lower() in coll_names_lower]
            missing_staples = [s for s in staples if s["name"].lower() not in coll_names_lower]

            owned_staples.sort(key=lambda x: x.get("synergy", 0), reverse=True)
            missing_staples.sort(key=lambda x: x.get("synergy", 0), reverse=True)

            owned_count = len(owned_staples)
            total_staples = min(len(staples), 100)
            avg_syn = sum(s.get("synergy", 0) for s in owned_staples[:15]) / 15.0 if owned_staples else 0
            theme_display = " • ".join(tags) if tags else "Sinergia Geral de Comandante"
            key_cards = [s["name"] for s in owned_staples[:4]]
            missing_upgrades = [s["name"] for s in missing_staples[:3]]
        else:
            # Fallback se o EDHREC não responder ou não tiver dados
            owned_count = 0
            total_staples = 0
            avg_syn = 0
            compat_non_lands = [c for c in cand_item["compat_cards"] if "Basic Land" not in c.get("type_line", "")]
            key_cards = [c["name"] for c in compat_non_lands[:4]]
            missing_upgrades = []
            theme_display = "Comandante viável na sua coleção"

        # Cálculo de Prontidão da Coleção:
        staples_ratio = min(1.0, owned_count / 40.0) if owned_count > 0 else 0
        pool_ratio = min(1.0, compat_count / 120.0)
        if owned_count > 0:
            match_pct = int((staples_ratio * 60) + (pool_ratio * 40))
        else:
            match_pct = int(pool_ratio * 75)
        match_pct = min(98, max(30, match_pct))

        composite_score = (owned_count * 2.5) + (compat_count * 0.25) + (avg_syn * 40)

        recommended_decks.append({
            "commander": cmd_name,
            "type_line": cmd.get("type_line", ""),
            "colors": colors,
            "color_name": color_name,
            "color_symbols": color_symbols,
            "color_identity": f"{color_name} ({color_symbols})",
            "theme": theme_display,
            "match_percentage": match_pct,
            "compatible_cards_count": compat_count,
            "owned_staples_count": owned_count,
            "total_staples_checked": total_staples,
            "key_owned_cards": key_cards,
            "top_missing_upgrades": missing_upgrades,
            "score": composite_score,
        })

    # 7. Ordena pelas melhores recomendações
    recommended_decks.sort(key=lambda x: x["score"], reverse=True)

    return {

        "total_collection_cards": sum(c.get("qty", 1) for c in coll.values()),
        "total_unique_cards": len(coll),
        "total_commanders_found": len(candidates),
        "decks": recommended_decks[:limit]
    }


async def draft_deck_for_commander(commander_name: str) -> Dict[str, Any]:
    """
    Monta automaticamente um rascunho de deck de 100 cartas utilizando as melhores cartas
    da coleção do jogador que cabem na identidade de cor do comandante, completando com terrenos.
    """
    state = collection.load_state()
    coll = state.get("collection", {})
    names = [c["name"] for c in coll.values()]
    resolved = await scryfall.resolve_many(names)
    all_cards = [info for info in resolved.values() if info]
    by_lower = {c["name"].lower(): c for c in all_cards}

    # Resolve o comandante
    client = scryfall.httpx.AsyncClient(timeout=10.0)
    async with client:
        cmd_info = await scryfall.resolve_single_card(commander_name, client)
    
    if not cmd_info:
        cmd_info = by_lower.get(commander_name.lower())
    
    if not cmd_info:
        raise ValueError(f"Comandante '{commander_name}' não encontrado.")

    cmd_colors = set(cmd_info.get("color_identity", []))
    
    # 1. Cartas da coleção compatíveis com a cor
    compat_cards = [
        c for c in all_cards 
        if set(c.get("color_identity", [])).issubset(cmd_colors) and c["name"].lower() != cmd_info["name"].lower()
    ]

    # 2. Busca staples do EDHREC para priorizar as melhores
    edhrec_staples = await edhrec.fetch_edhrec_recommendations(cmd_info["name"])
    staple_ranks = {s["name"].lower(): idx for idx, s in enumerate(edhrec_staples)}

    # Ordena as cartas compatíveis: primeiro as que aparecem no EDHREC (por ranking), depois as outras
    def sort_key(card):
        low = card["name"].lower()
        if low in staple_ranks:
            return (0, staple_ranks[low])
        return (1, 0)

    compat_cards.sort(key=sort_key)

    # 3. Seleciona até 63 mágicas/terrenos da coleção + 36 terrenos básicos + 1 comandante = 100 cartas
    chosen_non_basics = []
    for c in compat_cards:
        if len(chosen_non_basics) >= 63:
            break
        chosen_non_basics.append(c["name"])

    # 4. Distribui os terrenos básicos de acordo com as cores do comandante
    needed_basics = 99 - len(chosen_non_basics)
    basic_lands_map = {
        "W": "Plains",
        "U": "Island",
        "B": "Swamp",
        "R": "Mountain",
        "G": "Forest",
    }
    
    colors_list = list(cmd_colors) or ["W"] # fallback para Plains se incolor
    lands_per_color = max(1, needed_basics // len(colors_list))
    remainder = needed_basics % len(colors_list)

    deck_lines = [f"1 {cmd_info['name']}"]
    for cname in chosen_non_basics:
        deck_lines.append(f"1 {cname}")

    for idx, col in enumerate(colors_list):
        qty = lands_per_color + (remainder if idx == 0 else 0)
        land_name = basic_lands_map.get(col, "Plains")
        deck_lines.append(f"{qty} {land_name}")

    raw_decklist = "\n".join(deck_lines)

    return {
        "commander": cmd_info["name"],
        "raw_decklist": raw_decklist,
        "total_cards": 100,
        "cards_from_collection": len(chosen_non_basics) + 1,
    }

