"""기존 모험 회기 완료 때 아동에게 줄 배지와 몬스터 카드를 계산한다.
아이의 동기 부여용 보상이며 임상 판정과 관계없다. main.py의 회기 완료 처리가 부른다.
"""

MONSTER_CARDS = ["슬라임 사사", "바람 박쥐 소소", "구름 거북 수수", "번개 여우 시시", "별빛 고래 서서"]


def award(collection: dict, stats: dict) -> tuple[dict, list[str], list[str]]:
    """회기 통계로 새 배지("첫 모험", "마법 빔 마스터", "끈기 용사")와 깬 몬스터 단계 수만큼의 카드를 수집품에 더한다.
    갱신된 수집품과 이번에 새로 얻은 배지·카드 목록을 돌려줘 완료 화면에서 보여 준다.
    """
    result = dict(collection or {})
    badges = list(result.get("badges", []))
    cards = list(result.get("monsterCards", []))
    new_badges = []
    new_cards = []
    effort = result.get("effortCount", 0) + stats.get("retryThenSuccessCount", 0)
    result["effortCount"] = effort
    for name, earned in (("첫 모험", True), ("마법 빔 마스터", stats.get("beamSuccesses", 0) >= 3), ("끈기 용사", effort >= 5)):
        if earned and name not in badges:
            badges.append(name)
            new_badges.append(name)
    for _ in range(stats.get("monsterStagesCleared", 0)):
        next_card = next((card for card in MONSTER_CARDS if card not in cards), None)
        if next_card:
            cards.append(next_card)
            new_cards.append(next_card)
    result["badges"] = badges
    result["monsterCards"] = cards
    return result, new_badges, new_cards
