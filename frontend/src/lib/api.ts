import axios from 'axios'

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
  TutorSession,
} from '@/lib/types'

const http = axios.create({ baseURL: '/api' })

export const api = {
  listLessons: () => http.get<LessonSummary[]>('/lessons').then((r) => r.data),
  getLesson: (slug: string) => http.get<Lesson>(`/lessons/${slug}`).then((r) => r.data),
  fillers: (voice: string | null) => http.get<Spoken[]>('/fillers', { params: { voice } }).then((r) => r.data),
  bridges: (voice: string | null) => http.get<Spoken[]>('/bridges', { params: { voice } }).then((r) => r.data),

  createGuest: (body: { name: string; class_level: number | null; language: string }) =>
    http.post<Guest>('/guests', body).then((r) => r.data),
  getGuest: (id: string) => http.get<Guest>(`/guests/${id}`).then((r) => r.data),
  createSession: (guest_id: string, lesson_slug: string) =>
    http.post<TutorSession>('/sessions', { guest_id, lesson_slug }).then((r) => r.data),
  progress: (guestId: string) => http.get<LessonProgress[]>(`/guests/${guestId}/progress`).then((r) => r.data),
  resume: (guestId: string, slug: string) =>
    http.get<Resume | null>(`/guests/${guestId}/lessons/${slug}/resume`).then((r) => r.data),
  sessionState: (sessionId: string) => http.get<SessionState>(`/sessions/${sessionId}/state`).then((r) => r.data),
  savePosition: (sessionId: string, sceneIdx: number, stepIdx: number) =>
    http.post(`/sessions/${sessionId}/position`, { scene_idx: sceneIdx, step_idx: stepIdx }),

  askQuestion: (sessionId: string, audio: Blob, sceneIdx: number, stepIdx: number, duringCheck = false) => {
    const form = new FormData()
    form.append('audio', audio, 'question.webm')
    form.append('scene_idx', String(sceneIdx))
    form.append('step_idx', String(stepIdx))
    form.append('during_check', String(duringCheck))
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
}

/**
 * Stream an answer as NDJSON. axios can't read a response body incrementally
 * in the browser, so this one call uses fetch.
 */
export async function* streamAnswer(doubtId: string): AsyncGenerator<Clip> {
  const resp = await fetch(`/api/doubts/${doubtId}/answer`)
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
