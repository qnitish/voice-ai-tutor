import { describe, expect, it } from 'vitest'
import { resumeAfterCheckpoint } from '@/engine/video/checkpoints'
import type { Chapter } from '@/lib/types'

const chapters: Chapter[] = [
  { idx: 0, topic: 'Motion', title: 'Free fall', summary: '', key_terms: [], start_ms: 0, end_ms: 200_000 },
  { idx: 1, topic: 'Motion', title: 'Projectiles', summary: '', key_terms: [], start_ms: 200_000, end_ms: 400_000 },
]
const recap = { text: 'So, we were looking at free fall.', audio_url: '/media/r.wav', duration_ms: 3000 }

describe('resumeAfterCheckpoint', () => {
  it('resumes exactly at the checkpoint without a recap', () => {
    expect(resumeAfterCheckpoint(150_000, chapters, 3000, null)).toBe(150_000)
  })

  it('rewinds like after a doubt when a card thread left a recap', () => {
    expect(resumeAfterCheckpoint(150_000, chapters, 3000, recap)).toBe(147_000)
  })

  it('never rewinds before the chapter start', () => {
    expect(resumeAfterCheckpoint(201_000, chapters, 3000, recap)).toBe(200_000)
  })
})
