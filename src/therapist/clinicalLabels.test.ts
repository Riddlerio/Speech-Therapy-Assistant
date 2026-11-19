import { describe, expect, it } from 'vitest'
import { INDEPENDENCE_LABELS, JUDGMENT_METHODS, MEASUREMENT_SOURCE_LABELS, PROVENANCE_LABELS, QUALITY_LABELS } from './insights'
import { ACTIVITY_LABELS } from './planning'
import { GOAL_SOURCE_LABELS, sessionSourceText, sessionStatusText, verificationText } from './clinicalLabels'

describe('치료사 근거 표시', () => {
  it('DEMO 검토 상태를 임상 검증으로 표시하지 않는다', () => {
    expect(verificationText('DEMO_CONFIRMED')).toBe('DEMO 검토: 확인 (임상 검증 아님)')
    expect(verificationText('CONFIRMED')).toBe('확인')
  })
})

describe('회기 출처 표시', () => {
  it('seed 아동의 회기는 실제 마이크를 써도 샘플이다', () => {
    expect(sessionSourceText({ mode: 'real', isSeed: true })).toBe('샘플')
    expect(sessionSourceText({ mode: 'demo', isSeed: true })).toBe('샘플')
    expect(sessionSourceText({ mode: 'real', isSeed: false })).toBe('실제')
    expect(sessionSourceText({ mode: 'demo', isSeed: false })).toBe('DEMO')
  })
})

describe('회기 상태 이름표', () => {
  it('서버 상태 값을 한국어로 보인다', () => {
    expect(sessionStatusText('completed')).toBe('완료')
    expect(sessionStatusText('active')).toBe('진행 중')
    expect(sessionStatusText(undefined)).toBe('상태 미상')
  })
})

describe('관찰 카드 이름표', () => {
  it('독립성·음질·음향 출처를 영어 코드 대신 한국어로 보인다', () => {
    expect([INDEPENDENCE_LABELS.MODELED, INDEPENDENCE_LABELS.INDEPENDENT, INDEPENDENCE_LABELS.UNKNOWN]).toEqual(['시범 후', '혼자', '미기록'])
    expect(QUALITY_LABELS.UNKNOWN).toBe('미측정')
    expect(MEASUREMENT_SOURCE_LABELS.CLIENT_REPORTED).toBe('아동 기기 측정값')
  })
})

describe('목표 이력 이름표', () => {
  it('목표 출처를 한국어로 보인다', () => {
    expect(GOAL_SOURCE_LABELS.demo_rehearsal).toBe('시연용')
  })
})

describe('자동 추정 표기(Phase 2 AI 명칭 정리)', () => {
  it('규칙·기준값 결과를 AI라 부르지 않고, 모든 활동에 판정 방식을 적는다', () => {
    expect(PROVENANCE_LABELS.AI).toBe('자동 추정')
    for (const activity of Object.keys(ACTIVITY_LABELS)) expect(JUDGMENT_METHODS[activity], activity).toMatch(/근사/)
  })
})
