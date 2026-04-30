import { useMemo, useState } from "react"
import { AnimatePresence, motion } from "framer-motion"
import { Check, ChevronDown, X as XIcon } from "lucide-react"

import { displayExpectedActual, stripLeadingCheckNumber } from "@/lib/scheduleUtils"
import {
  deliveredMinutesFromPlan,
  mdhhsFormTotalsFromAuthorizedTasks,
  scheduledMonthlyMinutesFromPlan,
} from "@/lib/scheduleBuild"
import { formatMoney } from "@/lib/format"
import { cn } from "@/lib/utils"
import { easeOutSoft } from "@/lib/motion"
import type { Plan, ValidationCheck } from "@/types"

interface ReconciliationPanelProps {
  plan: Plan
}

/**
 * Some check names are per-task sub-checks of the master "task minute
 * reconciliation" check. We heuristically nest anything that looks like a
 * task-level check under the last parent it follows.
 */
function isSubCheck(name: string): boolean {
  return /task\b|per[- ]task|task minute|task amount/i.test(name)
}

function cleanCheckName(raw: string): string {
  return stripLeadingCheckNumber(raw)
}

type CheckRow = {
  idx: number
  raw: ValidationCheck
  depth: number // 0 root, 1 sub-check
}

function flatten(checks: readonly ValidationCheck[]): CheckRow[] {
  const out: CheckRow[] = []
  let parentIndex = -1
  checks.forEach((c, i) => {
    const sub = isSubCheck(c.name) && parentIndex >= 0
    out.push({ idx: i, raw: c, depth: sub ? 1 : 0 })
    if (!sub) parentIndex = i
  })
  return out
}

/* ---------- Status pill ---------- */

function StatusPill({ passed }: { passed: boolean }) {
  return passed ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--success-bg)] px-2 py-0.5 text-[11px] font-semibold text-success ring-1 ring-inset ring-[color:color-mix(in_srgb,var(--success)_25%,transparent)]">
      <Check className="h-3 w-3" strokeWidth={3} />
      Passed
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--danger-bg)] px-2 py-0.5 text-[11px] font-semibold text-danger ring-1 ring-inset ring-[color:color-mix(in_srgb,var(--danger)_25%,transparent)]">
      <XIcon className="h-3 w-3" strokeWidth={3} />
      Failed
    </span>
  )
}

/* ---------- Row ---------- */

function CheckRowView({
  row,
  displayNumber,
  expanded,
  onToggle,
}: {
  row: CheckRow
  displayNumber: number | null
  expanded: boolean
  onToggle: () => void
}) {
  const { raw, depth } = row
  const name = cleanCheckName(raw.name)
  const canExpand = !raw.passed && !!raw.detail

  return (
    <li
      className={cn(
        "border-t border-neutral-100 first:border-t-0",
        depth === 1 && "bg-neutral-50/40",
      )}
    >
      <button
        type="button"
        onClick={canExpand ? onToggle : undefined}
        className={cn(
          "grid w-full grid-cols-[40px_minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.6fr)_auto_auto] items-center gap-3 px-4 py-3 text-left",
          "transition-colors hover:bg-neutral-50",
          depth === 1 && "pl-12",
          canExpand ? "cursor-pointer" : "cursor-default",
        )}
      >
        {/* Number badge */}
        {displayNumber !== null ? (
          <span
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-semibold tabular",
              raw.passed
                ? "bg-primary-50 text-primary-800 ring-1 ring-inset ring-primary-100"
                : "bg-[color:var(--danger-bg)] text-danger ring-1 ring-inset ring-[color:color-mix(in_srgb,var(--danger)_25%,transparent)]",
            )}
          >
            {displayNumber}
          </span>
        ) : (
          <span className="flex h-7 w-7 items-center justify-center text-neutral-300">
            ·
          </span>
        )}

        {/* Name */}
        <span
          className={cn(
            "min-w-0 truncate text-sm",
            depth === 0 ? "font-medium text-neutral-900" : "text-neutral-700",
          )}
          title={name}
        >
          {name}
        </span>

        {/* Expected */}
        <span className="min-w-0 truncate font-mono text-xs text-neutral-600">
          {displayExpectedActual(raw.expected, raw.name)}
        </span>

        {/* Actual */}
        <span
          className={cn(
            "min-w-0 truncate font-mono text-xs",
            raw.passed ? "text-neutral-600" : "text-danger",
          )}
        >
          {displayExpectedActual(raw.actual, raw.name)}
        </span>

        {/* Tolerance */}
        <span className="min-w-0 truncate text-[11px] text-neutral-500 tabular">
          {raw.tolerance || "—"}
        </span>

        {/* Status */}
        <StatusPill passed={raw.passed} />

        {/* Chevron */}
        <motion.span
          animate={{ rotate: expanded ? 180 : 0 }}
          transition={{ duration: 0.18, ease: easeOutSoft }}
          className={cn(
            "inline-flex h-5 w-5 items-center justify-center text-neutral-400",
            !canExpand && "opacity-0",
          )}
          aria-hidden
        >
          <ChevronDown className="h-4 w-4" />
        </motion.span>
      </button>

      <AnimatePresence initial={false}>
        {expanded && canExpand && (
          <motion.div
            key="detail"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: easeOutSoft }}
            className={cn("overflow-hidden", depth === 1 && "pl-8")}
          >
            <div className="border-t border-neutral-100 bg-[color:var(--danger-bg)]/50 px-4 py-3 pl-16">
              <div className="label-caps mb-1 text-[10px] text-danger">
                Detail
              </div>
              <pre className="whitespace-pre-wrap text-xs text-neutral-800">
                {raw.detail}
              </pre>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  )
}

/* ---------- Main panel ---------- */

export function ReconciliationPanel({ plan }: ReconciliationPanelProps) {
  const checks = plan.validation?.checks ?? []
  const total = checks.length
  const passed = checks.filter((c) => c.passed).length

  const flat = useMemo(() => flatten(checks), [checks])

  // Assign display numbers only to root checks.
  const displayNumbers = useMemo(() => {
    let n = 0
    return flat.map((r) => (r.depth === 0 ? ++n : null))
  }, [flat])

  const [expanded, setExpanded] = useState<Set<number>>(
    () => new Set(flat.filter((r) => !r.raw.passed).map((r) => r.idx)),
  )
  const toggle = (idx: number) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })

  const allPassed = plan.validation_passed

  const schedRec = plan.schedule as Record<string, unknown> | undefined
  const authMin = scheduledMonthlyMinutesFromPlan(plan)
  const authTotals = useMemo(
    () => mdhhsFormTotalsFromAuthorizedTasks(plan.tasks, 0),
    [plan.tasks],
  )
  const authorizedMinutes =
    authMin > 0 ? authMin : authTotals.monthlyMinutes
  const deliveredMin = useMemo(() => deliveredMinutesFromPlan(plan), [plan])
  const billableMin = Number(
    plan.billable_minutes ?? schedRec?.billable_minutes ?? Math.min(deliveredMin, authorizedMinutes),
  )
  const nonBillMin = Math.max(0, deliveredMin - authorizedMinutes)
  const underMin = Math.max(0, authorizedMinutes - deliveredMin)

  const authorizedMonthly = useMemo(() => {
    if (plan.mdhhs_form_amount && plan.mdhhs_form_amount > 0) {
      return plan.mdhhs_form_amount
    }
    const sum = plan.tasks.reduce((s, t) => {
      const v = Number(t.monthly_amount)
      return Number.isFinite(v) && v > 0 ? s + v : s
    }, 0)
    if (sum > 0) return sum
    return plan.monthly_amount
  }, [plan.mdhhs_form_amount, plan.tasks, plan.monthly_amount])

  const deliveredMonthly = Number(
    plan.delivered_amount ?? schedRec?.delivered_amount ?? 0,
  )
  const billableMonthly = Number(
    plan.billable_amount ?? schedRec?.billable_amount ?? 0,
  )

  const validationStatus = plan.validation?.validation_status ?? ""
  const billingStatusLabel = useMemo(() => {
    const m: Record<string, string> = {
      BILLABLE_EXACT: "BILLABLE EXACT",
      BILLABLE_AT_CAP: "BILLABLE AT CAP",
      BILLABLE_UNDER_CAP: "BILLABLE UNDER CAP",
      INVALID: "INVALID",
    }
    return m[validationStatus] ?? (validationStatus || "—")
  }, [validationStatus])

  return (
    <div className="flex flex-col gap-4">
      {/* Cross-check table */}
      <section className="overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-xs">
        <header className="grid grid-cols-[40px_minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.6fr)_auto_auto] items-center gap-3 border-b border-neutral-200 bg-neutral-50 px-4 py-2.5">
          <span className="label-caps text-[10px]">#</span>
          <span className="label-caps text-[10px]">Check</span>
          <span className="label-caps text-[10px]">Expected</span>
          <span className="label-caps text-[10px]">Actual</span>
          <span className="label-caps text-[10px]">Tol.</span>
          <span className="label-caps text-[10px]">Status</span>
          <span className="w-5" />
        </header>
        {total === 0 ? (
          <div className="px-4 py-6 text-sm text-neutral-500">
            No validation data for this plan.
          </div>
        ) : (
          <ul>
            {flat.map((r, i) => (
              <CheckRowView
                key={r.raw.name + r.idx}
                row={r}
                displayNumber={displayNumbers[i]}
                expanded={expanded.has(r.idx)}
                onToggle={() => toggle(r.idx)}
              />
            ))}
          </ul>
        )}
      </section>

      {/* Grand total reconciliation */}
      <section
        className={cn(
          "rounded-xl border p-5 shadow-xs",
          allPassed
            ? "border-[color:color-mix(in_srgb,var(--success)_20%,transparent)] bg-[color:var(--success-bg)]/60"
            : "border-[color:color-mix(in_srgb,var(--warning)_20%,transparent)] bg-[color:var(--warning-bg)]/60",
        )}
      >
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-3">
            <span
              className={cn(
                "inline-flex h-8 w-8 items-center justify-center rounded-full text-white",
                allPassed ? "bg-success" : "bg-warning",
              )}
            >
              {allPassed ? (
                <Check className="h-4 w-4" strokeWidth={3} />
              ) : (
                <XIcon className="h-4 w-4" strokeWidth={3} />
              )}
            </span>
            <div>
              <div className="label-caps text-[10px]">Reconciliation</div>
              <div className="flex flex-wrap items-center gap-2">
                <div className="font-display text-lg font-semibold tracking-tight text-neutral-900">
                  {passed} of {total} checks passed
                </div>
                {validationStatus ? (
                  <span
                    className={cn(
                      "rounded-full px-2.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset",
                      validationStatus === "INVALID"
                        ? "bg-red-50 text-red-800 ring-red-200/80"
                        : "bg-emerald-50 text-emerald-900 ring-emerald-200/80",
                    )}
                  >
                    {billingStatusLabel}
                  </span>
                ) : null}
              </div>
              <p className="mt-1 max-w-xl text-xs text-neutral-600">
                Authorization is the billing cap. Delivered minutes are projected from the weekly pattern
                across the calendar month. Billable = min(delivered, authorized) per ASM 144.
              </p>
            </div>
            </div>
          </div>

          <div className="flex w-full min-w-0 shrink-0 flex-col gap-4 lg:max-w-2xl">
            <div className="grid grid-cols-2 gap-x-4 gap-y-3 md:grid-cols-4">
              <GrandStat
                label="Authorized (min)"
                value={`${authorizedMinutes}`}
              />
              <GrandStat label="Delivered (min)" value={`${deliveredMin}`} />
              <GrandStat label="Billable (min)" value={`${billableMin}`} />
              <GrandStat
                label={nonBillMin > 0 ? "Non-billable (min)" : "Under cap (min)"}
                value={`${nonBillMin > 0 ? nonBillMin : underMin}`}
                tone={allPassed ? "success" : "warning"}
              />
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-3 border-t border-neutral-200/80 pt-4 sm:grid-cols-3 sm:items-end">
              <GrandStat label="Authorized $" value={formatMoney(authorizedMonthly)} />
              <GrandStat label="Delivered $" value={formatMoney(deliveredMonthly)} />
              <GrandStat
                label="Billable $"
                value={formatMoney(billableMonthly)}
                tone={allPassed ? "success" : "warning"}
              />
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}

function GrandStat({
  label,
  value,
  tone = "default",
}: {
  label: string
  value: string
  tone?: "default" | "success" | "warning"
}) {
  return (
    <div className="flex min-h-[3.25rem] min-w-0 flex-col items-end justify-end tabular-nums">
      <div className="max-w-[11rem] text-right leading-tight">
        <div className="label-caps text-[10px]">{label}</div>
        <div
          className={cn(
            "mt-0.5 font-display text-xl font-semibold tabular",
            tone === "success"
              ? "text-success"
              : tone === "warning"
                ? "text-warning"
                : "text-neutral-900",
          )}
        >
          {value}
        </div>
      </div>
    </div>
  )
}

export default ReconciliationPanel
