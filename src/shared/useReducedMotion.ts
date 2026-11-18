import { useEffect, useState } from 'react'

/** 기기의 '움직임 줄이기' 설정을 지금 한 번 읽는다. */
export const prefersReducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** 기기의 '움직임 줄이기' 설정. 바뀌면 바로 따른다(빛의 마법 장면과 같은 규칙). */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion)
  useEffect(() => {
    const query = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!query) return
    const change = () => setReduced(query.matches)
    change()
    query.addEventListener('change', change)
    return () => query.removeEventListener('change', change)
  }, [])
  return reduced
}
