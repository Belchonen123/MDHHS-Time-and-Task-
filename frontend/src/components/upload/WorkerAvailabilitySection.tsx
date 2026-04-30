import { forwardRef } from "react"
import { AlertTriangle, Check } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  WEEK_DAYS,
  evaluateAvailabilityWindow,
  getShortDow,
} from "@/lib/scheduleUtils"
import {
  type WorkerAvailabilityDay,
  type WorkerAvailabilityMap,
  fromTimeInputValue,
  toTimeInputValue,
} from "@/lib/workerAvailability"
import { cn } from "@/lib/utils"

const TARGET_PRESETS_MIN = [60, 90, 120, 240] as const

type Props = {
  value: WorkerAvailabilityMap
  onChange: (next: WorkerAvailabilityMap) => void
  disabled?: boolean
  className?: string
  /** Hide heading / help text (e.g. when wrapped by ClientAvailabilityPanel). */
  hideIntro?: boolean
}

export const WorkerAvailabilitySection = forwardRef<HTMLElement, Props>(
  function WorkerAvailabilitySection(
    { value, onChange, disabled, className, hideIntro },
    ref,
  ) {
    const patchDay = (day: string, patch: Partial<WorkerAvailabilityDay>) => {
      const cur = value[day] ?? {}
      const nextDay: WorkerAvailabilityDay = { ...cur, ...patch }
      if (
        "preferred_duration_min" in patch &&
        patch.preferred_duration_min === undefined
      ) {
        delete nextDay.preferred_duration_min
      }
      onChange({
        ...value,
        [day]: nextDay,
      })
    }

    const setDayTime = (
      day: string,
      field: "earliest" | "latest",
      timeVal: string,
    ) => {
      patchDay(day, { [field]: fromTimeInputValue(timeVal) })
    }

    return (
      <section
        ref={ref}
        className={cn(
          "rounded-xl border border-neutral-200 bg-white px-4 py-4 shadow-sm",
          className,
        )}
      >
        {!hideIntro && (
          <>
            <h3 className="text-sm font-semibold text-neutral-900">
              When can visits be scheduled?
            </h3>
            <p className="mt-1 text-xs text-neutral-600">
              <span className="font-medium">From</span> = earliest time a shift may{" "}
              <em>start</em>. <span className="font-medium">To</span> = latest regular{" "}
              <em>end</em>. <span className="font-medium">Longer on visit days</span> +{" "}
              <span className="font-medium">Latest (if visits)</span> only extends how{" "}
              <em>late</em> shifts may end on days that actually have tasks—«From» still
              applies. Reversing From/To can create a one-hour window by mistake. Upload,
              re-run, the editor, Excel, and PDFs all use these rules.
            </p>
          </>
        )}
        <div
          className={cn(
            "grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4",
            !hideIntro && "mt-4",
          )}
        >
          {WEEK_DAYS.map((d) => {
            const row = value[d]
            const longer = row?.visit_day_longer ?? false
            const visitLatest =
              row?.visit_day_latest?.trim() || "10:00 PM"
            const ev = evaluateAvailabilityWindow(row ?? {})
            const warn = ev.reversed || ev.tooNarrow || Boolean(ev.note)
            const pref = row?.preferred_duration_min
            const prefActive =
              typeof pref === "number" && Number.isFinite(pref) && pref > 0
            const prefFits = !prefActive || pref <= ev.minutes
            return (
              <div
                key={d}
                className="flex flex-col gap-2 rounded-lg border border-neutral-100 bg-neutral-50/60 px-3 py-2"
              >
                <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-600">
                  {getShortDow(d)}
                </span>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex gap-6 text-[10px] font-medium text-neutral-500">
                    <span>From</span>
                    <span>To</span>
                  </div>
                  <div className="flex min-w-0 flex-1 items-center justify-end gap-1.5">
                    {warn ? (
                      <>
                        <AlertTriangle
                          className="h-3.5 w-3.5 shrink-0 text-amber-600"
                          aria-hidden
                        />
                        <span className="truncate text-right text-[10px] text-amber-900">
                          {ev.note}
                        </span>
                        {ev.reversed && !disabled ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="h-6 shrink-0 px-2 text-[10px]"
                            onClick={() =>
                              patchDay(d, {
                                earliest: row?.latest ?? "",
                                latest: row?.earliest ?? "",
                              })
                            }
                          >
                            Swap
                          </Button>
                        ) : null}
                      </>
                    ) : (
                      <>
                        <Check
                          className="h-3.5 w-3.5 shrink-0 text-green-600"
                          aria-hidden
                        />
                        <span className="text-[10px] tabular-nums text-neutral-600">
                          {ev.minutes} min
                        </span>
                      </>
                    )}
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <label className="flex flex-col gap-0.5">
                    <span className="text-[10px] text-neutral-500">From</span>
                    <Input
                      type="time"
                      disabled={disabled}
                      value={toTimeInputValue(row?.earliest ?? "")}
                      onChange={(e) => setDayTime(d, "earliest", e.target.value)}
                      className="h-8 bg-white text-xs tabular"
                    />
                  </label>
                  <label className="flex flex-col gap-0.5">
                    <span className="text-[10px] text-neutral-500">To</span>
                    <Input
                      type="time"
                      disabled={disabled}
                      value={toTimeInputValue(row?.latest ?? "")}
                      onChange={(e) => setDayTime(d, "latest", e.target.value)}
                      className="h-8 bg-white text-xs tabular"
                    />
                  </label>
                </div>
                <label className="flex cursor-pointer items-start gap-2 text-[11px] leading-snug text-neutral-800">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-3.5 w-3.5 rounded border-neutral-400"
                    disabled={disabled}
                    checked={longer}
                    onChange={(e) => {
                      const on = e.target.checked
                      patchDay(d, {
                        visit_day_longer: on,
                        visit_day_latest: on
                          ? row?.visit_day_latest?.trim() || "10:00 PM"
                          : row?.visit_day_latest ?? "",
                      })
                    }}
                  />
                  <span>
                    Longer on visit days
                    <span className="block text-[10px] font-normal text-neutral-500">
                      Later end only when this weekday has scheduled tasks.
                    </span>
                  </span>
                </label>
                {longer && (
                  <label className="flex flex-col gap-0.5">
                    <span className="text-[10px] text-neutral-500">
                      Latest (if visits this day)
                    </span>
                    <Input
                      type="time"
                      disabled={disabled}
                      value={toTimeInputValue(visitLatest)}
                      onChange={(e) =>
                        patchDay(d, {
                          visit_day_latest: fromTimeInputValue(e.target.value),
                        })
                      }
                      className="h-8 bg-white text-xs tabular"
                    />
                  </label>
                )}
                <div className="flex flex-col gap-1.5 border-t border-neutral-200/80 pt-2">
                  <span className="text-[10px] font-medium text-neutral-600">
                    Target length (optional)
                  </span>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Input
                      type="number"
                      min={1}
                      step={1}
                      disabled={disabled}
                      placeholder="Minutes"
                      className="h-8 max-w-[5.5rem] bg-white text-xs tabular"
                      value={prefActive ? pref : ""}
                      onChange={(e) => {
                        const raw = e.target.value.trim()
                        if (raw === "") {
                          patchDay(d, { preferred_duration_min: undefined })
                          return
                        }
                        const n = Number(raw)
                        if (!Number.isFinite(n) || n <= 0) return
                        patchDay(d, { preferred_duration_min: Math.trunc(n) })
                      }}
                    />
                    <div className="flex flex-wrap gap-1">
                      {TARGET_PRESETS_MIN.map((m) => (
                        <Button
                          key={m}
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={disabled}
                          className="h-7 px-2 text-[10px] tabular-nums"
                          onClick={() =>
                            patchDay(d, { preferred_duration_min: m })
                          }
                        >
                          {m}
                        </Button>
                      ))}
                    </div>
                  </div>
                  <p className="text-[10px] text-neutral-500">
                    e.g. 120 for 2h days — the planner favors this weekday length
                    when spreading tasks.
                  </p>
                  {prefActive ? (
                    <div
                      className={cn(
                        "flex items-center justify-end gap-1.5 rounded-md px-1 py-0.5",
                        prefFits ? "text-green-800" : "text-amber-900",
                      )}
                    >
                      {prefFits ? (
                        <Check
                          className="h-3.5 w-3.5 shrink-0 text-green-600"
                          aria-hidden
                        />
                      ) : (
                        <AlertTriangle
                          className="h-3.5 w-3.5 shrink-0 text-amber-600"
                          aria-hidden
                        />
                      )}
                      <span className="text-right text-[10px] tabular-nums leading-snug">
                        Target {pref}m vs {ev.minutes}m capacity
                        {!prefFits ? " — widen window or lower target" : ""}
                      </span>
                    </div>
                  ) : null}
                </div>
              </div>
            )
          })}
        </div>
      </section>
    )
  },
)

export default WorkerAvailabilitySection
