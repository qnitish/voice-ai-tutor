/** What the mic button drives: the scene Tutor (engine/tutor) or the VideoTutor (engine/video/videoTutor). */
export interface MicHost {
  /** Start recording a question; false when asking isn't possible right now */
  beginQuestion(): boolean
  cancelQuestion(): void
  submitQuestion(audio: Blob): Promise<void>
}

/** Mic button state supplied by a page whose player isn't the scene Tutor's store. */
export interface MicState {
  enabled: boolean
  listening: boolean
  thinking: boolean
}
