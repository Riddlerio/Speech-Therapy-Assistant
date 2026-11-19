import { describe, expect, it } from 'vitest'
import { detectCapabilities, missingCapabilities, supportsRealMode } from './capabilities'

const fn = () => undefined
const browser = (extra: Record<string, unknown> = {}) => ({
  navigator: { mediaDevices: { getUserMedia: fn } }, isSecureContext: true, AudioContext: fn, ...extra,
})

describe('실제 음성 기능 판별', () => {
  it('마이크·소리 분석·음성 인식을 따로 판단한다', () => {
    expect(detectCapabilities(browser())).toEqual({ MIC_AVAILABLE: true, ACOUSTIC_AVAILABLE: true, ASR_AVAILABLE: false })
    expect(detectCapabilities(browser({ webkitSpeechRecognition: fn })).ASR_AVAILABLE).toBe(true)
    expect(detectCapabilities(browser({ isSecureContext: false })).MIC_AVAILABLE).toBe(false)
    expect(detectCapabilities({}).ACOUSTIC_AVAILABLE).toBe(false)
    expect(detectCapabilities(undefined)).toEqual({ MIC_AVAILABLE: false, ACOUSTIC_AVAILABLE: false, ASR_AVAILABLE: false })
  })

  it('음성 인식이 없어도 지속 발성 게임은 실제 음성으로 할 수 있다', () => {
    const capabilities = detectCapabilities(browser())
    expect(supportsRealMode('daegu_crossing', capabilities)).toBe(true)
    expect(supportsRealMode('conversation_quest', capabilities)).toBe(false)
    expect(missingCapabilities('conversation_quest', capabilities)).toEqual(['ASR_AVAILABLE'])
  })

  it('마이크가 없으면 어떤 게임도 실제 음성으로 시작하지 않는다', () => {
    const capabilities = detectCapabilities({ AudioContext: fn, SpeechRecognition: fn })
    for (const game of ['daegu_crossing', 'conversation_quest'] as const) {
      expect(supportsRealMode(game, capabilities)).toBe(false)
    }
  })
})
