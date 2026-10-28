/** Client-side failures, logged and sent to the server's session events (backend app/telemetry). */
import { api } from '@/lib/api'

let context: { session_id?: string | null } = {}
let sent = 0
/** A runaway error loop (e.g. in a render) must not flood the server */
const MAX_REPORTS = 50

/** Events reported from now on carry this session id. */
export function setTelemetrySession(sessionId: string | null) {
  context = { session_id: sessionId }
}

/** Report a failure (`kind` like "video.answer_stream"); never throws. */
export function reportError(kind: string, error: unknown, ctx: Record<string, unknown> = {}): void {
  const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error)
  console.error(`[${kind}]`, error, ctx)
  if (sent++ >= MAX_REPORTS) return
  void api.clientEvent({ ...context, kind, level: 'error', data: { message, ...ctx } })
}

/** Report uncaught errors and unhandled promise rejections (called once from main.tsx). */
export function installErrorReporting(): void {
  window.addEventListener('error', (e) => {
    reportError('window.error', e.error ?? e.message, { source: e.filename, line: e.lineno, col: e.colno })
  })
  window.addEventListener('unhandledrejection', (e) => {
    reportError('window.unhandledrejection', e.reason)
  })
}
