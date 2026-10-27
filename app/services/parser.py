import re
from typing import Dict, List, Tuple

def parse_decklist_text(raw_text: str) -> Tuple[str, List[str]]:
    """
    Recebe o texto bruto exportado de deckbuilders (Moxfield, Archidekt, etc.)
    e extrai:
    - O comandante (se marcado ou inferido)
    - Lista de nomes de cartas limpas (repetidas conforme a quantidade, ex.: "10 Forest")
    """
    lines = raw_text.strip().splitlines()
    commander = ""
    cards = []
    
    # Regex para capturar linhas como:
    # "1 Sol Ring", "1x Sol Ring", "1 Sol Ring (CMR) 472 *F*"
    card_pattern = re.compile(r"^\s*(\d+x?|\*COMMANDER\*|COMMANDER:)?\s*(.+?)(?:\s+[\(\[\{].*)?$")

    in_commander_section = False

    for line in lines:
        cleaned_line = line.strip()
        if not cleaned_line or cleaned_line.startswith("//") or cleaned_line.startswith("#"):
            # Linhas vazias ou comentários/seções
            header_lower = cleaned_line.lower()
            if "commander" in header_lower:
                in_commander_section = True
            elif any(sec in header_lower for sec in ["deck", "creature", "sorcery", "instant", "artifact", "enchantment", "land"]):
                in_commander_section = False
            continue

        match = card_pattern.match(cleaned_line)
        if match:
            prefix, card_name = match.groups()
            # Remove sufixos como sets ou marcações de foil
            card_name = re.sub(r"\s+\*.*\*$", "", card_name).strip()
            
            if in_commander_section and not commander:
                commander = card_name
            elif prefix and "commander" in str(prefix).lower():
                commander = card_name
            else:
                # Respeita a quantidade ("10 Forest" -> 10 entradas) para que a contagem
                # de terrenos e o tamanho do deck fiquem corretos.
                qty_match = re.match(r"(\d+)", str(prefix or ""))
                quantity = int(qty_match.group(1)) if qty_match else 1
                cards.extend([card_name] * max(1, min(quantity, 99)))

    return commander, cards


def count_deck_cards(commander: str, cards: List[str]) -> int:
    """
    Total de cartas do deck, contando o comandante.

    O parser devolve as cartas da lista separadas do comandante (99 num deck de
    Commander); a convenção do formato conta o comandante nas 100.
    """
    return len(cards) + (1 if commander else 0)
