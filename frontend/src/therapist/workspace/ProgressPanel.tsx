import { Link } from 'react-router-dom'
import ConversationInsights from '../ConversationInsights'
import { formatDate, formatDateTime } from '../formatTime'
import type { GoalTrends } from '../insights'
import GoalTrendChart from '../charts/GoalTrendChart'
import { sessionSourceText, sessionStatusText } from '../clinicalLabels'
import { STATUS_LABELS } from '../planning'
import type { PlanningContext, SessionPlan } from '../planning'
import { EvidenceSummary } from './NextSessionPanel'

export interface SessionRow { id: string; mode: string; isSeed: boolean; startedAt: string; status: string }
export interface RuleRow { id: string; rule_type: string; params: Record<string, unknown> }

type Props = {
  childId: string; context: PlanningContext; trends: GoalTrends | null; sessions: SessionRow[]; plans: SessionPlan[]
  rules: RuleRow[]; onDeactivate: (id: string) => void
}

/** 경과 · 기록. 치료사 규칙(발화 피드백에서 생긴 것)은 접힌 고급 정보로 옮겨 첫 화면을 가볍게 한다. */
export default function ProgressPanel({ childId, context, trends, sessions, plans, rules, onDeactivate }: Props) {
  const pendingSessions = sessions.filter(session => session.mode === 'real' && !session.isSeed)
  return <>
    <GoalTrendChart data={trends} />
    <ConversationInsights key={childId} childId={childId} />
    <details className="card"><summary>다음 회기 제안에 사용되는 기존 근거 요약</summary><EvidenceSummary metrics={context.metrics} /></details>
    <section className="card"><h2>검토 대기 임상 관찰</h2>
      <p data-pending-review={context.evidenceAvailability.pendingReviewN}>치료사 확인을 기다리는 실제 음성 관찰 {context.evidenceAvailability.pendingReviewN}건. 확인하거나 교정한 관찰만 다음 회기 근거가 됩니다.</p>
      {context.evidenceAvailability.pendingReviewN > 0 && <ul>{pendingSessions.slice(0, 5).map(session => <li key={session.id}><Link to={`/therapist/sessions/${session.id}`}>{formatDateTime(session.startedAt)} 회기 관찰 검토</Link></li>)}</ul>}
      <p className="muted">DEMO·샘플 관찰 {context.evidenceAvailability.demoExcludedN}건은 근거에서 제외됩니다.</p>
    </section>
    <section className="card"><h2>최근 회기</h2>{sessions.length ? <ul>{sessions.map(session => <li key={session.id}><Link to={`/therapist/sessions/${session.id}`}>{formatDateTime(session.startedAt)} · {sessionSourceText(session)} · {sessionStatusText(session.status)}</Link></li>)}</ul> : <p>회기 기록이 없습니다.</p>}</section>
    <section className="card"><h2>이전 회기 계획</h2>{plans.length ? <table><thead><tr><th>rev.</th><th>상태</th><th>목표</th><th>활동</th><th>작성</th></tr></thead><tbody>{plans.map(plan => <tr key={plan.id}><td>{plan.revision}</td><td>{STATUS_LABELS[plan.status]}</td><td>v{plan.goalVersion} · /{plan.targetPhoneme}/</td><td>{plan.steps.length}개</td><td>{formatDate(plan.createdAt)}</td></tr>)}</tbody></table> : <p>작성한 계획이 없습니다.</p>}</section>
    <details className="card advanced"><summary>고급 정보: 적용 규칙</summary>
      <p className="muted">이 규칙은 예전 게임 기록용이며 현재 대구대 건너기·대화 판정에는 쓰이지 않습니다.</p>
      <h3>적용 규칙</h3>{rules.length ? rules.map(rule => <p key={rule.id}>{rule.rule_type} · {JSON.stringify(rule.params)} <button onClick={() => onDeactivate(rule.id)}>비활성화</button></p>) : <p>적용 중인 규칙이 없습니다.</p>}
    </details>
  </>
}
