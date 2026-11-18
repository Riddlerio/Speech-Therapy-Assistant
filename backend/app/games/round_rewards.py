"""라운드를 마친 아동에게 줄 별(1~3개)과 격려 문장을 정한다.
아이 동기 부여용이며 치료사가 확정한 임상 결과나 진단으로 해석하지 않는다. main.py가 라운드 완료 때 부른다.
"""


def round_reward(successes: int, evaluated: int) -> dict:
    """성공률이 70% 이상이면 별 3개, 50% 이상이면 2개, 그 밖은 1개와 격려 문장을 돌려준다.
    분모(evaluated)에는 success·retry만 들어가고 판단 보류·무발화는 빠진다.
    """
    # evaluated에는 success·retry만 들어간다. uncertain·no_speech는 분모에서 제외한다.
    ratio = successes / evaluated if evaluated > 0 else None
    if ratio is not None and ratio >= 0.7:
        stars, praise = 3, "역시 최고야!"
    elif ratio is not None and ratio >= 0.5:
        stars, praise = 2, "정말 잘했어!"
    else:
        stars, praise = 1, "조금만 더 노력해보자!"
    return {"stars": stars, "praise": praise, "successRate": ratio}
