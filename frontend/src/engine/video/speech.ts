/**
 * The tutor's voice on the video page: one <audio> element that plays one clip at a time and can be
 * faded out mid-word (a mic press, a pick on the checkpoint card). The cancelled play() resolves like a
 * finished one; callers tell the two apart with their own generation counter.
 *
 * A line without audio (TTS failed on the server; the text still arrives) is "played" silently: the caller's
 * caption stays up for `silentMs` and the ticks report the elapsed time, so a drawing still animates.
 */
import { FADE_MS, fadeTo } from '@/engine/fade'

/** How long a caption stays up when a line has no audio: ~60 ms per character, at least 1.2 s */
export function captionMs(text: string): number {
  return Math.max(1200, text.length * 60)
}

/** A one-sample silent WAV, used to unlock the element inside a user gesture (Safari allows play() per element). */
function silentWav(): string {
  const header = [
    0x52, 0x49, 0x46, 0x46, 38, 0, 0, 0, 0x57, 0x41, 0x56, 0x45, 0x66, 0x6d, 0x74, 0x20, 16, 0, 0, 0, 1, 0, 1, 0, 0x40, 0x1f, 0, 0, 0x80, 0x3e, 0, 0, 2, 0,
    16, 0, 0x64, 0x61, 0x74, 0x61, 2, 0, 0, 0, 0, 0,
  ]
  return `data:audio/wav;base64,${btoa(String.fromCharCode(...header))}`
}

export class Speech {
  private el = new Audio()
  /** Settles the pending play(); null when nothing is playing */
  private current: (() => void) | null = null

  /**
   * Call inside the click that starts the lesson: plays (and stops) a silent clip so Safari treats later
   * programmatic play() calls on this element as user-initiated. A no-op while something is playing.
   */
  unlock() {
    if (this.current) return
    const el = this.el
    el.src = silentWav()
    void el.play().then(
      () => {
        if (!this.current) el.pause() // a real line may have started meanwhile
      },
      () => {},
    )
  }

  play(url: string | null | undefined, onTime?: (ms: number) => void, silentMs = 1200): Promise<void> {
    this.current?.()
    const el = this.el
    return new Promise((resolve) => {
      let raf = 0
      let timer = 0
      const done = () => {
        if (this.current !== done) return
        this.current = null
        cancelAnimationFrame(raf)
        window.clearTimeout(timer)
        el.onended = null
        el.onerror = null
        resolve()
      }
      this.current = done
      if (!url) {
        // No audio for this line: hold the caption for a while, ticking so a drawing still progresses
        const startedAt = performance.now()
        if (onTime) {
          const tick = () => {
            onTime(performance.now() - startedAt)
            raf = requestAnimationFrame(tick)
          }
          raf = requestAnimationFrame(tick)
        }
        timer = window.setTimeout(done, silentMs)
        return
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

  /** Fade out what is playing and settle its play(); a play() started during the fade is left alone. */
  async cancel(): Promise<void> {
    const done = this.current
    if (!done) return
    await fadeTo(this.el, 0, FADE_MS)
    if (this.current === done) {
      this.el.pause()
      done()
    }
  }

  /** Immediate and final. */
  stop() {
    this.current?.()
    this.el.pause()
  }
}
