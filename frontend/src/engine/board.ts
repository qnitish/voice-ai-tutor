import type { DrawAction } from '@/lib/types'

/** Everything the earlier steps of a scene drew, to restore the board when resuming mid-scene. */
export function boardBefore(scene: { steps: { draw: DrawAction[] }[] }, stepIdx: number): DrawAction[] {
  return scene.steps.slice(0, Math.max(0, stepIdx)).flatMap((s) => s.draw)
}

export function progressPercent(lesson: { scenes: { steps: unknown[] }[] }, sceneIdx: number, stepIdx: number): number {
  const total = lesson.scenes.reduce((n, s) => n + s.steps.length, 0)
  if (!total) return 0
  const done = lesson.scenes.slice(0, Math.max(0, sceneIdx)).reduce((n, s) => n + s.steps.length, 0) + stepIdx
  return Math.min(100, Math.max(0, Math.round((100 * done) / total)))
}
