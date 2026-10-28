/**
 * State of the video lesson player (engine/video/videoTutor writes it, components read it).
 * The whiteboard overlay keeps using store/player (drawn, active, clipTime, layer), so Board and SkiaBoard work as is.
 */
import { create } from 'zustand'

import type { MicState } from '@/engine/host'
import type { AnsweredQuestion, CheckResult, Intent, Lesson, SessionSummary, Thread } from '@/lib/types'

export type VideoMode =
  | 'idle' // not started
  | 'playing'
  | 'paused' // paused by the student
  | 'listening' // mic held
  | 'thinking' // question being routed
  | 'answering' // ack / answer / recap playing on the overlay
  | 'thread_prompt' // "Got it?" after an answer, waiting for the student
  | 'checkpoint' // checkpoint card on screen
  | 'summary' // video ended: the summary is being made / read out (mic closed)
  | 'done'

export interface CheckpointView {
  idx: number
  questionIdx: number
  /** intro: intro line playing; answer: first pick; retry: after a hint; reteach: replay/re-teach playing; done: shown */
  stage: 'intro' | 'answer' | 'retry' | 'reteach' | 'done'
  /** Wrong picks so far, marked and disabled */
  wrong: number[]
  result: CheckResult | null
  /** A pick is being judged or a line is playing */
  busy: boolean
  /** After a re-teach: mic open for a doubt about this question */
  followup: boolean
}

export type VideoDoubtStatus = 'routing' | 'promised' | 'queued' | 'answering' | 'answered' | 'dismissed'

export interface VideoDoubtView {
  id: string
  atMs: number
  threadId: string | null
  question: string
  intent: Intent | null
  status: VideoDoubtStatus
}

export interface VideoPlayerState {
  lesson: Lesson | null
  sessionId: string | null
  mode: VideoMode
  /** Video time (ms), updated while playing */
  positionMs: number
  chapterIdx: number
  /** Whiteboard overlay over the paused video (answers, re-teach) */
  overlay: boolean
  caption: string
  /** The open doubt thread, if any */
  thread: Thread | null
  /** thread_prompt / awaiting follow-up: when (Date.now() ms) the idle timer ends the thread */
  promptEndsAt: number | null
  /** After "Ask another": waiting for the student's follow-up question (idle timer runs too) */
  awaitingFollowup: boolean
  /** True while a question is being recorded or routed (the mic button shows listening/thinking) */
  listening: boolean
  thinking: boolean
  checkpoint: CheckpointView | null
  /** Checkpoint answers so far (first/retry/skip) */
  answered: AnsweredQuestion[]
  doubts: VideoDoubtView[]
  /** Set by the engine when the video ends (it calls api.summary) */
  summary: SessionSummary | null
  error: string | null
}

export const initialVideoState: VideoPlayerState = {
  lesson: null,
  sessionId: null,
  mode: 'idle',
  positionMs: 0,
  chapterIdx: 0,
  overlay: false,
  caption: '',
  thread: null,
  promptEndsAt: null,
  awaitingFollowup: false,
  listening: false,
  thinking: false,
  checkpoint: null,
  answered: [],
  doubts: [],
  summary: null,
  error: null,
}

export const useVideo = create<VideoPlayerState>()(() => initialVideoState)

export const setVideo = useVideo.setState

/**
 * Whether the seek bar (and chapter list) is live: while the video plays or is paused, at the "Got it?" prompt (a
 * seek ends the thread) and on a checkpoint card that isn't busy (a seek back leaves the checkpoint for now).
 */
export function canSeek(s: VideoPlayerState): boolean {
  if (s.listening || s.thinking) return false
  if (s.mode === 'checkpoint') return !!s.checkpoint && !s.checkpoint.busy && s.checkpoint.stage !== 'reteach' && s.checkpoint.stage !== 'intro'
  return s.mode === 'playing' || s.mode === 'paused' || s.mode === 'thread_prompt'
}

/**
 * Mic button state for the video page: open while playing, paused, answering or at the "Got it?" prompt, and in a
 * checkpoint only at its follow-up step.
 */
export function micState(s: VideoPlayerState): MicState {
  const enabled = s.checkpoint
    ? s.mode === 'checkpoint' && s.checkpoint.followup && !s.checkpoint.busy
    : ['playing', 'paused', 'answering', 'thread_prompt'].includes(s.mode)
  return { enabled: enabled && !s.thinking, listening: s.listening, thinking: s.thinking }
}
