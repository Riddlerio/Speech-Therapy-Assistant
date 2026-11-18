"""목표 음소열과 인식된 음소열을 최소 비용으로 맞춰(편집 거리 정렬) 일치·대치·생략·첨가를 찾는다.
판정 파이프라인(pipeline.py)이 목표 소리를 바르게 냈는지, 다른 소리로 바꿨는지 알아내는 데 쓴다.
"""

from dataclasses import dataclass

from .g2p import Phone


SIMILAR = {frozenset(p) for p in [("ㅅ", "ㅆ"), ("ㅈ", "ㅉ"), ("ㄱ", "ㄲ"), ("ㄷ", "ㄸ"), ("ㅂ", "ㅃ")]}


@dataclass
class Operation:
    """정렬 결과 한 단계. 종류(match·sub·del·ins), 목표·인식 쪽 위치, 비용을 담는다."""
    kind: str
    target_index: int | None
    observed_index: int | None
    cost: float


def align(target: list[Phone], observed: list[Phone]) -> list[Operation]:
    """동적 계획법으로 두 음소열을 정렬해 Operation 목록을 돌려준다. 같은 자리(초성·중성·종성)끼리만 짝짓고, 비슷한 소리(ㅅ/ㅆ 등)는 비용 0.5로 본다.
    분석 결과의 정렬 기록과 대치 소리(substitution) 계산에 쓴다.
    """
    n, m = len(target), len(observed)
    dp = [[float("inf")] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            if i and dp[i - 1][j] + 1 < dp[i][j]:
                dp[i][j], back[i][j] = dp[i - 1][j] + 1, Operation("del", i - 1, None, 1)
            if j and dp[i][j - 1] + 1 < dp[i][j]:
                dp[i][j], back[i][j] = dp[i][j - 1] + 1, Operation("ins", None, j - 1, 1)
            if i and j and target[i - 1].slot == observed[j - 1].slot:
                a, b = target[i - 1].symbol, observed[j - 1].symbol
                cost = 0 if a == b else 0.5 if frozenset((a, b)) in SIMILAR else 1
                if dp[i - 1][j - 1] + cost <= dp[i][j]:
                    dp[i][j], back[i][j] = dp[i - 1][j - 1] + cost, Operation("match" if cost == 0 else "sub", i - 1, j - 1, cost)
    ops = []
    i, j = n, m
    while i or j:
        op = back[i][j]
        if op is None:
            raise ValueError("alignment failed")
        ops.append(op)
        i -= op.target_index is not None
        j -= op.observed_index is not None
    return list(reversed(ops))
