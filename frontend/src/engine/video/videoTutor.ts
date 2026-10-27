/**
 * Plays a video lesson: the lecture video with checkpoints, doubt threads and an answer overlay.
 * Writes store/video (and the overlay board into store/player); components only read the stores and call these
 * methods. Flow per docs/plan/video-lesson-spec.md (1.1 threads, 1.2 checkpoints, 1.3 resume).
 *
 * The engine owns the <video> (attached by the page) and a second audio element for the tutor's voice (`Speech`).
 * Everything the tutor says is a sequence of `say`/`playClip` calls; a mic press bumps `gen` and fades the voice
 * out, so a running sequence sees the generation change and stops, and the new question decides what happens next.
 */
import { FADE_MS, fadeTo } from '@/engine/fade'
import type { MicHost } from '@/engine/host'
import { layoutStep } from '@/engine/layout'
import { loadBoardFonts, schedule } from '@/engine/tutor'
import { chapterAt, clampSeek, doneCheckpoints, dueCheckpoint, resumePointMs } from '@/engine/video/checkpoints'
import { restoreDoubts, statusFor } from '@/engine/video/doubts'
import { Speech } from '@/engine/video/speech'
import { api, streamAnswer } from '@/lib/api'
import { t } from '@/lib/i18n'
import { reportError, setTelemetrySession } from '@/lib/telemetry'
import type {
  AnsweredQuestion,
  Checkpoint,
  CheckpointQuestion,
  Clip,
  Lesson,
  PlayerPolicy,
  ReplayRange,
  Spoken,
  Thread,
  VideoQuestionResult,
  VideoSessionState,
} from '@/lib/types'
import { initialPlayerState, setPlayer, usePlayer } from '@/store/player'
import { type CheckpointView, initialVideoState, micState, setVideo, useVideo, type VideoDoubtView } from '@/store/video'

const get = useVideo.getState

/** Used when a lesson carries no policy (mirrors backend/app/config.py) */
const DEFAULT_POLICY: PlayerPolicy = { max_followups: 3, recap_after_turns: 2, recap_after_seconds: 45, rewind_ms: 3000, idle_ms: 20_000 }
const SAVE_EVERY_MS = 5000
/** start(fromMs) asks a not-done checkpoint this close to fromMs right away */
const CHECKPOINT_SNAP_MS = 500

/** video: the lecture plays or is paused; thread: a doubt holds it; checkpoint: a card is up; ending: summary */
type Phase = 'video' | 'thread' | 'checkpoint' | 'ending'

/** What a doubt thread returns to when it ends: the video (resumed near atMs) or a checkpoint's follow-up card. */
type ThreadContext = { kind: 'video'; atMs: number } | { kind: 'checkpoint'; view: CheckpointView }

/** "You asked earlier…" for a covered_later doubt, played when the video reaches targetMs */
interface Callback {
  doubtId: string
  targetMs: number
  spoken: Spoken
  played: boolean
}

interface PendingAnswer {
  clips: Clip[]
  finished: boolean
  failed: boolean
  notify: (() => void) | null
}

function updateDoubt(id: string, patch: Partial<VideoDoubtView>) {
  setVideo((s) => ({ doubts: s.doubts.map((d) => (d.id === id ? { ...d, ...patch } : d)) }))
}

function patchCheckpoint(patch: Partial<CheckpointView>) {
  setVideo((s) => (s.checkpoint ? { checkpoint: { ...s.checkpoint, ...patch } } : {}))
}

export class VideoTutor implements MicHost {
  private video: HTMLVideoElement | null = null
  private voice = new Speech()
  private lesson: Lesson | null = null
  private sessionId = ''
  private policy = DEFAULT_POLICY
  private phase: Phase = 'video'
  private userPaused = false
  private micHold = false
  /** A question is being routed (a second mic press is ignored meanwhile) */
  private routing = false
  /** Bumped by every interruption; a speaking sequence stops when it no longer matches */
  private gen = 0
  /** Bumped when playback resumes, so a fade-out still running doesn't pause the resumed video */
  private holdGen = 0
  /** Where the thread in progress (or the next one) returns to */
  private threadCtx: ThreadContext = { kind: 'video', atMs: 0 }
  /** Last position seen while playing, for crossing detection */
  private prevMs = 0
  private callbacks: Callback[] = []
  private bridges: Spoken[] = []
  /** Bridges and board fonts, needed before the first answer is laid out */
  private ready: Promise<unknown> = Promise.resolve()
  private idleTimer = 0
  private saveTimer = 0
  /** Resolves the checkpoint question on screen (Continue or Skip) */
  private questionResolve: (() => void) | null = null
  /** Cuts a teacher replay short on stop() */
  private replayStop: (() => void) | null = null
  private started = false
  private stopped = false

  /** The page's <video> element; called once it is mounted */
  attach(video: HTMLVideoElement) {
    if (this.stopped) return
    this.detach()
    this.video = video
    video.addEventListener('timeupdate', this.onTime)
    video.addEventListener('ended', this.onEnded)
    video.addEventListener('error', this.onMediaError)
  }

  private detach() {
    const v = this.video
    if (!v) return
    v.removeEventListener('timeupdate', this.onTime)
    v.removeEventListener('ended', this.onEnded)
    v.removeEventListener('error', this.onMediaError)
    this.video = null
  }

  /** Start (or resume at fromMs with the session's earlier answers and doubts) */
  async start(lesson: Lesson, sessionId: string, fromMs = 0, restored?: VideoSessionState) {
    if (this.stopped || this.started) return
    if (!this.video) {
      reportError('video.start', 'attach() was not called before start()', { lesson: lesson.slug })
      setVideo({ error: t(lesson, 'videoError') })
      return
    }
    this.started = true
    this.lesson = lesson
    this.sessionId = sessionId
    this.policy = lesson.policy ?? DEFAULT_POLICY
    setTelemetrySession(sessionId)
    // MicButton, Board and t() read the lesson from the scene player's store; the overlay always draws on 'doubt'
    setPlayer({ ...initialPlayerState, lesson, sessionId, mode: 'playing', layer: 'doubt' })
    setVideo({
      ...initialVideoState,
      lesson,
      sessionId,
      mode: 'playing',
      positionMs: fromMs,
      chapterIdx: chapterAt(fromMs, this.chapters),
      answered: restored?.answered ?? [],
      doubts: restored ? restoreDoubts(restored) : [],
    })
    this.ready = Promise.all([
      api
        .bridges(lesson.voice, lesson.language)
        .then((b) => (this.bridges = b))
        .catch(() => {}),
      loadBoardFonts(),
    ])
    this.saveTimer = window.setInterval(() => {
      if (this.phase === 'video' && this.video && !this.video.paused) this.savePosition()
    }, SAVE_EVERY_MS)
    // play() must run in the click that started the lesson, so nothing is awaited before it
    const due = this.checkpoints.find((cp) => !this.done().has(cp.idx) && Math.abs(cp.at_ms - fromMs) < CHECKPOINT_SNAP_MS)
    if (due) {
      this.video.currentTime = due.at_ms / 1000
      void this.runCheckpoint(due)
    } else {
      this.resumeVideo(fromMs)
    }
  }

  /** Permanent: a new VideoTutor is made per page mount */
  stop() {
    this.stopped = true
    this.gen++
    this.holdGen++
    this.clearIdle()
    window.clearInterval(this.saveTimer)
    this.voice.stop()
    this.replayStop?.()
    this.questionResolve?.()
    this.video?.pause()
    this.detach()
  }

  // ---- lesson data ----

  private get chapters() {
    return this.lesson?.chapters ?? []
  }

  private get checkpoints() {
    return this.lesson?.checkpoints ?? []
  }

  private get durationMs() {
    return this.lesson?.video?.duration_ms ?? Infinity
  }

  private done() {
    return doneCheckpoints(this.checkpoints, get().answered)
  }

  private questionDone(checkpointIdx: number, questionIdx: number) {
    return get().answered.some(
      (a) => a.checkpoint_idx === checkpointIdx && a.question_idx === questionIdx && (a.kind === 'first' || a.kind === 'skip'),
    )
  }

  private recordAnswer(a: AnsweredQuestion) {
    setVideo((s) => ({ answered: [...s.answered, a] }))
  }

  private bridge(): Spoken | undefined {
    return this.bridges[Math.floor(Math.random() * this.bridges.length)]
  }

  // ---- video playback ----

  private get videoMode() {
    return this.userPaused ? 'paused' : 'playing'
  }

  /** Fade the video out instead of cutting the teacher's word, then pause it. */
  private async hold() {
    const v = this.video
    if (!v || v.paused) return
    const gen = ++this.holdGen
    await fadeTo(v, 0, FADE_MS)
    if (gen === this.holdGen) v.pause()
  }

  /** Seek to atMs and play with a fade-in (stays paused when the student paused), clearing the overlay. */
  private resumeVideo(atMs: number) {
    const v = this.video
    if (!v || this.stopped) return
    this.holdGen++
    const ms = Math.max(0, Math.min(atMs, this.durationMs))
    v.currentTime = ms / 1000
    this.prevMs = ms
    setVideo({ mode: this.videoMode, positionMs: ms, chapterIdx: chapterAt(ms, this.chapters), overlay: false, caption: '' })
    setPlayer({ layer: 'doubt', drawn: [], active: [], clipTime: 0, caption: '' })
    if (this.userPaused) return
    v.volume = 0
    v.play().catch((e) => {
      // Autoplay refused (no user gesture yet): the pause button then starts it
      reportError('video.play', e)
      this.userPaused = true
      setVideo({ mode: 'paused' })
    })
    void fadeTo(v, 1, FADE_MS)
  }

  private savePosition(atMs = get().positionMs) {
    if (this.sessionId) api.saveVideoPosition(this.sessionId, atMs).catch(() => {})
  }

  /** While the lecture plays: track the position, pause at checkpoints, play due callbacks. */
  private onTime = () => {
    const v = this.video
    if (!v || this.stopped) return
    const now = v.currentTime * 1000
    setVideo({ positionMs: now, chapterIdx: chapterAt(now, this.chapters) })
    if (this.phase !== 'video' || v.paused || this.micHold) {
      this.prevMs = now
      return
    }
    const cp = dueCheckpoint(this.prevMs, now, this.checkpoints, this.done())
    this.prevMs = now
    if (cp) return void this.runCheckpoint(cp)
    const cb = this.callbacks.find((c) => !c.played && c.targetMs <= now)
    if (cb) void this.playCallback(cb)
  }

  private onEnded = () => {
    if (this.phase !== 'video' || this.stopped) return
    const cp = dueCheckpoint(this.prevMs, Infinity, this.checkpoints, this.done())
    if (cp) return void this.runCheckpoint(cp) // finishes the lesson afterwards
    void this.finish()
  }

  private onMediaError = () => {
    const err = this.video?.error
    reportError('video.media', err ? `${err.code}: ${err.message}` : 'unknown', { position_ms: get().positionMs })
    setVideo({ error: t(this.lesson, 'videoError') })
  }

  togglePause() {
    if (this.stopped || this.phase !== 'video' || this.micHold || this.routing) return
    this.userPaused = !this.userPaused
    if (this.userPaused) {
      void this.hold()
      setVideo({ mode: 'paused' })
      this.savePosition()
    } else {
      this.resumeVideo(get().positionMs)
    }
  }

  /** Seek by the student (applies checkpoints.clampSeek) */
  seek(ms: number) {
    const v = this.video
    if (!v || this.stopped || this.phase !== 'video' || this.micHold || this.routing) return
    const from = v.currentTime * 1000
    const done = this.done()
    const to = clampSeek(from, Math.max(0, Math.min(ms, this.durationMs)), this.checkpoints, done)
    v.currentTime = to / 1000
    this.prevMs = to
    // Going back before a callback's target plays it again when the video gets there
    for (const cb of this.callbacks) if (cb.targetMs > to) cb.played = false
    setVideo({ positionMs: to, chapterIdx: chapterAt(to, this.chapters) })
    this.savePosition(to)
    const cp = this.checkpoints.find((c) => !done.has(c.idx) && c.at_ms === to)
    if (cp && to !== from) void this.runCheckpoint(cp)
  }

  // ---- speaking ----
  // Thread sequences carry the generation they started in (`gen`); a mic press bumps it, so a sequence that was
  // suspended (on a play or an API call) finds `live(gen)` false and stops instead of speaking over the new question.

  private live(gen: number) {
    return gen === this.gen && !this.stopped
  }

  /** Play a line on the voice track; false when the sequence was interrupted (mic press, stop). */
  private async say(spoken: Spoken, gen = this.gen): Promise<boolean> {
    if (!this.live(gen)) return false
    setVideo({ caption: spoken.text })
    setPlayer({ caption: spoken.text })
    await this.voice.play(spoken.audio_url)
    return this.live(gen)
  }

  /** Play an answer / re-teach clip on the overlay board, laid out against what is already drawn. */
  private async playClip(clip: Clip, gen = this.gen): Promise<boolean> {
    if (!this.live(gen)) return false
    const timeline = schedule(layoutStep(clip.draw, usePlayer.getState().drawn), clip.duration_ms)
    setVideo({ caption: clip.say, overlay: true })
    setPlayer({ active: timeline, clipTime: 0, caption: clip.say })
    await this.voice.play(clip.audio_url, (ms) => setPlayer({ clipTime: ms }))
    setPlayer((s) => ({ drawn: [...s.drawn, ...timeline.map((a) => a.action)], active: [], clipTime: 0 }))
    return this.live(gen)
  }

  /** Start generating the answer now, in the background, so its first line is ready when the ack ends. */
  private prefetch(doubtId: string): PendingAnswer {
    const p: PendingAnswer = { clips: [], finished: false, failed: false, notify: null }
    ;(async () => {
      for await (const clip of streamAnswer(doubtId)) {
        p.clips.push(clip)
        p.notify?.()
      }
    })()
      .catch((e) => {
        p.failed = true
        reportError('video.answer_stream', e, { doubt_id: doubtId })
      })
      .finally(() => {
        p.finished = true
        p.notify?.()
      })
    return p
  }

  /** Stream the answer onto a fresh overlay board. 'error' when the stream failed or had no lines. */
  private async playAnswer(doubtId: string, p: PendingAnswer, gen: number): Promise<'ok' | 'interrupted' | 'error'> {
    await this.ready
    if (!this.live(gen)) return 'interrupted'
    updateDoubt(doubtId, { status: 'answering' })
    setVideo({ overlay: true })
    setPlayer({ layer: 'doubt', drawn: [], active: [], clipTime: 0 })
    let lines = 0
    for (let i = 0; ; i++) {
      while (i >= p.clips.length && !p.finished) await new Promise<void>((r) => (p.notify = r))
      if (!this.live(gen)) break
      if (i >= p.clips.length) break
      lines++
      if (!(await this.playClip(p.clips[i], gen))) break
    }
    updateDoubt(doubtId, { status: lines ? 'answered' : 'dismissed' })
    if (!this.live(gen)) return 'interrupted'
    return p.failed || !lines ? 'error' : 'ok'
  }

  // ---- questions ----

  /** Mic pressed: hold the video (or cut the tutor short) where it is. */
  beginQuestion() {
    if (this.stopped || !this.started || this.micHold || this.routing || this.phase === 'ending') return false
    const s = get()
    if (!micState(s).enabled) return false
    this.micHold = true
    this.gen++
    void this.voice.cancel()
    this.clearIdle()
    if (this.phase === 'checkpoint' && s.checkpoint) {
      // A doubt at a checkpoint's follow-up: the card steps aside until its thread ends
      this.threadCtx = { kind: 'checkpoint', view: s.checkpoint }
      setVideo({ checkpoint: null })
    } else if (this.phase === 'video') {
      const atMs = this.video ? this.video.currentTime * 1000 : s.positionMs
      this.threadCtx = { kind: 'video', atMs }
      this.phase = 'thread'
      void this.hold()
      this.savePosition(atMs)
    }
    // Otherwise an answer, prompt or recap was interrupted: the thread context stays as it was
    setVideo({ mode: 'listening', listening: true, promptEndsAt: null, awaitingFollowup: false })
    return true
  }

  /** Mic released without usable audio. */
  cancelQuestion() {
    if (!this.micHold) return
    this.micHold = false
    setVideo({ listening: false })
    this.settle(this.gen)
  }

  /** Nothing is playing and no question is in flight: back to the "Got it?" prompt, the card, or the video. */
  private settle(gen: number) {
    if (get().thread?.status === 'open') this.prompt(gen)
    else this.closeThread(gen)
  }

  async submitQuestion(audio: Blob) {
    if (this.stopped || !this.micHold) return
    const gen = this.gen
    const ctx = this.threadCtx
    const open = get().thread
    const threadId = open?.status === 'open' ? open.id : null
    const atMs = ctx.kind === 'video' ? ctx.atMs : this.checkpoints[ctx.view.idx].at_ms
    const checkpoint = ctx.kind === 'checkpoint' ? { idx: ctx.view.idx, question_idx: ctx.view.questionIdx } : null
    this.routing = true
    setVideo({ mode: 'thinking', listening: false, thinking: true })
    const tempId = `tmp-${Date.now()}`
    setVideo((s) => ({ doubts: [...s.doubts, { id: tempId, atMs, threadId, question: '…', intent: null, status: 'routing' }] }))
    let res: VideoQuestionResult
    try {
      res = await api.videoQuestion(this.sessionId, audio, atMs, threadId, checkpoint)
    } catch (e) {
      reportError('video.question', e, { thread_id: threadId })
      this.routing = false
      this.micHold = false
      setVideo((s) => ({ doubts: s.doubts.filter((d) => d.id !== tempId), thinking: false, error: t(this.lesson, 'questionFailed') }))
      if (!this.stopped) this.settle(gen)
      return
    }
    this.routing = false
    this.micHold = false
    if (this.stopped) return
    setVideo((s) => ({
      thinking: false,
      thread: res.thread,
      doubts: s.doubts.map((d) =>
        d.id === tempId
          ? { id: res.doubt_id, atMs, threadId: res.thread_id, question: res.question || res.transcript, intent: res.intent, status: statusFor(res.answer_when) }
          : d,
      ),
    }))
    await this.guarded('video.thread', gen, () => this.handleRouted(res, ctx, gen))
  }

  /** Play order (spec 1.1.7): ack → answer (now) → thread.line → thread.recap → resume unless the thread is open. */
  private async handleRouted(res: VideoQuestionResult, ctx: ThreadContext, gen: number) {
    setVideo({ mode: 'answering' })
    if (res.answer_when === 'now') {
      const answer = this.prefetch(res.doubt_id)
      if (!(await this.say(res.ack, gen))) return
      const outcome = await this.playAnswer(res.doubt_id, answer, gen)
      if (outcome === 'interrupted') return
      if (outcome === 'error') return this.failThread(gen)
      if (!(await this.afterLine(res.thread, gen))) return
      return this.prompt(gen)
    }
    if (res.answer_when === 'target_chapter' && res.callback && res.target_ms !== null) {
      this.callbacks.push({ doubtId: res.doubt_id, targetMs: res.target_ms, spoken: res.callback, played: false })
    }
    if (!(await this.say(res.ack, gen))) return
    if (!(await this.afterLine(res.thread, gen))) return
    if (res.thread.status === 'open') return this.prompt(gen) // off-topic inside a thread: no turn used, ask again
    await this.endThread(res.thread, gen, res.answer_when === 'replay' ? res.replay_from_ms : null)
    // "Let's continue" at a checkpoint's follow-up works like its Continue button
    if (ctx.kind === 'checkpoint' && res.intent === 'continue' && this.live(gen)) this.continueAfterCheckpoint()
  }

  /** The thread's `line` ("Which part is still unclear?", the capped line); false when interrupted. */
  private async afterLine(thread: Thread, gen: number): Promise<boolean> {
    return thread.line ? this.say(thread.line, gen) : this.live(gen)
  }

  /** A closed thread: its recap (not at a checkpoint), then back to the video or the card. */
  private async endThread(thread: Thread, gen: number, seekMs: number | null = null) {
    const ctx = this.threadCtx
    if (ctx.kind === 'video' && thread.recap && !(await this.say(thread.recap, gen))) return
    this.closeThread(gen, seekMs)
  }

  /** Resume the video near where the doubt was asked (or at seekMs), or bring the checkpoint card back. */
  private closeThread(gen: number, seekMs: number | null = null) {
    if (!this.live(gen)) return
    this.clearIdle()
    const ctx = this.threadCtx
    setVideo({ thread: null, promptEndsAt: null, awaitingFollowup: false })
    if (ctx.kind === 'checkpoint') {
      setVideo({ mode: 'checkpoint', checkpoint: { ...ctx.view, busy: false }, overlay: false, caption: '' })
      setPlayer({ drawn: [], active: [], clipTime: 0, caption: '' })
      return
    }
    this.phase = 'video'
    this.resumeVideo(seekMs ?? resumePointMs(ctx.atMs, this.chapters, this.policy.rewind_ms))
  }

  /** "Got it?" (or, after "Ask another", wait for the follow-up): the idle timer ends the thread. */
  private prompt(gen: number, followup = false) {
    if (!this.live(gen)) return
    this.clearIdle()
    setVideo({ mode: 'thread_prompt', promptEndsAt: Date.now() + this.policy.idle_ms, awaitingFollowup: followup })
    this.idleTimer = window.setTimeout(() => {
      if (get().mode === 'thread_prompt') void this.guarded('video.thread_idle', this.gen, (g) => this.abandon('idle', g))
    }, this.policy.idle_ms)
  }

  private clearIdle() {
    window.clearTimeout(this.idleTimer)
    this.idleTimer = 0
    setVideo({ promptEndsAt: null })
  }

  /** The "Got it?" prompt: understood ends the thread, otherwise the student asks a follow-up */
  async reply(understood: boolean) {
    const thread = get().thread
    if (this.stopped || get().mode !== 'thread_prompt' || !thread || thread.status !== 'open') return
    const gen = this.gen
    this.clearIdle()
    setVideo({ mode: 'answering', awaitingFollowup: false })
    await this.guarded('video.thread_reply', gen, async () => {
      const out = await api.threadReply(thread.id, understood)
      if (!this.live(gen)) return
      setVideo({ thread: out })
      if (!(await this.afterLine(out, gen))) return
      if (out.status === 'open') return this.prompt(gen, !understood) // "Sure, which part is still unclear?"
      await this.endThread(out, gen)
    })
  }

  /** The prompt went unanswered (or answering failed): the thread ends, its recap plays, the video goes on. */
  private async abandon(reason: 'idle' | 'error', gen: number) {
    const thread = get().thread
    if (!thread || thread.status !== 'open') return this.closeThread(gen)
    setVideo({ mode: 'answering', awaitingFollowup: false })
    const out = await api.threadAbandon(thread.id, reason)
    if (!this.live(gen)) return
    setVideo({ thread: out })
    if (!(await this.afterLine(out, gen))) return
    await this.endThread(out, gen)
  }

  /** Answering failed (spec 1.1.9): a sorry line, then the lecture goes on; never leave the video stuck. */
  private async failThread(gen: number) {
    try {
      await this.abandon('error', gen)
    } catch (e) {
      if (!this.live(gen)) return
      reportError('video.thread_abandon', e)
      this.closeThread(gen)
      setVideo({ caption: t(this.lesson, 'videoError') })
    }
  }

  /** Run a thread step; whatever fails is reported and the lesson resumes. */
  private async guarded(kind: string, gen: number, fn: (gen: number) => Promise<void>) {
    try {
      await fn(gen)
    } catch (e) {
      if (!this.live(gen)) return
      reportError(kind, e)
      await this.failThread(gen)
    }
  }

  /** "You asked earlier…" when the video reaches the chapter that answers a promised doubt. */
  private async playCallback(cb: Callback) {
    if (this.phase !== 'video') return
    const gen = this.gen
    cb.played = true
    const atMs = this.video!.currentTime * 1000
    this.phase = 'thread'
    this.threadCtx = { kind: 'video', atMs }
    await this.hold()
    setVideo({ mode: 'answering' })
    updateDoubt(cb.doubtId, { status: 'answering' })
    const ok = await this.say(cb.spoken, gen)
    updateDoubt(cb.doubtId, { status: 'answered' })
    if (!ok) return // a mic press took over; its thread resumes the video
    this.phase = 'video'
    this.resumeVideo(atMs)
  }

  // ---- checkpoints ----

  /** Pause at the checkpoint, play its intro and ask its not-done questions, then a bridge and resume at at_ms. */
  private async runCheckpoint(cp: Checkpoint) {
    const v = this.video
    if (!v || this.phase !== 'video' || this.stopped) return
    this.phase = 'checkpoint'
    this.clearIdle()
    const atEnd = v.ended || cp.at_ms >= this.durationMs
    await this.hold()
    v.currentTime = cp.at_ms / 1000
    setVideo({ positionMs: cp.at_ms, chapterIdx: chapterAt(cp.at_ms, this.chapters) })
    this.savePosition(cp.at_ms)
    const firstIdx = cp.questions.findIndex((_, qi) => !this.questionDone(cp.idx, qi))
    setVideo({
      mode: 'checkpoint',
      checkpoint: { idx: cp.idx, questionIdx: Math.max(0, firstIdx), stage: 'intro', wrong: [], result: null, busy: true, followup: false },
    })
    await this.say(cp.intro) // the mic is closed here, so only stop() interrupts
    for (const [qi, q] of cp.questions.entries()) {
      if (this.stopped) return
      if (!this.questionDone(cp.idx, qi)) await this.askQuestion(cp, qi, q)
    }
    if (this.stopped) return
    setVideo({ checkpoint: null, caption: '' })
    const bridge = this.bridge()
    if (bridge && !(await this.say(bridge))) return
    this.phase = 'video'
    if (atEnd) return this.finish()
    this.resumeVideo(cp.at_ms)
  }

  /** Show one question and read it out; the student may answer before the read-out ends. */
  private async askQuestion(cp: Checkpoint, qi: number, q: CheckpointQuestion) {
    setVideo({ checkpoint: { idx: cp.idx, questionIdx: qi, stage: 'answer', wrong: [], result: null, busy: false, followup: false } })
    const continued = new Promise<void>((r) => (this.questionResolve = r))
    void this.say({ text: q.say, audio_url: q.audio_url, duration_ms: q.duration_ms })
    await continued
    this.questionResolve = null
    void this.voice.cancel()
  }

  /** The question on screen, while its card is up (null once it was continued or a thread took over). */
  private currentQuestion(): { view: CheckpointView; q: CheckpointQuestion } | null {
    const view = get().checkpoint
    if (!view || this.phase !== 'checkpoint' || !this.questionResolve) return null
    return { view, q: this.checkpoints[view.idx].questions[view.questionIdx] }
  }

  /**
   * Judge a pick. A wrong first pick plays the hint for that option and allows one more try; a wrong retry
   * reveals the answer, replays the teacher's own explanation, re-teaches on the overlay and opens the mic.
   */
  async answerCheckpoint(chosen: number) {
    const cur = this.currentQuestion()
    if (!cur) return
    const { view, q } = cur
    if (view.busy || (view.stage !== 'answer' && view.stage !== 'retry') || view.wrong.includes(chosen)) return
    const kind = view.stage === 'retry' ? 'retry' : 'first'
    const resolve = this.questionResolve
    const live = () => this.questionResolve === resolve && !this.stopped
    patchCheckpoint({ busy: true })
    void this.voice.cancel()
    let result
    try {
      result = await api.checkpointAnswer(this.sessionId, view.idx, view.questionIdx, chosen, kind)
    } catch (e) {
      reportError('video.checkpoint_answer', e, { checkpoint_idx: view.idx, question_idx: view.questionIdx })
      if (live()) patchCheckpoint({ busy: false })
      setVideo({ error: t(this.lesson, 'answerFailed') })
      return
    }
    this.recordAnswer({ checkpoint_idx: view.idx, question_idx: view.questionIdx, kind, correct: result.correct })
    if (!live()) return
    const wrong = result.correct ? view.wrong : [...view.wrong, chosen]
    if (result.correct || kind === 'retry') patchCheckpoint({ result, wrong, stage: 'done' })
    else patchCheckpoint({ result, wrong, stage: 'retry' })
    await this.say(result.feedback)
    if (live() && !result.correct && kind === 'first') {
      const hint = q.why_wrong[chosen]
      if (hint) await this.say(hint)
    } else if (live() && !result.correct) {
      await this.reteach(q, live)
    }
    if (live()) patchCheckpoint({ busy: false })
  }

  /** After a second miss: the teacher's replay (card hidden), re-teach clips on the overlay, then the follow-up. */
  private async reteach(q: CheckpointQuestion, live: () => boolean) {
    if (q.replay) {
      patchCheckpoint({ stage: 'reteach' })
      await this.replayTeacher(q.replay)
    }
    if (live() && q.reteach.length) {
      patchCheckpoint({ stage: 'reteach' })
      setVideo({ overlay: true })
      setPlayer({ layer: 'doubt', drawn: [], active: [], clipTime: 0 })
      for (const clip of q.reteach) {
        if (!live() || !(await this.playClip(clip))) break
      }
      setVideo({ overlay: false })
      setPlayer({ drawn: [], active: [], clipTime: 0 })
    }
    if (!live()) return
    patchCheckpoint({ stage: 'done', followup: !!q.followup })
    if (q.followup) await this.say(q.followup)
  }

  /** Play the video from range.start_ms to range.end_ms, then pause it again. */
  private replayTeacher(range: ReplayRange): Promise<void> {
    const v = this.video
    if (!v) return Promise.resolve()
    setVideo({ caption: t(this.lesson, 'replayingTeacher') })
    setPlayer({ caption: t(this.lesson, 'replayingTeacher') })
    return new Promise((resolve) => {
      const endS = range.end_ms / 1000
      const finish = () => {
        if (this.replayStop !== finish) return
        this.replayStop = null
        v.removeEventListener('timeupdate', check)
        v.removeEventListener('ended', check)
        void this.hold().then(resolve)
      }
      const check = () => {
        if (v.ended || v.currentTime >= endS) finish()
      }
      this.replayStop = finish
      v.addEventListener('timeupdate', check)
      v.addEventListener('ended', check)
      this.holdGen++
      v.currentTime = range.start_ms / 1000
      v.volume = 0
      v.play().catch((e) => {
        reportError('video.replay', e)
        finish()
      })
      void fadeTo(v, 1, FADE_MS)
    })
  }

  /** Skip the current checkpoint question */
  skipCheckpointQuestion(): void {
    const cur = this.currentQuestion()
    if (!cur || cur.view.busy) return
    const { view } = cur
    if (view.stage === 'done') return this.continueAfterCheckpoint()
    if (view.stage !== 'answer' && view.stage !== 'retry') return
    api.checkpointSkip(this.sessionId, view.idx, view.questionIdx).catch((e) => reportError('video.checkpoint_skip', e))
    if (view.stage === 'answer') this.recordAnswer({ checkpoint_idx: view.idx, question_idx: view.questionIdx, kind: 'skip', correct: false })
    this.questionResolve?.()
  }

  /** Close the shown answer / follow-up and go to the next question or back to the video */
  continueAfterCheckpoint(): void {
    const cur = this.currentQuestion()
    if (!cur || cur.view.busy) return
    // Closed before the answer was shown: it's a skip, so a review can tell it from a card that never appeared
    if (cur.view.stage !== 'done') return this.skipCheckpointQuestion()
    this.questionResolve?.()
  }

  // ---- end ----

  private async finish() {
    if (this.stopped || this.phase === 'ending') return
    this.phase = 'ending'
    window.clearInterval(this.saveTimer)
    this.savePosition(this.durationMs)
    setVideo({ mode: 'answering', overlay: true, checkpoint: null, caption: t(this.lesson, 'makingSummary') })
    setPlayer({ layer: 'doubt', drawn: [], active: [], clipTime: 0 })
    try {
      const summary = await api.summary(this.sessionId)
      if (this.stopped) return
      setVideo({ summary })
      await this.say(summary.speech)
    } catch (e) {
      if (this.stopped) return
      reportError('video.summary', e)
    }
    setVideo({ mode: 'done', overlay: false, caption: t(this.lesson, 'videoEnded') })
  }
}
