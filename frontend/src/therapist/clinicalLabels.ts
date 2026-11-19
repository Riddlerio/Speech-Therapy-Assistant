const STATE_TEXT: Record<string, string> = { PENDING: '검토 전', CONFIRMED: '확인', CORRECTED: '교정', REJECTED: '거부' }

export function verificationText(state: string): string {
  if (state.startsWith('DEMO_')) return `DEMO 검토: ${STATE_TEXT[state.slice(5)] || state} (임상 검증 아님)`
  return STATE_TEXT[state] || state
}

/** 회기 출처. 입력 모드(mode)와 대상(isSeed)은 다른 축이다. seed 아동의 회기는 모드와 관계없이 샘플이다. */
export function sessionSourceText(session: { mode?: string; isSeed?: boolean }): '샘플' | '실제' | 'DEMO' {
  return session.isSeed ? '샘플' : session.mode === 'real' ? '실제' : 'DEMO'
}

/** 회기 진행 상태. 서버 값(active·completed)을 화면에 영어로 보이지 않게 한다. */
export function sessionStatusText(status?: string | null): string {
  return status === 'completed' ? '완료' : status === 'active' ? '진행 중' : '상태 미상'
}

/** 목표 버전이 어디서 왔는지. */
export const GOAL_SOURCE_LABELS: Record<string, string> = { manual: '치료사 입력', seed: '샘플', recommendation: '추천 수락', recommendation_modified: '추천 수정 반영', demo_rehearsal: '시연용' }
