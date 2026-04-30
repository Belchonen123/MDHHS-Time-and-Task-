import { useEffect, useMemo, useState } from "react"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"
import {
  ApiError,
  getClient,
  isDayCapacityDetail,
  patchClientAvailability,
  rerunPlan,
  type WorkerAvailabilityMap,
} from "@/api/client"
import { DayCapacityErrorPanel } from "@/components/DayCapacityErrorPanel"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  MonthYearPicker,
  currentMonthYear,
  type MonthYear,
} from "@/components/upload/MonthYearPicker"
import { initEditorFromPlan } from "@/lib/scheduleBuild"
import { WEEK_DAYS, getShortDow } from "@/lib/scheduleUtils"
import { normalizeWorkerAvailability } from "@/lib/workerAvailability"
import type { DayCapacityDetail, Plan, WorkerAvailabilityDay } from "@/types"
import { cn } from "@/lib/utils"

const DEFAULT_IDEAL_START_BY_DAY: Record<string, string> = {
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

type ReRunDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  clientId: string
  planVersion: number
  /** Year/month of the plan being re-run — used to seed the month picker. */
  initialMonth?: { year: number; month: number }
  /** Plan being re-run — used to prefill weekday start/end from saved config + schedule. */
  sourcePlan?: Plan | null
  onSuccess: (plan: Plan) => void
  /** Saved client availability — used to merge one-click widen / longer fixes. */
  workerAvailability?: Record<string, WorkerAvailabilityDay> | null
}

export function ReRunDialog({
  open,
  onOpenChange,
  clientId,
  planVersion,
  initialMonth,
  sourcePlan,
  onSuccess,
  workerAvailability,
}: ReRunDialogProps) {
  const [startByDay, setStartByDay] = useState<Record<string, string>>(
    () => ({ ...DEFAULT_IDEAL_START_BY_DAY }),
  )
  const [useLlm, setUseLlm] = useState(false)
  const [llmNotes, setLlmNotes] = useState("")
  const [loading, setLoading] = useState(false)
  const [month, setMonth] = useState<MonthYear>(() =>
    initialMonth && initialMonth.year && initialMonth.month
      ? { year: initialMonth.year, month: initialMonth.month }
      : currentMonthYear(),
  )
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [dayCapacity, setDayCapacity] = useState<DayCapacityDetail | null>(null)
  const [availabilityForPatch, setAvailabilityForPatch] =
    useState<WorkerAvailabilityMap | null>(null)

  const normalizedAvailability = useMemo(
    () => normalizeWorkerAvailability(workerAvailability ?? undefined),
    [workerAvailability],
  )
  const availabilityPanel = availabilityForPatch ?? normalizedAvailability

  const savedEndByDay = useMemo(() => {
    const days = (sourcePlan?.schedule?.days ?? {}) as Record<
      string,
      { end?: string }
    >
    const out: Record<string, string> = {}
    for (const d of WEEK_DAYS) {
      const e = days[d]?.end
      out[d] = typeof e === "string" && e.trim() ? e.trim() : ""
    }
    return out
  }, [sourcePlan])

  // Reset transient state when the dialog opens; seed month + weekday times
  // from the selected plan (same rules as Schedule editor).
  useEffect(() => {
    if (!open) return
    setErrorMsg(null)
    setDayCapacity(null)
    setAvailabilityForPatch(null)
    setUseLlm(false)
    setLlmNotes("")
    setMonth(
      initialMonth && initialMonth.year && initialMonth.month
        ? { year: initialMonth.year, month: initialMonth.month }
        : currentMonthYear(),
    )
    if (sourcePlan) {
      const { times } = initEditorFromPlan(sourcePlan)
      setStartByDay(
        Object.fromEntries(WEEK_DAYS.map((d) => [d, times[d]!.start])) as Record<
          string,
          string
        >,
      )
    } else {
      setStartByDay({ ...DEFAULT_IDEAL_START_BY_DAY })
    }
  }, [open, initialMonth, sourcePlan])

  const submitRerun = async () => {
    const start_time_by_weekday: Record<string, string> = {}
    for (const d of WEEK_DAYS) {
      start_time_by_weekday[d] = startByDay[d] || DEFAULT_IDEAL_START_BY_DAY[d]
    }
    const plan = await rerunPlan(clientId, planVersion, {
      preferred_window: {
        weekday_start: startByDay.Monday || "1:00 PM",
        weekend_start: startByDay.Saturday || "1:00 PM",
        start_time_by_weekday,
      },
      use_llm: useLlm,
      llm_notes: useLlm ? llmNotes.trim() || null : null,
      year: month.year,
      month: month.month,
    })
    const degenerate =
      plan.tasks.length === 0 ||
      Object.keys(plan.schedule?.days ?? {}).length === 0 ||
      plan.monthly_amount === 0
    if (degenerate) {
      toast.warning(
        "Re-run produced an empty plan — check the source PDF or disable Claude suggestions.",
      )
    } else {
      toast.success(`New plan v${plan.version} created for the selected month`)
    }
    onSuccess(plan)
    onOpenChange(false)
  }

  const handleRerunFailure = (err: unknown) => {
    const raw = err instanceof Error ? err.message : String(err)
    if (err instanceof ApiError && isDayCapacityDetail(err.detail)) {
      setDayCapacity(err.detail)
      setErrorMsg(null)
      return
    }
    setDayCapacity(null)
    const avail =
      /this day'?s visits need/i.test(raw) ||
      /minutes fit between/i.test(raw) ||
      /scheduled visits on this day total/i.test(raw) ||
      /worker availability for that weekday only allows/i.test(raw) ||
      /worker availability only allows \d+ minutes on that weekday/i.test(raw) ||
      /could not parse worker availability times/i.test(raw) ||
      (/min needed/i.test(raw) && /min available/i.test(raw))
    if (avail) {
      setErrorMsg(
        `${raw}\n\nThe PDF was re-read successfully. In Client → Worker availability, widen the weekday named in the message between «From» and the end time used on visit days. Reversed «From»/«To» often creates a one-hour window. Or adjust visit lengths in the authorization.`,
      )
    } else if (
      /calibrat/i.test(raw) &&
      !/invalid time in availability/i.test(raw)
    ) {
      setErrorMsg(
        `Calibration not yet supported for this month (calendar edge case). ` +
          `Try a different month or contact support. (${raw})`,
      )
    } else if (
      /invalid time in availability|no tasks supplied|invalid year\/month/i.test(
        raw,
      )
    ) {
      setErrorMsg(raw)
    } else if (raw.trim()) {
      setErrorMsg(raw)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (planVersion < 1) {
      toast.error("No plan version selected")
      return
    }
    setLoading(true)
    setErrorMsg(null)
    setDayCapacity(null)
    try {
      await submitRerun()
    } catch (err) {
      handleRerunFailure(err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl" onClick={(e) => e.stopPropagation()}>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Re-run plan</DialogTitle>
            <DialogDescription>
              Re-parses the stored authorization PDF and creates a <strong>new</strong> plan
              version calibrated to the month below.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="space-y-2">
              <div className="text-sm font-medium">Which month should this plan cover?</div>
              <MonthYearPicker
                value={month}
                onChange={setMonth}
                disabled={loading}
              />
              <p className="text-xs text-muted-foreground">
                Schedule dates, catch-up day, and monthly totals are calibrated to this month.
              </p>
            </div>
            <div className="space-y-2">
              <div className="text-sm font-medium">
                Daily visit start (from saved plan when available)
              </div>
              <p className="text-xs text-muted-foreground">
                Prefilled from this version’s schedule settings and last calibrated day
                blocks. Only <strong>start</strong> times are sent when re-running; the
                typical <strong>end</strong> from the saved plan is shown below for reference.
              </p>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 md:grid-cols-7">
                {WEEK_DAYS.map((d) => (
                  <label
                    key={d}
                    className="flex flex-col gap-1 rounded-md border border-neutral-200 bg-neutral-50/50 px-3 py-2"
                  >
                    <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-600">
                      {getShortDow(d)}
                    </span>
                    <span className="text-[10px] font-medium text-neutral-500">Start</span>
                    <Input
                      type="time"
                      id={`rr-start-${d}`}
                      value={toTimeInputValue(startByDay[d] || "")}
                      onChange={(e) =>
                        setStartByDay((prev) => ({
                          ...prev,
                          [d]: fromTimeInputValue(e.target.value),
                        }))
                      }
                      disabled={loading}
                      className="h-8 w-full bg-white tabular text-sm"
                    />
                    <span className="text-[10px] font-medium text-neutral-500">
                      End (saved)
                    </span>
                    <div className="min-h-[32px] rounded border border-transparent bg-white/80 px-2 py-1.5 text-xs tabular text-neutral-700">
                      {savedEndByDay[d] || "—"}
                    </div>
                  </label>
                ))}
              </div>
            </div>
            <div className="space-y-3 rounded-md border p-3">
              <label className="flex cursor-pointer items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 rounded border"
                  checked={useLlm}
                  onChange={(e) => setUseLlm(e.target.checked)}
                  disabled={loading}
                />
                <span>
                  Use Claude to suggest weekday placement and visit start times{" "}
                  <span className="text-muted-foreground">(optional; off by default)</span>
                </span>
              </label>
              {useLlm && (
                <>
                  <div className="space-y-2">
                    <div className="text-sm font-medium">Instructions for Claude</div>
                    <p className="text-xs text-muted-foreground">
                      Paste scheduling preferences, constraints, or context (e.g. avoid certain
                      days, prefer morning blocks, worker route notes). Claude returns a proposed
                      task-on-weekday pattern and per-day start times; the server still runs the
                      normal calendar engine with your authorization data.
                    </p>
                    <textarea
                      id="rerun-llm-notes"
                      value={llmNotes}
                      onChange={(e) => setLlmNotes(e.target.value)}
                      maxLength={12000}
                      rows={5}
                      placeholder="Example: No Friday visits. Prefer Bathing and Grooming on the same days when possible."
                      disabled={loading}
                      className={cn(
                        "flex min-h-[100px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
                      )}
                    />
                    <p className="text-xs text-muted-foreground">
                      {llmNotes.length.toLocaleString()} / 12,000 characters
                    </p>
                  </div>
                  <p className="text-sm leading-snug text-red-700">
                    Sent to Anthropic: task minutes and days/week from the PDF, the proposed
                    schedule above, your month and visit-start grid, and whatever you type here.
                    Do not paste client names, IDs, addresses, or other PHI you are not allowed
                    to share with a third-party API.
                  </p>
                </>
              )}
            </div>
            {dayCapacity ? (
              <DayCapacityErrorPanel
                detail={dayCapacity}
                availability={availabilityPanel}
                disabled={loading}
                onApplyAvailability={async (next) => {
                  setLoading(true)
                  setErrorMsg(null)
                  try {
                    await patchClientAvailability(clientId, next)
                    const fresh = await getClient(clientId)
                    setAvailabilityForPatch(
                      normalizeWorkerAvailability(fresh.client.availability),
                    )
                    setDayCapacity(null)
                    await submitRerun()
                  } catch (err) {
                    handleRerunFailure(err)
                  } finally {
                    setLoading(false)
                  }
                }}
                onEditAuthorization={() => {
                  onOpenChange(false)
                  window.dispatchEvent(new CustomEvent("app:focus-task-placement"))
                }}
              />
            ) : null}
            {errorMsg && !dayCapacity ? (
              <div className="whitespace-pre-line rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-900">
                {errorMsg}
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={loading}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={loading} className={cn(loading && "min-w-28")}>
              {loading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Re-running…
                </>
              ) : (
                "Re-run"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
