"""치료사 회기 계획 그래프(LangGraph). 한 방향 6단계이며 루프·재시도·도구 호출·다중 에이전트는 없다.
LLM은 GenerateSummary 한 곳에서만, 설정된 경우에만 부른다. 나머지는 모두 결정적 Python이다.
DB 세션과 아동 id는 state가 아니라 config로 넘긴다. 선택적 LangSmith 추적을 켜도 state에는
이름·play code·transcript·치료사 메모가 없다.
"""
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..models import TrainingGoal
from .evidence import calculate_metrics, filter_verified, latest_decisions, load_observations, recent_real_sessions
from .proposal import propose_plan
from .summary import build_context, finalize, llm_summary, template_summary, validate_summary


class PlanningDeps:
    """state 밖에서 노드에 넘기는 의존성(DB 세션, 아동 id, LLM 사용 여부·클라이언트, 불러온 회기·관찰·결정). 추적 metadata로 직렬화되지 않는다.
    개인정보가 추적 기록에 남지 않도록 state와 분리해 둔다.
    """

    def __init__(self, db, child_id: str, use_llm: bool = True, llm_client=None):
        """의존성 값을 저장하고, 단계 사이에 넘길 회기·관찰·결정 목록을 빈 값으로 준비한다.
        run_planning이 그래프를 실행하기 전에 만든다.
        """
        self.db, self.child_id, self.use_llm, self.llm_client = db, child_id, use_llm, llm_client
        self.observations: list = []
        self.decisions: dict = {}
        self.sessions: list = []


class PlanningState(TypedDict, total=False):
    """그래프 단계 사이에 전달되는 상태(목표, 확인된 근거, 검토 건수, 지표, 제안, 요약과 그 출처, 거절 사유)."""
    goal: dict
    verified: list[dict]
    review: dict
    metrics: dict
    proposal: dict
    summary: dict
    summary_source: str
    rejection: str | None


def goal_view(goal: TrainingGoal) -> dict:
    """계획에 필요한 목표 필드만 dict로 뽑는다. note·child_id는 담지 않는다.
    그래프 state에 개인정보나 자유 메모가 들어가지 않게 하려고 있다.
    """
    return {"goalId": goal.id, "version": goal.version, "targetPhoneme": goal.target_phoneme,
            "wordPosition": goal.word_position, "level": goal.level, "minLevel": goal.min_level,
            "sessionDurationMin": goal.session_duration_min, "repetitionTarget": goal.repetition_target,
            "preferredCue": goal.preferred_cue, "priorityTargets": list(goal.priority_targets or []),
            "excludedWords": list(goal.excluded_words or [])}


def _deps(config) -> PlanningDeps:
    """노드 config에서 PlanningDeps를 꺼낸다.
    모든 노드가 DB와 불러온 자료에 접근할 때 쓰는 짧은 도우미다.
    """
    return config["configurable"]["deps"]


def load_planning_context(state: PlanningState, config) -> PlanningState:
    """1단계: 최근 실제 회기, 그 관찰, 관찰별 마지막 치료사 결정을 불러와 deps에 저장한다.
    state에는 아무것도 넣지 않아 원자료가 추적에 남지 않게 한다.
    """
    deps = _deps(config)
    deps.sessions = recent_real_sessions(deps.db, deps.child_id)
    deps.observations = load_observations(deps.db, deps.child_id, deps.sessions)
    deps.decisions = latest_decisions(deps.db, deps.observations)
    return {}


def filter_verified_evidence(state: PlanningState, config) -> PlanningState:
    """2단계: 치료사가 확인·교정한 관찰만 근거로 고르고 검토 대기·거절 건수를 state에 넣는다."""
    deps = _deps(config)
    verified, review = filter_verified(deps.observations, deps.decisions)
    return {"verified": verified, "review": review}


def calculate(state: PlanningState, config) -> PlanningState:
    """3단계: 확인된 근거로 결정적 지표를 계산해 state에 넣는다."""
    deps = _deps(config)
    return {"metrics": calculate_metrics(deps.observations, state["verified"], state["review"], deps.sessions)}


def generate_plan_proposal(state: PlanningState, config) -> PlanningState:
    """4단계: 목표와 확인된 지표로 다음 회기 계획 초안을 만든다(proposal.propose_plan)."""
    return {"proposal": propose_plan(state["goal"], state["metrics"])}


def generate_summary(state: PlanningState, config) -> PlanningState:
    """5단계: 설정돼 있으면 LLM으로 요약 초안을 만들고, 아니거나 실패하면 결정적 템플릿 요약을 쓴다.
    요약 출처(LLM·TEMPLATE)를 함께 기록한다.
    """
    deps = _deps(config)
    context = build_context(state["goal"], state["metrics"], state["proposal"])
    draft = llm_summary(context, deps.llm_client) if deps.use_llm else None
    if draft is None:
        return {"summary": template_summary(state["metrics"], state["proposal"]).model_dump(by_alias=True),
                "summary_source": "TEMPLATE", "rejection": None}
    return {"summary": draft.model_dump(by_alias=True), "summary_source": "LLM", "rejection": None}


def validate_output(state: PlanningState, config) -> PlanningState:
    """6단계: LLM 요약이면 숫자·표현을 검증해 통과하면 다듬어 쓰고, 통과하지 못하면 다시 부르지 않고 템플릿으로 대체한다.
    거절 사유는 rejection에 남긴다. 템플릿 요약이면 아무것도 하지 않는다.
    """
    if state["summary_source"] != "LLM":
        return {}
    from .schemas import SummaryOutput
    context = build_context(state["goal"], state["metrics"], state["proposal"])
    draft = SummaryOutput.model_validate(state["summary"])
    reason = validate_summary(draft, context)
    fallback = template_summary(state["metrics"], state["proposal"])
    if reason is None:
        return {"summary": finalize(draft, fallback).model_dump(by_alias=True)}
    # 검증을 통과하지 못한 LLM 문장은 보여 주지 않는다. 다시 부르지 않고 템플릿으로 대체한다.
    return {"summary": fallback.model_dump(by_alias=True), "summary_source": "TEMPLATE", "rejection": reason}


def build_graph():
    """6개 노드를 순서대로 한 줄로 이은 LangGraph를 만들고 컴파일한다.
    모듈을 불러올 때 한 번 만들어 PLANNING_GRAPH로 재사용한다.
    """
    graph = StateGraph(PlanningState)
    nodes = [("LoadPlanningContext", load_planning_context), ("FilterVerifiedEvidence", filter_verified_evidence),
             ("CalculateMetrics", calculate), ("GeneratePlanProposal", generate_plan_proposal),
             ("GenerateSummary", generate_summary), ("ValidateOutput", validate_output)]
    previous = START
    for name, node in nodes:
        graph.add_node(name, node)
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()


PLANNING_GRAPH = build_graph()


def run_planning(db, child_id: str, goal: TrainingGoal, use_llm: bool = True, llm_client=None) -> dict[str, Any]:
    """의존성을 만들고 그래프를 실행해 최종 state와 불러온 관찰 목록을 돌려준다.
    치료사 회기 계획 API(therapist_planning/api.py)가 계획 초안을 만들 때 부른다.
    """
    deps = PlanningDeps(db, child_id, use_llm, llm_client)
    state = PLANNING_GRAPH.invoke({"goal": goal_view(goal)}, config={"configurable": {"deps": deps}})
    return {**state, "observations": deps.observations}
