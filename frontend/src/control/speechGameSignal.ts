/** daegu_crossing: '대구대 건너기'. conversation_quest는 두두 대화의 마이크 지원 확인(capabilities)에 쓴다. */
export type GameKind = 'conversation_quest' | 'daegu_crossing'
export type HoyaAction = 'IDLE' | 'LISTENING' | 'TALKING' | 'CHARGE' | 'BEAM' | 'RELEASE' | 'FLY' | 'LAND' | 'CAST' | 'ATTACK' | 'WALK_TO' | 'PICK_UP' | 'PUT_IN_BAG' | 'WAVE' | 'CHEER' | 'ENCOURAGE'
  // 호야와 대화하기에서만 명시적으로 쓴다.
  | 'THINKING'
