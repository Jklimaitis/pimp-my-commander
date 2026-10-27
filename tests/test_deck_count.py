from app.services.parser import count_deck_cards, parse_decklist_text


def test_conta_o_comandante_alem_das_cartas_da_lista():
    _, cartas = parse_decklist_text("1 Sol Ring\n98 Plains")
    assert len(cartas) == 99
    assert count_deck_cards("Zurgo Stormrender", cartas) == 100


def test_sem_comandante_conta_apenas_as_cartas():
    _, cartas = parse_decklist_text("1 Sol Ring\n3 Plains")
    assert count_deck_cards("", cartas) == 4


def test_comandante_sem_cartas_conta_um():
    assert count_deck_cards("Zurgo Stormrender", []) == 1


def test_lista_de_commander_fecha_em_cem():
    texto = "// COMMANDER\n1 Zurgo Stormrender\n\n1 Sol Ring\n1 Arcane Signet\n97 Mountain"
    comandante, cartas = parse_decklist_text(texto)
    assert comandante == "Zurgo Stormrender"
    assert count_deck_cards(comandante, cartas) == 100
