/** Pure timeline rules of the video player (unit-tested; implemented per docs/plan/video-lesson-spec.md). */
import type { AnsweredQuestion, Chapter, Checkpoint } from '@/lib/types'

/** Checkpoints whose every question has a first answer or a skip. */
export function doneCheckpoints(checkpoints: Checkpoint[], answered: AnsweredQuestion[]): Set<number> {
  void checkpoints
  void answered
  throw new Error('not implemented')
}

/** The first not-done checkpoint crossed while playing from prevMs to nowMs (prevMs < at_ms <= nowMs), else null. */
export function dueCheckpoint(prevMs: number, nowMs: number, checkpoints: Checkpoint[], done: Set<number>): Checkpoint | null {
  void prevMs
  void nowMs
  void checkpoints
  void done
  throw new Error('not implemented')
}

/** Where a seek from fromMs to toMs lands: back anywhere; forward stops at the first not-done checkpoint passed. */
export function clampSeek(fromMs: number, toMs: number, checkpoints: Checkpoint[], done: Set<number>): number {
  void fromMs
  void toMs
  void checkpoints
  void done
  throw new Error('not implemented')
}

/** Index of the chapter playing at ms (the last chapter starting at or before ms; 0 when none). */
export function chapterAt(ms: number, chapters: Chapter[]): number {
  void ms
  void chapters
  throw new Error('not implemented')
}

/** Where to resume after pausing at atMs: rewindMs earlier, but not before its chapter's start or 0. */
export function resumePointMs(atMs: number, chapters: Chapter[], rewindMs: number): number {
  void atMs
  void chapters
  void rewindMs
  throw new Error('not implemented')
}
