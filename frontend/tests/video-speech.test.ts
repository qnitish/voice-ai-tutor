import { describe, expect, it } from 'vitest'

import { captionMs } from '@/engine/video/speech'

// A fixed line whose TTS failed arrives as text only: its caption stays up for a while instead of being skipped.
describe('captionMs', () => {
  it('shows a short line for at least 1.2 s', () => {
    expect(captionMs('')).toBe(1200)
    expect(captionMs('Okay.')).toBe(1200)
  })

  it('scales with the text at about 60 ms per character', () => {
    expect(captionMs('x'.repeat(20))).toBe(1200)
    expect(captionMs('x'.repeat(50))).toBe(3000)
  })
})
