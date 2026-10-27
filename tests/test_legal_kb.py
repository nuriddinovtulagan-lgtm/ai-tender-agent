"""Lawyer knowledge base (/pravo)."""

from trans_ocean.legal_kb import NOTES, answer, search


def test_topics_and_search():
    assert "Темы" in answer("/pravo") and len(NOTES) >= 8
    ids = [n["id"] for n in search("ндс")]
    assert "nds-perevozchik" in ids and "nds-ekspediciya" in ids
    assert [n["id"] for n in search("ЭТТН")][0] == "ettn"
    assert "250" in answer("/pravo экспедиция")
    assert "Ничего не нашёл" in answer("/pravo марсоход")


def test_reply_fits_telegram():
    for word in ["ндс", "налог", "оплата", "статья"]:
        assert len(answer(f"/pravo {word}")) < 4096
