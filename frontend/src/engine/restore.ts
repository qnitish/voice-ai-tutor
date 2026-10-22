/** Rebuild a resumed session's doubts: what was answered, what is still promised, what to answer again. */
import type { SessionState, Spoken } from '@/lib/types'
import type { DoubtView } from '@/store/player'

export interface Restored {
  doubts: (Omit<DoubtView, 'status' | 'intent'> & {
    intent: SessionState['doubts'][number]['intent']
    status: 'promised' | 'queued' | 'answered' | 'dismissed'
  })[]
  callbacks: { sceneIdx: number; doubtId: string; spoken: Spoken }[]
  /** Unanswered doubts, answered again at the end of the resumed scene */
  redeliver: string[]
  /** Scenes whose check was already answered in this session (answering again is a 409) */
  answeredChecks: number[]
}

const REDELIVER = new Set(['not_covered', 'prerequisite', 'confused'])

export function restoreSession(state: SessionState, resumeSceneIdx: number): Restored {
  const out: Restored = { doubts: [], callbacks: [], redeliver: [], answeredChecks: [...state.answered_checks] }
  for (const d of state.doubts) {
    let status: Restored['doubts'][number]['status']
    if (d.answered) status = 'answered'
    else if (d.intent === 'covered_later' && d.callback && d.target_scene_idx !== null && d.target_scene_idx > resumeSceneIdx) {
      status = 'promised'
      out.callbacks.push({ sceneIdx: d.target_scene_idx, doubtId: d.id, spoken: d.callback })
    } else if (d.intent === 'covered_later' || d.intent === 'repeat') status = 'answered'
    else if (REDELIVER.has(d.intent)) {
      status = 'queued'
      out.redeliver.push(d.id)
    } else status = 'dismissed'
    out.doubts.push({ id: d.id, question: d.question, intent: d.intent, targetSceneIdx: d.target_scene_idx, status })
  }
  return out
}
