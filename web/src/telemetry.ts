import { api } from './api'
import type { TelemetryUiEvent } from './api'

/** The setup steps as the schema names them. Welcome is its own screen; the rest are SetupShell's 1-5. */
export const SETUP_STEPS = ['welcome', 'folder', 'format', 'telegram', 'soulseek', 'ready'] as const
export type SetupStepName = typeof SETUP_STEPS[number]
export type StepAction = 'shown' | 'completed' | 'skipped' | 'back'

/* The schema's `dur` bucket edges, copied from flackey/telemetry/buckets.py: a value below an edge
   takes that edge's label. A time leaves as one of these labels, never as the number. */
const DUR: [number, string][] = [[1, '<1s'], [5, '1-5s'], [15, '5-15s'], [60, '15-60s'], [300, '1-5m'],
  [900, '5-15m'], [3600, '15-60m'], [6 * 3600, '1-6h'], [24 * 3600, '6-24h'], [3 * 86400, '1-3d']]

export function durationBucket(ms: number): string {
  const s = Math.max(0, ms) / 1000
  for (const [edge, label] of DUR) if (s < edge) return label
  return '>3d'
}

/* Every post waits on the opt-in, so the funnel's first event lands after the PUT that switched stats
   on -- without the wizard itself ever waiting on it. */
let gate: Promise<unknown> = Promise.resolve()

/** Switch usage stats on (the Welcome step's ticked box). Never throws, never blocks. */
export function optIn(): void {
  gate = api.setTelemetry(true).catch(() => undefined)
}

/** Fire and forget. The server drops it if the owner has not opted in, and a failure costs nothing.
 *  Only enums, buckets and booleans go in `fields`: never paths, names or free text. */
export function track(event: TelemetryUiEvent, fields: Record<string, string | boolean>): void {
  gate.then(() => api.telemetryUi(event, fields)).catch(() => undefined)
}

/** For tests: forget a previous opt-in's pending gate. */
export function resetTelemetryGate(): void { gate = Promise.resolve() }
