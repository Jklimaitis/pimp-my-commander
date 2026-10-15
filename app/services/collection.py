import asyncio
import csv
import io
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.parser import parse_decklist_text
from app.services.scryfall import resolve_many

# Ferramenta pessoal: a coleção e os decks montados ficam num único arquivo JSON local.
DATA_FILE = Path(os.getenv("PMC_DATA_DIR", "data")) / "collection.json"
_lock = asyncio.Lock()

# Cabeçalhos aceitos no CSV (Manabox usa "Name" e "Quantity"; LigaMagic/outros podem variar)
NAME_COLUMNS = ("name", "card name", "card", "nome", "nome da carta", "carta")
QTY_COLUMNS = ("quantity", "qty", "count", "quantidade", "qtd", "amount")

# Linhas de seção que aparecem em exports de texto e não são cartas
SECTION_WORDS = {
    "deck", "commander", "commanders", "sideboard", "maybeboard", "mainboard",
    "companion", "collection", "coleção", "colecao", "main", "sb",
}


class CollectionError(Exception):
    """Erro de entrada do usuário ao importar/gerenciar a coleção (HTTP 400)."""


# --- Parsing ----------------------------------------------------------------------
def _clean_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"\s+\*.*\*$", "", name)            # marcas de foil: *F*
    name = re.sub(r"\s+[\(\[\{].*$", "", name)         # (SET) 123 / [tags]
    name = name.strip().strip('"')
    return name.split(" // ")[0].strip() if " // " in name else name


def _add(entries: Dict[str, Tuple[str, int]], name: str, qty: int) -> None:
    name = _clean_name(name)
    if not name or qty <= 0:
        return
    key = name.lower()
    display, current = entries.get(key, (name, 0))
    entries[key] = (display, current + qty)


def _detect_delimiter(header_line: str) -> Optional[str]:
    """Retorna o delimitador se a 1ª linha parece um cabeçalho de CSV com coluna de nome."""
    for delim in (",", ";", "\t"):
        if delim in header_line:
            cells = [c.strip().strip('"').lower() for c in header_line.split(delim)]
            if any(c in NAME_COLUMNS for c in cells):
                return delim
    return None


def _parse_csv(text: str, delimiter: str) -> Dict[str, Tuple[str, int]]:
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    fields = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}
    name_col = next((fields[c] for c in NAME_COLUMNS if c in fields), None)
    qty_col = next((fields[c] for c in QTY_COLUMNS if c in fields), None)
    if not name_col:
        raise CollectionError("CSV sem coluna de nome (esperado: Name).")

    entries: Dict[str, Tuple[str, int]] = {}
    for row in reader:
        name = (row.get(name_col) or "").strip()
        if not name:
            continue
        try:
            qty = int(float((row.get(qty_col) or "1").strip())) if qty_col else 1
        except ValueError:
            qty = 1
        _add(entries, name, qty)
    return entries


_TEXT_LINE = re.compile(r"^\s*(?:(\d+)\s*x?\s+)?(.+?)\s*$", re.IGNORECASE)


def _parse_text(text: str) -> Dict[str, Tuple[str, int]]:
    entries: Dict[str, Tuple[str, int]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        line = re.sub(r"^SB:\s*", "", line, flags=re.IGNORECASE)
        if line.rstrip(":").strip().lower() in SECTION_WORDS:
            continue
        match = _TEXT_LINE.match(line)
        if not match:
            continue
        qty = int(match.group(1)) if match.group(1) else 1
        _add(entries, match.group(2), qty)
    return entries


def parse_collection_input(raw: str) -> Dict[str, Tuple[str, int]]:
    """
    Aceita CSV (Manabox e similares) ou texto ("1 Sol Ring", "2x Forest", só o nome...).
    Retorna {nome_minúsculo: (nome_original, quantidade)}.
    """
    text = (raw or "").lstrip("\ufeff").strip()
    if not text:
        return {}
    first_line = text.splitlines()[0]
    delimiter = _detect_delimiter(first_line)
    return _parse_csv(text, delimiter) if delimiter else _parse_text(text)


# --- Persistência -----------------------------------------------------------------
def _empty_state() -> Dict[str, Any]:
    return {"collection": {}, "built_decks": {}, "updated_at": None}


def load_state() -> Dict[str, Any]:
    try:
        state = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return _empty_state()
    for key, default in _empty_state().items():
        state.setdefault(key, default)
    return state


def _save_state(state: Dict[str, Any]) -> None:
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, DATA_FILE)  # escrita atômica: nunca deixa o arquivo pela metade


def format_card_name(key: str, state: Dict[str, Any], deck_data: Optional[Dict[str, Any]] = None) -> str:
    """Retorna o nome oficial formatado para exibição e busca no Scryfall/EDHREC."""
    if deck_data and "card_names" in deck_data and key in deck_data["card_names"]:
        return deck_data["card_names"][key]
    coll_entry = state.get("collection", {}).get(key)
    if coll_entry and isinstance(coll_entry, dict) and "name" in coll_entry:
        return coll_entry["name"]
    # Fallback inteligente para nomes em inglês corrigindo o bug do 'S e preposições
    formatted = key.title()
    formatted = re.sub(r"\'S\b", "'s", formatted)
    for prep in ("Of", "The", "To", "In", "For", "With", "By", "At", "From", "And"):
        formatted = re.sub(rf"\b{prep}\b", prep.lower(), formatted)
    return formatted


def summarize(state: Dict[str, Any]) -> Dict[str, Any]:
    collection = state["collection"]
    built_decks = []
    for name, d in state.get("built_decks", {}).items():
        cards = d.get("cards", {})
        first_card = next(iter(cards.keys()), "")
        cmd = d.get("commander") or (format_card_name(first_card, state, d) if first_card else name)
        raw = d.get("raw_decklist") or "\n".join(f"{qty} {format_card_name(k, state, d)}" for k, qty in cards.items())
        total = sum(cards.values())
        built_decks.append({
            "name": name,
            "commander": cmd,
            "cards": total,
            "cards_count": total,
            "raw_decklist": raw,
        })

    return {
        "unique_cards": len(collection),
        "total_cards": sum(c["qty"] for c in collection.values()),
        "updated_at": state.get("updated_at"),
        "built_decks": built_decks,
    }


def get_built_deck(state: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    deck = state.get("built_decks", {}).get(name)
    if not deck:
        return None
    cards = deck.get("cards", {})
    first_card = next(iter(cards.keys()), "")
    cmd = deck.get("commander") or (format_card_name(first_card, state, deck) if first_card else name)
    raw = deck.get("raw_decklist") or "\n".join(f"{qty} {format_card_name(k, state, deck)}" for k, qty in cards.items())
    return {
        "name": name,
        "commander": cmd,
        "cards_count": sum(cards.values()),
        "raw_decklist": raw
    }


# --- Resolução de nomes (PT/EN -> nome canônico em inglês) -------------------------
async def _to_canonical(entries: Dict[str, Tuple[str, int]]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    resolved = await resolve_many([display for display, _ in entries.values()])
    result: Dict[str, Dict[str, Any]] = {}
    unresolved: List[str] = []
    for low, (display, qty) in entries.items():
        info = resolved.get(display.strip().lower())
        if info:
            key, name = info["name"].lower(), info["name"]
        else:
            key, name = low, display
            unresolved.append(display)
        current = result.get(key, {"name": name, "qty": 0})
        current["qty"] += qty
        result[key] = current
    return result, unresolved


# --- Operações --------------------------------------------------------------------
async def import_collection(raw_text: str, mode: str = "replace") -> Dict[str, Any]:
    entries = parse_collection_input(raw_text)
    if not entries:
        raise CollectionError("Nenhuma carta encontrada no texto enviado.")

    new_cards, unresolved = await _to_canonical(entries)

    async with _lock:
        state = load_state()
        if mode == "add":
            for key, card in new_cards.items():
                current = state["collection"].get(key, {"name": card["name"], "qty": 0})
                current["qty"] += card["qty"]
                state["collection"][key] = current
        else:
            state["collection"] = new_cards
        _save_state(state)

    return {**summarize(state), "imported_lines": len(entries), "unresolved": unresolved}


async def clear_collection() -> Dict[str, Any]:
    async with _lock:
        state = load_state()
        state["collection"] = {}
        _save_state(state)
    return summarize(state)


async def save_built_deck(name: str, raw_decklist: str, commander: Optional[str] = None) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name or len(name) > 60:
        raise CollectionError("Informe um nome para o deck (até 60 caracteres).")

    parsed_commander, cards = parse_decklist_text(raw_decklist)
    commander = (commander or parsed_commander or "").strip()
    names = ([commander] if commander else []) + cards
    if not names:
        raise CollectionError("Nenhuma carta encontrada na lista do deck.")

    counts = Counter(n.strip() for n in names)
    entries = {n.lower(): (n, q) for n, q in counts.items()}
    deck_cards, unresolved = await _to_canonical(entries)

    async with _lock:
        state = load_state()
        state["built_decks"][name] = {
            "commander": commander,
            "raw_decklist": raw_decklist,
            "cards": {key: c["qty"] for key, c in deck_cards.items()},
            "card_names": {key: c["name"] for key, c in deck_cards.items()},
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _save_state(state)

    return {**summarize(state), "saved_deck": name, "unresolved": unresolved}


async def delete_built_deck(name: str) -> Dict[str, Any]:
    async with _lock:
        state = load_state()
        if name not in state["built_decks"]:
            raise CollectionError(f"Deck '{name}' não encontrado.")
        del state["built_decks"][name]
        _save_state(state)
    return summarize(state)


# --- Disponibilidade --------------------------------------------------------------
def availability(
    state: Dict[str, Any], 
    card_name: str, 
    exclude_deck: Optional[str] = None
) -> Tuple[str, List[str]]:
    """
    Retorna (status, decks_que_usam):
      - "not_owned": não está na coleção
      - "in_use": tenho, mas todas as cópias estão em decks montados
      - "available": tenho cópia livre (coleção - cópias em decks montados > 0)
    `exclude_deck`: se informado, ignora as cartas do próprio deck que está sendo analisado.
    """
    key = card_name.split(" // ")[0].strip().lower()
    owned = state["collection"].get(key, {}).get("qty", 0)
    if owned <= 0:
        return "not_owned", []

    used, in_decks = 0, []
    for deck_name, deck in state["built_decks"].items():
        if exclude_deck and deck_name.strip().lower() == exclude_deck.strip().lower():
            continue
        qty = deck["cards"].get(key, 0)
        if qty:
            used += qty
            in_decks.append(deck_name)

    return ("available" if owned - used > 0 else "in_use"), in_decks

