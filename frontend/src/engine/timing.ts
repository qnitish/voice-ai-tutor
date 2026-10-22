/** Where to restart a clip after an interruption, so the student never resumes mid-sentence. */

export const MAX_REWIND_MS = 6000
export const FALLBACK_REWIND_MS = 2000

export function splitSentences(text: string): string[] {
  return text
    .split(/(?<=[.?!।])\s+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

/** Sentence start times, assuming speech time is proportional to character count. */
export function sentenceStarts(text: string, durationMs: number): number[] {
  const sentences = splitSentences(text)
  const total = sentences.reduce((n, s) => n + s.length, 0)
  const starts: number[] = []
  let chars = 0
  for (const s of sentences) {
    starts.push(total ? (chars / total) * durationMs : 0)
    chars += s.length
  }
  return starts
}

export function resumePoint(text: string, durationMs: number, pausedAtMs: number): number {
  if (pausedAtMs <= 0) return 0
  const starts = sentenceStarts(text, durationMs)
  const start = starts.filter((t) => t <= pausedAtMs).at(-1) ?? 0
  if (pausedAtMs - start <= MAX_REWIND_MS) return start
  return Math.max(0, pausedAtMs - FALLBACK_REWIND_MS)
}
