import axios from 'axios'

import { reportError } from '@/lib/telemetry'
import type {
  CheckResult,
  Clip,
  Guest,
  Lesson,
  LessonProgress,
  LessonSummary,
  QuestionResult,
  Resume,
  SessionState,
  SessionSummary,
  Spoken,
  Thread,
  TutorSession,
  VideoCheckResult,
  VideoQuestionResult,
  VideoSessionState,
} from '@/lib/types'

const http = axios.create({ baseURL: '/api' })
// Video calls the player waits on: a hung request must not leave it stuck (the caller then moves the lesson on).
// Routing includes STT and an LLM call; closing a thread may make its recap.
const QUESTION_TIMEOUT_MS = 60_000
const THREAD_TIMEOUT_MS = 45_000

// Server errors and network failures are reported as session events (telemetry.ts); the
// report itself goes through /events, which is left out so a failing report can't loop.
http.interceptors.response.use(undefined, (error: unknown) => {
  if (axios.isAxiosError(error) && !axios.isCancel(error)) {
    const url = error.config?.url ?? ''
    const status = error.response?.status
    if (!url.includes('/events') && (status === undefined || status >= 500)) {
      reportError('api.request', error, { url, method: error.config?.method, status: status ?? null })
    }
  }
  return Promise.reject(error)
})

export const api = {
  listLessons: () => http.get<LessonSummary[]>('/lessons').then((r) => r.data),
  getLesson: (slug: string) => http.get<Lesson>(`/lessons/${slug}`).then((r) => r.data),
  fillers: (voice: string | null, language: string) =>
    http.get<Spoken[]>('/fillers', { params: { voice, language } }).then((r) => r.data),
  bridges: (voice: string | null, language: string) =>
    http.get<Spoken[]>('/bridges', { params: { voice, language } }).then((r) => r.data),

  createGuest: (body: { name: string; class_level: number | null; language: string }) =>
    http.post<Guest>('/guests', body).then((r) => r.data),
  getGuest: (id: string) => http.get<Guest>(`/guests/${id}`).then((r) => r.data),
  setLanguage: (id: string, language: string) => http.patch<Guest>(`/guests/${id}`, { language }).then((r) => r.data),
  createSession: (guest_id: string, lesson_slug: string) =>
    http.post<TutorSession>('/sessions', { guest_id, lesson_slug }).then((r) => r.data),
  progress: (guestId: string) => http.get<LessonProgress[]>(`/guests/${guestId}/progress`).then((r) => r.data),
  resume: (guestId: string, slug: string) =>
    http.get<Resume | null>(`/guests/${guestId}/lessons/${slug}/resume`).then((r) => r.data),
  sessionState: (sessionId: string) => http.get<SessionState>(`/sessions/${sessionId}/state`).then((r) => r.data),
  savePosition: (sessionId: string, sceneIdx: number, stepIdx: number) =>
    http.post(`/sessions/${sessionId}/position`, { scene_idx: sceneIdx, step_idx: stepIdx }),

  /** followUpOf: the doubt whose answer was playing when the student asked */
  askQuestion: (sessionId: string, audio: Blob, sceneIdx: number, stepIdx: number, duringCheck = false, followUpOf: string | null = null) => {
    const form = new FormData()
    form.append('audio', audio, 'question.webm')
    form.append('scene_idx', String(sceneIdx))
    form.append('step_idx', String(stepIdx))
    form.append('during_check', String(duringCheck))
    if (followUpOf) form.append('follow_up_of', followUpOf)
    return http.post<QuestionResult>(`/sessions/${sessionId}/questions`, form).then((r) => r.data)
  },
  /** kind: first answer, a second try after the hint, or the twin question */
  recordAnswer: (sessionId: string, sceneIdx: number, chosenIndex: number, kind: 'first' | 'retry' | 'twin' = 'first') =>
    http
      .post<CheckResult>(`/sessions/${sessionId}/check${kind === 'first' ? '' : `/${kind}`}`, {
        scene_idx: sceneIdx,
        chosen_index: chosenIndex,
      })
      .then((r) => r.data),
  skipCheck: (sessionId: string, sceneIdx: number, twin: boolean) =>
    http.post(`/sessions/${sessionId}/check/skip`, { scene_idx: sceneIdx, twin }),
  summary: (sessionId: string) => http.post<SessionSummary>(`/sessions/${sessionId}/summary`).then((r) => r.data),

  // ---- Video lessons (docs/plan/video-lesson-spec.md) ----
  /** threadId: continue that doubt thread (a follow-up); checkpoint: asked from a checkpoint's follow-up prompt */
  videoQuestion: (sessionId: string, audio: Blob, atMs: number, threadId: string | null = null, checkpoint: { idx: number; question_idx: number } | null = null) => {
    const form = new FormData()
    form.append('audio', audio, 'question.webm')
    form.append('at_ms', String(Math.round(atMs)))
    if (threadId) form.append('thread_id', threadId)
    if (checkpoint) {
      form.append('checkpoint_idx', String(checkpoint.idx))
      form.append('question_idx', String(checkpoint.question_idx))
    }
    return http.post<VideoQuestionResult>(`/sessions/${sessionId}/video/questions`, form, { timeout: QUESTION_TIMEOUT_MS }).then((r) => r.data)
  },
  /** "Got it" (understood) or "Ask another" after an answer */
  threadReply: (threadId: string, understood: boolean) =>
    http.post<Thread>(`/threads/${threadId}/reply`, { understood }, { timeout: THREAD_TIMEOUT_MS }).then((r) => r.data),
  /** The "Got it?" prompt went unanswered (policy.idle_ms), or answering failed ("error": the reply has a sorry line) */
  threadAbandon: (threadId: string, reason: 'idle' | 'error' = 'idle') =>
    http.post<Thread>(`/threads/${threadId}/abandon`, { reason }, { timeout: THREAD_TIMEOUT_MS }).then((r) => r.data),
  /** A promised doubt is due: it gets an open thread of its own, as that thread's answered turn */
  deliverDoubt: (doubtId: string) =>
    http.post<Thread>(`/doubts/${doubtId}/deliver`, null, { timeout: THREAD_TIMEOUT_MS }).then((r) => r.data),
  saveVideoPosition: (sessionId: string, atMs: number) =>
    http.post(`/sessions/${sessionId}/video/position`, { at_ms: Math.round(atMs) }),
  checkpointAnswer: (sessionId: string, checkpointIdx: number, questionIdx: number, chosenIndex: number, kind: 'first' | 'retry' = 'first') =>
    http
      .post<VideoCheckResult>(`/sessions/${sessionId}/checkpoints/${checkpointIdx}/answer`, { question_idx: questionIdx, chosen_index: chosenIndex, kind })
      .then((r) => r.data),
  /** The Solution button: routed like a spoken "show me the solution" (threadId: the open thread at the question) */
  checkpointSolution: (sessionId: string, checkpointIdx: number, questionIdx: number, threadId: string | null = null) =>
    http
      .post<VideoQuestionResult>(`/sessions/${sessionId}/checkpoints/${checkpointIdx}/solution`, { question_idx: questionIdx, thread_id: threadId }, { timeout: QUESTION_TIMEOUT_MS })
      .then((r) => r.data),
  checkpointSkip: (sessionId: string, checkpointIdx: number, questionIdx: number) =>
    http.post(`/sessions/${sessionId}/checkpoints/${checkpointIdx}/skip`, { question_idx: questionIdx }),
  videoState: (sessionId: string) => http.get<VideoSessionState>(`/sessions/${sessionId}/video/state`).then((r) => r.data),
  /** Client-side events and errors, stored with the session (app.telemetry) */
  clientEvent: (event: { session_id?: string | null; thread_id?: string | null; doubt_id?: string | null; kind: string; level?: 'info' | 'warn' | 'error'; data?: Record<string, unknown> }) =>
    http.post('/events', event).catch(() => {}),
}

/**
 * Stream an answer as NDJSON. axios can't read a response body incrementally
 * in the browser, so this one call uses fetch.
 */
export async function* streamAnswer(doubtId: string, signal?: AbortSignal): AsyncGenerator<Clip> {
  const resp = await fetch(`/api/doubts/${doubtId}/answer`, { signal })
  if (!resp.ok || !resp.body) throw new Error(`Answer stream failed: ${resp.status}`)
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += value
    let nl: number
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim()
      buf = buf.slice(nl + 1)
      if (line) yield JSON.parse(line) as Clip
    }
  }
  if (buf.trim()) yield JSON.parse(buf) as Clip
}
