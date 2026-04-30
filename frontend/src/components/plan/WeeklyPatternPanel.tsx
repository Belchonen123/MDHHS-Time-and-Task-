import { useMemo } from "react"
import { CalendarDays, Clock, Sparkles } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  WEEK_DAYS,
  dayNamesForFrequency,
  isWeekendDay,
  isFridayDay,
  getShortDow,
} from "@/lib/scheduleUtils"
import { formatHoursMinutes, formatInt } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { Plan, Task } from "@/types"

interface WeeklyPatternPanelProps {
  plan: Plan
  /** Weekly Pattern tab — same blob download as DownloadsPanel (weekly .xlsx). */
  onDownloadWeekly?: () => void
}

type DayName = (typeof WEEK_DAYS)[number]

type FreqCategory = "daily" | "housework" | "errand" | "workweek" | "other"

/** Map days_per_week into a visual bucket + tint. */
function categorize(daysPerWeek: number | string): FreqCategory {
  const d = Math.trunc(Number(daysPerWeek))
  if (d === 7) return "daily"
  if (d === 3) return "housework"
  if (d === 2) return "errand"
  if (d === 5) return "workweek"
  return "other"
}

const CATEGORY_STYLE: Record<FreqCategory, string> = {
  daily: "bg-neutral-100 text-neutral-800 ring-1 ring-inset ring-neutral-200",
  housework: "bg-amber-50 text-amber-900 ring-1 ring-inset ring-amber-200",
  errand: "bg-blue-50 text-blue-900 ring-1 ring-inset ring-blue-200",
  workweek: "bg-sky-50 text-sky-900 ring-1 ring-inset ring-sky-200",
  other: "bg-neutral-50 text-neutral-700 ring-1 ring-inset ring-neutral-200",
}

function taskDayNames(t: Task, configMap: Map<string, string[]>): Set<string> {
  // Primary source of truth: the user-edited ScheduleConfig saved on the plan.
  // Falls back to the weekend-weighted defaults only for legacy plans.
  const name = String(t.task_name || "").trim()
  const fromConfig = configMap.get(name)
  if (fromConfig && fromConfig.length > 0) return new Set(fromConfig)
  try {
    return new Set(dayNamesForFrequency(Number(t.days_per_week)))
  } catch {
    return new Set()
  }
}

interface TasksByDay {
  tasks: Array<{ name: string; minutes: number; perWeek: number; category: FreqCategory }>
  totalMinutes: number
}

function groupTasksByDay(plan: Plan): Record<DayName, TasksByDay> {
  const out = Object.fromEntries(
    WEEK_DAYS.map((d) => [d, { tasks: [], totalMinutes: 0 } as TasksByDay]),
  ) as Record<DayName, TasksByDay>

  const cfgTasks = (plan.config?.tasks ?? []) as Array<{
    task_name?: string
    selected_weekdays?: string[]
  }>
  const configMap = new Map<string, string[]>()
  for (const ct of cfgTasks) {
    const nm = String(ct.task_name ?? "").trim()
    if (nm) configMap.set(nm, Array.isArray(ct.selected_weekdays) ? ct.selected_weekdays : [])
  }

  for (const t of plan.tasks) {
    const name = (t.task_name || "").trim()
    if (!name) continue
    const minutes = Number(t.min_per_day) || 0
    const dpw = Number(t.days_per_week) || 0
    const days = taskDayNames(t, configMap)
    const cat = categorize(dpw)
    for (const d of days) {
      if ((WEEK_DAYS as readonly string[]).includes(d)) {
        const slot = out[d as DayName]
        slot.tasks.push({ name, minutes, perWeek: dpw, category: cat })
        slot.totalMinutes += minutes
      }
    }
  }

  return out
}

function TaskChip({
  name,
  minutes,
  perWeek,
  category,
}: {
  name: string
  minutes: number
  perWeek: number
  category: FreqCategory
}) {
  const weeklyTotal = minutes * perWeek
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex max-w-full cursor-default items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium",
            CATEGORY_STYLE[category],
          )}
        >
          <span className="truncate">{name}</span>
          <span className="shrink-0 tabular text-[10px] opacity-70">
            {minutes}m
          </span>
        </span>
      </TooltipTrigger>
      <TooltipContent side="top">
        <span className="tabular">
          {formatInt(minutes)} min × {perWeek} day{perWeek === 1 ? "" : "s"}/wk ={" "}
          {formatInt(weeklyTotal)} min/wk
        </span>
      </TooltipContent>
    </Tooltip>
  )
}

export function WeeklyPatternPanel({
  plan,
  onDownloadWeekly,
}: WeeklyPatternPanelProps) {
  const byDay = useMemo(() => groupTasksByDay(plan), [plan])
  const days = plan.schedule?.days ?? {}
  const legacyWeekly = !(plan.xlsx_path && plan.xlsx_path.trim() !== "")
  const downloadDisabled = legacyWeekly || !onDownloadWeekly

  return (
    <TooltipProvider delayDuration={150}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-neutral-500">
          Download this view as a client-facing schedule
        </p>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <Button
                type="button"
                variant="outline"
                size="icon"
                className="h-8 w-8 shrink-0 text-neutral-600"
                disabled={downloadDisabled}
                aria-label="Download weekly schedule spreadsheet"
                onClick={() => onDownloadWeekly?.()}
              >
                <CalendarDays className="h-4 w-4" />
              </Button>
            </span>
          </TooltipTrigger>
          <TooltipContent side="top">
            {legacyWeekly
              ? "Plan workbook not available"
              : "Download weekly schedule (same workbook as XLSX)"}
          </TooltipContent>
        </Tooltip>
      </div>

      {legacyWeekly && (
        <div
          className="mb-3 rounded-lg border border-amber-200/90 bg-amber-50/90 px-3 py-2 text-sm text-amber-950/90"
          role="status"
        >
          This plan has no saved Excel workbook on the server. Upload or
          re-generate the plan to enable downloads.
        </div>
      )}

      <div className="grid grid-cols-7 gap-3">
        {WEEK_DAYS.map((day) => {
          const dayBlock = days[day]
          const entry = byDay[day as DayName]
          const weekend = isWeekendDay(day)
          const hwDay = isFridayDay(day)
          const start = dayBlock?.start as string | undefined
          const end = dayBlock?.end as string | undefined
          const minutes =
            typeof dayBlock?.minutes === "number"
              ? dayBlock.minutes
              : entry?.totalMinutes ?? 0

          return (
            <div
              key={day}
              className={cn(
                "flex min-h-[220px] flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-xs",
                weekend && "bg-[color:#FFF4EC]",
                hwDay && !weekend && "bg-amber-50/60",
              )}
            >
              {/* Header */}
              <div className="flex items-baseline justify-between gap-2 border-b border-neutral-200 px-3 py-2.5">
                <div className="flex items-baseline gap-1.5">
                  <div className="font-display text-base font-semibold text-neutral-900">
                    {getShortDow(day)}
                  </div>
                  {hwDay && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="inline-flex h-4 w-4 items-center justify-center text-amber-600">
                          <Sparkles className="h-3 w-3" />
                        </span>
                      </TooltipTrigger>
                      <TooltipContent side="top">
                        Deviation day — longer shift, housework included
                      </TooltipContent>
                    </Tooltip>
                  )}
                </div>
                <div className="tabular text-[11px] text-neutral-500">
                  {minutes > 0 ? `${formatInt(minutes)}m` : "—"}
                </div>
              </div>

              {/* Shift times */}
              <div className="border-b border-neutral-100 px-3 py-2.5">
                {start && end ? (
                  <div className="flex items-center gap-2 text-xs">
                    <Clock className="h-3.5 w-3.5 shrink-0 text-neutral-400" />
                    <span className="inline-flex items-center gap-1 rounded-full bg-white px-2 py-0.5 tabular text-[11px] font-medium text-neutral-800 ring-1 ring-inset ring-neutral-200">
                      {start}
                    </span>
                    <span className="text-neutral-400">→</span>
                    <span className="inline-flex items-center gap-1 rounded-full bg-white px-2 py-0.5 tabular text-[11px] font-medium text-neutral-800 ring-1 ring-inset ring-neutral-200">
                      {end}
                    </span>
                  </div>
                ) : (
                  <div className="text-xs text-neutral-400">No shift</div>
                )}
                {minutes > 0 && (
                  <div className="mt-1.5 tabular text-[11px] text-neutral-500">
                    {formatHoursMinutes(minutes)}
                  </div>
                )}
              </div>

              {/* Tasks */}
              <div className="flex flex-1 flex-wrap content-start gap-1.5 p-3">
                {entry && entry.tasks.length > 0 ? (
                  entry.tasks.map((t, i) => (
                    <TaskChip
                      key={t.name + i}
                      name={t.name}
                      minutes={t.minutes}
                      perWeek={t.perWeek}
                      category={t.category}
                    />
                  ))
                ) : (
                  <span className="text-[11px] text-neutral-400">No tasks</span>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Legend */}
      <div className="mt-4 flex flex-wrap items-center gap-3 text-[11px] text-neutral-600">
        <span className="label-caps text-[10px]">Frequency</span>
        <LegendDot className="bg-neutral-100 ring-neutral-200" label="7/wk · daily" />
        <LegendDot className="bg-sky-50 ring-sky-200" label="5/wk · workweek" />
        <LegendDot
          className="bg-amber-50 ring-amber-200"
          label="3/wk · housework"
        />
        <LegendDot className="bg-blue-50 ring-blue-200" label="2/wk · errand" />
      </div>
    </TooltipProvider>
  )
}

function LegendDot({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className={cn(
          "inline-block h-3 w-3 rounded-sm ring-1 ring-inset",
          className,
        )}
      />
      <span>{label}</span>
    </span>
  )
}

export default WeeklyPatternPanel
