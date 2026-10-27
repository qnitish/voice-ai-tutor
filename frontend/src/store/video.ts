/**
 * State of the video lesson player (engine/video/videoTutor writes it, components read it).
 * The whiteboard overlay keeps using store/player (drawn, active, clipTime, layer), so Board and SkiaBoard work as is.
 */
import { create } from 'zustand'

import type { AnsweredQuestion, CheckResult, Intent, Lesson, Thread } from '@/lib/types'

export type VideoMode =
  | 'idle' // not started
  | 'playing'
  | 'paused' // paused by the student
  | 'listening' // mic held
  | 'thinking' // question being routed
  | 'answering' // ack / answer / recap playing on the overlay
  | 'thread_prompt' // "Got it?" after an answer, waiting for the student
  | 'checkpoint' // checkpoint card on screen
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
  checkpoint: CheckpointView | null
  /** Checkpoint answers so far (first/retry/skip) */
  answered: AnsweredQuestion[]
  doubts: VideoDoubtView[]
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
  checkpoint: null,
  answered: [],
  doubts: [],
  error: null,
}

export const useVideo = create<VideoPlayerState>()(() => initialVideoState)

export const setVideo = useVideo.setState
