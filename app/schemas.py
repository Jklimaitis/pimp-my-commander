from pydantic import BaseModel, Field
from typing import List, Literal, Optional

class DeckTextInput(BaseModel):
    raw_decklist: str
    commander: Optional[str] = None
    use_collection: bool = False
    current_deck_name: Optional[str] = None

class CardRecommendation(BaseModel):
    name: str
    synergy: Optional[float] = 0.0
    inclusion_rate: Optional[int] = 0
    # Só preenchidos quando a análise usa a coleção:
    # "available" (tenho livre), "in_use" (tenho, mas está em outro deck montado), "not_owned"
    status: Optional[str] = None
    in_decks: List[str] = Field(default_factory=list)

class CutSuggestion(BaseModel):
    card_name: str
    reason: str

class StrategyReport(BaseModel):
    tactical_summary: str
    recommended_cuts: List[CutSuggestion]
    priority_additions: List[str]
    # Cartas que o jogador tem na coleção e pode incluir imediatamente
    collection_additions: List[str] = Field(default_factory=list)
    mana_curve_feedback: str
    # Mantido para retrocompatibilidade
    future_upgrades: List[str] = Field(default_factory=list)

class FullAnalysisResponse(BaseModel):
    commander: str
    total_deck_cards: int
    missing_top_staples: List[CardRecommendation]
    missing_budget_staples: List[CardRecommendation] = Field(default_factory=list)
    present_top_staples: List[str]
    agent_advice: StrategyReport
    collection_used: bool = False

class CollectionImportInput(BaseModel):
    raw_text: str
    # "replace": a lista enviada vira a coleção (padrão, ideal p/ export completo do Manabox)
    # "add": soma as quantidades à coleção existente
    mode: Literal["replace", "add"] = "replace"

class BuiltDeckInput(BaseModel):
    name: str
    raw_decklist: str
    commander: Optional[str] = None

class DeckUrlInput(BaseModel):
    url: str

class DeckFromUrlResponse(BaseModel):
    name: str
    commander: str
    raw_decklist: str
    total_cards: int