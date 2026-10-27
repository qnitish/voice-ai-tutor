/** The video page's doubt list: how a routed question shows up, and how a resumed session's doubts are rebuilt. */
import type { Intent, VideoAnswerWhen, VideoSessionState } from '@/lib/types'
import type { VideoDoubtStatus, VideoDoubtView } from '@/store/video'

const INTENTS = new Set<Intent>([
  'covered_later',
  'not_covered',
  'prerequisite',
  'confused',
  'repeat',
  'continue',
  'off_topic',
  'unclear',
  'clarify',
  'reply',
  'overview',
])

/** Status right after routing: a "now" answer is queued until it plays. */
export function statusFor(answerWhen: VideoAnswerWhen): VideoDoubtStatus {
  switch (answerWhen) {
    case 'target_chapter':
      return 'promised'
    case 'replay':
      return 'answered'
    case 'none':
      return 'dismissed'
    default:
      return 'queued'
  }
}

/**
 * Earlier doubts of a resumed session. Promised callbacks can't be restored (the state carries no target), so a
 * covered_later or repeat doubt counts as answered; an unanswered answerable doubt is shown as dismissed.
 */
export function restoreDoubts(state: VideoSessionState): VideoDoubtView[] {
  return state.doubts.map((d) => ({
    id: d.id,
    atMs: d.at_ms,
    threadId: d.thread_id,
    question: d.question,
    intent: INTENTS.has(d.intent as Intent) ? (d.intent as Intent) : null,
    status: d.answered || d.intent === 'covered_later' || d.intent === 'repeat' ? 'answered' : 'dismissed',
  }))
}
