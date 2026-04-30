/**
 * State, types, and pure helpers for the drag-and-drop schedule editor.
 *
 * The editor models the schedule as a flat list of *placements* — one per
 * (day, task) occurrence — because dnd-kit draggables need stable ids and
 * cross-container moves are clearer with a single list than a nested map.
 *
 * All state transitions are pure so the undo/redo stack can snapshot freely.
 *
 * Placement authority (IMPORTANT):
 *   `plan.config.tasks[].selected_weekdays` is the authoritative source
 *   for weekday placement; `selected_dates` is the authoritative source
 *   for one-off per-date overrides (e.g. a month's 5th-Wed catch-up).
 *   `planToEditorState` reads both and carries `selected_dates` through
 *   `EditorState.selectedDatesByTask` so that editing the weekday grid
 *   never silently resets a user's per-date overrides. The weekday
 *   template (`dayNamesForFrequency`) is only consulted for truly
 *   legacy plans that never went through the ScheduleConfig editor.
 */

import { WEEK_DAYS, selectedWeekdaysForTask } from "@/lib/scheduleUtils"
import { mdhhsFormTotalsFromAuthorizedTasks } from "@/lib/scheduleBuild"
import type { Plan, ScheduleConfig, Task, TaskPlacement } from "@/types"

export type DayName = (typeof WEEK_DAYS)[number]

export type Placement = {
  /** Stable id usable as a dnd-kit draggable key. */
  id: string
  day: DayName
  taskName: string
  /** Minutes for this specific placement — defaults to task.min_per_day. */
  minutes: number
}

export type EditorTimes = Record<DayName, { start: string; end: string }>

export interface EditorState {
  placements: Placement[]
  times: EditorTimes
  /**
   * Per-task explicit dates (ISO "YYYY-MM-DD") carried verbatim from
   * `plan.config.tasks[].selected_dates`. The weekday grid cannot
   * display them, but they must round-trip through save so
   * month-specific placements (e.g. a 5th-Wed catch-up) survive edits.
   * Undefined is treated as an empty record — legacy plans without a
   * config never have dates to preserve.
   */
  selectedDatesByTask?: Record<string, string[]>
}

export interface TaskMeta {
  name: string
  minPerDay: number
  daysPerWeek: number
  monthlyAmount: number
}

const DEFAULT_START = "1:00 PM"
const DEFAULT_END = "8:00 PM"

let __uid = 0
function nextId(): string {
  __uid += 1
  return `p${Date.now().toString(36)}${__uid.toString(36)}`
}

// ---------------------------------------------------------------------------
// Task metadata
// ---------------------------------------------------------------------------

export function taskMetaList(tasks: readonly Task[]): TaskMeta[] {
  return tasks
    .filter((t) => (t.task_name || "").trim())
    .map((t) => ({
      name: String(t.task_name),
      minPerDay: Number(t.min_per_day) || 0,
      daysPerWeek: Number(t.days_per_week) || 0,
      monthlyAmount: Number(t.monthly_amount) || 0,
    }))
}

export function taskMetaByName(tasks: readonly Task[]): Map<string, TaskMeta> {
  const m = new Map<string, TaskMeta>()
  for (const meta of taskMetaList(tasks)) m.set(meta.name, meta)
  return m
}

// ---------------------------------------------------------------------------
// Plan -> editor state
// ---------------------------------------------------------------------------

/**
 * Hydrate the editor state from a persisted plan.
 *
 * Weekday placement resolution follows the 3-tier rule in
 * `selectedWeekdaysForTask`:
 *   1. `plan.config.tasks[].selected_weekdays` (authoritative).
 *   2. `plan.schedule.days[weekday].tasks[]` (last calibration).
 *   3. `dayNamesForFrequency(days_per_week)` (legacy template).
 *
 * Per-task `selected_dates` from `plan.config` are preserved in
 * `state.selectedDatesByTask` verbatim, so month-specific overrides
 * survive the round-trip through the weekday-only editor grid.
 *
 * Per-weekday start times prefer `plan.config.start_time_by_weekday`
 * before falling back to the saved weekly pattern, keeping customized
 * times stable across reloads.
 */
export function planToEditorState(plan: Plan): EditorState {
  const times: EditorTimes = {} as EditorTimes
  const dayMap = (plan.schedule?.days ?? {}) as Record<
    string,
    { start?: unknown; end?: unknown; tasks?: string[] }
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
          : DEFAULT_START
    times[d as DayName] = {
      start,
      end: b.end != null ? String(b.end) : DEFAULT_END,
    }
  }

  const metaByName = taskMetaByName(plan.tasks)
  const placements: Placement[] = []
  const selectedDatesByTask: Record<string, string[]> = {}
  const cfgTasks = (plan.config?.tasks ?? []) as Array<{
    task_name?: string
    selected_dates?: string[]
  }>
  const datesByName = new Map<string, string[]>()
  for (const ct of cfgTasks) {
    const nm = String(ct.task_name ?? "").trim()
    if (nm && Array.isArray(ct.selected_dates)) {
      datesByName.set(nm, ct.selected_dates.slice())
    }
  }

  for (const t of plan.tasks) {
    const n = String(t.task_name || "").trim()
    if (!n || !metaByName.has(n)) continue
    const dpw = metaByName.get(n)!.daysPerWeek
    const weekdays = selectedWeekdaysForTask(plan, n, dpw)
    for (const d of weekdays) {
      if (!(WEEK_DAYS as readonly string[]).includes(d)) continue
      placements.push({
        id: nextId(),
        day: d as DayName,
        taskName: n,
        minutes: metaByName.get(n)!.minPerDay,
      })
    }
    const dates = datesByName.get(n) ?? []
    if (dates.length > 0) selectedDatesByTask[n] = dates
  }

  return { placements, times, selectedDatesByTask }
}

// ---------------------------------------------------------------------------
// Derived selectors
// ---------------------------------------------------------------------------

export function placementsByDay(
  state: EditorState,
): Record<DayName, Placement[]> {
  const out: Record<DayName, Placement[]> = Object.fromEntries(
    WEEK_DAYS.map((d) => [d as DayName, [] as Placement[]]),
  ) as Record<DayName, Placement[]>
  for (const p of state.placements) {
    out[p.day].push(p)
  }
  return out
}

export function placedCountByTask(state: EditorState): Map<string, number> {
  const m = new Map<string, number>()
  for (const p of state.placements) {
    m.set(p.taskName, (m.get(p.taskName) ?? 0) + 1)
  }
  return m
}

export function minutesByDay(state: EditorState): Record<DayName, number> {
  const out: Record<DayName, number> = Object.fromEntries(
    WEEK_DAYS.map((d) => [d, 0]),
  ) as Record<DayName, number>
  for (const p of state.placements) out[p.day] += p.minutes
  return out
}

export function weeklyMinutes(state: EditorState): number {
  return state.placements.reduce((s, p) => s + p.minutes, 0)
}

export function isPlaced(
  state: EditorState,
  day: DayName,
  taskName: string,
): boolean {
  return state.placements.some((p) => p.day === day && p.taskName === taskName)
}

// ---------------------------------------------------------------------------
// State mutations — all return new state (safe to snapshot)
// ---------------------------------------------------------------------------

export function addPlacement(
  state: EditorState,
  day: DayName,
  taskName: string,
  minutes: number,
): EditorState {
  if (isPlaced(state, day, taskName)) return state
  return {
    ...state,
    placements: [
      ...state.placements,
      { id: nextId(), day, taskName, minutes },
    ],
  }
}

export function removePlacement(state: EditorState, id: string): EditorState {
  return {
    ...state,
    placements: state.placements.filter((p) => p.id !== id),
  }
}

export function movePlacement(
  state: EditorState,
  id: string,
  toDay: DayName,
): EditorState {
  const existing = state.placements.find((p) => p.id === id)
  if (!existing) return state
  if (existing.day === toDay) return state
  // If the target day already has this task placed, drop the drag (no-op).
  if (isPlaced(state, toDay, existing.taskName)) return state
  return {
    ...state,
    placements: state.placements.map((p) =>
      p.id === id ? { ...p, day: toDay } : p,
    ),
  }
}

export function setPlacementMinutes(
  state: EditorState,
  id: string,
  minutes: number,
): EditorState {
  const clamped = Math.max(0, Math.round(minutes))
  return {
    ...state,
    placements: state.placements.map((p) =>
      p.id === id ? { ...p, minutes: clamped } : p,
    ),
  }
}

export function setDayTime(
  state: EditorState,
  day: DayName,
  key: "start" | "end",
  value: string,
): EditorState {
  return {
    ...state,
    times: {
      ...state.times,
      [day]: { ...state.times[day], [key]: value },
    },
  }
}

// ---------------------------------------------------------------------------
// Build API schedule payload
// ---------------------------------------------------------------------------

export interface ApiSchedule {
  weekly_minutes: number
  monthly_minutes: number
  monthly_amount: number
  days: Record<
    string,
    { start: string; end: string; minutes: number; tasks: string[] }
  >
}

/**
 * Build the legacy weekly-pattern payload consumed by `PATCH /plans/{v}`.
 *
 * Note — this payload shape **cannot represent per-date overrides**
 * (`selected_dates`). Callers that need to preserve month-specific
 * placements should use `editorStateToScheduleConfig` together with
 * `PATCH /plans/{v}/config` instead, which regenerates a calendar-aware
 * schedule server-side from a full ScheduleConfig.
 *
 * **`monthly_minutes` / `monthly_amount`** follow MDHHS per-line Σ from
 * **`tasks`** (authorization), not `(weekly placements × WEEKS_PER_MONTH)`.
 */
export function editorStateToApiSchedule(
  state: EditorState,
  payRate: number,
  tasks: readonly Task[],
): ApiSchedule {
  const days: ApiSchedule["days"] = {}
  const byDay = placementsByDay(state)
  let weekly = 0
  for (const d of WEEK_DAYS) {
    const dayName = d as DayName
    const placements = byDay[dayName]
    const minutes = placements.reduce((s, p) => s + p.minutes, 0)
    weekly += minutes
    days[d] = {
      start: state.times[dayName]?.start || DEFAULT_START,
      end: state.times[dayName]?.end || DEFAULT_END,
      minutes,
      tasks: placements.map((p) => p.taskName),
    }
  }

  const { monthlyMinutes, monthlyAmount } = mdhhsFormTotalsFromAuthorizedTasks(
    tasks,
    payRate,
  )

  return {
    weekly_minutes: weekly,
    monthly_minutes: monthlyMinutes,
    monthly_amount: monthlyAmount,
    days,
  }
}

/**
 * Build a `ScheduleConfig` payload that preserves arbitrary
 * month-specific placements across save.
 *
 * Per-task layout:
 *   - `selected_weekdays`: the weekdays the user has the task placed on
 *     in the editor grid, canonically sorted Monday→Sunday.
 *   - `selected_dates`: carried through from `state.selectedDatesByTask`
 *     verbatim. The weekday grid can't edit these directly, but they
 *     round-trip so saving an unrelated checkbox doesn't silently wipe
 *     a month's catch-up date (or any other explicit per-date override).
 *   - `min_per_day` / `days_per_week`: read from the authorized task
 *     list; the editor doesn't renegotiate authorization.
 *
 * Start times come from `state.times[day].start`; per-day end times are
 * not part of the ScheduleConfig model.
 */
export function editorStateToScheduleConfig(
  state: EditorState,
  tasks: readonly Task[],
): ScheduleConfig {
  const metaByName = taskMetaByName(tasks)
  const weekdaysByTask = new Map<string, Set<DayName>>()
  for (const p of state.placements) {
    const nm = p.taskName
    if (!weekdaysByTask.has(nm)) weekdaysByTask.set(nm, new Set<DayName>())
    weekdaysByTask.get(nm)!.add(p.day)
  }

  const dates = state.selectedDatesByTask ?? {}

  // Preserve the order of the authorized task list so saves are stable.
  const taskPlacements: TaskPlacement[] = tasks
    .filter((t) => (t.task_name || "").trim())
    .map((t): TaskPlacement => {
      const nm = String(t.task_name)
      const meta = metaByName.get(nm)
      const placedSet = weekdaysByTask.get(nm) ?? new Set<DayName>()
      const orderedWeekdays = WEEK_DAYS.filter((d) =>
        placedSet.has(d as DayName),
      )
      const taskDates = Array.isArray(dates[nm]) ? dates[nm].slice() : []
      return {
        task_name: nm,
        min_per_day: meta?.minPerDay ?? (Number(t.min_per_day) || 0),
        days_per_week: meta?.daysPerWeek ?? (Number(t.days_per_week) || 0),
        selected_weekdays: orderedWeekdays.slice(),
        selected_dates: taskDates,
      }
    })

  const startByDay: Record<string, string> = {}
  for (const d of WEEK_DAYS) {
    const t = state.times[d as DayName]
    startByDay[d] = t?.start || DEFAULT_START
  }

  return {
    tasks: taskPlacements,
    start_time_by_weekday: startByDay,
  }
}

// ---------------------------------------------------------------------------
// Budget + coverage
// ---------------------------------------------------------------------------

export function authorizedWeeklyMinutes(tasks: readonly Task[]): number {
  return tasks.reduce(
    (s, t) => s + (Number(t.min_per_day) || 0) * (Number(t.days_per_week) || 0),
    0,
  )
}

export function authorizedMonthlyAmount(tasks: readonly Task[]): number {
  return tasks.reduce((s, t) => {
    const v = Number(t.monthly_amount)
    return Number.isFinite(v) ? s + v : s
  }, 0)
}

export type CoverageStatus = "exact" | "over" | "under"

export interface TaskCoverage {
  name: string
  placed: number
  required: number
  status: CoverageStatus
}

export function perTaskCoverage(
  state: EditorState,
  tasks: readonly Task[],
): TaskCoverage[] {
  const counts = placedCountByTask(state)
  return taskMetaList(tasks).map((meta): TaskCoverage => {
    const placed = counts.get(meta.name) ?? 0
    let status: CoverageStatus = "exact"
    if (placed > meta.daysPerWeek) status = "over"
    else if (placed < meta.daysPerWeek) status = "under"
    return {
      name: meta.name,
      placed,
      required: meta.daysPerWeek,
      status,
    }
  })
}

// ---------------------------------------------------------------------------
// Issues (human-readable warnings)
// ---------------------------------------------------------------------------

export type IssueSeverity = "warning" | "error"

export interface Issue {
  severity: IssueSeverity
  message: string
}

export function computeIssues(
  state: EditorState,
  tasks: readonly Task[],
): Issue[] {
  const out: Issue[] = []
  const byDay = placementsByDay(state)

  // 1. Empty days break coverage for daily (7/7) tasks.
  const dailyTasks = taskMetaList(tasks).filter((t) => t.daysPerWeek === 7)
  if (dailyTasks.length > 0) {
    for (const d of WEEK_DAYS) {
      const placements = byDay[d as DayName]
      const names = new Set(placements.map((p) => p.taskName))
      const missing = dailyTasks.filter((t) => !names.has(t.name))
      if (placements.length === 0) {
        out.push({
          severity: "warning",
          message: `${d} has 0 tasks scheduled — daily tasks need all 7 days.`,
        })
      } else if (missing.length > 0) {
        out.push({
          severity: "warning",
          message: `${d} is missing ${missing.length} daily task${
            missing.length === 1 ? "" : "s"
          } (${missing
            .slice(0, 2)
            .map((t) => t.name)
            .join(", ")}${missing.length > 2 ? "…" : ""}).`,
        })
      }
    }
  }

  // 2. Over/under scheduled frequency per task.
  for (const c of perTaskCoverage(state, tasks)) {
    if (c.status === "over") {
      out.push({
        severity: "error",
        message: `${c.name} over-scheduled (${c.placed} days, authorized for ${c.required}).`,
      })
    } else if (c.status === "under" && c.required > 0) {
      out.push({
        severity: "warning",
        message: `${c.name} under-scheduled (${c.placed} / ${c.required} days).`,
      })
    }
  }

  return out
}

// ---------------------------------------------------------------------------
// Equality (used to avoid pushing dupe history entries)
// ---------------------------------------------------------------------------

export function statesEqual(a: EditorState, b: EditorState): boolean {
  if (a === b) return true
  if (a.placements.length !== b.placements.length) return false
  for (let i = 0; i < a.placements.length; i++) {
    const p = a.placements[i]
    const q = b.placements[i]
    if (
      p.id !== q.id ||
      p.day !== q.day ||
      p.taskName !== q.taskName ||
      p.minutes !== q.minutes
    ) {
      return false
    }
  }
  for (const d of WEEK_DAYS) {
    if (
      a.times[d as DayName]?.start !== b.times[d as DayName]?.start ||
      a.times[d as DayName]?.end !== b.times[d as DayName]?.end
    ) {
      return false
    }
  }
  // Month-specific overrides must also match — editing one implies a
  // material change even if every weekday placement stayed put.
  const ad = a.selectedDatesByTask ?? {}
  const bd = b.selectedDatesByTask ?? {}
  const aKeys = Object.keys(ad)
  const bKeys = Object.keys(bd)
  if (aKeys.length !== bKeys.length) return false
  for (const k of aKeys) {
    const ax = ad[k] ?? []
    const bx = bd[k] ?? []
    if (ax.length !== bx.length) return false
    for (let i = 0; i < ax.length; i++) {
      if (ax[i] !== bx[i]) return false
    }
  }
  return true
}
