from fastapi.testclient import TestClient

from app.main import app
from app.services.parser import parse_decklist_text

client = TestClient(app)


def test_serves_frontend():
    resposta = client.get("/")
    assert resposta.status_code == 200
    assert "text/html" in resposta.headers["content-type"]


def test_collection_can_be_read_and_cleared():
    assert client.delete("/collection").status_code == 200
    resposta = client.get("/collection")
    assert resposta.status_code == 200
    assert isinstance(resposta.json(), dict)


def test_parser_keeps_quantities_and_names():
    comandante, cartas = parse_decklist_text("1 Sol Ring\n2 Arcane Signet")
    assert "Sol Ring" in cartas
    assert cartas.count("Arcane Signet") == 2
    assert comandante == ""


def test_parser_ignores_comments_and_empty_lines():
    comandante, cartas = parse_decklist_text("# Deck\n\n1 Sol Ring\n// fim")
    assert cartas == ["Sol Ring"]
    assert comandante == ""
