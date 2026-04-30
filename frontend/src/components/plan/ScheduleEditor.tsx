import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { RotateCcw, Loader2, CheckCircle2, AlertTriangle } from "lucide-react"
import { toast } from "sonner"

import { patchPlanConfig } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { WEEK_DAYS, dayNamesForFrequency, getShortDow } from "@/lib/scheduleUtils"
import { isCompanionTask, parentOf } from "@/lib/companionTasks"
import { formatHoursMinutes, formatInt, formatMoney } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { Plan, ScheduleConfig, TaskPlacement } from "@/types"

interface ScheduleEditorProps {
  clientId: string
  plan: Plan
  onPlanUpdated: (plan: Plan) => void
  /** Optional per-weekday target shift length from availability — hints why auto-placement favored certain days. */
  weekdayPreferredMinutes?: Partial<Record<string, number>>
}

const DEFAULT_START_TIMES: Record<string, string> = {
  Monday: "1:00 PM",
  Tuesday: "1:00 PM",
  Wednesday: "1:00 PM",
  Thursday: "1:00 PM",
  Friday: "1:00 PM",
  Saturday: "1:00 PM",
  Sunday: "1:00 PM",
}

/** Display time → "HH:MM" for <input type="time">; "" on parse failure. */
function toTimeInputValue(display: string): string {
  const m = String(display || "").match(/^(\d{1,2}):(\d{2})\s*(AM|PM)?$/i)
  if (!m) return ""
  let h = Number(m[1])
  const min = m[2]
  const ampm = (m[3] || "").toUpperCase()
  if (ampm === "PM" && h < 12) h += 12
  if (ampm === "AM" && h === 12) h = 0
  return `${String(h).padStart(2, "0")}:${min}`
}

/** "13:30" → "1:30 PM"; falls back to "1:00 PM" on bad input. */
function fromTimeInputValue(v: string): string {
  const m = String(v || "").match(/^(\d{1,2}):(\d{2})$/)
  if (!m) return "1:00 PM"
  let h = Number(m[1])
  const min = m[2]
  const ampm = h >= 12 ? "PM" : "AM"
  if (h === 0) h = 12
  else if (h > 12) h -= 12
  return `${h}:${min} ${ampm}`
}

function normalizeTaskRow(
  planTask: Plan["tasks"][number],
  existing: Partial<TaskPlacement> | undefined,
): TaskPlacement {
  const name = String(planTask.task_name || "").trim()
  const hasPrefW =
    existing &&
    typeof existing === "object" &&
    "preferred_weekdays" in existing
  const tmpl = [...dayNamesForFrequency(Number(planTask.days_per_week) || 0)]
  const selW = Array.isArray(existing?.selected_weekdays)
    ? [...existing.selected_weekdays]
    : tmpl
  const prefW = hasPrefW
    ? [...(existing.preferred_weekdays ?? [])]
    : [...selW]
  const hasPrefD =
    existing &&
    typeof existing === "object" &&
    "preferred_dates" in existing
  const selD = [...(existing?.selected_dates ?? [])]
  const prefD = hasPrefD ? [...(existing.preferred_dates ?? [])] : [...selD]
  const unspec =
    existing?.preference_unspecified === undefined
      ? !hasPrefW
      : Boolean(existing.preference_unspecified)

  return {
    task_name: name,
    min_per_day: Number(planTask.min_per_day) || 0,
    days_per_week: Number(planTask.days_per_week) || 0,
    selected_weekdays: selW,
    selected_dates: selD,
    preferred_weekdays: prefW,
    preferred_dates: prefD,
    placement_overrides: Array.isArray(existing?.placement_overrides)
      ? [...(existing.placement_overrides ?? [])]
      : [],
    preference_unspecified: unspec,
    placement_fallback: Boolean(existing?.placement_fallback),
  }
}

function buildInitialConfig(plan: Plan): ScheduleConfig {
  const src = plan.config ?? {}
  const srcTasks = Array.isArray(src.tasks)
    ? (src.tasks as Array<Partial<TaskPlacement>>)
    : []
  const tasks: TaskPlacement[] = plan.tasks.map((t) => {
    const name = String(t.task_name || "").trim()
    const existing = srcTasks.find(
      (x) => String(x.task_name || "").trim() === name,
    )
    return normalizeTaskRow(t, existing)
  })

  const startTimes: Record<string, string> = {}
  const srcStart =
    (src.start_time_by_weekday as Record<string, string> | undefined) ?? {}
  for (const d of WEEK_DAYS) {
    startTimes[d] = srcStart[d] || DEFAULT_START_TIMES[d] || "1:00 PM"
  }

  return { tasks, start_time_by_weekday: startTimes }
}

function buildDefaultConfig(plan: Plan): ScheduleConfig {
  const tasks: TaskPlacement[] = plan.tasks.map((t) => {
    const tmpl = [...dayNamesForFrequency(Number(t.days_per_week) || 0)]
    return {
      task_name: String(t.task_name || "").trim(),
      min_per_day: Number(t.min_per_day) || 0,
      days_per_week: Number(t.days_per_week) || 0,
      selected_weekdays: [...tmpl],
      preferred_weekdays: [...tmpl],
      selected_dates: [],
      preferred_dates: [],
      preference_unspecified: true,
    }
  })
  return {
    tasks,
    start_time_by_weekday: { ...DEFAULT_START_TIMES },
  }
}

/** API expects `selected_*` fields to carry the user's draft picks. */
function toPatchPayload(cfg: ScheduleConfig): ScheduleConfig {
  return {
    start_time_by_weekday: { ...cfg.start_time_by_weekday },
    tasks: cfg.tasks.map((t) => ({
      task_name: t.task_name,
      min_per_day: t.min_per_day,
      days_per_week: t.days_per_week,
      selected_weekdays: [...(t.preferred_weekdays ?? [])],
      selected_dates: [...(t.preferred_dates ?? [])],
      placement_fallback: t.placement_fallback,
      preferred_weekdays: [...(t.preferred_weekdays ?? [])],
      preferred_dates: [...(t.preferred_dates ?? [])],
      preference_unspecified: t.preference_unspecified,
    })),
  }
}

function strListEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false
  }
  return true
}

/** Deep-equal for our draft shape. */
function configsEqual(a: ScheduleConfig, b: ScheduleConfig): boolean {
  if (a.tasks.length !== b.tasks.length) return false
  for (let i = 0; i < a.tasks.length; i++) {
    const x = a.tasks[i]!
    const y = b.tasks[i]!
    if (
      x.task_name !== y.task_name ||
      x.min_per_day !== y.min_per_day ||
      x.days_per_week !== y.days_per_week
    )
      return false
    if (!strListEqual(x.selected_weekdays, y.selected_weekdays)) return false
    if (!strListEqual(x.selected_dates, y.selected_dates)) return false
    if (!strListEqual(x.preferred_weekdays ?? [], y.preferred_weekdays ?? []))
      return false
    if (!strListEqual(x.preferred_dates ?? [], y.preferred_dates ?? []))
      return false
    if (Boolean(x.preference_unspecified) !== Boolean(y.preference_unspecified))
      return false
  }
  for (const d of WEEK_DAYS) {
    if (a.start_time_by_weekday[d] !== b.start_time_by_weekday[d]) return false
  }
  return true
}

/**
 * Count how many calendar occurrences a TaskPlacement produces for the plan's
 * year/month. Uses actual `selected_weekdays` / `selected_dates` from the server.
 */
function countOccurrences(
  placement: TaskPlacement,
  year: number,
  month: number,
): number {
  if (!year || !month) return 0
  const daysInMonth = new Date(year, month, 0).getDate()
  let n = 0
  const sel = new Set(placement.selected_weekdays)
  const selDates = new Set(placement.selected_dates)
  for (let day = 1; day <= daysInMonth; day++) {
    const dt = new Date(year, month - 1, day)
    const weekday = dt.toLocaleDateString("en-US", { weekday: "long" })
    const iso = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`
    if (sel.has(weekday) || selDates.has(iso)) n++
  }
  return n
}

type ChipKind = "empty" | "solid" | "auto" | "miss"

function weekdayChipKind(t: TaskPlacement, weekday: string): ChipKind {
  const pref = t.preferred_weekdays ?? []
  const sel = t.selected_weekdays ?? []
  const unspec = t.preference_unspecified !== false
  const inP = pref.includes(weekday)
  const inS = sel.includes(weekday)
  if (!inP && !inS) return "empty"
  if (inP && !inS) return "miss"
  if (inS && inP && !unspec) return "solid"
  return "auto"
}

function WeekdayChipButton({
  weekday,
  task,
  displayTask,
  disabled,
  taskIdx,
  onToggle,
}: {
  weekday: string
  task: TaskPlacement
  displayTask?: TaskPlacement
  disabled?: boolean
  taskIdx: number
  onToggle: (taskIdx: number, weekday: string) => void
}) {
  const effective = displayTask ?? task
  const kind = weekdayChipKind(effective, weekday)
  const ov = task.placement_overrides?.find((o) => o.preferred === weekday)
  const tipMiss =
    ov != null
      ? `You picked ${weekday} but it didn't fit — moved to ${ov.placed_on}. ${ov.reason}`
      : `You picked ${weekday} but the scheduler placed this task on other days instead.`

  const inner = (
    <button
      type="button"
      role={disabled ? undefined : "checkbox"}
      aria-checked={disabled ? undefined : kind !== "empty"}
      aria-disabled={disabled || undefined}
      disabled={disabled}
      onClick={disabled ? undefined : () => onToggle(taskIdx, weekday)}
      className={cn(
        "relative inline-flex h-6 w-6 items-center justify-center rounded border transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-700 focus-visible:ring-offset-1",
        disabled &&
          "cursor-not-allowed border-neutral-200 bg-neutral-100 opacity-85",
        !disabled && kind === "empty" && "border-neutral-300 bg-white hover:border-neutral-400",
        !disabled && kind === "solid" && "border-primary-700 bg-primary-700 text-white shadow-xs",
        !disabled && kind === "auto" && "border-primary-700 bg-primary-700 text-white shadow-xs",
        !disabled &&
          kind === "miss" &&
          "border-primary-600 bg-white text-primary-800 hover:bg-primary-50",
      )}
    >
      {kind === "solid" ? (
        <svg
          viewBox="0 0 12 12"
          className="h-3 w-3"
          aria-hidden
        >
          <path
            d="M2.5 6.5l2.2 2.2L9.5 3.8"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      ) : null}
      {kind === "auto" ? (
        <span
          className="absolute bottom-0.5 right-0.5 block h-1.5 w-1.5 rounded-full bg-white/95 shadow-[0_0_0_1px_rgba(0,0,0,0.12)]"
          aria-hidden
        />
      ) : null}
      {kind === "miss" ? (
        <span className="text-[10px] font-bold leading-none" aria-hidden>
          ↛
        </span>
      ) : null}
    </button>
  )

  if (kind === "miss") {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{inner}</TooltipTrigger>
        <TooltipContent side="top" className="max-w-[280px] text-xs">
          {tipMiss}
        </TooltipContent>
      </Tooltip>
    )
  }
  if (disabled && isCompanionTask(task.task_name)) {
    const pn = parentOf(task.task_name)
    return (
      <Tooltip>
        <TooltipTrigger asChild>{inner}</TooltipTrigger>
        <TooltipContent side="top" className="max-w-[280px] text-xs">
          Travel happens on the same day as {pn}. Edit the parent row to change which
          weekdays apply.
        </TooltipContent>
      </Tooltip>
    )
  }
  if (kind === "auto") {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{inner}</TooltipTrigger>
        <TooltipContent side="top" className="max-w-[260px] text-xs">
          Auto-placed by scheduler (no explicit day pick for this weekday).
        </TooltipContent>
      </Tooltip>
    )
  }
  if (kind === "solid") {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{inner}</TooltipTrigger>
        <TooltipContent side="top" className="max-w-[220px] text-xs">
          Scheduled on {weekday} as you picked.
        </TooltipContent>
      </Tooltip>
    )
  }
  return inner
}

export function ScheduleEditor({
  clientId,
  plan,
  onPlanUpdated,
  weekdayPreferredMinutes,
}: ScheduleEditorProps) {
  const [cfg, setCfg] = useState<ScheduleConfig>(() => buildInitialConfig(plan))
  const [saving, setSaving] = useState(false)
  const [lastSavedAt, setLastSavedAt] = useState<number | null>(null)

  const planKey = `${plan.version}:${JSON.stringify(plan.config ?? {})}`
  const lastSeenKeyRef = useRef(planKey)
  useEffect(() => {
    if (lastSeenKeyRef.current !== planKey) {
      lastSeenKeyRef.current = planKey
      setCfg(buildInitialConfig(plan))
    }
  }, [planKey, plan])

  const lastPersistedRef = useRef<ScheduleConfig>(cfg)
  const inFlightRef = useRef<AbortController | null>(null)
  const pendingRef = useRef<ScheduleConfig | null>(null)

  const resetToAutoPlacement = useCallback(async () => {
    setSaving(true)
    try {
      const updated = await patchPlanConfig(
        clientId,
        plan.version,
        toPatchPayload(cfg),
        { reseedPlacement: true },
      )
      const merged = buildInitialConfig(updated)
      setCfg(merged)
      lastPersistedRef.current = merged
      setLastSavedAt(Date.now())
      onPlanUpdated(updated)
      toast.success("Placement re-seeded from availability", {
        description: "Day picks were cleared; tasks were auto-placed again.",
      })
    } catch {
      /* api toasts */
    } finally {
      setSaving(false)
    }
  }, [cfg, clientId, plan.version, onPlanUpdated])

  const runSave = useCallback(
    async (next: ScheduleConfig) => {
      if (configsEqual(next, lastPersistedRef.current)) return
      if (inFlightRef.current) {
        pendingRef.current = next
        return
      }
      const ctrl = new AbortController()
      inFlightRef.current = ctrl
      setSaving(true)
      try {
        const wire = toPatchPayload(next)
        const updated = await patchPlanConfig(clientId, plan.version, wire)
        lastPersistedRef.current = next
        setLastSavedAt(Date.now())
        onPlanUpdated(updated)
      } catch {
        // api layer already toasts
      } finally {
        inFlightRef.current = null
        setSaving(false)
        const q = pendingRef.current
        if (q) {
          pendingRef.current = null
          void runSave(q)
        }
      }
    },
    [clientId, plan.version, onPlanUpdated],
  )

  useEffect(() => {
    const t = window.setTimeout(() => {
      void runSave(cfg)
    }, 300)
    return () => window.clearTimeout(t)
  }, [cfg, runSave])

  const toggleTaskWeekday = (taskIdx: number, weekday: string) => {
    setCfg((prev) => ({
      ...prev,
      tasks: prev.tasks.map((t, i) => {
        if (i !== taskIdx) return t
        const pref = t.preferred_weekdays ?? []
        const has = pref.includes(weekday)
        const nextPref = has
          ? pref.filter((d) => d !== weekday)
          : [...pref, weekday]
        const orderedPref = WEEK_DAYS.filter((d) => nextPref.includes(d))
        return {
          ...t,
          preferred_weekdays: orderedPref,
          preference_unspecified: false,
        }
      }),
    }))
  }

  const clearTaskPicks = (taskIdx: number) => {
    setCfg((prev) => ({
      ...prev,
      tasks: prev.tasks.map((t, i) =>
        i === taskIdx
          ? {
              ...t,
              preferred_weekdays: [],
              preferred_dates: [],
              preference_unspecified: false,
            }
          : t,
      ),
    }))
  }

  const clearAllPicks = () => {
    setCfg((prev) => ({
      ...prev,
      tasks: prev.tasks.map((t) => ({
        ...t,
        preferred_weekdays: [],
        preferred_dates: [],
        preference_unspecified: false,
      })),
    }))
  }

  const setStartTime = (weekday: string, value: string) => {
    setCfg((prev) => ({
      ...prev,
      start_time_by_weekday: {
        ...prev.start_time_by_weekday,
        [weekday]: value,
      },
    }))
  }

  const resetToDefaults = () => {
    const fresh = buildDefaultConfig(plan)
    setCfg(fresh)
    toast.success("Reset to weekend-weighted defaults")
  }

  const year = plan.year || Number(plan.schedule?.year) || 0
  const month = plan.month || Number(plan.schedule?.month) || 0

  const totalWeeklyAuth = useMemo(
    () =>
      plan.tasks.reduce(
        (s, t) =>
          s + (Number(t.min_per_day) || 0) * (Number(t.days_per_week) || 0),
        0,
      ),
    [plan.tasks],
  )

  return (
    <TooltipProvider delayDuration={150}>
      <div id="task-placement-editor" className="flex flex-col gap-6">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-neutral-200 bg-white px-4 py-3 shadow-xs">
          <div className="flex min-w-0 flex-col">
            <div className="label-caps text-[10px]">Editor</div>
            <div className="font-display text-base font-semibold text-neutral-900">
              {plan.tasks.length} tasks · {formatInt(totalWeeklyAuth)} min/wk
              authorized
              {totalWeeklyAuth > 0 ? (
                <span className="ml-2 text-sm font-normal text-neutral-500 tabular">
                  ({formatHoursMinutes(totalWeeklyAuth)})
                </span>
              ) : null}
              <span className="ml-2 text-sm font-medium text-neutral-500">
                @ {formatMoney(Number(plan.schedule?.pay_rate) || 0)}/hr
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <SaveIndicator saving={saving} lastSavedAt={lastSavedAt} />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={clearAllPicks}
              disabled={saving}
            >
              Clear all day picks
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void resetToAutoPlacement()}
              disabled={saving}
              className="gap-2"
            >
              Re-seed from availability
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={resetToDefaults}
              className="gap-2"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Reset to defaults
            </Button>
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-xs">
          <table className="w-full table-fixed border-collapse text-sm">
            <thead className="bg-neutral-50">
              <tr>
                <th className="w-[28%] px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-neutral-600">
                  Task
                </th>
                {WEEK_DAYS.map((d) => {
                  const targetMin = weekdayPreferredMinutes?.[d]
                  return (
                    <th
                      key={d}
                      className="px-1 py-2 text-center text-[11px] font-semibold uppercase tracking-wider text-neutral-600"
                    >
                      <div className="flex flex-col items-center gap-1">
                        <span>{getShortDow(d)}</span>
                        {typeof targetMin === "number" && targetMin > 0 ? (
                          <span className="rounded-full bg-neutral-200/90 px-1.5 py-0.5 text-[9px] font-medium normal-case tracking-normal text-neutral-700">
                            target {targetMin}m
                          </span>
                        ) : null}
                      </div>
                    </th>
                  )
                })}
                <th className="w-[12%] px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wider text-neutral-600">
                  Sessions/mo
                </th>
                <th className="w-[10%] px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wider text-neutral-600">
                  Min/day
                </th>
              </tr>
            </thead>
            <tbody>
              {cfg.tasks.map((t, idx) => {
                const sessions = countOccurrences(t, year, month)
                const companionRow = isCompanionTask(t.task_name)
                const pname = parentOf(t.task_name)
                const parentPlacement = pname
                  ? cfg.tasks.find((x) => x.task_name === pname)
                  : undefined
                return (
                  <tr
                    key={t.task_name + idx}
                    className="border-t border-neutral-100 hover:bg-neutral-50/60"
                  >
                    <td className="px-3 py-2">
                      <div className="flex min-w-0 flex-col gap-1">
                        <div className="flex min-w-0 flex-wrap items-center gap-x-2">
                          <span className="truncate font-medium text-neutral-900">
                            {t.task_name}
                          </span>
                          {companionRow && pname ? (
                            <span
                              className="shrink-0 rounded bg-sky-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-sky-900 ring-1 ring-sky-100"
                              title={`Follows ${pname}. Edit that row to choose days.`}
                            >
                              ↳ follows {pname}
                            </span>
                          ) : null}
                        </div>
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-neutral-500">
                          <span>{t.days_per_week}/wk authorized</span>
                          <button
                            type="button"
                            className="font-medium text-primary-700 underline underline-offset-2 hover:text-primary-900 disabled:cursor-not-allowed disabled:opacity-40 disabled:no-underline"
                            onClick={() => clearTaskPicks(idx)}
                            disabled={companionRow}
                          >
                            Clear my day picks
                          </button>
                        </div>
                      </div>
                    </td>
                    {WEEK_DAYS.map((d) => (
                      <td key={d} className="px-1 py-2 text-center">
                        <WeekdayChipButton
                          weekday={d}
                          task={t}
                          displayTask={
                            companionRow && parentPlacement
                              ? parentPlacement
                              : t
                          }
                          disabled={companionRow}
                          taskIdx={idx}
                          onToggle={toggleTaskWeekday}
                        />
                      </td>
                    ))}
                    <td className="px-3 py-2 text-right">
                      <SessionsCell
                        value={sessions}
                        target={t.days_per_week * 4}
                      />
                    </td>
                    <td className="px-3 py-2 text-right tabular text-neutral-700">
                      <>
                        <span className="text-neutral-900">
                          {formatHoursMinutes(Number(t.min_per_day) || 0)}
                        </span>
                        <span className="text-neutral-400">
                          {" "}
                          ({Number(t.min_per_day) || 0} min)
                        </span>
                      </>
                    </td>
                  </tr>
                )
              })}
              {cfg.tasks.length === 0 && (
                <tr>
                  <td
                    colSpan={10}
                    className="px-3 py-8 text-center text-sm text-neutral-500"
                  >
                    No tasks on this plan.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="rounded-lg border border-neutral-200 bg-white p-4 shadow-xs">
          <div className="mb-3 flex items-baseline justify-between">
            <div>
              <div className="label-caps text-[10px]">Per-weekday start</div>
              <div className="font-display text-base font-semibold text-neutral-900">
                When each day's shift begins
              </div>
            </div>
            <div className="text-[11px] text-neutral-500">
              Defaults: weekdays 7 AM · weekends 12 PM
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 md:grid-cols-7">
            {WEEK_DAYS.map((d) => (
              <label
                key={d}
                className="flex flex-col gap-1 rounded-md border border-neutral-200 bg-neutral-50/50 px-3 py-2"
              >
                <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-600">
                  {getShortDow(d)}
                </span>
                <Input
                  type="time"
                  value={toTimeInputValue(cfg.start_time_by_weekday[d] || "")}
                  onChange={(e) =>
                    setStartTime(d, fromTimeInputValue(e.target.value))
                  }
                  className="h-8 w-full bg-white tabular text-sm"
                />
              </label>
            ))}
          </div>
        </div>
      </div>
    </TooltipProvider>
  )
}

function SaveIndicator({
  saving,
  lastSavedAt,
}: {
  saving: boolean
  lastSavedAt: number | null
}) {
  const [recent, setRecent] = useState(false)
  useEffect(() => {
    if (!lastSavedAt) return
    setRecent(true)
    const t = window.setTimeout(() => setRecent(false), 1500)
    return () => window.clearTimeout(t)
  }, [lastSavedAt])

  if (saving) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-neutral-600">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Saving…
      </span>
    )
  }
  if (recent) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-success">
        <CheckCircle2 className="h-3.5 w-3.5" />
        Saved
      </span>
    )
  }
  return null
}

function SessionsCell({ value, target }: { value: number; target: number }) {
  const off = Math.abs(value - target)
  const tone =
    off === 0
      ? "text-neutral-700"
      : off <= 2
        ? "text-neutral-700"
        : "text-warning"
  const tip =
    off === 0
      ? `Matches ${target} authorized sessions`
      : `${value} vs ~${target} authorized (${value > target ? "+" : ""}${value - target})`
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn("tabular font-medium", tone)}>
          {value}
          {off > 2 && (
            <AlertTriangle
              className="ml-1 inline h-3 w-3 -translate-y-[1px]"
              aria-hidden
            />
          )}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top">{tip}</TooltipContent>
    </Tooltip>
  )
}

export default ScheduleEditor
