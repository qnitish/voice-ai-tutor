import type { Lesson } from '@/lib/types'

/** "rahi" or "raha" for UI lines in the tutor's first person ("Soch rahi hoon…"), matching the lesson's voice. */
export function rahi(lesson: Lesson | null): string {
  return lesson?.voice_gender === 'male' ? 'raha' : 'rahi'
}
