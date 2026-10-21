import { create } from 'zustand'

import type { CheckResult, DrawAction, Intent, Lesson, SessionSummary } from '@/lib/types'

export type Mode =
  | 'idle' // not started yet
  | 'playing'
  | 'paused' // paused by the student
  | 'listening' // mic held down
  | 'thinking' // waiting for the question to be routed
  | 'answering' // playing an answer on the doubt board
  | 'check' // check question on screen
  | 'summary'
  | 'done'

/** A draw action placed on the current clip's timeline (ms from clip start). */
export interface TimedAction {
  action: DrawAction
  start: number
  end: number
}

export type DoubtStatus = 'routing' | 'promised' | 'queued' | 'answering' | 'answered' | 'dismissed'

export interface DoubtView {
  id: string
  question: string
  intent: Intent | null
  targetSceneIdx: number | null
  status: DoubtStatus
}

export interface CheckView {
  sceneIdx: number
  result: CheckResult | null
  chosen: number | null
}

interface PlayerState {
  lesson: Lesson | null
  sessionId: string | null
  sceneIdx: number
  stepIdx: number
  mode: Mode
  layer: 'lesson' | 'doubt'
  /** Fully drawn items on the visible board */
  drawn: DrawAction[]
  /** Items animating with the current clip */
  active: TimedAction[]
  clipTime: number
  caption: string
  doubts: DoubtView[]
  check: CheckView | null
  summary: SessionSummary | null
  error: string | null
}

export const initialPlayerState: PlayerState = {
  lesson: null,
  sessionId: null,
  sceneIdx: 0,
  stepIdx: 0,
  mode: 'idle',
  layer: 'lesson',
  drawn: [],
  active: [],
  clipTime: 0,
  caption: '',
  doubts: [],
  check: null,
  summary: null,
  error: null,
}

export const usePlayer = create<PlayerState>()(() => initialPlayerState)

export const setPlayer = usePlayer.setState

export function updateDoubt(id: string, patch: Partial<DoubtView>) {
  setPlayer((s) => ({ doubts: s.doubts.map((d) => (d.id === id ? { ...d, ...patch } : d)) }))
}
