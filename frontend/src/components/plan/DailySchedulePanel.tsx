import { useMemo, useState } from "react"
import { AnimatePresence, motion } from "framer-motion"
import { addDays, format, startOfWeek } from "date-fns"
import { ChevronDown, Clock, Sparkles } from "lucide-react"

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  WEEK_DAYS,
  isFridayDay,
  isWeekendDay,
  longDayForLogRow,
  selectedWeekdaysForTask,
} from "@/lib/scheduleUtils"
import { scheduledMonthlyMinutesFromPlan } from "@/lib/scheduleBuild"
import {
  formatHoursMinutes,
  formatInt,
  formatMoney,
  parseBackendUtcInstant,
} from "@/lib/format"
import { cn } from "@/lib/utils"
import { easeOutSoft } from "@/lib/motion"
import type { Client, Plan, Task } from "@/types"

interface DailySchedulePanelProps {
  plan: Plan
  client: Client
}

type DayRow = {
  index: number
  date: Date
  weekIndex: number
  dayName: (typeof WEEK_DAYS)[number]
  shiftStart?: string
  shiftEnd?: string
  taskMinutes: Array<{ taskName: string; minutes: number }>
  totalMinutes: number
  amount: number
  weekend: boolean
  hwDay: boolean
  deviation: boolean
}

function buildRows(plan: Plan, referenceStart: Date): DayRow[] {
  const tasks: Task[] = plan.tasks.filter((t) => (t.task_name || "").trim())
  const targetMin = scheduledMonthlyMinutesFromPlan(plan)
  const pay = Number(plan.schedule?.monthly_amount)
    ? plan.schedule.monthly_amount / Math.max(targetMin / 60, 1)
    : 0
  const hourly = Number.isFinite(pay) && pay > 0 ? pay : 0

  const rows = Array.from({ length: 30 }, (_, i) => {
    const dayName = longDayForLogRow(i)
    const block = plan.schedule?.days?.[dayName] as
      | { start?: unknown; end?: unknown; minutes?: unknown }
      | undefined

    const taskMinutes: DayRow["taskMinutes"] = []
    for (const t of tasks) {
      const mpd = Number(t.min_per_day) || 0
      const days = selectedWeekdaysForTask(
        plan,
        String(t.task_name),
        Number(t.days_per_week) || 0,
      )
      const set = new Set(days)
      if (set.has(dayName) && mpd > 0) {
        taskMinutes.push({ taskName: String(t.task_name), minutes: mpd })
      }
    }

    const totalMinutes = taskMinutes.reduce((s, v) => s + v.minutes, 0)
    const amount = hourly > 0 ? (totalMinutes / 60) * hourly : 0

    return {
      index: i,
      date: addDays(referenceStart, i),
      weekIndex: Math.floor(i / 7),
      dayName,
      shiftStart: block?.start != null ? String(block.start) : undefined,
      shiftEnd: block?.end != null ? String(block.end) : undefined,
      taskMinutes,
      totalMinutes,
      amount,
      weekend: isWeekendDay(dayName),
      hwDay: isFridayDay(dayName),
      deviation: false,
    } as DayRow
  })

  // Simple deviation detection: the day with the single largest total minutes
  // across the 30-day window, IF it's ≥15% above the average non-zero day.
  const nonZero = rows.filter((r) => r.totalMinutes > 0)
  if (nonZero.length > 0) {
    const avg = nonZero.reduce((s, r) => s + r.totalMinutes, 0) / nonZero.length
    const max = nonZero.reduce((best, r) => (r.totalMinutes > best.totalMinutes ? r : best))
    if (max.totalMinutes >= avg * 1.15) {
      max.deviation = true
    }
  }

  return rows
}

/* ---------- Row ---------- */

function DateChip({ date }: { date: Date }) {
  return (
    <div className="flex h-12 w-12 shrink-0 flex-col items-center justify-center rounded-md bg-white ring-1 ring-inset ring-neutral-200">
      <div className="text-[9px] font-semibold uppercase tracking-wider text-neutral-500">
        {format(date, "MMM")}
      </div>
      <div className="font-display text-lg font-semibold leading-none text-neutral-900 tabular">
        {format(date, "d")}
      </div>
    </div>
  )
}

function ShiftBadge({ label }: { label: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium",
        label === "Weekend"
          ? "border-orange-200 bg-orange-50 text-orange-800"
          : label === "HW day"
            ? "border-amber-200 bg-amber-50 text-amber-900"
            : label === "Off"
              ? "border-neutral-200 bg-neutral-50 text-neutral-500"
              : "border-neutral-200 bg-white text-neutral-700",
      )}
    >
      {label}
    </span>
  )
}

function DailyRow({
  row,
  expanded,
  onToggle,
}: {
  row: DayRow
  expanded: boolean
  onToggle: () => void
}) {
  const shiftLabel = row.totalMinutes === 0
    ? "Off"
    : row.weekend
      ? "Weekend"
      : row.hwDay
        ? "HW day"
        : "Shift"

  return (
    <li
      className={cn(
        "overflow-hidden border-b border-neutral-100 last:border-b-0",
        row.deviation && "bg-amber-50/50",
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className={cn(
          "flex w-full items-center gap-4 px-3 py-2.5 text-left transition-colors",
          "hover:bg-neutral-50",
          expanded && "bg-neutral-50/80",
        )}
      >
        <DateChip date={row.date} />

        <div className="flex min-w-[90px] items-center gap-1.5">
          <span className="text-sm font-medium text-neutral-800">
            {format(row.date, "EEEE")}
          </span>
          {row.deviation && (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex h-4 w-4 items-center justify-center text-amber-600">
                  <Sparkles className="h-3 w-3" />
                </span>
              </TooltipTrigger>
              <TooltipContent side="top">
                Deviation day — see Weekly Pattern tab
              </TooltipContent>
            </Tooltip>
          )}
        </div>

        <ShiftBadge label={shiftLabel} />

        <div className="flex min-w-[140px] items-center gap-1.5 text-sm text-neutral-700">
          {row.shiftStart && row.shiftEnd ? (
            <>
              <Clock className="h-3.5 w-3.5 shrink-0 text-neutral-400" />
              <span className="tabular">
                {row.shiftStart} – {row.shiftEnd}
              </span>
            </>
          ) : (
            <span className="text-neutral-400">—</span>
          )}
        </div>

        <div className="min-w-[100px] tabular text-sm text-neutral-700">
          {row.totalMinutes > 0
            ? `${formatHoursMinutes(row.totalMinutes)} (${formatInt(row.totalMinutes)} min)`
            : "—"}
        </div>

        <div className="ml-auto flex items-center gap-3">
          <div className="tabular text-sm font-medium text-neutral-900">
            {row.amount > 0 ? formatMoney(row.amount) : "—"}
          </div>
          <motion.span
            animate={{ rotate: expanded ? 180 : 0 }}
            transition={{ duration: 0.18, ease: easeOutSoft }}
            className="inline-flex h-6 w-6 items-center justify-center text-neutral-400"
          >
            <ChevronDown className="h-4 w-4" />
          </motion.span>
        </div>
      </button>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.26, ease: easeOutSoft }}
            className="overflow-hidden"
          >
            <div className="border-t border-neutral-100 bg-white px-16 py-3">
              {row.taskMinutes.length === 0 ? (
                <p className="text-xs text-neutral-500">No tasks scheduled.</p>
              ) : (
                <div className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1.5">
                  {row.taskMinutes.map((t, i) => (
                    <div key={t.taskName + i} className="contents">
                      <span className="truncate text-sm text-neutral-700">
                        {t.taskName}
                      </span>
                      <span className="tabular text-sm text-neutral-900">
                        {formatHoursMinutes(t.minutes)}
                        <span className="ml-1.5 font-normal text-neutral-500">
                          ({formatInt(t.minutes)} min)
                        </span>
                      </span>
                    </div>
                  ))}
                  <div className="col-span-2 mt-1 border-t border-neutral-100 pt-1.5" />
                  <span className="label-caps text-[10px]">Total</span>
                  <span className="tabular text-sm font-semibold text-neutral-900">
                    {row.totalMinutes > 0 ? (
                      <>
                        {formatHoursMinutes(row.totalMinutes)}
                        <span className="ml-1.5 font-normal text-neutral-500">
                          ({formatInt(row.totalMinutes)} min)
                        </span>
                      </>
                    ) : (
                      "—"
                    )}
                  </span>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  )
}

/* ---------- Main panel ---------- */

export function DailySchedulePanel({ plan }: DailySchedulePanelProps) {
  // Anchor day 1 to the Monday on/before the first of the plan-creation month.
  const referenceStart = useMemo(() => {
    const created = plan.created_at
      ? parseBackendUtcInstant(plan.created_at)
      : new Date()
    const firstOfMonth = new Date(
      created.getFullYear(),
      created.getMonth(),
      1,
    )
    return startOfWeek(firstOfMonth, { weekStartsOn: 1 /* Monday */ })
  }, [plan.created_at])

  const rows = useMemo(() => buildRows(plan, referenceStart), [plan, referenceStart])

  const [expanded, setExpanded] = useState<Set<number>>(() => new Set())
  const toggle = (n: number) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(n)) next.delete(n)
      else next.add(n)
      return next
    })

  const weekBuckets = useMemo(() => {
    const m = new Map<number, DayRow[]>()
    for (const r of rows) {
      const arr = m.get(r.weekIndex) ?? []
      arr.push(r)
      m.set(r.weekIndex, arr)
    }
    return [...m.entries()].sort((a, b) => a[0] - b[0])
  }, [rows])

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex flex-col gap-4">
        {weekBuckets.map(([weekIndex, weekRows]) => {
          const first = weekRows[0]?.date
          const last = weekRows[weekRows.length - 1]?.date
          const range =
            first && last
              ? `${format(first, "MMM d")} to ${format(last, "MMM d")}`
              : ""
          const weekMinutes = weekRows.reduce((s, r) => s + r.totalMinutes, 0)
          const weekAmount = weekRows.reduce((s, r) => s + r.amount, 0)

          return (
            <section
              key={weekIndex}
              className="overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-xs"
            >
              <div className="flex items-baseline justify-between gap-4 border-b border-neutral-200 bg-neutral-50 px-4 py-2.5">
                <div className="flex items-baseline gap-3">
                  <span className="label-caps text-[10px]">
                    Week {weekIndex + 1}
                  </span>
                  <span className="text-sm text-neutral-700 tabular">{range}</span>
                </div>
                <div className="flex items-baseline gap-4 text-xs text-neutral-600 tabular">
                  <span>
                    <span className="label-caps mr-1.5 text-[10px]">Min</span>
                    {formatInt(weekMinutes)}
                    {weekMinutes > 0 ? (
                      <span className="ml-1.5 text-neutral-400">
                        · {formatHoursMinutes(weekMinutes)}
                      </span>
                    ) : null}
                  </span>
                  <span>
                    <span className="label-caps mr-1.5 text-[10px]">Billed</span>
                    {weekAmount > 0 ? formatMoney(weekAmount) : "—"}
                  </span>
                </div>
              </div>

              <ul>
                {weekRows.map((r) => (
                  <DailyRow
                    key={r.index}
                    row={r}
                    expanded={expanded.has(r.index)}
                    onToggle={() => toggle(r.index)}
                  />
                ))}
              </ul>
            </section>
          )
        })}
      </div>
    </TooltipProvider>
  )
}

export default DailySchedulePanel
