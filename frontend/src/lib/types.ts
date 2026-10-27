// Mirrors backend/app/schemas.py

export type Color = 'ink' | 'blue' | 'red' | 'green' | 'orange'
export type Point = [number, number]

/** Label positions chosen by `engine/layout` (frontend only), keyed by label slot */
export interface Placed {
  labelPos?: Record<string, Point>
}

export type DrawAction = Placed &
  (
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
  // Blocks: drawn with fixed proportions by the board (see engine/geometry.ts)
  | { type: 'wire'; id: string; start: Point; end: Point; current: string | null; color: Color }
  | {
      type: 'charge'
      id: string
      x: number
      y: number
      sign: '+' | '-'
      label: string | null
      velocity_deg: number | null
      force_deg: number | null
      color: Color
    }
  | { type: 'compass'; id: string; x: number; y: number; needle_deg: number; label: string | null }
  | { type: 'magnet'; id: string; x: number; y: number; w: number; h: number; north: 'left' | 'right' | 'top' | 'bottom' }
  | { type: 'axes'; id: string; x: number; y: number; w: number; h: number; x_label: string | null; y_label: string | null; color: Color }
  | {
      type: 'region'
      id: string
      cx: number
      cy: number
      w: number
      h: number
      shape: 'ellipse' | 'rect'
      label: string | null
      curve_label: string | null
      orientation: 'ccw' | 'cw' | null
      color: Color
    }
  | {
      type: 'flow'
      id: string
      x: number
      y: number
      items: string[]
      dir: 'down' | 'right'
      connector: string | null
      size: 'h2' | 'body' | 'small'
      color: Color
    }
  | {
      type: 'chain'
      id: string
      x: number
      y: number
      groups: string[]
      bonds: Bond[]
      branches: { at: number; groups: string[]; dir: 'up' | 'down'; bond: Bond }[]
      color: Color
    }
  | { type: 'ring'; id: string; cx: number; cy: number; r: number; size: 3 | 4 | 5 | 6; aromatic: boolean; label: string | null; color: Color }
  | { type: 'curve'; id: string; points: Point[]; color: Color; dashed: boolean; label: string | null }
  | {
      type: 'body'
      id: string
      x: number
      y: number
      shape: 'ball' | 'box'
      label: string | null
      vectors: { deg: number; label: string; color: Color }[]
      color: Color
    }
  | { type: 'ground'; id: string; x: number; y: number; w: number; label: string | null }
  // Moves earlier items by the equations of motion (engine/motion.ts); v in px/s, a in px/s², +y up
  | {
      type: 'motion'
      id: string
      tracks: { target: string; v: Point; a: Point }[]
      seconds: number
      ground: string | null
      trace: boolean
      ghosts: number
      show_velocity: boolean
      color: Color
    }
  )

export type Bond = '-' | '=' | '≡'

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
  /** What the tutor speaks; UI lines follow it (lib/i18n) */
  language: string
  /** Language, voice, build ("v2") and build note, shown in the header */
  tags?: string[]
  /** Which whiteboard draws it: components/Board (SVG) or components/SkiaBoard (CanvasKit) */
  renderer?: 'svg' | 'skia'
  scenes: Scene[]
  /** 'video': the recorded lecture plays (scenes is empty); see docs/plan/video-lesson-spec.md */
  kind?: LessonKind
  video?: VideoInfo | null
  chapters?: Chapter[]
  checkpoints?: Checkpoint[]
  policy?: PlayerPolicy | null
}

export interface LessonSummary {
  slug: string
  title: string
  scene_count: number
  version: number
  language: string
  /** Language, voice, build ("v2") and build note, shown on the card */
  tags?: string[]
  kind?: LessonKind
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
  /** Video lessons: where playback resumes */
  position_ms?: number | null
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

// ---- Video lessons (mirrors backend/app/schemas.py; docs/plan/video-lesson-spec.md) ----

export type LessonKind = 'generated' | 'video'

export interface VideoInfo {
  url: string
  duration_ms: number
}

export interface Chapter {
  idx: number
  topic: string
  title: string
  summary: string
  key_terms: string[]
  start_ms: number
  end_ms: number
}

export interface ReplayRange {
  start_ms: number
  end_ms: number
}

export interface CheckpointQuestion {
  question: string
  say: string
  audio_url: string
  duration_ms: number
  options: string[]
  correct_index: number
  concept: string
  explanation: string
  /** Hint per option after a wrong pick (null for the correct one) */
  why_wrong: (Spoken | null)[]
  /** The teacher explaining this concept, replayed after a second wrong pick */
  replay: ReplayRange | null
  /** Generated re-explanation on the overlay board, after the replay */
  reteach: Clip[]
  /** "Which part confused you? Ask me, or let's continue." */
  followup: Spoken | null
}

export interface Checkpoint {
  idx: number
  at_ms: number
  chapter_idx: number
  intro: Spoken
  questions: CheckpointQuestion[]
}

export interface PlayerPolicy {
  max_followups: number
  recap_after_turns: number
  recap_after_seconds: number
  rewind_ms: number
  /** An unanswered "Got it?" prompt ends the thread after this long */
  idle_ms: number
}

export type ThreadStatus = 'open' | 'resolved' | 'capped' | 'abandoned'

export interface Thread {
  id: string
  status: ThreadStatus
  turns: number
  audio_ms: number
  can_follow_up: boolean
  /** Played before resuming when the thread was long */
  recap: Spoken | null
  /** A line to play now ("Which part is still unclear?", or the capped "Let's continue…") */
  line: Spoken | null
}

export type VideoAnswerWhen = 'now' | 'chapter_end' | 'target_chapter' | 'replay' | 'none'

export interface VideoQuestionResult {
  doubt_id: string
  thread_id: string
  turn: number
  transcript: string
  question: string
  intent: Intent
  answer_when: VideoAnswerWhen
  target_chapter_idx: number | null
  /** covered_later: when the callback plays */
  target_ms: number | null
  /** repeat: where the video seeks back to */
  replay_from_ms: number | null
  ack: Spoken
  callback: Spoken | null
  thread: Thread
}

export interface AnsweredQuestion {
  checkpoint_idx: number
  question_idx: number
  kind: 'first' | 'retry' | 'skip'
  correct: boolean
}

export interface VideoStateDoubt {
  id: string
  at_ms: number
  thread_id: string | null
  turn: number
  question: string
  intent: string
  answered: boolean
}

export interface VideoSessionState {
  session_id: string
  completed: boolean
  position_ms: number | null
  answered: AnsweredQuestion[]
  doubts: VideoStateDoubt[]
}
