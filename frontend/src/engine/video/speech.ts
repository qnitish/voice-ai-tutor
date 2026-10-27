/**
 * The tutor's voice on the video page: one <audio> element that plays one clip at a time and can be
 * faded out mid-word (a mic press, a pick on the checkpoint card). The cancelled play() resolves like a
 * finished one; callers tell the two apart with their own generation counter.
 */
import { FADE_MS, fadeTo } from '@/engine/fade'

export class Speech {
  private el = new Audio()
  /** Settles the pending play(); null when nothing is playing */
  private current: (() => void) | null = null

  play(url: string, onTime?: (ms: number) => void): Promise<void> {
    this.current?.()
    const el = this.el
    return new Promise((resolve) => {
      let raf = 0
      const done = () => {
        if (this.current !== done) return
        this.current = null
        cancelAnimationFrame(raf)
        el.onended = null
        el.onerror = null
        resolve()
      }
      this.current = done
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
