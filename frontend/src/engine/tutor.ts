/**
 * Lesson playback engine. Code, not the LLM, owns the lesson pointer:
 * this loop walks scene -> step, pauses for questions, and decides where
 * answers are delivered (right away, end of step, end of scene, or a later scene's start).
 */
import { FADE_MS, fadeTo } from '@/engine/fade'
import { liftBoard, scrollFor, Segment } from '@/engine/followup'
import { FONT_SIZE, FONT_STACK } from '@/engine/geometry'
import type { MicHost } from '@/engine/host'
import { layoutScene, layoutStep } from '@/engine/layout'
import { twinSlot, wrongNext } from '@/engine/remedy'
import type { Restored } from '@/engine/restore'
import { resumePoint } from '@/engine/timing'
import { api, streamAnswer } from '@/lib/api'
import { t } from '@/lib/i18n'
import type { CheckQuestion, Clip, DrawAction, Lesson, QuestionResult, Spoken } from '@/lib/types'
import { type CheckView, initialPlayerState, setPlayer, type TimedAction, updateDoubt, usePlayer } from '@/store/player'

const get = usePlayer.getState

/** How long an action takes to draw (ms); a motion plays in real time. */
function drawMs(action: DrawAction): number {
  if (action.type === 'motion') return action.seconds * 1000
  const chars = action.type === 'text' ? action.text.length : action.type === 'flow' ? action.items.join('').length : 0
  return chars ? 400 + chars * 45 : 900
}

/** Spread a clip's draw actions over its audio so drawing finishes with the speech. */
export function schedule(draw: DrawAction[], durationMs: number): TimedAction[] {
  if (!draw.length) return []
  const span = Math.max(600, durationMs * 0.85)
  if (!draw.some((a) => a.type === 'motion')) {
    const slot = span / draw.length
    return draw.map((action, i) => ({ action, start: i * slot, end: i * slot + Math.min(slot * 0.95, drawMs(action)) }))
  }
  // Equal slots would cut a motion short: each action gets the time it needs (squeezed to fit), spare time spread out
  const want = draw.map(drawMs)
  const scale = Math.min(1, span / want.reduce((a, b) => a + b, 0))
  const spare = (span - want.reduce((a, b) => a + b, 0) * scale) / draw.length
  let t = 0
  return draw.map((action, i) => {
    const out = { action, start: t, end: t + want[i] * scale }
    t = out.end + spare
    return out
  })
}

/** The layout measures text, so the board fonts must be loaded before anything is laid out. */
export async function loadBoardFonts() {
  try {
    await Promise.all(
      Object.values(FONT_SIZE).flatMap((px) => [400, 700].map((w) => document.fonts.load(`${w} ${px}px ${FONT_STACK}`, 'Aa∮∂₂'))),
    )
  } catch {
    // Measuring with a fallback font only makes the layout a little looser
  }
}

interface PendingAnswer {
  doubtId: string
  when: 'step_end' | 'scene_end'
  clips: Clip[]
  finished: boolean
  notify: (() => void) | null
  /** Set by a follow-up: cancel = stop here (its answer comes next); redo = say the interrupted line again */
  stop: 'cancel' | 'redo' | null
}

/** The doubt board's conversation: the answer playing, and a follow-up asked during it, answered below it. */
interface Thread {
  el: HTMLAudioElement
  current: PendingAnswer | null
  next: PendingAnswer | null
}


type Phase = 'lesson' | 'answer' | 'check' | 'summary'

/** Where a resumed lesson starts, and the session's earlier doubts. */
export interface StartAt {
  sceneIdx: number
  stepIdx: number
  restored?: Restored
}

export class Tutor implements MicHost {
  private main = new Audio()
  /** Fillers, acks, feedback, bridges and "now" answers play here while the main track is held. */
  private voice = new Audio()
  private phase: Phase = 'lesson'
  private userPaused = false
  private micHold = false
  /** A "now" answer is playing over the held lesson */
  private interjecting = false
  private resumeWaiters: { held: () => boolean; resume: () => void }[] = []
  private thread: Thread | null = null
  /** Set at the mic press when an answer is on the board: the question follows up on that doubt */
  private followUpOf: string | null = null
  /** Resolves the clip playing on each element, so a follow-up can cut it short */
  private audioDone = new Map<HTMLAudioElement, () => void>()
  private pending: PendingAnswer[] = []
  private callbacks = new Map<number, { doubtId: string; spoken: Spoken }[]>()
  private answeredChecks = new Set<number>()
  private fillers: Spoken[] = []
  private bridges: Spoken[] = []
  private checkResolve: (() => void) | null = null
  /** Bumped when a check ends, so a late answer callback can't act on the next one */
  private checkToken = 0
  /** Twin questions due after a scene's end ('end': after the last scene), by scene they test */
  private twins = new Map<number | 'end', number[]>()
  /** Clip on the main track, for sentence-aligned resume */
  private clip: Clip | null = null
  /** Answer clip on the second track ("now" answers), for the same */
  private voiceClip: Clip | null = null
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
      api.fillers(lesson.voice, lesson.language).catch(() => []),
      api.bridges(lesson.voice, lesson.language).catch(() => []),
      loadBoardFonts(),
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
      const laid = layoutScene(scene.steps.map((s) => s.draw))
      setPlayer({ sceneIdx: scene.idx, stepIdx: firstStep, drawn: laid.slice(0, firstStep).flat(), active: [], layer: 'lesson' })
      await this.playCallbacks(scene.idx)
      for (const step of scene.steps.slice(firstStep)) {
        setPlayer({ stepIdx: step.idx })
        api.savePosition(sessionId!, scene.idx, step.idx).catch(() => {})
        await this.playClip({ ...step, draw: laid[step.idx] }, this.main, true, false)
        await this.deliver('step_end')
      }
      await this.deliver('scene_end')
      await this.runTwins(scene.idx)
      if (scene.check_question && !this.answeredChecks.has(scene.idx)) {
        await this.runCheck(scene.idx, scene.check_question)
      }
    }
    await this.deliver('scene_end')
    await this.runTwins('end')
    await this.finish()
  }

  /** `layout` = false when the clip's draw actions are already laid out (lesson steps). */
  private async playClip(clip: Clip, el = this.main, gated = true, layout = true) {
    if (gated) await this.gate()
    const timeline = schedule(layout ? layoutStep(clip.draw, get().drawn) : clip.draw, clip.duration_ms)
    if (el === this.main) this.clip = clip
    else this.voiceClip = clip
    this.clipCaption = clip.say
    setPlayer({ active: timeline, clipTime: 0, caption: clip.say })
    await this.playAudio(el, clip.audio_url, (ms) => setPlayer({ clipTime: ms }))
    setPlayer((s) => ({ drawn: [...s.drawn, ...timeline.map((t) => t.action)], active: [], clipTime: 0 }))
  }

  private playAudio(el: HTMLAudioElement, url: string | null | undefined, onTime?: (ms: number) => void): Promise<void> {
    return new Promise((resolve) => {
      let raf = 0
      const done = () => {
        cancelAnimationFrame(raf)
        if (this.audioDone.get(el) === done) this.audioDone.delete(el)
        resolve()
      }
      this.audioDone.set(el, done)
      // A line whose TTS failed still arrives as text: keep its caption up for a moment, then go on
      if (!url) return void window.setTimeout(done, 1200)
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

  /** Stop what an element plays and let its playAudio() return. */
  private abortAudio(el: HTMLAudioElement) {
    const done = this.audioDone.get(el)
    el.pause()
    el.removeAttribute('src')
    el.load()
    done?.()
  }


  // ---- pausing ----

  /** The lesson waits while paused, while the mic is held and while a "now" answer plays over it */
  private get held() {
    return this.userPaused || this.micHold || this.interjecting
  }

  /** An answer on the doubt board waits only for the student */
  private get answerHeld() {
    return this.userPaused || this.micHold
  }

  /** Resolves once nothing is holding playback (`held`: what counts, by default the lesson's holds). */
  private gate(held = () => this.held): Promise<void> {
    if (this.stopped) return new Promise(() => {})
    if (!held()) return Promise.resolve()
    return new Promise((resume) => this.resumeWaiters.push({ held, resume }))
  }

  private syncMode() {
    if (this.micHold) return // listening/thinking set explicitly
    if (this.userPaused) return setPlayer({ mode: 'paused' })
    if (this.thread) return setPlayer({ mode: 'answering' })
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
    if (!this.held) this.resumeTrack(this.main, this.clip)
    // A "now" answer held by the mic (a press that brought no question)
    if (this.thread?.el === this.voice && !this.answerHeld) this.resumeTrack(this.voice, this.voiceClip)
    const ready = this.resumeWaiters.filter((w) => !w.held())
    this.resumeWaiters = this.resumeWaiters.filter((w) => w.held())
    ready.forEach((w) => w.resume())
  }

  private resumeTrack(el: HTMLAudioElement, clip: Clip | null) {
    if (!el.src || el.ended || el.currentTime <= 0) return
    const pausedAt = el.currentTime * 1000
    // Back to the start of the interrupted sentence, so the student hears it whole
    const restart = this.replayFromStart && el === this.main ? 0 : clip ? resumePoint(clip.say, clip.duration_ms, pausedAt) : pausedAt
    if (el === this.main) this.replayFromStart = false
    el.currentTime = restart / 1000
    el.volume = 0
    if (el.paused) void el.play()
    void fadeTo(el, 1, FADE_MS)
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
    const { mode, check } = get()
    // A question while an answer is on the board follows up on it
    if (this.thread) return mode === 'answering' || mode === 'paused'
    if (this.interjecting) return false
    // During a check only the follow-up after a re-teach takes questions (not the re-teach itself)
    if (this.phase === 'check') return mode === 'check' && !!check?.followup && !check.busy
    return mode === 'playing' || mode === 'paused' || mode === 'answering'
  }

  /** Mic pressed: hold the lesson where it is. */
  beginQuestion() {
    if (!this.canAsk()) return false
    this.micHold = true
    this.followUpOf = this.thread?.current?.doubtId ?? null
    this.holdMain()
    if (this.thread?.el === this.voice) {
      void fadeTo(this.voice, 0, FADE_MS).then(() => {
        if (this.micHold) this.voice.pause()
      })
    }
    setPlayer({ mode: 'listening' })
    return true
  }

  /** Mic released without usable audio. */
  cancelQuestion() {
    this.micHold = false
    this.followUpOf = null
    this.maybeResume()
  }

  async submitQuestion(audio: Blob) {
    const { sessionId, sceneIdx, stepIdx, check } = get()
    const followUpOf = this.followUpOf
    this.followUpOf = null
    const thread = followUpOf ? this.thread : null
    // "Kis part mein confusion hua?" after a wrong check answer: answered right away, knowing the picks
    const duringCheck = this.phase === 'check' && !thread
    setPlayer({ mode: 'thinking' })
    const tempId = `tmp-${Date.now()}`
    setPlayer((s) => ({
      doubts: [...s.doubts, { id: tempId, question: '…', intent: null, targetSceneIdx: null, status: 'routing' }],
    }))
    // A follow-up continues the answer on the board: no filler while it is routed
    const filler = thread ? undefined : this.fillers[Math.floor(Math.random() * this.fillers.length)]
    const fillerDone = filler ? this.playSpoken(filler) : Promise.resolve()
    let answerNow: (() => Promise<void>) | null = null
    try {
      const res = await api.askQuestion(sessionId!, audio, check?.sceneIdx ?? sceneIdx, stepIdx, duringCheck, followUpOf)
      await fillerDone
      this.register(tempId, res, duringCheck, !!thread)
      if (thread) this.followUp(thread, res)
      if (res.ack) await this.playSpoken(res.ack)
      if (thread) return
      if (res.answer_when === 'replay') this.replayFromStart = true
      // "Clear hai, aage chalo" on the check card works like "Aage chalein"
      if (res.intent === 'continue' && duringCheck) this.continueAfterCheck()
      if (res.answer_when === 'now' && duringCheck) answerNow = () => this.answerDuringCheck(res.doubt_id)
      else if (res.answer_when === 'now' && this.phase === 'lesson') answerNow = () => this.interject(res.doubt_id)
    } catch (e) {
      console.error(e)
      setPlayer((s) => ({ doubts: s.doubts.filter((d) => d.id !== tempId), error: t(get().lesson, 'questionFailed') }))
      // The interrupted line is said again
      if (thread?.current) this.stopAnswer(thread, 'redo')
    } finally {
      setPlayer({ caption: this.clipCaption })
      this.micHold = false
      // The lesson stays held from here until the "now" answer is over (the mic opens for follow-ups meanwhile)
      if (answerNow) this.interjecting = true
      this.maybeResume()
    }
    if (answerNow) await answerNow()
  }

  /**
   * A question asked during an answer: answered right below it (the rest of that answer is dropped), or, when
   * there is nothing to answer, the interrupted line is said again after the ack. "Aage chalo" ends the conversation.
   */
  private followUp(thread: Thread, res: QuestionResult) {
    if (res.answer_when === 'now') {
      thread.next = this.prefetch(res.doubt_id, 'step_end')
      this.stopAnswer(thread, 'cancel')
    } else {
      this.stopAnswer(thread, res.intent === 'continue' ? 'cancel' : 'redo')
    }
  }

  private stopAnswer(thread: Thread, stop: 'cancel' | 'redo') {
    const p = thread.current
    if (!p) return
    p.stop = stop
    p.notify?.()
    this.abortAudio(thread.el)
  }

  private register(tempId: string, res: QuestionResult, duringCheck = false, followUp = false) {
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
    } else if (res.answer_when === 'now' && this.phase !== 'lesson' && !duringCheck && !followUp) {
      // Asked while another answer plays: it joins the current delivery right after it
      this.pending.push(this.prefetch(res.doubt_id, 'step_end'))
    }
  }

  /** Start generating the answer now, in the background, so it's ready when its turn comes. */
  private prefetch(doubtId: string, when: PendingAnswer['when']): PendingAnswer {
    const p: PendingAnswer = { doubtId, when, clips: [], finished: false, notify: null, stop: null }
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

  /**
   * Answer a doubt on a blank doubt board. A follow-up asked meanwhile is answered below it, the board scrolling
   * up to make room, and so on, until an answer plays to its end.
   */
  private async runThread(first: PendingAnswer, el: HTMLAudioElement) {
    const thread: Thread = { el, current: null, next: first }
    this.thread = thread
    this.syncMode()
    setPlayer({ layer: 'doubt', drawn: [], active: [], boardScroll: 0 })
    try {
      for (let n = 0; thread.next && !this.stopped; n++) {
        const p = thread.next
        thread.next = null
        thread.current = p
        await this.playAnswer(p, el, new Segment(get().drawn, n))
      }
    } finally {
      this.thread = null
      this.syncMode()
    }
  }

  private async playAnswer(p: PendingAnswer, el: HTMLAudioElement, segment: Segment) {
    updateDoubt(p.doubtId, { status: 'answering' })
    // p.stop changes while this awaits (a follow-up sets it)
    const stop = () => p.stop
    let separator = segment.separator()
    for (let i = 0; ; i++) {
      while (i >= p.clips.length && !p.finished && !p.stop) await new Promise<void>((r) => (p.notify = r))
      if (stop() === 'cancel' || i >= p.clips.length) break
      await this.gate(() => this.answerHeld)
      if (stop() === 'cancel') break
      const clip = p.clips[i]
      const draw = [...separator, ...segment.place(clip.draw)]
      separator = []
      await liftBoard(scrollFor(get().boardScroll, draw), () => this.stopped)
      await this.playClip({ ...clip, draw }, el, false, false)
      // Asked about something else: the cut-off line is said again (its drawing is already done)
      while (stop() === 'redo') {
        p.stop = null
        await this.gate(() => this.answerHeld)
        await this.playClip({ ...clip, draw: [] }, el, false, false)
      }
      if (stop() === 'cancel') break
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
      await this.runThread(p, this.main)
    }
    const bridge = this.bridge()
    if (bridge) {
      await this.gate()
      await this.playSpoken(bridge)
    }
    this.phase = 'lesson'
    setPlayer({ layer: 'lesson', drawn: lessonBoard, active: [], boardScroll: 0 })
    this.syncMode()
  }

  /** A "now" answer, played over the held lesson clip; the lesson then resumes mid-step. */
  private async interject(doubtId: string) {
    const lessonBoard = get()
    const saved = { drawn: lessonBoard.drawn, active: lessonBoard.active, clipTime: lessonBoard.clipTime }
    const caption = this.clipCaption
    // submitQuestion set interjecting, which holds the lesson until this ends
    this.phase = 'answer'
    try {
      await this.runThread(this.prefetch(doubtId, 'step_end'), this.voice)
      const bridge = this.bridge()
      if (bridge) await this.playSpoken(bridge)
    } finally {
      this.interjecting = false
      this.phase = 'lesson'
      this.clipCaption = caption
      setPlayer({ layer: 'lesson', boardScroll: 0, ...saved })
      setPlayer({ caption })
      this.maybeResume()
    }
  }

  /** A doubt asked from the check card: its answer plays on the doubt board, then the card comes back. */
  private async answerDuringCheck(doubtId: string) {
    const lessonBoard = get().drawn
    try {
      await this.runThread(this.prefetch(doubtId, 'step_end'), this.voice)
    } finally {
      this.interjecting = false
      setPlayer({ layer: 'lesson', drawn: lessonBoard, active: [], boardScroll: 0 })
      this.maybeResume()
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

  private async runCheck(sceneIdx: number, q: { say: string; audio_url: string }, twin = false) {
    await this.gate()
    this.phase = 'check'
    this.clip = null
    this.clipCaption = q.say
    setPlayer({
      check: { sceneIdx, twin, stage: 'answer', wrong: [], result: null, busy: false, followup: false },
      caption: q.say,
    })
    this.syncMode()
    // The student may answer and continue before the question has finished being read out
    const continued = new Promise<void>((r) => (this.checkResolve = r))
    void this.playAudio(this.main, q.audio_url)
    await continued
    this.silenceMain()
    this.checkResolve = null
    this.checkToken++
    this.phase = 'lesson'
    setPlayer({ check: null })
    this.syncMode()
  }

  /** Twin questions of earlier wrongly answered scenes that are due now. */
  private async runTwins(slot: number | 'end') {
    const due = this.twins.get(slot) ?? []
    this.twins.delete(slot)
    for (const sceneIdx of due) {
      const twin = get().lesson!.scenes[sceneIdx].check_question?.twin
      if (twin) await this.runCheck(sceneIdx, twin, true)
    }
  }

  /** Stop a read-out the student answered over, so resuming after a mic press won't replay it. */
  private silenceMain() {
    this.main.pause()
    this.main.removeAttribute('src')
    this.main.load()
  }

  private patchCheck(patch: Partial<CheckView>) {
    setPlayer((s) => (s.check ? { check: { ...s.check, ...patch } } : {}))
  }

  /**
   * Judge a pick. A wrong first pick plays the hint for that option and allows one more try;
   * a second miss re-teaches on the doubt board, then shows the answer and opens the mic for follow-ups.
   */
  async answerCheck(chosen: number) {
    const { sessionId, check, lesson } = get()
    if (!check || check.busy || check.stage === 'done' || check.wrong.includes(chosen)) return
    const q = lesson!.scenes[check.sceneIdx].check_question!
    const kind = check.twin ? 'twin' : check.stage === 'retry' ? 'retry' : 'first'
    const token = this.checkToken
    const live = () => token === this.checkToken && !this.stopped
    this.patchCheck({ busy: true })
    this.silenceMain()
    let result
    try {
      result = await api.recordAnswer(sessionId!, check.sceneIdx, chosen, kind)
    } catch (e) {
      console.error(e)
      this.patchCheck({ busy: false })
      setPlayer({ error: t(get().lesson, 'answerFailed') })
      return
    }
    if (kind === 'first') this.answeredChecks.add(check.sceneIdx)
    if (kind === 'first' && !result.correct && q.twin) {
      const slot = twinSlot(lesson!.scenes.length, check.sceneIdx)
      this.twins.set(slot, [...(this.twins.get(slot) ?? []), check.sceneIdx])
    }
    const next = result.correct || kind === 'twin' ? 'reveal' : wrongNext(q, kind)
    if (!live()) return
    this.patchCheck({
      result,
      wrong: result.correct ? check.wrong : [...check.wrong, chosen],
      stage: next === 'retry' ? 'retry' : next === 'reveal' ? 'done' : check.stage,
    })
    await this.playSpoken(result.feedback)
    if (live() && next === 'retry') {
      const hint = q.why_wrong?.[chosen]
      if (hint) await this.playSpoken(hint)
    } else if (live() && next === 'reteach') {
      await this.reteach(q, live)
    }
    if (live()) this.patchCheck({ busy: false })
  }

  /** Explain the idea again on the doubt board, then show the answer and ask where the confusion was. */
  private async reteach(q: CheckQuestion, live: () => boolean) {
    const lessonBoard = get().drawn
    setPlayer({ mode: 'answering', layer: 'doubt', drawn: [], active: [] })
    for (const clip of q.reteach ?? []) {
      if (!live()) break
      await this.playClip(clip, this.main, false)
    }
    setPlayer({ layer: 'lesson', drawn: lessonBoard, active: [] })
    if (!live()) return
    this.patchCheck({ stage: 'done', followup: !!q.followup })
    this.syncMode()
    if (q.followup) {
      this.clipCaption = q.followup.text
      await this.playSpoken(q.followup)
    }
  }

  continueAfterCheck() {
    const { check, sessionId } = get()
    // Closed before the answer was shown: record the skip, so a review can tell it from a card that never appeared
    if (check && check.stage !== 'done') api.skipCheck(sessionId!, check.sceneIdx, check.twin).catch(() => {})
    this.voice.pause()
    this.checkResolve?.()
  }

  private async finish() {
    this.phase = 'summary'
    this.syncMode()
    setPlayer({ layer: 'doubt', drawn: [], active: [], caption: t(get().lesson, 'makingSummary') })
    const summary = await api.summary(get().sessionId!)
    setPlayer({ summary, caption: summary.speech.text })
    await this.playAudio(this.main, summary.speech.audio_url)
    setPlayer({ mode: 'done' })
  }
}
