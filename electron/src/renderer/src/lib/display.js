/**
 * display — one place where "we have no value" becomes visible text.
 *
 * The LIVE DATA INTEGRITY INVARIANT (2026-07-31): while `connected` is true the
 * HUD may not show a synthetic, placeholder, state-derived or random value in
 * any field. A field with no real value says so.
 *
 * This module exists because the previous behaviour was the opposite, in three
 * different ways at once, and each one had to be found separately:
 *   - the server fabricated CPU/RAM/GPU with random.uniform() when psutil was
 *     missing, and hardcoded latency to 0;
 *   - this hook seeded metrics with invented numbers so a freshly-connected
 *     HUD looked fully instrumented before any frame arrived;
 *   - panels hardcoded strings outright — a model label derived from the
 *     animation state ('thinking' ? 'Gemini 2.5 Pro' : 'Gemini 2.5 Flash'),
 *     'VOICE SYNTH · EDGE-TTS' when the real engine is Piper, and a literal
 *     Tailscale IP.
 *
 * Keeping the formatting here means a panel cannot accidentally re-introduce a
 * plausible default: it has nothing to write but NO_VALUE.
 */

export const NO_VALUE = '—'

/** True when a value is genuinely absent (null/undefined/NaN) — 0 is a real reading. */
export function isMissing(v) {
  return v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v))
}

/** Number with optional unit, or NO_VALUE. `digits` rounds; omit for integers. */
export function fmtNum(v, unit = '', digits = 0) {
  if (isMissing(v)) return NO_VALUE
  const n = typeof digits === 'number' ? Number(v).toFixed(digits) : v
  return `${digits > 0 ? n : Math.round(Number(v))}${unit}`
}

/** Any string-ish value, or NO_VALUE when absent/empty. */
export function fmtText(v) {
  if (isMissing(v)) return NO_VALUE
  const s = String(v).trim()
  return s === '' ? NO_VALUE : s
}

/**
 * "model · Provider" from a model_status frame, or NO_VALUE when no turn has
 * run yet. Mirrors jarvis/providers/labels.py:describe_model so the HUD and the
 * CLI cannot describe the same turn differently.
 */
const PROVIDER_LABELS = {
  ollama: 'Ollama',
  vertex: 'Vertex',
  aistudio: 'AI Studio',
  local: 'Ollama',
  groq: 'Groq',
  gemini: 'Gemini',
}

export function providerLabel(provider) {
  if (isMissing(provider) || provider === '') return NO_VALUE
  return PROVIDER_LABELS[provider] || provider.charAt(0).toUpperCase() + provider.slice(1)
}

export function describeModel(modelStatus) {
  const model = modelStatus?.model
  if (isMissing(model) || model === '') return NO_VALUE
  const label = providerLabel(modelStatus?.provider)
  return label === NO_VALUE ? model : `${model} · ${label}`
}

/** Uppercase caption form, e.g. "QWEN3:8B · OLLAMA" — or a "no value" caption. */
export function describeModelCaption(modelStatus) {
  const d = describeModel(modelStatus)
  return d === NO_VALUE ? 'MODEL BİLİNMİYOR' : d.toUpperCase()
}
