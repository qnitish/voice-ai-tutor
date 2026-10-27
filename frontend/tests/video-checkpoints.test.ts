import { describe, expect, it } from 'vitest'
import { chapterAt, clampSeek, doneCheckpoints, dueCheckpoint, resumePointMs } from '@/engine/video/checkpoints'
import type { AnsweredQuestion, Chapter, Checkpoint, CheckpointQuestion } from '@/lib/types'

// docs/plan/video-lesson-spec.md 1.1.8, 1.2, 3.1

const spoken = (text: string) => ({ text, audio_url: `/media/${text.length}.wav`, duration_ms: 800 })

function question(concept: string): CheckpointQuestion {
  return {
    question: `Question about ${concept}?`,
    say: `Question about ${concept}?`,
    audio_url: '/media/q.wav',
    duration_ms: 1500,
    options: ['Alpha', 'Beta', 'Gamma', 'Delta'],
    correct_index: 1,
    concept,
    explanation: 'Because.',
    why_wrong: [spoken('a'), null, spoken('c'), spoken('d')],
    replay: null,
    reteach: [],
    followup: null,
  }
}

function cp(idx: number, at_ms: number, questions: number, chapter_idx = 0): Checkpoint {
  return {
    idx,
    at_ms,
    chapter_idx,
    intro: spoken('intro'),
    questions: Array.from({ length: questions }, (_, i) => question(`c${idx}q${i}`)),
  }
}

function answered(checkpoint_idx: number, question_idx: number, kind: AnsweredQuestion['kind'], correct = true): AnsweredQuestion {
  return { checkpoint_idx, question_idx, kind, correct }
}

const chapters: Chapter[] = [
  { idx: 0, topic: 'Motion', title: 'Free fall', summary: '', key_terms: [], start_ms: 0, end_ms: 200_000 },
  { idx: 1, topic: 'Motion', title: 'Projectiles', summary: '', key_terms: [], start_ms: 200_000, end_ms: 400_000 },
  { idx: 2, topic: 'Motion', title: 'Relative velocity', summary: '', key_terms: [], start_ms: 400_000, end_ms: 600_000 },
]

// Checkpoints at 200 s (two questions), 400 s (one question) and 550 s (none)
const checkpoints: Checkpoint[] = [cp(0, 200_000, 2, 0), cp(1, 400_000, 1, 1), cp(2, 550_000, 0, 2)]

describe('doneCheckpoints', () => {
  it('counts a checkpoint without questions as done', () => {
    expect(doneCheckpoints(checkpoints, [])).toEqual(new Set([2]))
  })

  it('is done when every question has a first answer', () => {
    const done = doneCheckpoints(checkpoints, [answered(0, 0, 'first', false), answered(0, 1, 'first')])
    expect(done.has(0)).toBe(true)
  })

  it('is not done while a question is unanswered', () => {
    expect(doneCheckpoints(checkpoints, [answered(0, 0, 'first')]).has(0)).toBe(false)
  })

  it('counts a skip as answered', () => {
    const done = doneCheckpoints(checkpoints, [answered(0, 0, 'first'), answered(0, 1, 'skip', false)])
    expect(done.has(0)).toBe(true)
    expect(doneCheckpoints(checkpoints, [answered(1, 0, 'skip', false)]).has(1)).toBe(true)
  })

  it('does not count a retry on its own', () => {
    expect(doneCheckpoints(checkpoints, [answered(1, 0, 'retry')]).has(1)).toBe(false)
  })

  it('keeps answers to other checkpoints apart', () => {
    const done = doneCheckpoints(checkpoints, [answered(1, 0, 'first'), answered(1, 1, 'first')])
    expect(done.has(1)).toBe(true)
    expect(done.has(0)).toBe(false)
  })

  it('is empty for no checkpoints', () => {
    expect(doneCheckpoints([], [answered(0, 0, 'first')])).toEqual(new Set())
  })
})

describe('dueCheckpoint', () => {
  const none = new Set<number>()

  it('returns the checkpoint crossed between prevMs and nowMs', () => {
    expect(dueCheckpoint(199_000, 200_500, checkpoints, none)?.idx).toBe(0)
  })

  it('includes a checkpoint exactly at nowMs', () => {
    expect(dueCheckpoint(199_000, 200_000, checkpoints, none)?.idx).toBe(0)
  })

  it('excludes a checkpoint exactly at prevMs', () => {
    expect(dueCheckpoint(200_000, 201_000, checkpoints, none)).toBeNull()
  })

  it('returns null when nothing was crossed', () => {
    expect(dueCheckpoint(10_000, 20_000, checkpoints, none)).toBeNull()
    expect(dueCheckpoint(0, 0, checkpoints, none)).toBeNull()
  })

  it('returns the first by time when several were crossed', () => {
    expect(dueCheckpoint(0, 600_000, checkpoints, none)?.idx).toBe(0)
    const shuffled = [checkpoints[1], checkpoints[2], checkpoints[0]]
    expect(dueCheckpoint(0, 600_000, shuffled, none)?.idx).toBe(0)
  })

  it('skips done checkpoints', () => {
    expect(dueCheckpoint(0, 600_000, checkpoints, new Set([0]))?.idx).toBe(1)
    expect(dueCheckpoint(199_000, 200_500, checkpoints, new Set([0]))).toBeNull()
  })

  it('finds the first not-done checkpoint when the video ended (nowMs = Infinity)', () => {
    expect(dueCheckpoint(500_000, Infinity, checkpoints, new Set([0, 1]))?.idx).toBe(2)
    expect(dueCheckpoint(500_000, Infinity, checkpoints, new Set([0, 1, 2]))).toBeNull()
  })
})

describe('clampSeek', () => {
  const none = new Set<number>()

  it('allows seeking back anywhere', () => {
    expect(clampSeek(450_000, 10_000, checkpoints, none)).toBe(10_000)
    expect(clampSeek(450_000, 400_000, checkpoints, none)).toBe(400_000)
  })

  it('allows a forward seek that crosses no checkpoint', () => {
    expect(clampSeek(10_000, 150_000, checkpoints, none)).toBe(150_000)
  })

  it('stops exactly at the first not-done checkpoint passed', () => {
    expect(clampSeek(10_000, 300_000, checkpoints, none)).toBe(200_000)
    expect(clampSeek(10_000, 590_000, checkpoints, none)).toBe(200_000)
  })

  it('passes done checkpoints', () => {
    expect(clampSeek(10_000, 300_000, checkpoints, new Set([0]))).toBe(300_000)
    expect(clampSeek(10_000, 590_000, checkpoints, new Set([0]))).toBe(400_000)
  })

  it('stops at the earliest checkpoint whatever the list order', () => {
    const shuffled = [checkpoints[2], checkpoints[1], checkpoints[0]]
    expect(clampSeek(10_000, 590_000, shuffled, none)).toBe(200_000)
  })

  it('seeking to a checkpoint lands on it', () => {
    expect(clampSeek(10_000, 200_000, checkpoints, none)).toBe(200_000)
  })

  it('a seek to the same time stays there', () => {
    expect(clampSeek(123_000, 123_000, checkpoints, none)).toBe(123_000)
  })
})

describe('chapterAt', () => {
  it('returns the chapter containing ms', () => {
    expect(chapterAt(0, chapters)).toBe(0)
    expect(chapterAt(199_999, chapters)).toBe(0)
    expect(chapterAt(250_000, chapters)).toBe(1)
    expect(chapterAt(450_000, chapters)).toBe(2)
  })

  it('a chapter starts at its start_ms', () => {
    expect(chapterAt(200_000, chapters)).toBe(1)
    expect(chapterAt(400_000, chapters)).toBe(2)
  })

  it('stays on the last chapter after its end', () => {
    expect(chapterAt(999_999, chapters)).toBe(2)
  })

  it('is 0 before the first chapter and without chapters', () => {
    const late: Chapter[] = [{ ...chapters[0], start_ms: 1000, end_ms: 2000 }]
    expect(chapterAt(500, late)).toBe(0)
    expect(chapterAt(500, [])).toBe(0)
  })
})

describe('resumePointMs', () => {
  it('rewinds by rewindMs', () => {
    expect(resumePointMs(250_000, chapters, 3000)).toBe(247_000)
  })

  it('does not rewind before the chapter start', () => {
    expect(resumePointMs(201_000, chapters, 3000)).toBe(200_000)
    expect(resumePointMs(200_000, chapters, 3000)).toBe(200_000)
  })

  it('does not go below 0', () => {
    expect(resumePointMs(1000, chapters, 3000)).toBe(0)
    expect(resumePointMs(0, chapters, 3000)).toBe(0)
    expect(resumePointMs(1000, [], 3000)).toBe(0)
  })

  it('stays put with no rewind', () => {
    expect(resumePointMs(250_000, chapters, 0)).toBe(250_000)
  })

  it('rewinds freely inside the first chapter', () => {
    expect(resumePointMs(150_000, chapters, 3000)).toBe(147_000)
  })
})
