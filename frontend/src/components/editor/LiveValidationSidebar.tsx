import { AnimatePresence, motion } from "framer-motion"
import { AlertCircle, CheckCircle2 } from "lucide-react"

import { formatInt, formatMoney, formatSignedMoney } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { Issue, TaskCoverage } from "@/lib/scheduleEditor"

interface LiveValidationSidebarProps {
  weeklyMinutes: number
  targetWeeklyMinutes: number
  currentMonthlyAmount: number
  authorizedMonthlyAmount: number
  taskCoverage: readonly TaskCoverage[]
  issues: readonly Issue[]
}

function coverageStatus(
  current: number,
  target: number,
): "exact" | "near" | "off" {
  if (target === 0) return "exact"
  if (current === target) return "exact"
  const diff = Math.abs(current - target) / target
  return diff <= 0.05 ? "near" : "off"
}

export function LiveValidationSidebar({
  weeklyMinutes,
  targetWeeklyMinutes,
  currentMonthlyAmount,
  authorizedMonthlyAmount,
  taskCoverage,
  issues,
}: LiveValidationSidebarProps) {
  const delta = weeklyMinutes - targetWeeklyMinutes
  const status = coverageStatus(weeklyMinutes, targetWeeklyMinutes)
  const pct =
    targetWeeklyMinutes > 0
      ? Math.min(100, Math.round((weeklyMinutes / targetWeeklyMinutes) * 100))
      : 0
  const variance = currentMonthlyAmount - authorizedMonthlyAmount

  return (
    <aside className="flex h-full w-[320px] shrink-0 flex-col overflow-y-auto border-l border-neutral-200 bg-white">
      <div className="flex flex-col gap-5 p-4">
        {/* 1. Weekly coverage */}
        <section>
          <div className="flex items-baseline justify-between">
            <h2 className="label-caps text-[10px]">Weekly coverage</h2>
            <StatusBadge status={status} />
          </div>
          <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-neutral-100">
            <motion.div
              className={cn(
                "h-full rounded-full",
                status === "exact" && "bg-success",
                status === "near" && "bg-warning",
                status === "off" && "bg-danger",
              )}
              initial={false}
              animate={{ width: `${pct}%` }}
              transition={{ type: "spring", stiffness: 260, damping: 30 }}
            />
          </div>
          <div className="mt-2 flex items-baseline justify-between">
            <div className="font-display text-xl font-semibold tabular text-neutral-900">
              {formatInt(weeklyMinutes)}{" "}
              <span className="text-sm font-medium text-neutral-500">
                / {formatInt(targetWeeklyMinutes)} min
              </span>
            </div>
            {delta !== 0 && (
              <span
                className={cn(
                  "text-xs font-semibold tabular",
                  delta > 0 ? "text-danger" : "text-warning",
                )}
              >
                {delta > 0 ? `+${delta}` : delta} min {delta > 0 ? "over" : "under"}
              </span>
            )}
          </div>
        </section>

        {/* 2. Per-task frequency */}
        <section>
          <h2 className="label-caps text-[10px]">Per-task frequency</h2>
          <ul className="mt-2 space-y-2">
            {taskCoverage.map((c) => {
              const barPct =
                c.required > 0
                  ? Math.min(100, Math.round((c.placed / c.required) * 100))
                  : c.placed > 0
                    ? 100
                    : 0
              const color =
                c.status === "exact"
                  ? "bg-success"
                  : c.status === "over"
                    ? "bg-danger"
                    : c.placed === 0
                      ? "bg-neutral-300"
                      : "bg-warning"
              return (
                <li key={c.name} className="text-xs">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate font-medium text-neutral-700">
                      {c.name}
                    </span>
                    <span
                      className={cn(
                        "shrink-0 tabular text-[11px] font-semibold",
                        c.status === "exact"
                          ? "text-success"
                          : c.status === "over"
                            ? "text-danger"
                            : "text-warning",
                      )}
                    >
                      {c.placed}/{c.required}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-neutral-100">
                    <motion.div
                      className={cn("h-full rounded-full", color)}
                      initial={false}
                      animate={{ width: `${barPct}%` }}
                      transition={{ duration: 0.2 }}
                    />
                  </div>
                </li>
              )
            })}
          </ul>
        </section>

        {/* 3. Monthly projection */}
        <section className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
          <h2 className="label-caps text-[10px]">Monthly projection</h2>
          <div className="mt-1.5">
            <motion.div
              key={currentMonthlyAmount}
              initial={{ y: -3, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              transition={{ type: "spring", stiffness: 300, damping: 28 }}
              className="font-display text-2xl font-semibold tabular text-neutral-900"
            >
              {formatMoney(currentMonthlyAmount)}
            </motion.div>
            <p className="mt-1 text-xs text-neutral-600">
              MDHHS auth:{" "}
              <span className="tabular font-medium text-neutral-900">
                {formatMoney(authorizedMonthlyAmount)}
              </span>
              {" · "}
              Variance{" "}
              <span
                className={cn(
                  "tabular font-semibold",
                  Math.abs(variance) < 0.01
                    ? "text-success"
                    : variance < 0
                      ? "text-warning"
                      : "text-danger",
                )}
              >
                {formatSignedMoney(variance)}
              </span>
            </p>
          </div>
        </section>

        {/* 4. Issues list */}
        <AnimatePresence initial={false}>
          {issues.length > 0 && (
            <motion.section
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
            >
              <h2 className="label-caps text-[10px]">
                Issues
                <span className="ml-1 tabular text-neutral-400">({issues.length})</span>
              </h2>
              <ul className="mt-2 space-y-1.5">
                {issues.map((i, idx) => (
                  <motion.li
                    key={`${idx}-${i.message}`}
                    layout
                    initial={{ opacity: 0, x: -3 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: -3 }}
                    className={cn(
                      "flex items-start gap-2 rounded-md border px-2.5 py-1.5 text-[11px] leading-snug",
                      i.severity === "error"
                        ? "border-danger/20 bg-danger-bg text-danger"
                        : "border-warning/20 bg-warning-bg text-warning",
                    )}
                  >
                    <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                    <span>{i.message}</span>
                  </motion.li>
                ))}
              </ul>
            </motion.section>
          )}
        </AnimatePresence>
      </div>
    </aside>
  )
}

function StatusBadge({ status }: { status: "exact" | "near" | "off" }) {
  if (status === "exact") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-success-bg px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-success">
        <CheckCircle2 className="h-3 w-3" strokeWidth={2.5} /> On target
      </span>
    )
  }
  if (status === "near") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-warning-bg px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-warning">
        Close
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-danger-bg px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-danger">
      Off target
    </span>
  )
}

export default LiveValidationSidebar
