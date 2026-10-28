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
import { liftBoard, scrollFor, Segment } from '@/engine/followup'
import type { MicHost } from '@/engine/host'
import { layoutStep } from '@/engine/layout'
import { loadBoardFonts, schedule } from '@/engine/tutor'
import { chapterAt, clampSeek, doneCheckpoints, dueCheckpoint, resumeAfterCheckpoint, resumePointMs } from '@/engine/video/checkpoints'
import { restoreDoubts, statusFor } from '@/engine/video/doubts'
import { markRight, markWrong, note, quizLayout, type QuizLayout } from '@/engine/video/quizBoard'
import { captionMs, Speech } from '@/engine/video/speech'
import { api, streamAnswer } from '@/lib/api'
import { t } from '@/lib/i18n'
import { reportError, setTelemetrySession } from '@/lib/telemetry'
import type {
  AnsweredQuestion,
  Checkpoint,
  CheckpointQuestion,
  Clip,
  Color,
  DrawAction,
  Lesson,
  PlayerPolicy,
  ReplayRange,
  Spoken,
  Thread,
  VideoCheckResult,
  VideoQuestionResult,
  VideoSessionState,
} from '@/lib/types'
import { initialPlayerState, setPlayer, usePlayer } from '@/store/player'
import { canSeek, type CheckpointView, initialVideoState, micState, setVideo, useVideo, type VideoDoubtView } from '@/store/video'

const get = useVideo.getState

/** Used when a lesson carries no policy (mirrors backend/app/config.py) */
const DEFAULT_POLICY: PlayerPolicy = { max_followups: 3, recap_after_turns: 2, recap_after_seconds: 30, rewind_ms: 3000, idle_ms: 10_000 }
const SAVE_EVERY_MS = 5000
/** start(fromMs) asks a not-done checkpoint this close to fromMs right away */
const CHECKPOINT_SNAP_MS = 500
/** An answer stream that delivers no line for this long is given up (the thread then ends with the sorry line) */
const ANSWER_STALL_MS = 30_000
/** How long the options take to write after the question is read, and the longest a silent note takes */
const OPTIONS_MS = 2500
const NOTE_SILENT_MS = 4000

/** video: the lecture plays or is paused; thread: a doubt holds it; checkpoint: a card is up; ending: summary */
type Phase = 'video' | 'thread' | 'checkpoint' | 'ending'

/** What a doubt thread returns to when it ends: the video (resumed near atMs) or a checkpoint's follow-up card. */
type ThreadContext = { kind: 'video'; atMs: number } | { kind: 'checkpoint'; view: CheckpointView }

/**
 * A covered_later doubt: its answer is prepared once the video reaches the target chapter (targetMs), and at the
 * chapter's end (deliverMs) the video pauses for "You asked earlier…" and the answer on the board.
 */
interface Callback {
  doubtId: string
  targetMs: number
  deliverMs: number
  spoken: Spoken
  answer: PendingAnswer | null
  played: boolean
}

interface PendingAnswer {
  clips: Clip[]
  finished: boolean
  failed: boolean
  notify: (() => void) | null
  /** Cuts the NDJSON stream when the answer will never be played */
  abort: AbortController
  /** Where on the board the answer goes (set when it starts), and each clip's actions placed there */
  segment: Segment | null
  laid: DrawAction[][]
}

/**
 * A "now" answer in progress (ack, then clips). A mic press interrupts it; if that press sends no question
 * (too short, silence, permission refused) the answer resumes from here instead of being lost.
 */
interface NowAnswer {
  doubtId: string
  /** null for a follow-up: its answer continues on the board right away */
  ack: Spoken | null
  thread: Thread
  answer: PendingAnswer
  /** 'ack' while the ack plays, 'answer' once the clips started */
  stage: 'ack' | 'answer'
  /** The clip that was playing (replayed whole on resume) */
  clipIdx: number
  /** The overlay board before that clip, restored so its drawing isn't laid out twice */
  drawnBefore: DrawAction[]
}

const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError'

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
  /** The thread whose answers are on the board, and how many follow-ups are stacked below its first answer */
  private boardThread: string | null = null
  private boardSegments = 0
  /** The "now" answer being played, until its prompt is reached (resumed after an empty mic press) */
  private nowAnswer: NowAnswer | null = null
  /** Whether the prompt a mic press interrupted was waiting for a follow-up, so an empty press shows it again */
  private promptFollowup = false
  /** Answer streams started and not yet finished (aborted on stop) */
  private prefetches = new Set<PendingAnswer>()
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
  /** The checkpoint question written on the board: its layout, where the next note goes, and the board as the
   *  question left it (brought back when a doubt or the solution asked there ends) */
  private quiz: { layout: QuizLayout; bottom: number; drawn: DrawAction[] } | null = null
  /** Cuts a teacher replay short on stop() */
  private replayStop: (() => void) | null = null
  /** Set by a seek back from a checkpoint card: the checkpoint stops asking and the video resumes here */
  private leaveTo: number | null = null
  /** The recap of a long thread asked on the current checkpoint's card, played when the video resumes after it */
  private checkpointRecap: Spoken | null = null
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

  /**
   * Call synchronously in the Start click, before anything is awaited: Safari allows play() only on elements
   * that were played inside a user gesture, so the video and the tutor's voice are each started and stopped here.
   * (Chrome remembers the page's activation anyway.)
   */
  unlock() {
    if (this.stopped || this.started) return
    const v = this.video
    if (v && v.paused) {
      // Silent (resumeVideo fades it in later); stopped again once it really started, unless the lesson took
      // over meanwhile (start() plays or holds it)
      v.volume = 0
      v.play().then(
        () => {
          if (!this.started) v.pause()
        },
        () => {},
      )
    }
    this.voice.unlock()
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
    for (const p of this.prefetches) p.abort.abort()
    this.prefetches.clear()
    this.nowAnswer = null
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
    setPlayer({ layer: 'doubt', drawn: [], active: [], clipTime: 0, boardScroll: 0, caption: '' })
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
    if (!v || this.stopped || !this.started) return // unlock() plays a moment before start()
    const now = v.currentTime * 1000
    setVideo({ positionMs: now, chapterIdx: chapterAt(now, this.chapters) })
    if (this.phase !== 'video' || v.paused || this.micHold) {
      this.prevMs = now
      return
    }
    for (const c of this.callbacks) if (!c.played && !c.answer && c.targetMs <= now) c.answer = this.prefetch(c.doubtId)
    // A promised doubt due here is answered before the checkpoint on the same section
    const cb = this.callbacks.find((c) => !c.played && c.deliverMs <= now)
    if (cb) return void this.playCallback(cb)
    const cp = dueCheckpoint(this.prevMs, now, this.checkpoints, this.done())
    this.prevMs = now
    if (cp) return void this.runCheckpoint(cp)
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

  /**
   * Seek by the student (applies checkpoints.clampSeek). At the "Got it?" prompt the seek ends the thread as
   * understood; on a checkpoint card a seek back leaves the checkpoint (it is asked again when the video gets there).
   */
  seek(ms: number) {
    const v = this.video
    if (!v || this.stopped || this.micHold || this.routing || !canSeek(get())) return
    const target = Math.max(0, Math.min(ms, this.durationMs))
    if (get().mode === 'thread_prompt') return this.seekFromThread(target)
    if (this.phase === 'checkpoint') return this.seekFromCheckpoint(target)
    if (this.phase !== 'video') return
    const from = v.currentTime * 1000
    const done = this.done()
    const to = clampSeek(from, target, this.checkpoints, done)
    v.currentTime = to / 1000
    this.prevMs = to
    setVideo({ positionMs: to, chapterIdx: chapterAt(to, this.chapters) })
    this.savePosition(to)
    const cp = this.checkpoints.find((c) => !done.has(c.idx) && c.at_ms === to)
    if (cp && to !== from) void this.runCheckpoint(cp)
  }

  /** A seek at the "Got it?" prompt: the thread ends as understood (no recap) and the video plays from there. */
  private seekFromThread(target: number) {
    const thread = get().thread
    this.gen++ // the prompt's sequence and idle timer stop here
    this.clearIdle()
    void this.voice.cancel()
    if (thread?.status === 'open') api.threadReply(thread.id, true).catch((e) => reportError('video.thread_seek', e, { thread_id: thread.id }))
    setVideo({ thread: null, promptEndsAt: null, awaitingFollowup: false })
    const ctx = this.threadCtx
    if (ctx.kind === 'checkpoint') {
      setVideo({ mode: 'checkpoint', checkpoint: { ...ctx.view, busy: false }, overlay: false, caption: '' })
      setPlayer({ drawn: [], active: [], clipTime: 0, boardScroll: 0, caption: '' })
      return this.seekFromCheckpoint(target)
    }
    this.phase = 'video'
    const to = clampSeek(ctx.atMs, target, this.checkpoints, this.done())
    this.savePosition(to)
    this.resumeVideo(to)
    const cp = this.checkpoints.find((c) => !this.done().has(c.idx) && c.at_ms === to)
    if (cp && to !== ctx.atMs) void this.runCheckpoint(cp)
  }

  /** A seek back from the checkpoint card; forward stays blocked by this (unanswered) checkpoint. */
  private seekFromCheckpoint(target: number) {
    const view = get().checkpoint
    if (!view || target >= this.checkpoints[view.idx].at_ms || !this.questionResolve) return
    this.leaveTo = target
    void this.voice.cancel()
    this.questionResolve()
  }

  // ---- speaking ----
  // Thread sequences carry the generation they started in (`gen`); a mic press bumps it, so a sequence that was
  // suspended (on a play or an API call) finds `live(gen)` false and stops instead of speaking over the new question.

  private live(gen: number) {
    return gen === this.gen && !this.stopped
  }

  /** Play a line on the voice track (none: nothing to say); false when the sequence was interrupted (mic press, stop). */
  private async say(spoken: Spoken | null, gen = this.gen): Promise<boolean> {
    if (!this.live(gen)) return false
    if (!spoken) return true
    setVideo({ caption: spoken.text })
    setPlayer({ caption: spoken.text })
    await this.voice.play(spoken.audio_url, undefined, captionMs(spoken.text))
    return this.live(gen)
  }

  /** Play an answer / re-teach clip on the overlay board, laid out against what is already drawn (`laid`: already placed). */
  private async playClip(clip: Clip, gen = this.gen, laid?: DrawAction[]): Promise<boolean> {
    if (!this.live(gen)) return false
    const timeline = schedule(laid ?? layoutStep(clip.draw, usePlayer.getState().drawn), clip.duration_ms)
    setVideo({ caption: clip.say, overlay: true })
    setPlayer({ active: timeline, clipTime: 0, caption: clip.say })
    await this.voice.play(clip.audio_url, (ms) => setPlayer({ clipTime: ms }), captionMs(clip.say))
    setPlayer((s) => ({ drawn: [...s.drawn, ...timeline.map((a) => a.action)], active: [], clipTime: 0 }))
    return this.live(gen)
  }

  /** Start generating the answer now, in the background, so its first line is ready when the ack ends. */
  private prefetch(doubtId: string): PendingAnswer {
    const p: PendingAnswer = { clips: [], finished: false, failed: false, notify: null, abort: new AbortController(), segment: null, laid: [] }
    this.prefetches.add(p)
    // A stream that stops delivering would leave the player waiting on the overlay for ever
    let stall = 0
    const arm = () => {
      window.clearTimeout(stall)
      stall = window.setTimeout(() => {
        reportError('video.answer_stalled', `no line for ${ANSWER_STALL_MS} ms`, { doubt_id: doubtId, lines: p.clips.length })
        p.abort.abort()
      }, ANSWER_STALL_MS)
    }
    arm()
    ;(async () => {
      for await (const clip of streamAnswer(doubtId, p.abort.signal)) {
        p.clips.push(clip)
        arm()
        p.notify?.()
      }
    })()
      .catch((e) => {
        p.failed = true
        if (!isAbort(e)) reportError('video.answer_stream', e, { doubt_id: doubtId })
      })
      .finally(() => {
        window.clearTimeout(stall)
        p.finished = true
        this.prefetches.delete(p)
        p.notify?.()
      })
    return p
  }

  /** An answer that will never be played: stop its stream. */
  private dropAnswer(p: PendingAnswer) {
    if (!p.finished) p.abort.abort()
    this.prefetches.delete(p)
  }

  /**
   * Stream the answer onto the overlay board; `from` > 0 resumes an interrupted answer at that clip (the board as it
   * was before it). 'error' when the stream failed or had no lines. A follow-up in the thread whose answer is on the
   * board goes below that answer (the board lifts to make room) instead of replacing it.
   */
  private async playAnswer(doubtId: string, threadId: string, p: PendingAnswer, gen: number, from = 0): Promise<'ok' | 'interrupted' | 'error'> {
    await this.ready
    if (!this.live(gen)) return 'interrupted'
    updateDoubt(doubtId, { status: 'answering' })
    setVideo({ overlay: true })
    if (!p.segment) {
      const drawn = usePlayer.getState().drawn
      // A follow-up in the thread on the board, or a doubt / solution at a checkpoint: below what is there
      if ((threadId === this.boardThread || (this.quiz && this.phase === 'checkpoint')) && drawn.length) {
        p.segment = new Segment(drawn, ++this.boardSegments)
        this.boardThread = threadId
      } else {
        setPlayer({ layer: 'doubt', drawn: [], active: [], clipTime: 0, boardScroll: 0 })
        this.boardThread = threadId
        this.boardSegments = 0
        p.segment = new Segment([], 0)
      }
    }
    const segment = p.segment
    let lines = from
    for (let i = from; ; i++) {
      while (i >= p.clips.length && !p.finished) await new Promise<void>((r) => (p.notify = r))
      if (!this.live(gen)) break
      if (i >= p.clips.length) break
      lines++
      if (this.nowAnswer?.answer === p) Object.assign(this.nowAnswer, { stage: 'answer', clipIdx: i, drawnBefore: usePlayer.getState().drawn })
      // Placed once, so a clip replayed after an empty mic press lands where it was
      const laid = (p.laid[i] ??= [...(i === 0 ? segment.separator() : []), ...segment.place(p.clips[i].draw)])
      await liftBoard(scrollFor(usePlayer.getState().boardScroll, laid), () => this.stopped)
      if (!(await this.playClip(p.clips[i], gen, laid))) break
    }
    if (!this.live(gen)) return 'interrupted'
    updateDoubt(doubtId, { status: lines ? 'answered' : 'dismissed' })
    return p.failed || !lines ? 'error' : 'ok'
  }

  // ---- questions ----

  /** Mic pressed: hold the video (or cut the tutor short) where it is. */
  beginQuestion() {
    if (this.stopped || !this.started || this.micHold || this.routing || this.phase === 'ending') return false
    const s = get()
    if (!micState(s).enabled) return false
    // In a checkpoint the mic only serves the card's follow-up or a thread started there (not e.g. the hold() gap)
    if (this.phase === 'checkpoint' && !s.checkpoint && !s.thread) return false
    this.micHold = true
    this.gen++
    this.promptFollowup = s.awaitingFollowup
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

  /** Mic released without usable audio: the answer that was interrupted goes on, or the prompt comes back. */
  cancelQuestion() {
    if (!this.micHold) return
    this.micHold = false
    setVideo({ listening: false })
    this.settle(this.gen)
  }

  /**
   * Nothing is playing and no question is in flight: pick up the interrupted "now" answer where it was cut, else
   * back to the "Got it?" prompt, the card, or the video.
   */
  private settle(gen: number) {
    const now = this.nowAnswer
    if (now) return void this.guarded('video.thread_resume', gen, (g) => this.resumeNow(now, g))
    if (get().thread?.status === 'open') void this.prompt(gen, this.promptFollowup)
    else this.closeThread(gen)
  }

  /** Replay the ack (if it was cut) or the interrupted clip, then the rest of the answer and its prompt. */
  private async resumeNow(now: NowAnswer, gen: number) {
    setVideo({ mode: 'answering' })
    if (now.stage === 'ack') {
      if (!(await this.say(now.ack, gen))) return
      return this.afterAnswer(now, gen, 0)
    }
    setPlayer({ layer: 'doubt', drawn: now.drawnBefore, active: [], clipTime: 0 })
    return this.afterAnswer(now, gen, now.clipIdx)
  }

  /** The answer from clip `from`, then the thread's line and the "Got it?" prompt. */
  private async afterAnswer(now: NowAnswer, gen: number, from: number) {
    now.stage = 'answer' // the ack is done: an interruption from here on resumes at a clip, not the ack
    now.clipIdx = from
    const outcome = await this.playAnswer(now.doubtId, now.thread.id, now.answer, gen, from)
    if (outcome === 'interrupted') return
    this.nowAnswer = null
    if (outcome === 'error') return this.failThread(gen)
    if (!(await this.afterLine(now.thread, gen))) return
    return this.prompt(gen, false, now.thread.prompt ?? null)
  }

  /** A new question replaced the interrupted answer: its stream stops and the doubt shows what was heard of it. */
  private dropNowAnswer() {
    const now = this.nowAnswer
    if (!now) return
    this.nowAnswer = null
    this.dropAnswer(now.answer)
    updateDoubt(now.doubtId, { status: now.stage === 'answer' ? 'answered' : 'dismissed' })
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
    this.dropNowAnswer() // a real question took over: the interrupted answer is not resumed
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
      const now: NowAnswer = {
        doubtId: res.doubt_id,
        ack: res.ack,
        thread: res.thread,
        answer: this.prefetch(res.doubt_id),
        stage: 'ack',
        clipIdx: 0,
        drawnBefore: [],
      }
      this.nowAnswer = now
      if (!(await this.say(res.ack, gen))) return
      return this.afterAnswer(now, gen, 0)
    }
    if (res.answer_when === 'target_chapter' && res.callback && res.target_ms !== null) {
      const deliverMs = res.deliver_ms ?? res.target_ms
      this.callbacks.push({ doubtId: res.doubt_id, targetMs: res.target_ms, deliverMs, spoken: res.callback, answer: null, played: false })
    }
    if (!(await this.say(res.ack, gen))) return
    if (!(await this.afterLine(res.thread, gen))) return
    // Still open with no turn used: off-topic inside a thread ("Got it?" again) or unclear speech (ask again)
    if (res.thread.status === 'open') return this.prompt(gen, res.thread.turns === 0 || res.intent === 'unclear')
    await this.endThread(res.thread, gen, res.answer_when === 'replay' ? res.replay_from_ms : null)
    // "Let's continue" at a checkpoint's follow-up works like its Continue button
    if (ctx.kind === 'checkpoint' && res.intent === 'continue' && this.live(gen)) this.continueAfterCheckpoint()
  }

  /** The thread's `line` ("Which part is still unclear?", the capped line); false when interrupted. */
  private async afterLine(thread: Thread, gen: number): Promise<boolean> {
    return thread.line ? this.say(thread.line, gen) : this.live(gen)
  }

  /**
   * A closed thread: back to the video or the card. Before the video, the board closes and the recap plays over the
   * paused lecture at the point it resumes from; the video starts only once the recap has finished.
   */
  private async endThread(thread: Thread, gen: number, seekMs: number | null = null) {
    const ctx = this.threadCtx
    if (ctx.kind === 'video' && thread.recap && this.live(gen)) {
      this.showPaused(seekMs ?? resumePointMs(ctx.atMs, this.chapters, this.policy.rewind_ms))
      if (!(await this.say(thread.recap, gen))) return
    }
    // Back to the card for now: the recap plays once the checkpoint hands back to the video
    if (ctx.kind === 'checkpoint' && thread.recap) this.checkpointRecap = thread.recap
    this.closeThread(gen, seekMs)
  }

  /** The lecture video at atMs, paused and silent, with the whiteboard closed. */
  private showPaused(atMs: number) {
    const v = this.video
    const ms = Math.max(0, Math.min(atMs, this.durationMs))
    if (v) {
      this.holdGen++ // a fade-out still running must not fight this
      v.pause()
      v.currentTime = ms / 1000
    }
    setVideo({ overlay: false, positionMs: ms, chapterIdx: chapterAt(ms, this.chapters) })
    setPlayer({ drawn: [], active: [], clipTime: 0, boardScroll: 0 })
  }

  /** Resume the video near where the doubt was asked (or at seekMs), or bring the checkpoint card back. */
  private closeThread(gen: number, seekMs: number | null = null) {
    if (!this.live(gen)) return
    this.clearIdle()
    const ctx = this.threadCtx
    setVideo({ thread: null, promptEndsAt: null, awaitingFollowup: false })
    if (ctx.kind === 'checkpoint') {
      // Back to the question as it was on the board (the doubt's or solution's answer below it is cleared)
      setVideo({ mode: 'checkpoint', checkpoint: { ...ctx.view, busy: false }, overlay: !!this.quiz, caption: '' })
      setPlayer({ layer: 'doubt', drawn: this.quiz?.drawn ?? [], active: [], clipTime: 0, boardScroll: 0, caption: '' })
      this.boardThread = null
      this.boardSegments = 0
      return
    }
    this.phase = 'video'
    this.resumeVideo(seekMs ?? resumePointMs(ctx.atMs, this.chapters, this.policy.rewind_ms))
  }

  /**
   * "Got it?" (or, after "Ask another", wait for the follow-up): the idle timer ends the thread. `ask` is the
   * teacher's spoken "Did you get it?…": the mic is open while she asks, and the wait starts once she has.
   */
  private async prompt(gen: number, followup = false, ask: Spoken | null = null) {
    if (!this.live(gen)) return
    this.clearIdle()
    setVideo({ mode: 'thread_prompt', awaitingFollowup: followup })
    if (ask && !(await this.say(ask, gen))) return
    setVideo({ promptEndsAt: Date.now() + this.policy.idle_ms })
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

  /**
   * A promised doubt is due (the video has played its chapter): pause, "You asked earlier…", then its answer on the
   * board in a thread of its own, with "Got it?" and follow-ups as for a doubt answered now. Delivered once: seeking
   * back over its chapter doesn't answer it again.
   */
  private async playCallback(cb: Callback) {
    if (this.phase !== 'video') return
    const gen = this.gen
    cb.played = true
    const atMs = this.video!.currentTime * 1000
    this.phase = 'thread'
    this.threadCtx = { kind: 'video', atMs }
    this.clearIdle()
    await this.hold()
    setVideo({ mode: 'answering' })
    updateDoubt(cb.doubtId, { status: 'answering' })
    await this.guarded('video.deliver', gen, async () => {
      const thread = await api.deliverDoubt(cb.doubtId)
      if (!this.live(gen)) return
      setVideo({ thread })
      const now: NowAnswer = {
        doubtId: cb.doubtId,
        ack: cb.spoken,
        thread,
        answer: cb.answer ?? this.prefetch(cb.doubtId),
        stage: 'ack',
        clipIdx: 0,
        drawnBefore: [],
      }
      this.nowAnswer = now
      if (!(await this.say(cb.spoken, gen))) return
      return this.afterAnswer(now, gen, 0)
    })
  }

  // ---- checkpoints ----

  /** Pause at the checkpoint, play its intro and ask its not-done questions, then a bridge and resume at at_ms. */
  private async runCheckpoint(cp: Checkpoint) {
    const v = this.video
    if (!v || this.phase !== 'video' || this.stopped) return
    this.phase = 'checkpoint'
    this.checkpointRecap = null
    // Closes the mic right away: with no card yet, a press during the fade-out below would have nowhere to go
    setVideo({ mode: 'checkpoint' })
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
      overlay: true,
    })
    setPlayer({ layer: 'doubt', drawn: [], active: [], clipTime: 0, boardScroll: 0, caption: '' })
    await this.say(cp.intro) // the mic is closed here, so only stop() interrupts
    for (const [qi, q] of cp.questions.entries()) {
      if (this.stopped) return
      if (!this.questionDone(cp.idx, qi)) await this.askQuestion(cp, qi, q)
      if (this.leaveTo !== null) return this.leaveCheckpoint()
    }
    if (this.stopped) return
    this.quiz = null
    setVideo({ checkpoint: null, caption: '', overlay: false })
    setPlayer({ drawn: [], active: [], clipTime: 0, boardScroll: 0, caption: '' })
    // A long doubt thread on the card: its recap over the paused video, a few seconds back, instead of the bridge
    const recap = atEnd ? null : this.checkpointRecap
    this.checkpointRecap = null
    const resumeMs = resumeAfterCheckpoint(cp.at_ms, this.chapters, this.policy.rewind_ms, recap)
    if (recap) {
      this.showPaused(resumeMs)
      if (!(await this.say(recap))) return
    } else {
      const bridge = this.bridge()
      if (bridge && !(await this.say(bridge))) return
    }
    this.phase = 'video'
    if (atEnd) return this.finish()
    this.resumeVideo(resumeMs)
  }

  /** The student sought back from the card: no bridge, the video plays from there. */
  private leaveCheckpoint() {
    const to = this.leaveTo ?? 0
    this.leaveTo = null
    this.checkpointRecap = null
    this.quiz = null
    setVideo({ checkpoint: null, caption: '' })
    this.phase = 'video'
    this.savePosition(to)
    this.resumeVideo(to)
  }

  /**
   * Write the question on the board while it is read out, then its options; A–D and the mic open once they are
   * up. Waits until the student continues or skips.
   */
  private async askQuestion(cp: Checkpoint, qi: number, q: CheckpointQuestion) {
    setVideo({
      checkpoint: { idx: cp.idx, questionIdx: qi, stage: 'answer', wrong: [], result: null, busy: true, followup: false },
      overlay: true,
    })
    setPlayer({ layer: 'doubt', drawn: [], active: [], clipTime: 0, boardScroll: 0 })
    this.boardThread = null
    this.boardSegments = 0
    const layout = quizLayout(q.question, q.options, qi + 1, cp.questions.length, q.image ?? null)
    this.quiz = { layout, bottom: layout.bottom, drawn: [] }
    const continued = new Promise<void>((r) => (this.questionResolve = r))
    const resolve = this.questionResolve
    const live = () => this.questionResolve === resolve && !this.stopped
    await this.ready
    const read = { text: q.say, audio_url: q.audio_url, duration_ms: q.duration_ms }
    if (await this.write(layout.question, read, live)) await this.write(layout.options.flat(), null, live, OPTIONS_MS)
    if (live()) patchCheckpoint({ busy: false, followup: true })
    await continued
    this.questionResolve = null
    void this.voice.cancel()
  }

  /**
   * Write actions on the quiz board while `spoken` plays (or over `silentMs` when there is nothing to say), the board
   * lifting first if they go below it; false once the question was left (continue, skip, seek, stop).
   */
  private async write(draw: DrawAction[], spoken: Spoken | null, live: () => boolean, silentMs = 0): Promise<boolean> {
    if (!live()) return false
    await liftBoard(scrollFor(usePlayer.getState().boardScroll, draw), () => this.stopped)
    const holdMs = spoken ? captionMs(spoken.text) : silentMs
    const timeline = schedule(draw, spoken?.audio_url && spoken.duration_ms ? spoken.duration_ms : holdMs)
    if (spoken) setVideo({ caption: spoken.text })
    setPlayer({ active: timeline, clipTime: 0, caption: spoken?.text ?? '' })
    await this.voice.play(spoken?.audio_url ?? '', (ms) => setPlayer({ clipTime: ms }), holdMs)
    setPlayer((s) => ({ drawn: [...s.drawn, ...timeline.map((a) => a.action)], active: [], clipTime: 0 }))
    if (this.quiz) this.quiz.drawn = usePlayer.getState().drawn
    return live()
  }

  /** A hint or explanation written below what is on the quiz board, while it is spoken (silently when not voiced). */
  private writeNote(id: string, body: string, color: Color, spoken: Spoken | null, live: () => boolean): Promise<boolean> {
    if (!this.quiz) return Promise.resolve(live())
    const { actions, bottom } = note(id, body, this.quiz.bottom, color)
    this.quiz.bottom = bottom
    return this.write(actions, spoken, live, Math.min(captionMs(body), NOTE_SILENT_MS))
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
    let result: VideoCheckResult
    try {
      result = await api.checkpointAnswer(this.sessionId, view.idx, view.questionIdx, chosen, kind)
    } catch (e) {
      reportError('video.checkpoint_answer', e, { checkpoint_idx: view.idx, question_idx: view.questionIdx })
      if (live()) patchCheckpoint({ busy: false })
      setVideo({ error: t(this.lesson, 'answerFailed') })
      return
    }
    this.recordAnswer({ checkpoint_idx: view.idx, question_idx: view.questionIdx, kind, correct: result.correct })
    if (!live() || !this.quiz) return
    const boxes = this.quiz.layout.boxes
    const wrong = result.correct ? view.wrong : [...view.wrong, chosen]
    const hint = !result.correct && kind === 'first' ? q.why_wrong[chosen] : null
    if (hint) {
      // Crossed out on the board, the hint written below, one more try
      patchCheckpoint({ result, wrong, stage: 'retry' })
      if (await this.write(markWrong(boxes[chosen], chosen), result.feedback, live)) {
        await this.writeNote(`qz-hint${wrong.length}`, `Hint: ${hint.text}`, 'orange', hint, live)
      }
    } else {
      // Answered: the right option boxed and ticked (a wrong pick crossed out), then the explanation
      patchCheckpoint({ result, wrong, stage: 'done' })
      const marks = [...(result.correct ? [] : markWrong(boxes[chosen], chosen)), ...markRight(boxes[result.correct_index], result.correct_index)]
      if (await this.write(marks, result.feedback, live)) {
        await this.writeNote('qz-why', result.explanation, 'green', result.explanation_spoken, live)
      }
      if (live() && !result.correct && kind === 'retry') await this.reteach(q, live)
    }
    if (live()) patchCheckpoint({ busy: false, followup: true })
  }

  /** After a second miss: the teacher's replay (card hidden), re-teach clips on the overlay, then the follow-up. */
  private async reteach(q: CheckpointQuestion, live: () => boolean) {
    if (q.replay) {
      patchCheckpoint({ stage: 'reteach' })
      setVideo({ overlay: false })
      await this.replayTeacher(q.replay)
      if (live()) setVideo({ overlay: true, caption: '' })
    }
    if (live() && q.reteach.length) {
      patchCheckpoint({ stage: 'reteach' })
      const segment = new Segment(usePlayer.getState().drawn, 1)
      for (const [i, clip] of q.reteach.entries()) {
        const laid = [...(i === 0 ? segment.separator() : []), ...segment.place(clip.draw)]
        await liftBoard(scrollFor(usePlayer.getState().boardScroll, laid), () => this.stopped)
        if (!live() || !(await this.playClip(clip, this.gen, laid))) break
      }
      if (this.quiz) this.quiz.drawn = usePlayer.getState().drawn
    }
    if (!live()) return
    patchCheckpoint({ stage: 'done', followup: true })
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

  /**
   * The Solution button (once the question is answered): routed like a spoken "show me the solution", so the
   * worked solution plays in a thread of its own below the question, with "Got it?" and follow-ups.
   */
  async showSolution() {
    const cur = this.currentQuestion()
    if (!cur || cur.view.busy || cur.view.stage !== 'done' || this.micHold || this.routing) return
    const { view } = cur
    const gen = ++this.gen
    this.routing = true
    patchCheckpoint({ busy: true })
    let res: VideoQuestionResult
    try {
      res = await api.checkpointSolution(this.sessionId, view.idx, view.questionIdx)
    } catch (e) {
      reportError('video.solution', e, { checkpoint_idx: view.idx, question_idx: view.questionIdx })
      this.routing = false
      if (this.live(gen)) patchCheckpoint({ busy: false })
      setVideo({ error: t(this.lesson, 'questionFailed') })
      return
    }
    this.routing = false
    if (!this.live(gen)) return
    const ctx: ThreadContext = { kind: 'checkpoint', view: { ...view, busy: false } }
    this.threadCtx = ctx
    const atMs = this.checkpoints[view.idx].at_ms
    setVideo((s) => ({
      checkpoint: null,
      thread: res.thread,
      doubts: [...s.doubts, { id: res.doubt_id, atMs, threadId: res.thread_id, question: res.question, intent: res.intent, status: statusFor(res.answer_when) }],
    }))
    await this.guarded('video.thread', gen, () => this.handleRouted(res, ctx, gen))
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
    setVideo({ mode: 'summary', overlay: true, checkpoint: null, caption: t(this.lesson, 'makingSummary') })
    setPlayer({ layer: 'doubt', drawn: [], active: [], clipTime: 0, boardScroll: 0 })
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
