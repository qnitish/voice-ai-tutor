// Mirrors backend/app/schemas.py

export type Color = 'ink' | 'blue' | 'red' | 'green' | 'orange'
export type Point = [number, number]

export type DrawAction =
  | { type: 'text'; id: string; x: number; y: number; text: string; size: 'h1' | 'h2' | 'body' | 'small'; color: Color }
  | { type: 'line'; id: string; start: Point; end: Point; color: Color; dashed: boolean }
  | { type: 'arrow'; id: string; start: Point; end: Point; color: Color; label: string | null }
  | { type: 'rect'; id: string; x: number; y: number; w: number; h: number; color: Color; label: string | null }
  | { type: 'circle'; id: string; cx: number; cy: number; r: number; color: Color; filled: boolean; label: string | null }
  | {
      type: 'angle'
      id: string
      cx: number
      cy: number
      r: number
      start_deg: number
      end_deg: number
      color: Color
      label: string | null
    }
  | { type: 'field_mark'; id: string; x: number; y: number; dir: 'in' | 'out'; color: Color }
  | { type: 'highlight'; id: string; target: string; color: Color }

export interface Clip {
  say: string
  draw: DrawAction[]
  audio_url: string
  duration_ms: number
}

export interface TwinQuestion {
  question: string
  say: string
  options: string[]
  correct_index: number
  explanation: string
  audio_url: string
}

export interface CheckQuestion {
  question: string
  say: string
  options: string[]
  correct_index: number
  concept: string
  explanation: string
  audio_url: string
  // Help after a wrong answer; missing on lessons prepared without the `remedy` stage
  /** Hint per option (null for the correct one), played after a wrong pick */
  why_wrong?: (Spoken | null)[]
  /** Fresh explanation on the doubt board after a second wrong pick */
  reteach?: Clip[]
  /** "Kis part mein confusion hua?", played while the mic is open */
  followup?: Spoken
  /** Same concept, asked a scene later */
  twin?: TwinQuestion | null
}

export interface Scene {
  idx: number
  title: string
  goal: string
  key_terms: string[]
  check_question: CheckQuestion | null
  steps: (Clip & { idx: number })[]
}

export interface Lesson {
  slug: string
  title: string
  version: number
  /** Tutor voice (null = default); fillers and bridges are fetched for it */
  voice: string | null
  voice_gender: 'female' | 'male'
  scenes: Scene[]
}

export interface LessonSummary {
  slug: string
  title: string
  scene_count: number
  version: number
}

export interface Guest {
  id: string
  name: string
  class_level: number | null
  language: string
  weak_concepts: string[]
}

export interface TutorSession {
  id: string
  guest: Guest
  lesson_slug: string
  lesson_version: number
}

export interface Resume {
  session_id: string
  scene_idx: number
  step_idx: number
}

/** A session as saved on the server, for resuming with its earlier doubts. */
export interface SessionState {
  session_id: string
  completed: boolean
  position: { scene_idx: number; step_idx: number } | null
  answered_checks: number[]
  doubts: {
    id: string
    scene_idx: number
    step_idx: number
    question: string
    intent: Intent
    target_scene_idx: number | null
    answered: boolean
    callback: Spoken | null
  }[]
}

export interface LessonProgress {
  slug: string
  title: string
  version: number
  percent: number
  completed: boolean
  checks_correct: number
  checks_total: number
  doubts: number
  sessions: number
  resume: Resume | null
}

export interface Spoken {
  text: string
  audio_url: string
  duration_ms: number
}

export type Intent =
  | 'covered_later'
  | 'not_covered'
  | 'prerequisite'
  | 'confused'
  | 'repeat'
  | 'continue'
  | 'off_topic'
  | 'unclear'
  | 'clarify'
/** "now" plays the answer immediately; "replay" restarts the current step */
export type AnswerWhen = 'none' | 'now' | 'replay' | 'step_end' | 'scene_end' | 'target_scene'

export interface QuestionResult {
  doubt_id: string
  transcript: string
  question: string
  intent: Intent
  target_scene_idx: number | null
  answer_when: AnswerWhen
  ack: Spoken
  callback: Spoken | null
}

export interface CheckResult {
  correct: boolean
  correct_index: number
  explanation: string
  feedback: Spoken
}

export interface SessionSummary {
  lines: string[]
  weak_concepts: string[]
  revise: string[]
  speech: Spoken
}
