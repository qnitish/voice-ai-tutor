import type { Lesson } from '@/lib/types'
import { rahi } from '@/lib/voice'

export type Language = 'hinglish' | 'english'

/** A lesson's or student's language; anything but English (e.g. "hindi") gets the Hinglish lessons. */
export const languageOf = (value: string | null | undefined): Language => (value === 'english' ? 'english' : 'hinglish')

const LINES = {
  hinglish: {
    quickCheck: 'Quick check',
    oneMore: 'Ek aur sawal',
    tryAgain: 'Ek baar phir try karo',
    confusion: 'Kahin confusion hai? Mic dabake batao.',
    next: 'Aage chalein',
    skip: 'Skip',
    summaryTitle: 'Aaj ka summary',
    revise: 'Revise karo',
    noDoubts: 'Koi doubt? Mic button dabake poochho.',
    routing: 'Sun {rahi} hoon…',
    promised: 'Aage aayega',
    queued: 'Scene ke baad',
    answering: 'Abhi',
    answered: 'Done',
    heardNothing: 'Kuch sunai nahi diya. Mic dabake apna sawal bolo.',
    tooShort: 'Thoda lamba bolo, phir mic chhodo.',
    micPermission: 'Microphone permission chahiye sawal poochne ke liye.',
    speakTap: 'Bolo… chup hone par khud bhej denge',
    speakHold: 'Bolo… chhodne par bhej denge',
    thinking: 'Soch {rahi} hoon…',
    askHint: 'Tap or hold to ask (or Space)',
    questionFailed: 'Sawal bhejne mein dikkat hui. Phir se try karo.',
    answerFailed: 'Jawab bhejne mein dikkat hui. Phir se try karo.',
    makingSummary: 'Aaj ka summary bana {rahi} hoon…',
  },
  english: {
    quickCheck: 'Quick check',
    oneMore: 'One more question',
    tryAgain: 'Try once more',
    confusion: 'Confused somewhere? Press the mic and tell me.',
    next: 'Continue',
    skip: 'Skip',
    summaryTitle: "Today's summary",
    revise: 'Revise',
    noDoubts: 'Any doubt? Press the mic button and ask.',
    routing: 'Listening…',
    promised: 'Coming up',
    queued: 'After this scene',
    answering: 'Now',
    answered: 'Done',
    heardNothing: "I couldn't hear anything. Press the mic and ask your question.",
    tooShort: 'Speak a little longer, then release the mic.',
    micPermission: 'Microphone permission is needed to ask questions.',
    speakTap: "Speak… I'll send it when you stop",
    speakHold: "Speak… I'll send it when you release",
    thinking: 'Thinking…',
    askHint: 'Tap or hold to ask (or Space)',
    questionFailed: "Couldn't send your question. Please try again.",
    answerFailed: "Couldn't send your answer. Please try again.",
    makingSummary: "Preparing today's summary…",
  },
} satisfies Record<Language, Record<string, string>>

export type LineKey = keyof (typeof LINES)['hinglish']

/** A UI line in the lesson's language, in the tutor's first person where it has one. */
export function t(lesson: Lesson | null, key: LineKey): string {
  return LINES[languageOf(lesson?.language)][key].replace('{rahi}', rahi(lesson))
}
