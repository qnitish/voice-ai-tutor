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

export interface CheckQuestion {
  question: string
  say: string
  options: string[]
  correct_index: number
  concept: string
  explanation: string
  audio_url: string
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
  scenes: Scene[]
}

export interface LessonSummary {
  slug: string
  title: string
  scene_count: number
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
}

export interface Spoken {
  text: string
  audio_url: string
  duration_ms: number
}

export type Intent = 'covered_later' | 'not_covered' | 'repeat' | 'off_topic' | 'unclear'
export type AnswerWhen = 'none' | 'step_end' | 'scene_end' | 'target_scene'

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
