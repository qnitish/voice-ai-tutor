/** Video time as m:ss (the lecture is under an hour). */
export function mmss(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** Fill `{name}` slots of a UI line, e.g. fill('Question {n} of {total}', { n: 1, total: 3 }). */
export function fill(line: string, vars: Record<string, string | number>): string {
  return Object.entries(vars).reduce((out, [k, v]) => out.replace(`{${k}}`, String(v)), line)
}
