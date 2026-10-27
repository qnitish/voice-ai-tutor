/** Client-side failures, logged and sent to the server's session events (backend app/telemetry). */
import { api } from '@/lib/api'

let context: { session_id?: string | null } = {}

/** Events reported from now on carry this session id. */
export function setTelemetrySession(sessionId: string | null) {
  context = { session_id: sessionId }
}

/** Report a failure (`kind` like "video.answer_stream"); never throws. */
export function reportError(kind: string, error: unknown, ctx: Record<string, unknown> = {}): void {
  const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error)
  console.error(`[${kind}]`, error, ctx)
  void api.clientEvent({ ...context, kind, level: 'error', data: { message, ...ctx } })
}
