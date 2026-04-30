/**
 * Build API schedule objects and helpers for the schedule editor.
 * Aligned with backend `calculate` / `validate.cross_check` expectations.
 *
 * Placement authority: `plan.config.tasks[].selected_weekdays` is the
 * source of truth for which weekdays each task runs on. The helpers
 * below never invoke `dayNamesForFrequency` directly — they delegate to
 * `selectedWeekdaysForTask`, which tries the config first and only
 * falls back to the legacy template when both the config and the saved
 * weekly pattern are missing.
 */

import { WEEK_DAYS, selectedWeekdaysForTask } from "@/lib/scheduleUtils"
import type { Plan, Task } from "@/types"

export const WEEKS_PER_MONTH = 4.3

/** Positive rational numerator/denom → nearest integer — HALF_EVEN at exact halves.
 *
 * Mirrors ``Decimal.quantize(..., ROUND_HALF_EVEN)`` for values like
 * ``mpd × dpw × (43/10)`` with exact integers everywhere.
 */
function divHalfEvenPositive(numer: bigint, denom: bigint): bigint {
  const q = numer / denom
  const rem = numer % denom
  const twice = rem + rem
  if (twice < denom) return q
  if (twice > denom) return q + 1n
  return q % 2n === 0n ? q : q + 1n
}

/** MDHHS-style round-half-up (non–6064-P paths: ratios, averages). */
export function roundHalfUp(x: number): number {
  return Math.floor(x + 0.5)
}

/** Per-task monthly minutes as displayed on MDHHS-6064-P (banker's rounding). */
export function taskMonthlyMinutes(mpd: number, dpw: number): number {
  const n = BigInt(Math.trunc(mpd)) * BigInt(Math.trunc(dpw)) * 43n
  return Number(divHalfEvenPositive(n, 10n))
}

/** Per-line monthly $ — unrounded mpd × dpw × 4.3 × pay / 60, cents HALF_EVEN (matches backend). */
export function taskMonthlyAmount(mpd: number, dpw: number, payRate: number): number {
  const pc = Math.round(payRate * 100 + Number.EPSILON)
  const n = BigInt(Math.trunc(mpd)) * BigInt(Math.trunc(dpw)) * 43n * BigInt(pc)
  const centInt = divHalfEvenPositive(n * 100n, 60000n)
  return Number(centInt) / 100
}

/** Σ unrounded aggregate minutes → one HALF_EVEN; Σ unrounded $ → one HALF_EVEN (`compute_mdhhs_form_*`). */
export function mdhhsFormTotalsFromAuthorizedTasks(
  tasks: ReadonlyArray<{
    task_name?: string
    min_per_day?: number | string | null
    days_per_week?: number | string | null
  }>,
  payRate: number,
): { monthlyMinutes: number; monthlyAmount: number } {
  const named = tasks.filter((t) => String(t.task_name ?? "").trim() !== "")
  const pc = Math.round(payRate * 100 + Number.EPSILON)
  let sumMinNumer = 0n
  let sumUsdNumer = 0n
  for (const t of named) {
    const mpd = Math.trunc(Number(t.min_per_day) || 0)
    const dpw = Math.trunc(Number(t.days_per_week) || 0)
    const m = BigInt(mpd) * BigInt(dpw) * 43n
    sumMinNumer += m
    sumUsdNumer += m * BigInt(pc)
  }
  const monthlyMinutes = Number(divHalfEvenPositive(sumMinNumer, 10n))
  const centsTotal = divHalfEvenPositive(sumUsdNumer * 100n, 60000n)
  const monthlyAmount = Number(centsTotal) / 100
  return { monthlyMinutes, monthlyAmount }
}

/**
 * Calendar / DB monthly minute target — prefer ``mdhhs_monthly_minutes`` on the
 * schedule payload (per-line Σ); fall back to legacy top-level fields.
 */
export function scheduledMonthlyMinutesFromPlan(plan: Plan): number {
  const s = plan.schedule as Record<string, unknown> | undefined
  const raw =
    (s?.mdhhs_monthly_minutes as number | undefined) ??
    (s?.monthly_minutes as number | undefined) ??
    plan.monthly_minutes
  const n = Number(raw)
  return Number.isFinite(n) && n > 0 ? Math.round(n) : 0
}

/** Σ ``duration_min`` from ``daily_schedule``, or ``delivered_minutes`` when present on payload. */
export function deliveredMinutesFromPlan(plan: Plan): number {
  const s = plan.schedule as Record<string, unknown> | undefined
  const direct =
    (s?.delivered_minutes as number | undefined) ??
    (plan.delivered_minutes as number | undefined)
  if (direct != null && Number.isFinite(Number(direct))) {
    return Math.round(Number(direct))
  }
  const ds = s?.daily_schedule
  if (Array.isArray(ds)) {
    return ds.reduce(
      (acc, row) =>
        acc + Number((row as { duration_min?: number }).duration_min ?? 0),
      0,
    )
  }
  return 0
}

export type BillingCapStatus = "at_cap" | "under_cap" | "exact"

/** ASM 144 posture: delivered vs 6064-P authorized minutes for this calendar month. */
export function billingCapStatus(
  authorizedMin: number,
  deliveredMin: number,
): BillingCapStatus {
  if (deliveredMin > authorizedMin) return "at_cap"
  if (deliveredMin < authorizedMin) return "under_cap"
  return "exact"
}

export function computeWeeklyBudget(
  tasks: Array<{ min_per_day?: number; days_per_week?: number }>
): number {
  return tasks.reduce(
    (s, t) => s + (Number(t.min_per_day) || 0) * (Number(t.days_per_week) || 0),
    0
  )
}

/** Build full schedule dict for PATCH/validate from editor state. */
export function buildScheduleForApi(
  times: Record<string, { start: string; end: string }>,
  /**
   * For each non-empty task name, which long weekday names (e.g. "Monday") are selected.
   */
  grid: Map<string, Set<string>>,
  tasks: Task[],
  payRate: number
): {
  weekly_minutes: number
  monthly_minutes: number
  monthly_amount: number
  days: Record<
    string,
    { start: string; end: string; minutes: number; tasks: string[] }
  >
} {
  const named = tasks.filter((t) => (t.task_name || "").trim())
  const days: Record<string, { start: string; end: string; minutes: number; tasks: string[] }> = {}
  let weeklyMinutes = 0

  for (const d of WEEK_DAYS) {
    const st = (times[d]?.start ?? "1:00 PM").trim() || "1:00 PM"
    const en = (times[d]?.end ?? "8:00 PM").trim() || "8:00 PM"
    const names: string[] = []
    for (const t of named) {
      const n = String(t.task_name)
      if (grid.get(n)?.has(d)) names.push(n)
    }
    let minutes = 0
    for (const t of named) {
      const n = String(t.task_name)
      if (!grid.get(n)?.has(d)) continue
      minutes += Number(t.min_per_day) || 0
    }
    weeklyMinutes += minutes
    days[d] = { start: st, end: en, minutes, tasks: names }
  }

  // Per-line summation — matches MDHHS-6064-P and the backend's
  // `mdhhs_form_minutes` / `mdhhs_form_amount` (post–2026 per-line refactor).
  const { monthlyMinutes, monthlyAmount } = mdhhsFormTotalsFromAuthorizedTasks(
    tasks,
    payRate,
  )
  return {
    weekly_minutes: weeklyMinutes,
    monthly_minutes: monthlyMinutes,
    monthly_amount: monthlyAmount,
    days,
  }
}

/**
 * Hydrate the (times, task→weekdays grid) initial editor state from a
 * persisted plan.
 *
 * Weekday placement resolution — `selectedWeekdaysForTask` enforces the
 * standard 3-tier lookup:
 *   1. `plan.config.tasks[].selected_weekdays` (authoritative).
 *   2. `plan.schedule.days[weekday].tasks[]` (last calibration).
 *   3. `dayNamesForFrequency(days_per_week)` (legacy template fallback,
 *      only used when tiers 1 and 2 are both empty).
 *
 * Per-weekday start times still come from `plan.config.start_time_by_
 * weekday` (preferred) before falling back to the saved weekly pattern,
 * so reloads don't snap the time grid back to idle defaults when the
 * user has customized it.
 */
export function initEditorFromPlan(plan: Plan): {
  times: Record<string, { start: string; end: string }>
  grid: Map<string, Set<string>>
} {
  const times: Record<string, { start: string; end: string }> = {}
  const grid = new Map<string, Set<string>>()
  const dayMap = (plan.schedule?.days ?? {}) as Record<
    string,
    { start?: string; end?: string; tasks?: string[] }
  >
  const cfgStart = (plan.config?.start_time_by_weekday ?? {}) as Record<
    string,
    string | undefined
  >

  for (const d of WEEK_DAYS) {
    const startFromCfg = cfgStart[d]
    const b = dayMap[d] ?? {}
    const start =
      typeof startFromCfg === "string" && startFromCfg
        ? startFromCfg
        : b.start != null
          ? String(b.start)
          : "1:00 PM"
    const end = b.end != null ? String(b.end) : "8:00 PM"
    times[d] = { start, end }
  }
  for (const t of plan.tasks) {
    const n = String(t.task_name || "").trim()
    if (!n) continue
    const dpw = Number(t.days_per_week) || 0
    const weekdays = selectedWeekdaysForTask(plan, n, dpw)
    const s = new Set<string>()
    for (const d of weekdays) {
      if ((WEEK_DAYS as readonly string[]).includes(d)) s.add(d)
    }
    grid.set(n, s)
  }
  return { times, grid }
}

export function parse12hToMinutes(s: string): number | null {
  const t = s.trim()
  const m = t.match(/^(\d{1,2}):(\d{2})\s*([AP]M)$/i)
  if (!m) return null
  const hh = parseInt(m[1]!, 10)
  const mi = parseInt(m[2]!, 10)
  const ap = m[3]!.toUpperCase()
  if (mi < 0 || mi > 59 || hh < 1 || hh > 12) return null
  let h24: number
  if (ap === "AM") {
    h24 = hh === 12 ? 0 : hh
  } else {
    h24 = hh === 12 ? 12 : hh + 12
  }
  return h24 * 60 + mi
}

export function formatMinutesTo12h(total: number): string {
  let m = ((total % (24 * 60)) + 24 * 60) % (24 * 60)
  const h24 = Math.floor(m / 60)
  const mi = m % 60
  if (h24 === 0) {
    return `12:${String(mi).padStart(2, "0")} AM`
  }
  if (h24 < 12) {
    return `${h24}:${String(mi).padStart(2, "0")} AM`
  }
  if (h24 === 12) {
    return `12:${String(mi).padStart(2, "0")} PM`
  }
  return `${h24 - 12}:${String(mi).padStart(2, "0")} PM`
}

/** Naive same-day window length in minutes. If end < start, assume next calendar day. */
export function visitWindowMinutes(start: string, end: string): number | null {
  const a = parse12hToMinutes(start)
  const b = parse12hToMinutes(end)
  if (a == null || b == null) return null
  if (b >= a) return b - a
  return b + 24 * 60 - a
}
