from app.speech.pipeline import SimpleKoreanG2P


def test_ng_final_does_not_link():
    phones = SimpleKoreanG2P().to_phonemes("강아지")
    assert [(p.slot, p.symbol) for p in phones] == [("I", "ㄱ"), ("M", "ㅏ"), ("F", "ㅇ"), ("I", "ㅇ0"), ("M", "ㅏ"), ("I", "ㅈ"), ("M", "ㅣ")]


def test_cluster_final_splits_before_vowel():
    phones = SimpleKoreanG2P().to_phonemes("넋이")
    assert [(p.slot, p.symbol) for p in phones] == [("I", "ㄴ"), ("M", "ㅓ"), ("F", "ㄱ"), ("I", "ㅆ"), ("M", "ㅣ")]
