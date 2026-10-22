/** What happens after a wrong check answer: try again after a hint, re-teach, or just show the answer. */
import type { CheckQuestion } from '@/lib/types'

export function wrongNext(q: CheckQuestion, attempt: 'first' | 'retry'): 'retry' | 'reteach' | 'reveal' {
  if (attempt === 'first' && q.why_wrong?.some((h) => h !== null)) return 'retry'
  return q.reteach?.length ? 'reteach' : 'reveal'
}

/** The scene after whose end a wrongly answered scene's twin question is asked. */
export function twinSlot(sceneCount: number, sceneIdx: number): number | 'end' {
  return sceneIdx + 1 < sceneCount ? sceneIdx + 1 : 'end'
}
