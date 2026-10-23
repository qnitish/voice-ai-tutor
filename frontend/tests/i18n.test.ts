import { describe, expect, it } from 'vitest'
import { languageOf, t } from '@/lib/i18n'
import type { Lesson } from '@/lib/types'

const lesson = (language: string, voice_gender: 'female' | 'male' = 'female') =>
  ({ slug: 'x', title: 'X', version: 1, voice: null, voice_gender, language, scenes: [] }) as Lesson

describe('languageOf', () => {
  it('maps English to english and everything else to hinglish', () => {
    expect(languageOf('english')).toBe('english')
    expect(languageOf('hinglish')).toBe('hinglish')
    expect(languageOf('hindi')).toBe('hinglish')
    expect(languageOf(undefined)).toBe('hinglish')
  })
})

describe('t', () => {
  it("uses the lesson's language", () => {
    expect(t(lesson('english'), 'next')).toBe('Continue')
    expect(t(lesson('hinglish'), 'next')).toBe('Aage chalein')
  })

  it("puts the voice's first-person verb into Hinglish lines", () => {
    expect(t(lesson('hinglish', 'male'), 'thinking')).toBe('Soch raha hoon…')
    expect(t(lesson('hinglish', 'female'), 'thinking')).toBe('Soch rahi hoon…')
    expect(t(lesson('english', 'male'), 'thinking')).toBe('Thinking…')
  })

  it('falls back to Hinglish without a lesson', () => {
    expect(t(null, 'summaryTitle')).toBe('Aaj ka summary')
  })
})
