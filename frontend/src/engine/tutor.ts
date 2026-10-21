/**
 * Lesson playback engine. Code, not the LLM, owns the lesson pointer:
 * this loop walks scene -> step, pauses for questions, and decides where
 * answers are delivered (end of step, end of scene, or a later scene's start).
 */
import { api, streamAnswer } from '@/lib/api'
import type { CheckQuestion, Clip, DrawAction, Lesson, QuestionResult, Spoken } from '@/lib/types'
import { initialPlayerState, setPlayer, type TimedAction, updateDoubt, usePlayer } from '@/store/player'

const get = usePlayer.getState

/** Spread a clip's draw actions over its audio so drawing finishes with the speech. */
export function schedule(draw: DrawAction[], durationMs: number): TimedAction[] {
  if (!draw.length) return []
  const span = Math.max(600, durationMs * 0.85)
  const slot = span / draw.length
  return draw.map((action, i) => {
    const want = action.type === 'text' ? 400 + action.text.length * 45 : 900
    return { action, start: i * slot, end: i * slot + Math.min(slot * 0.95, want) }
  })
}

interface PendingAnswer {
  doubtId: string
  when: 'step_end' | 'scene_end'
  clips: Clip[]
  finished: boolean
  notify: (() => void) | null
}

type Phase = 'lesson' | 'answer' | 'check' | 'summary'

export class Tutor {
  private main = new Audio()
  /** Fillers, acks and feedback play here while the main track is held. */
  private voice = new Audio()
  private phase: Phase = 'lesson'
  private userPaused = false
  private micHold = false
  private resumeWaiters: (() => void)[] = []
  private pending: PendingAnswer[] = []
  private callbacks = new Map<number, { doubtId: string; spoken: Spoken }[]>()
  private fillers: Spoken[] = []
  private checkResolve: (() => void) | null = null
  private clipCaption = ''
  private stopped = false

  async start(lesson: Lesson, sessionId: string) {
    setPlayer({ ...initialPlayerState, lesson, sessionId, mode: 'playing' })
    this.fillers = await api.fillers().catch(() => [])
    this.run().catch((e) => {
      if (!this.stopped) setPlayer({ error: String(e), mode: 'done' })
    })
  }

  stop() {
    this.stopped = true
    this.main.pause()
    this.voice.pause()
  }

  // ---- main loop ----

  private async run() {
    const lesson = get().lesson!
    for (const scene of lesson.scenes) {
      setPlayer({ sceneIdx: scene.idx, stepIdx: 0, drawn: [], active: [], layer: 'lesson' })
      await this.playCallbacks(scene.idx)
      for (const step of scene.steps) {
        setPlayer({ stepIdx: step.idx })
        await this.playClip(step)
        await this.deliver('step_end')
      }
      await this.deliver('scene_end')
      if (scene.check_question) await this.runCheck(scene.idx, scene.check_question)
    }
    await this.deliver('scene_end')
    await this.finish()
  }

  private async playClip(clip: Clip) {
    await this.gate()
    const timeline = schedule(clip.draw, clip.duration_ms)
    this.clipCaption = clip.say
    setPlayer({ active: timeline, clipTime: 0, caption: clip.say })
    await this.playAudio(this.main, clip.audio_url, (ms) => setPlayer({ clipTime: ms }))
    setPlayer((s) => ({ drawn: [...s.drawn, ...timeline.map((t) => t.action)], active: [], clipTime: 0 }))
  }

  private playAudio(el: HTMLAudioElement, url: string, onTime?: (ms: number) => void): Promise<void> {
    return new Promise((resolve) => {
      let raf = 0
      const done = () => {
        cancelAnimationFrame(raf)
        resolve()
      }
      el.src = url
      el.onended = done
      // A missing clip shouldn't stall the lesson
      el.onerror = () => {
        console.warn('audio failed', url)
        done()
      }
      if (onTime) {
        const tick = () => {
          if (this.stopped) return
          onTime(el.currentTime * 1000)
          raf = requestAnimationFrame(tick)
        }
        raf = requestAnimationFrame(tick)
      }
      el.play().catch((e) => {
        console.warn('play blocked', e)
        done()
      })
    })
  }

  // ---- pausing ----

  private get held() {
    return this.userPaused || this.micHold
  }

  /** Resolves once nothing is holding playback. */
  private gate(): Promise<void> {
    if (this.stopped) return new Promise(() => {})
    if (!this.held) return Promise.resolve()
    return new Promise((r) => this.resumeWaiters.push(r))
  }

  private syncMode() {
    if (this.micHold) return // listening/thinking set explicitly
    if (this.userPaused) return setPlayer({ mode: 'paused' })
    const byPhase = { lesson: 'playing', answer: 'answering', check: 'check', summary: 'summary' } as const
    setPlayer({ mode: byPhase[this.phase] })
  }

  private maybeResume() {
    this.syncMode()
    if (this.held) return
    if (this.main.src && !this.main.ended && this.main.paused && this.main.currentTime > 0) {
      void this.main.play()
    }
    const waiters = this.resumeWaiters
    this.resumeWaiters = []
    waiters.forEach((w) => w())
  }

  togglePause() {
    if (this.phase === 'check' || this.micHold) return
    this.userPaused = !this.userPaused
    if (this.userPaused) {
      this.main.pause()
      this.syncMode()
    } else {
      this.maybeResume()
    }
  }

  // ---- questions ----

  canAsk() {
    const { mode } = get()
    return mode === 'playing' || mode === 'paused' || mode === 'answering'
  }

  /** Mic pressed: hold the lesson where it is. */
  beginQuestion() {
    if (!this.canAsk()) return false
    this.micHold = true
    this.main.pause()
    setPlayer({ mode: 'listening' })
    return true
  }

  /** Mic released without usable audio. */
  cancelQuestion() {
    this.micHold = false
    this.maybeResume()
  }

  async submitQuestion(audio: Blob) {
    const { sessionId, sceneIdx, stepIdx } = get()
    setPlayer({ mode: 'thinking' })
    const tempId = `tmp-${Date.now()}`
    setPlayer((s) => ({
      doubts: [...s.doubts, { id: tempId, question: '…', intent: null, targetSceneIdx: null, status: 'routing' }],
    }))
    const filler = this.fillers[Math.floor(Math.random() * this.fillers.length)]
    const fillerDone = filler ? this.playAudio(this.voice, filler.audio_url) : Promise.resolve()
    if (filler) setPlayer({ caption: filler.text })
    try {
      const res = await api.askQuestion(sessionId!, audio, sceneIdx, stepIdx)
      await fillerDone
      this.register(tempId, res)
      setPlayer({ caption: res.ack.text })
      await this.playAudio(this.voice, res.ack.audio_url)
    } catch (e) {
      console.error(e)
      setPlayer((s) => ({ doubts: s.doubts.filter((d) => d.id !== tempId), error: 'Sawal bhejne mein dikkat hui. Phir se try karo.' }))
    } finally {
      setPlayer({ caption: this.clipCaption })
      this.micHold = false
      this.maybeResume()
    }
  }

  private register(tempId: string, res: QuestionResult) {
    const status =
      res.answer_when === 'target_scene' ? 'promised' : res.answer_when === 'none' ? 'dismissed' : 'queued'
    setPlayer((s) => ({
      doubts: s.doubts.map((d) =>
        d.id === tempId
          ? { id: res.doubt_id, question: res.question || res.transcript, intent: res.intent, targetSceneIdx: res.target_scene_idx, status }
          : d,
      ),
    }))
    if (res.answer_when === 'target_scene' && res.callback && res.target_scene_idx !== null) {
      const list = this.callbacks.get(res.target_scene_idx) ?? []
      list.push({ doubtId: res.doubt_id, spoken: res.callback })
      this.callbacks.set(res.target_scene_idx, list)
    } else if (res.answer_when === 'step_end' || res.answer_when === 'scene_end') {
      this.pending.push(this.prefetch(res.doubt_id, res.answer_when))
    }
  }

  /** Start generating the answer now, in the background, so it's ready when its turn comes. */
  private prefetch(doubtId: string, when: PendingAnswer['when']): PendingAnswer {
    const p: PendingAnswer = { doubtId, when, clips: [], finished: false, notify: null }
    ;(async () => {
      for await (const clip of streamAnswer(doubtId)) {
        p.clips.push(clip)
        p.notify?.()
      }
    })()
      .catch((e) => console.error('answer stream', e))
      .finally(() => {
        p.finished = true
        p.notify?.()
      })
    return p
  }

  /** Play queued answers on the doubt board, then restore the lesson board. */
  private async deliver(at: 'step_end' | 'scene_end') {
    const due = this.pending.filter((p) => at === 'scene_end' || p.when === 'step_end')
    if (!due.length) return
    this.pending = this.pending.filter((p) => !due.includes(p))
    const lessonBoard = get().drawn
    this.phase = 'answer'
    for (const p of due) {
      updateDoubt(p.doubtId, { status: 'answering' })
      setPlayer({ layer: 'doubt', drawn: [], active: [] })
      this.syncMode()
      for (let i = 0; ; i++) {
        while (i >= p.clips.length && !p.finished) await new Promise<void>((r) => (p.notify = r))
        if (i >= p.clips.length) break
        await this.playClip(p.clips[i])
      }
      updateDoubt(p.doubtId, { status: 'answered' })
    }
    this.phase = 'lesson'
    setPlayer({ layer: 'lesson', drawn: lessonBoard, active: [] })
    this.syncMode()
  }

  /** "Tumne pucha tha…" when the scene that answers a promised doubt begins. */
  private async playCallbacks(sceneIdx: number) {
    const list = this.callbacks.get(sceneIdx) ?? []
    this.callbacks.delete(sceneIdx)
    for (const cb of list) {
      await this.gate()
      updateDoubt(cb.doubtId, { status: 'answering' })
      this.clipCaption = cb.spoken.text
      setPlayer({ caption: cb.spoken.text })
      await this.playAudio(this.main, cb.spoken.audio_url)
      updateDoubt(cb.doubtId, { status: 'answered' })
    }
  }

  // ---- check questions and summary ----

  private async runCheck(sceneIdx: number, q: CheckQuestion) {
    await this.gate()
    this.phase = 'check'
    this.clipCaption = q.say
    setPlayer({ check: { sceneIdx, result: null, chosen: null }, caption: q.say })
    this.syncMode()
    // The student may answer and continue before the question has finished being read out
    const continued = new Promise<void>((r) => (this.checkResolve = r))
    void this.playAudio(this.main, q.audio_url)
    await continued
    this.main.pause()
    this.checkResolve = null
    this.phase = 'lesson'
    setPlayer({ check: null })
    this.syncMode()
  }

  async answerCheck(chosen: number) {
    const { sessionId, check } = get()
    if (!check || check.chosen !== null) return
    setPlayer({ check: { ...check, chosen } })
    const result = await api.recordAnswer(sessionId!, check.sceneIdx, chosen)
    setPlayer({ check: { ...check, chosen, result }, caption: result.feedback.text })
    await this.playAudio(this.voice, result.feedback.audio_url)
  }

  continueAfterCheck() {
    this.voice.pause()
    this.checkResolve?.()
  }

  private async finish() {
    this.phase = 'summary'
    this.syncMode()
    setPlayer({ layer: 'doubt', drawn: [], active: [], caption: 'Aaj ka summary bana rahi hoon…' })
    const summary = await api.summary(get().sessionId!)
    setPlayer({ summary, caption: summary.speech.text })
    await this.playAudio(this.main, summary.speech.audio_url)
    setPlayer({ mode: 'done' })
  }
}
