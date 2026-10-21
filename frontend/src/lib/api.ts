import axios from 'axios'

import type {
  CheckResult,
  Clip,
  Guest,
  Lesson,
  LessonSummary,
  QuestionResult,
  SessionSummary,
  Spoken,
  TutorSession,
} from '@/lib/types'

const http = axios.create({ baseURL: '/api' })

export const api = {
  listLessons: () => http.get<LessonSummary[]>('/lessons').then((r) => r.data),
  getLesson: (slug: string) => http.get<Lesson>(`/lessons/${slug}`).then((r) => r.data),
  fillers: () => http.get<Spoken[]>('/fillers').then((r) => r.data),

  createGuest: (body: { name: string; class_level: number | null; language: string }) =>
    http.post<Guest>('/guests', body).then((r) => r.data),
  getGuest: (id: string) => http.get<Guest>(`/guests/${id}`).then((r) => r.data),
  createSession: (guest_id: string, lesson_slug: string) =>
    http.post<TutorSession>('/sessions', { guest_id, lesson_slug }).then((r) => r.data),

  askQuestion: (sessionId: string, audio: Blob, sceneIdx: number, stepIdx: number) => {
    const form = new FormData()
    form.append('audio', audio, 'question.webm')
    form.append('scene_idx', String(sceneIdx))
    form.append('step_idx', String(stepIdx))
    return http.post<QuestionResult>(`/sessions/${sessionId}/questions`, form).then((r) => r.data)
  },
  recordAnswer: (sessionId: string, sceneIdx: number, chosenIndex: number) =>
    http
      .post<CheckResult>(`/sessions/${sessionId}/check`, { scene_idx: sceneIdx, chosen_index: chosenIndex })
      .then((r) => r.data),
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
