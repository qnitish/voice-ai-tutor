/** Voice activity detection for tap-to-talk: stop recording once the student goes quiet. */

export type VadState = 'waiting' | 'speaking' | 'end' | 'no_speech' | 'max'

export function rms(samples: ArrayLike<number>): number {
  if (!samples.length) return 0
  let sum = 0
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i]
  return Math.sqrt(sum / samples.length)
}

export interface VadOptions {
  /** RMS level that counts as speech */
  threshold?: number
  /** Quiet time after speech that ends the recording */
  silenceMs?: number
  /** Give up if nothing is said for this long */
  noSpeechMs?: number
  maxMs?: number
}

export class SilenceDetector {
  private readonly threshold: number
  private readonly silenceMs: number
  private readonly noSpeechMs: number
  private readonly maxMs: number
  private t0: number | null = null
  private lastVoice = 0
  private heard = false
  private terminal: VadState | null = null

  constructor({ threshold = 0.02, silenceMs = 1200, noSpeechMs = 6000, maxMs = 25000 }: VadOptions = {}) {
    this.threshold = threshold
    this.silenceMs = silenceMs
    this.noSpeechMs = noSpeechMs
    this.maxMs = maxMs
  }

  push(level: number, atMs: number): VadState {
    if (this.terminal) return this.terminal
    this.t0 ??= atMs
    if (atMs - this.t0 >= this.maxMs) return (this.terminal = 'max')
    if (level >= this.threshold) {
      this.heard = true
      this.lastVoice = atMs
      return 'speaking'
    }
    if (!this.heard) return atMs - this.t0 >= this.noSpeechMs ? (this.terminal = 'no_speech') : 'waiting'
    return atMs - this.lastVoice >= this.silenceMs ? (this.terminal = 'end') : 'speaking'
  }
}

/** A quick tap starts tap-to-talk; a longer press is hold-to-talk. */
export function pressKind(downAtMs: number, upAtMs: number, tapMaxMs = 300): 'tap' | 'hold' {
  return upAtMs - downAtMs < tapMaxMs ? 'tap' : 'hold'
}
