/** Pure timeline rules of the video player (unit-tested; implemented per docs/plan/video-lesson-spec.md). */
import type { AnsweredQuestion, Chapter, Checkpoint } from '@/lib/types'

/** A question counts as done once it has a first answer or a skip (a retry alone never happens). */
function questionDone(answered: AnsweredQuestion[], checkpointIdx: number, questionIdx: number): boolean {
  return answered.some(
    (a) => a.checkpoint_idx === checkpointIdx && a.question_idx === questionIdx && (a.kind === 'first' || a.kind === 'skip'),
  )
}

/** Checkpoints whose every question has a first answer or a skip (one with no questions is done). */
export function doneCheckpoints(checkpoints: Checkpoint[], answered: AnsweredQuestion[]): Set<number> {
  const done = new Set<number>()
  for (const cp of checkpoints) {
    if (cp.questions.every((_, qi) => questionDone(answered, cp.idx, qi))) done.add(cp.idx)
  }
  return done
}

const byTime = (checkpoints: Checkpoint[]) => [...checkpoints].sort((a, b) => a.at_ms - b.at_ms)

/** The first not-done checkpoint crossed while playing from prevMs to nowMs (prevMs < at_ms <= nowMs), else null. */
export function dueCheckpoint(prevMs: number, nowMs: number, checkpoints: Checkpoint[], done: Set<number>): Checkpoint | null {
  return byTime(checkpoints).find((cp) => !done.has(cp.idx) && prevMs < cp.at_ms && cp.at_ms <= nowMs) ?? null
}

/** Where a seek from fromMs to toMs lands: back anywhere; forward stops at the first not-done checkpoint passed. */
export function clampSeek(fromMs: number, toMs: number, checkpoints: Checkpoint[], done: Set<number>): number {
  if (toMs <= fromMs) return toMs
  return dueCheckpoint(fromMs, toMs, checkpoints, done)?.at_ms ?? toMs
}

/** Index of the chapter playing at ms (the last chapter starting at or before ms; 0 when none). */
export function chapterAt(ms: number, chapters: Chapter[]): number {
  let idx = 0
  let best = -Infinity
  chapters.forEach((ch, i) => {
    if (ch.start_ms <= ms && ch.start_ms >= best) [idx, best] = [i, ch.start_ms]
  })
  return idx
}

/** Where to resume after pausing at atMs: rewindMs earlier, but not before its chapter's start or 0. */
export function resumePointMs(atMs: number, chapters: Chapter[], rewindMs: number): number {
  const chapterStart = chapters.length ? chapters[chapterAt(atMs, chapters)].start_ms : 0
  return Math.max(chapterStart, atMs - rewindMs, 0)
}

/**
 * Where the video resumes after a checkpoint at atMs: right there, unless a long doubt thread on its card left a
 * recap, which plays over the paused video rewound like after a doubt.
 */
export function resumeAfterCheckpoint(atMs: number, chapters: Chapter[], rewindMs: number, recap: unknown): number {
  return recap ? resumePointMs(atMs, chapters, rewindMs) : atMs
}
