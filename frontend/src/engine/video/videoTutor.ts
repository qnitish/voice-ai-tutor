/**
 * Plays a video lesson: the lecture video with checkpoints, doubt threads and an answer overlay.
 * Writes store/video (and the overlay board into store/player); components only read the stores and call these
 * methods. Skeleton: implemented per docs/plan/video-lesson-spec.md.
 */
import type { MicHost } from '@/engine/host'
import type { Lesson, VideoSessionState } from '@/lib/types'

export class VideoTutor implements MicHost {
  /** The page's <video> element; called once it is mounted */
  attach(video: HTMLVideoElement): void {
    void video
    throw new Error('not implemented')
  }

  /** Start (or resume at fromMs with the session's earlier answers and doubts) */
  async start(lesson: Lesson, sessionId: string, fromMs?: number, restored?: VideoSessionState): Promise<void> {
    void lesson
    void sessionId
    void fromMs
    void restored
    throw new Error('not implemented')
  }

  /** Permanent: a new VideoTutor is made per page mount */
  stop(): void {
    throw new Error('not implemented')
  }

  togglePause(): void {
    throw new Error('not implemented')
  }

  /** Seek by the student (applies checkpoints.clampSeek) */
  seek(ms: number): void {
    void ms
    throw new Error('not implemented')
  }

  beginQuestion(): boolean {
    throw new Error('not implemented')
  }

  cancelQuestion(): void {
    throw new Error('not implemented')
  }

  async submitQuestion(audio: Blob): Promise<void> {
    void audio
    throw new Error('not implemented')
  }

  /** The "Got it?" prompt: understood ends the thread, otherwise the student asks a follow-up */
  async reply(understood: boolean): Promise<void> {
    void understood
    throw new Error('not implemented')
  }

  async answerCheckpoint(chosen: number): Promise<void> {
    void chosen
    throw new Error('not implemented')
  }

  /** Skip the current checkpoint question */
  skipCheckpointQuestion(): void {
    throw new Error('not implemented')
  }

  /** Close the shown answer / follow-up and go to the next question or back to the video */
  continueAfterCheckpoint(): void {
    throw new Error('not implemented')
  }
}
