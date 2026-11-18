"""소풍 대화 게임(conversation_quest)에서 호야가 할 정해진(결정적) DEMO 응답을 고른다.
허용된 소풍 장면에서만 진행하며, main.py의 발화 제출 응답에 대화 내용으로 들어간다.
"""


def quest_reply(round_index: int, transcript: str | None) -> dict:
    """라운드 번호에 맞는 짧은 응답 문장과 호야 동작 목록을 돌려준다.
    아동 문장을 로그나 모델 프롬프트에 넣지 않고 장면별로 미리 정한 응답만 쓰기 위해 있다.
    """
    # 아동 문장을 로그나 모델 프롬프트에 넣지 않고 장면별 짧은 응답만 고른다.
    scenes = {
        1: ("좋아! 가방에 챙겨 볼게.", ["WALK_TO", "PICK_UP", "PUT_IN_BAG"]),
        2: ("좋은 생각이야! 같이 챙기자.", ["WALK_TO", "PICK_UP"]),
        3: ("가방에 쏙 넣었어!", ["PUT_IN_BAG", "CHEER"]),
        4: ("친구랑 같이 가면 즐겁겠다!", ["WALK_TO", "WAVE"]),
        5: ("네 이야기를 들으니 정말 즐거워!", ["CHEER"]),
    }
    text, actions = scenes[round_index]
    return {"provider": "DEMO_RULES", "text": text, "hoyaActions": actions}
