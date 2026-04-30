import { useState } from "react"

import type { WorkerAvailabilityMap } from "@/api/client"
import { Button } from "@/components/ui/button"
import type {
  DayCapacityDetail,
  DayCapacitySuggestion,
  WorkerAvailabilityDay,
} from "@/types"
import { cn } from "@/lib/utils"

function mergeSuggestionIntoAvailability(
  base: WorkerAvailabilityMap,
  s: DayCapacitySuggestion,
): WorkerAvailabilityMap {
  const wk = s.weekday
  if (!wk || !base[wk]) return { ...base }
  const prev: WorkerAvailabilityDay = base[wk]
  const row: WorkerAvailabilityDay = { ...prev }
  if (s.action === "set_latest" && s.latest) {
    row.latest = s.latest
  } else if (s.action === "visit_day_longer") {
    row.visit_day_longer = true
    if (s.visit_day_latest) row.visit_day_latest = s.visit_day_latest
  } else if (s.action === "extend_visit_day_latest") {
    row.visit_day_longer = true
    if (s.visit_day_latest) row.visit_day_latest = s.visit_day_latest
  } else {
    return { ...base }
  }
  return { ...base, [wk]: row }
}

export type DayCapacityErrorPanelProps = {
  detail: DayCapacityDetail
  className?: string
  availability: WorkerAvailabilityMap
  onApplyAvailability?: (next: WorkerAvailabilityMap) => Promise<void> | void
  onEditAuthorization?: () => void
  disabled?: boolean
}

export function DayCapacityErrorPanel({
  detail,
  className,
  availability,
  onApplyAvailability,
  onEditAuthorization,
  disabled,
}: DayCapacityErrorPanelProps) {
  const [busyLabel, setBusyLabel] = useState<string | null>(null)

  const runApply = async (s: DayCapacitySuggestion) => {
    if (!onApplyAvailability) return
    const next = mergeSuggestionIntoAvailability(availability, s)
    setBusyLabel(s.label)
    try {
      await onApplyAvailability(next)
    } finally {
      setBusyLabel(null)
    }
  }

  return (
    <div
      className={cn(
        "rounded-lg border border-red-200 bg-red-50/90 px-4 py-4 text-left text-red-950",
        className,
      )}
    >
      <p className="font-display text-lg font-semibold tabular text-red-950">
        {detail.weekday}:{" "}
        <span className="text-red-800">{detail.needed_minutes} min needed</span>
        <span className="mx-1 font-normal text-red-700">/</span>
        <span className="text-red-800">{detail.available_minutes} min available</span>
      </p>
      <p className="mt-1 text-xs text-red-800/85">
        Window {detail.earliest} – {detail.latest}
      </p>
      <ul className="mt-3 flex flex-col gap-2">
        {(detail.suggestions ?? []).map((s, i) => {
          const isPatch =
            s.action === "set_latest" ||
            s.action === "visit_day_longer" ||
            s.action === "extend_visit_day_latest"
          const isReduce = s.action === "reduce_task"
          return (
            <li key={`${s.action}-${i}`}>
              {isPatch && onApplyAvailability ? (
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  className="h-auto w-full justify-start whitespace-normal border border-red-200 bg-white py-2 text-left text-red-900 hover:bg-red-50"
                  disabled={disabled || busyLabel !== null}
                  onClick={() => void runApply(s)}
                >
                  {busyLabel === s.label ? "Applying…" : s.label}
                </Button>
              ) : isReduce && onEditAuthorization ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-auto w-full justify-start whitespace-normal border-red-200 bg-white py-2 text-left text-red-900 hover:bg-red-50"
                  disabled={disabled}
                  onClick={() => onEditAuthorization()}
                >
                  {s.label}
                </Button>
              ) : (
                <p className="text-sm text-red-900">• {s.label}</p>
              )}
            </li>
          )
        })}
      </ul>
      {onEditAuthorization ? (
        <button
          type="button"
          className="mt-3 text-sm font-medium text-red-800 underline underline-offset-2 hover:text-red-950"
          onClick={onEditAuthorization}
        >
          Edit authorization minutes instead
        </button>
      ) : null}
    </div>
  )
}
