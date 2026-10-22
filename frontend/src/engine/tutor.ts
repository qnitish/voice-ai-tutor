/**
 * Lesson playback engine. Code, not the LLM, owns the lesson pointer:
 * this loop walks scene -> step, pauses for questions, and decides where
 * answers are delivered (right away, end of step, end of scene, or a later scene's start).
 */
import { boardBefore } from '@/engine/board'
import { FADE_MS, fadeTo } from '@/engine/fade'
import type { Restored } from '@/engine/restore'
import { resumePoint } from '@/engine/timing'
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

/** Where a resumed lesson starts, and the session's earlier doubts. */
export interface StartAt {
  sceneIdx: number
  stepIdx: number
  restored?: Restored
}

export class Tutor {
  private main = new Audio()
  /** Fillers, acks, feedback, bridges and "now" answers play here while the main track is held. */
  private voice = new Audio()
  private phase: Phase = 'lesson'
  private userPaused = false
  private micHold = false
  /** A "now" answer is playing over the held lesson */
  private interjecting = false
  private resumeWaiters: (() => void)[] = []
  private pending: PendingAnswer[] = []
  private callbacks = new Map<number, { doubtId: string; spoken: Spoken }[]>()
  private answeredChecks = new Set<number>()
  private fillers: Spoken[] = []
  private bridges: Spoken[] = []
  private checkResolve: (() => void) | null = null
  /** Clip on the main track, for sentence-aligned resume */
  private clip: Clip | null = null
  /** Next resume restarts the current clip from 0 ("phir se bolo") */
  private replayFromStart = false
  private clipCaption = ''
  private stopped = false

  async start(lesson: Lesson, sessionId: string, from?: StartAt) {
    const restored = from?.restored
    setPlayer({ ...initialPlayerState, lesson, sessionId, mode: 'playing', doubts: restored?.doubts ?? [] })
    if (restored) {
      for (const cb of restored.callbacks) {
        this.callbacks.set(cb.sceneIdx, [...(this.callbacks.get(cb.sceneIdx) ?? []), cb])
      }
      this.pending = restored.redeliver.map((id) => this.prefetch(id, 'scene_end'))
      this.answeredChecks = new Set(restored.answeredChecks)
    }
    ;[this.fillers, this.bridges] = await Promise.all([
      api.fillers().catch(() => []),
      api.bridges().catch(() => []),
    ])
    this.run(from).catch((e) => {
      if (!this.stopped) setPlayer({ error: String(e), mode: 'done' })
    })
  }

  stop() {
    this.stopped = true
    this.main.pause()
    this.voice.pause()
  }

  // ---- main loop ----

  private async run(from?: StartAt) {
    const lesson = get().lesson!
    const { sessionId } = get()
    for (const scene of lesson.scenes.slice(from?.sceneIdx ?? 0)) {
      const firstStep = scene.idx === from?.sceneIdx ? from.stepIdx : 0
      setPlayer({ sceneIdx: scene.idx, stepIdx: firstStep, drawn: boardBefore(scene, firstStep), active: [], layer: 'lesson' })
      await this.playCallbacks(scene.idx)
      for (const step of scene.steps.slice(firstStep)) {
        setPlayer({ stepIdx: step.idx })
        api.savePosition(sessionId!, scene.idx, step.idx).catch(() => {})
        await this.playClip(step)
        await this.deliver('step_end')
      }
      await this.deliver('scene_end')
      if (scene.check_question && !this.answeredChecks.has(scene.idx)) {
        await this.runCheck(scene.idx, scene.check_question)
      }
    }
    await this.deliver('scene_end')
    await this.finish()
  }

  private async playClip(clip: Clip, el = this.main, gated = true) {
    if (gated) await this.gate()
    const timeline = schedule(clip.draw, clip.duration_ms)
    if (el === this.main) this.clip = clip
    this.clipCaption = clip.say
    setPlayer({ active: timeline, clipTime: 0, caption: clip.say })
    await this.playAudio(el, clip.audio_url, (ms) => setPlayer({ clipTime: ms }))
    setPlayer((s) => ({ drawn: [...s.drawn, ...timeline.map((t) => t.action)], active: [], clipTime: 0 }))
  }

  private playAudio(el: HTMLAudioElement, url: string, onTime?: (ms: number) => void): Promise<void> {
    return new Promise((resolve) => {
      let raf = 0
      const done = () => {
        cancelAnimationFrame(raf)
        resolve()
      }
      void fadeTo(el, 1, 0) // undo a fade-out left over from an interruption
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
          // A held track must not drive the board while another clip (e.g. a "now" answer) plays
          if (!el.paused) onTime(el.currentTime * 1000)
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

  private playSpoken(spoken: Spoken) {
    setPlayer({ caption: spoken.text })
    return this.playAudio(this.voice, spoken.audio_url)
  }

  private bridge(): Spoken | undefined {
    return this.bridges[Math.floor(Math.random() * this.bridges.length)]
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

  /** Fade the main track out instead of cutting the word, then pause it. */
  private holdMain() {
    void fadeTo(this.main, 0, FADE_MS).then(() => {
      if (this.held) this.main.pause()
    })
  }

  private maybeResume() {
    this.syncMode()
    if (this.held) return
    const el = this.main
    if (el.src && !el.ended && el.currentTime > 0) {
      const clip = this.clip
      const pausedAt = el.currentTime * 1000
      // Back to the start of the interrupted sentence, so the student hears it whole
      const restart = this.replayFromStart ? 0 : clip ? resumePoint(clip.say, clip.duration_ms, pausedAt) : pausedAt
      this.replayFromStart = false
      el.currentTime = restart / 1000
      el.volume = 0
      if (el.paused) void el.play()
      void fadeTo(el, 1, FADE_MS)
    }
    const waiters = this.resumeWaiters
    this.resumeWaiters = []
    waiters.forEach((w) => w())
  }

  togglePause() {
    if (this.phase === 'check' || this.micHold || this.interjecting) return
    this.userPaused = !this.userPaused
    if (this.userPaused) {
      this.holdMain()
      this.syncMode()
    } else {
      this.maybeResume()
    }
  }

  // ---- questions ----

  canAsk() {
    const { mode } = get()
    return !this.interjecting && (mode === 'playing' || mode === 'paused' || mode === 'answering')
  }

  /** Mic pressed: hold the lesson where it is. */
  beginQuestion() {
    if (!this.canAsk()) return false
    this.micHold = true
    this.holdMain()
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
    const fillerDone = filler ? this.playSpoken(filler) : Promise.resolve()
    try {
      const res = await api.askQuestion(sessionId!, audio, sceneIdx, stepIdx)
      await fillerDone
      this.register(tempId, res)
      await this.playSpoken(res.ack)
      if (res.answer_when === 'replay') this.replayFromStart = true
      if (res.answer_when === 'now' && this.phase === 'lesson') await this.interject(res.doubt_id)
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
      res.answer_when === 'target_scene'
        ? 'promised'
        : res.answer_when === 'none'
          ? 'dismissed'
          : res.answer_when === 'replay'
            ? 'answered'
            : 'queued'
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
    } else if (res.answer_when === 'now' && this.phase !== 'lesson') {
      // Asked while another answer plays: it joins the current delivery right after it
      this.pending.push(this.prefetch(res.doubt_id, 'step_end'))
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

  private async playAnswer(p: PendingAnswer, el: HTMLAudioElement, gated: boolean) {
    updateDoubt(p.doubtId, { status: 'answering' })
    setPlayer({ layer: 'doubt', drawn: [], active: [] })
    for (let i = 0; ; i++) {
      while (i >= p.clips.length && !p.finished) await new Promise<void>((r) => (p.notify = r))
      if (i >= p.clips.length) break
      await this.playClip(p.clips[i], el, gated)
    }
    updateDoubt(p.doubtId, { status: 'answered' })
  }

  /** Play queued answers on the doubt board, then restore the lesson board. */
  private async deliver(at: 'step_end' | 'scene_end') {
    const isDue = (p: PendingAnswer) => at === 'scene_end' || p.when === 'step_end'
    if (!this.pending.some(isDue)) return
    const lessonBoard = get().drawn
    this.phase = 'answer'
    this.syncMode()
    // Doubts asked during an answer are picked up by the next round of this loop
    for (let p = this.pending.find(isDue); p; p = this.pending.find(isDue)) {
      this.pending = this.pending.filter((x) => x !== p)
      await this.playAnswer(p, this.main, true)
    }
    const bridge = this.bridge()
    if (bridge) {
      await this.gate()
      await this.playSpoken(bridge)
    }
    this.phase = 'lesson'
    setPlayer({ layer: 'lesson', drawn: lessonBoard, active: [] })
    this.syncMode()
  }

  /** A "now" answer, played over the held lesson clip; the lesson then resumes mid-step. */
  private async interject(doubtId: string) {
    const lessonBoard = get()
    const saved = { drawn: lessonBoard.drawn, active: lessonBoard.active, clipTime: lessonBoard.clipTime }
    const caption = this.clipCaption
    this.interjecting = true
    this.phase = 'answer'
    setPlayer({ mode: 'answering' })
    try {
      await this.playAnswer(this.prefetch(doubtId, 'step_end'), this.voice, false)
      const bridge = this.bridge()
      if (bridge) await this.playSpoken(bridge)
    } finally {
      this.interjecting = false
      this.phase = 'lesson'
      this.clipCaption = caption
      setPlayer({ layer: 'lesson', ...saved })
    }
  }

  /** "Tumne pucha tha…" when the scene that answers a promised doubt begins. */
  private async playCallbacks(sceneIdx: number) {
    const list = this.callbacks.get(sceneIdx) ?? []
    this.callbacks.delete(sceneIdx)
    for (const cb of list) {
      await this.gate()
      updateDoubt(cb.doubtId, { status: 'answering' })
      this.clip = null
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
    this.clip = null
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
    this.answeredChecks.add(check.sceneIdx)
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
