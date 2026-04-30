import { useEffect, useMemo, useRef, useState } from "react"
import { AnimatePresence, motion } from "framer-motion"
import confetti from "canvas-confetti"
import { AlertTriangle, CheckCircle2, ChevronDown } from "lucide-react"

import { AnimatedNumber } from "@/components/AnimatedNumber"
import { MoneyOdometer } from "@/components/MoneyOdometer"
import { successPulse } from "@/lib/motion"
import { useReducedMotion } from "@/lib/useReducedMotion"
import { cn } from "@/lib/utils"
import {
  formatHoursMinutes,
  formatInt,
  formatSignedMoney,
} from "@/lib/format"
import type {
  Plan,
  PlacementOverride,
  TaskPlacement,
  WeekdayDurationOverride,
} from "@/types"
import {
  billingCapStatus,
  deliveredMinutesFromPlan,
  mdhhsFormTotalsFromAuthorizedTasks,
  scheduledMonthlyMinutesFromPlan,
} from "@/lib/scheduleBuild"
import { stripLeadingCheckNumber } from "@/lib/scheduleUtils"

interface ReconciliationBannerProps {
  plan: Plan
  /** Pay rate ($/hr) — required to match per-line Σ monthly minutes display. */
  payRate: number
  /** If true, fires a one-time confetti burst on mount (from the upload flow). */
  celebrate?: boolean
}

/**
 * Month-at-a-glance hero: authorized vs delivered vs billable, with validation status.
 *
 * Animated behaviors:
 *   - Two variants (success / warning) that flip between each other with a
 *     3D rotateX crossfade when `passed` changes (e.g. after an edit).
 *   - Numbers tween smoothly when they change (AnimatedNumber / MoneyOdometer).
 *   - Optional confetti burst fires once when `celebrate` is set.
 */
export function ReconciliationBanner({
  plan,
  payRate,
  celebrate = false,
}: ReconciliationBannerProps) {
  const reduced = useReducedMotion()
  const iconRef = useRef<HTMLDivElement>(null)
  const firedConfettiRef = useRef(false)

  const checks = plan.validation?.checks ?? []
  const total = checks.length
  const failedChecks = checks.filter((c) => !c.passed)
  const passed = plan.validation_passed

  const authTotals = useMemo(
    () => mdhhsFormTotalsFromAuthorizedTasks(plan.tasks, Number(payRate) || 0),
    [plan.tasks, payRate],
  )
  const authorizedMin = useMemo(() => {
    const fromSched = scheduledMonthlyMinutesFromPlan(plan)
    return fromSched > 0 ? fromSched : authTotals.monthlyMinutes
  }, [plan, authTotals.monthlyMinutes])

  const deliveredMin = useMemo(() => deliveredMinutesFromPlan(plan), [plan])

  const capStatus = useMemo(
    () => billingCapStatus(authorizedMin, deliveredMin),
    [authorizedMin, deliveredMin],
  )

  // MDHHS-authorized monthly amount — strict hierarchy:
  //   1. Trust the form total if extraction captured it cleanly.
  //   2. Else sum the per-line amounts from the PDF (each > 0).
  //   3. Last resort: use the scheduled amount (yields variance = 0).
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

  const scheduledMonthly = plan.monthly_amount

  const schedRec = plan.schedule as Record<string, unknown> | undefined
  const deliveredDollars = Number(schedRec?.delivered_amount ?? 0)
  const billableDollars = Number(
    plan.billable_amount ?? schedRec?.billable_amount ?? 0,
  )
  const nonBillableOverMin = Math.max(0, deliveredMin - authorizedMin)
  const underDeliveryMin = Math.max(0, authorizedMin - deliveredMin)
  const nonBillableOverDollars =
    deliveredDollars > 0 && billableDollars > 0
      ? Math.max(0, deliveredDollars - billableDollars)
      : 0
  const underDeliveryDollars =
    authorizedMonthly > 0 && billableDollars > 0 && capStatus === "under_cap"
      ? Math.max(0, authorizedMonthly - billableDollars)
      : Math.max(0, authorizedMonthly - deliveredDollars)

  const capHeadline =
    capStatus === "at_cap"
      ? "AT CAP — billable equals authorization"
      : capStatus === "under_cap"
        ? "UNDER CAP — billable equals delivered"
        : "EXACT — delivered matches authorization"

  const showSuccess = passed

  const weeklyMinutes = plan.weekly_minutes

  const placementNoteRows = useMemo(() => {
    const tasks = plan.config?.tasks as TaskPlacement[] | undefined
    if (!Array.isArray(tasks)) return []
    const rows: { taskName: string; overrides: PlacementOverride[] }[] = []
    for (const t of tasks) {
      const o = t.placement_overrides
      if (Array.isArray(o) && o.length > 0) {
        rows.push({ taskName: t.task_name, overrides: o as PlacementOverride[] })
      }
    }
    return rows
  }, [plan.config])

  const placementOverrideCount = useMemo(
    () => placementNoteRows.reduce((s, r) => s + r.overrides.length, 0),
    [placementNoteRows],
  )

  const [scheduleNotesOpen, setScheduleNotesOpen] = useState(false)
  const [durationTargetsOpen, setDurationTargetsOpen] = useState(false)

  const weekdayDurationOverrides = useMemo((): WeekdayDurationOverride[] => {
    const raw = plan.config?.weekday_override_log
    if (!Array.isArray(raw)) return []
    return raw as WeekdayDurationOverride[]
  }, [plan.config])

  // ----- Flip on passed change -----
  // We derive a "version" for the content keyed on `passed`. AnimatePresence
  // swaps the whole banner body out with a rotateX crossfade when this flips
  // (success → warning or vice-versa). We use `initial={false}` so the very
  // first render doesn't flip.
  const [variantKey, setVariantKey] = useState<"success" | "warning">(
    showSuccess ? "success" : "warning",
  )
  useEffect(() => {
    setVariantKey(showSuccess ? "success" : "warning")
  }, [showSuccess])

  // ----- Confetti burst -----
  useEffect(() => {
    if (!celebrate || reduced || firedConfettiRef.current || !showSuccess) return
    firedConfettiRef.current = true

    // Fire from the icon's on-screen position, two bursts for depth.
    const el = iconRef.current
    const rect = el?.getBoundingClientRect()
    const origin = rect
      ? {
          x: (rect.left + rect.width / 2) / window.innerWidth,
          y: (rect.top + rect.height / 2) / window.innerHeight,
        }
      : { x: 0.1, y: 0.2 }

    confetti({
      particleCount: 40,
      spread: 70,
      startVelocity: 35,
      ticks: 120,
      origin,
      colors: ["#047857", "#10b981", "#34d399", "#a7f3d0", "#ecfdf5"],
      scalar: 0.9,
      disableForReducedMotion: true,
    })
      window.setTimeout(() => {
      confetti({
        particleCount: 25,
        spread: 100,
        startVelocity: 28,
        ticks: 100,
        origin,
        colors: ["#047857", "#10b981", "#34d399"],
        scalar: 0.75,
        disableForReducedMotion: true,
      })
    }, 180)
  }, [celebrate, showSuccess, reduced])

  return (
    <motion.section
      initial={reduced ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
      style={{ perspective: 1200 }}
      className="relative"
      aria-label={showSuccess ? "Plan passed validation" : "Plan needs review"}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={variantKey}
          initial={
            reduced ? { opacity: 0 } : { opacity: 0, rotateX: -90 }
          }
          animate={{ opacity: 1, rotateX: 0 }}
          exit={reduced ? { opacity: 0 } : { opacity: 0, rotateX: 90 }}
          transition={{ duration: reduced ? 0.12 : 0.5, ease: [0.22, 1, 0.36, 1] }}
          style={{ transformOrigin: "center" }}
          className={cn(
            "flex w-full items-center gap-6 rounded-xl px-6 py-6 shadow-md",
            "min-h-[140px]",
            showSuccess
              ? "border border-[color:color-mix(in_srgb,var(--success)_20%,transparent)] bg-gradient-to-br from-[color:var(--success-bg)] to-white"
              : "border border-[color:color-mix(in_srgb,var(--warning)_20%,transparent)] bg-gradient-to-br from-[color:var(--warning-bg)] to-white",
          )}
        >
          {/* Status icon */}
          <motion.div
            ref={iconRef}
            initial={showSuccess && !reduced ? successPulse.initial : { scale: 1 }}
            animate={showSuccess && !reduced ? successPulse.animate : { scale: 1 }}
            transition={showSuccess && !reduced ? successPulse.transition : undefined}
            className={cn(
              "flex h-12 w-12 shrink-0 items-center justify-center rounded-full text-white shadow-sm",
              showSuccess ? "bg-success" : "bg-warning",
            )}
            aria-hidden
          >
            {showSuccess ? (
              <CheckCircle2 className="h-6 w-6" strokeWidth={2.25} />
            ) : (
              <AlertTriangle className="h-6 w-6" strokeWidth={2.25} />
            )}
          </motion.div>

          {/* Headline block */}
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div
              className={cn(
                "text-[11px] font-semibold uppercase tracking-wider",
                showSuccess ? "text-success" : "text-warning",
              )}
            >
              {showSuccess ? capHeadline : "Review needed — validation"}
            </div>
            <h2 className="font-display text-2xl font-semibold tracking-tight text-neutral-900">
              {showSuccess ? (
                <>
                  <MoneyOdometer
                    value={
                      billableDollars > 0 ? billableDollars : scheduledMonthly
                    }
                  />{" "}
                  billable this month
                </>
              ) : (
                <>
                  {failedChecks.length} of {total} checks need attention
                </>
              )}
            </h2>
            <p className="text-sm text-neutral-600">
              {showSuccess ? (
                <>
                  Delivered{" "}
                  <AnimatedNumber
                    value={deliveredMin}
                    format={(v) => formatHoursMinutes(v)}
                    className="tabular"
                  />{" "}
                  vs. authorized{" "}
                  <AnimatedNumber
                    value={authorizedMin}
                    format={(v) => formatHoursMinutes(v)}
                    className="tabular"
                  />
                  .
                  {nonBillableOverMin > 0 ? (
                    <>
                      {" "}
                      Non-billable overshoot:{" "}
                      <span className="tabular font-medium text-neutral-800">
                        {formatInt(nonBillableOverMin)} min
                      </span>
                      {nonBillableOverDollars > 0 ? (
                        <>
                          {" "}
                          ({formatSignedMoney(nonBillableOverDollars)})
                        </>
                      ) : null}
                      .
                    </>
                  ) : null}
                  {underDeliveryMin > 0 ? (
                    <>
                      {" "}
                      Under-delivery:{" "}
                      <span className="tabular font-medium text-neutral-800">
                        {formatInt(underDeliveryMin)} min
                      </span>
                      {underDeliveryDollars > 0 ? (
                        <>
                          {" "}
                          ({formatSignedMoney(-underDeliveryDollars)})
                        </>
                      ) : null}
                      .
                    </>
                  ) : null}{" "}
                  <span className="tabular">{total}</span>/
                  <span className="tabular">{total}</span> cross-checks passed.
                </>
              ) : failedChecks.length > 0 ? (
                <>
                  Failing:{" "}
                  {failedChecks.slice(0, 2).map((c, i) => (
                    <span key={c.name + i}>
                      {i > 0 && <span className="text-neutral-400"> · </span>}
                      <span className="font-medium text-neutral-800">
                        {stripLeadingCheckNumber(c.name)}
                      </span>
                    </span>
                  ))}
                  {failedChecks.length > 2 && (
                    <span className="text-neutral-500">
                      {" "}and {failedChecks.length - 2} more
                    </span>
                  )}
                </>
              ) : (
                "Open the Reconciliation tab for details."
              )}
            </p>
            {placementOverrideCount > 0 ? (
              <div className="mt-3 rounded-lg border border-neutral-200/90 bg-white/60 text-left">
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm font-medium text-neutral-800 hover:bg-neutral-50/80"
                  onClick={() => setScheduleNotesOpen((o) => !o)}
                  aria-expanded={scheduleNotesOpen}
                >
                  <span>
                    Schedule notes — {placementOverrideCount} of your day picks
                    were moved to fit worker availability.
                  </span>
                  <ChevronDown
                    className={cn(
                      "h-4 w-4 shrink-0 text-neutral-500 transition-transform",
                      scheduleNotesOpen && "rotate-180",
                    )}
                    aria-hidden
                  />
                </button>
                {scheduleNotesOpen ? (
                  <ul className="space-y-2 border-t border-neutral-200/80 px-3 py-3 text-xs text-neutral-700">
                    {placementNoteRows.map((row) =>
                      row.overrides.map((o, j) => (
                        <li key={`${row.taskName}-${j}-${o.preferred}`}>
                          <span className="font-semibold text-neutral-900">
                            {row.taskName}
                          </span>
                          : you picked {o.preferred}, placed on {o.placed_on}.{" "}
                          {o.reason}
                        </li>
                      )),
                    )}
                  </ul>
                ) : null}
              </div>
            ) : null}
            {weekdayDurationOverrides.length > 0 ? (
              <div className="mt-3 rounded-lg border border-neutral-200/90 bg-white/60 text-left">
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm font-medium text-neutral-800 hover:bg-neutral-50/80"
                  onClick={() => setDurationTargetsOpen((o) => !o)}
                  aria-expanded={durationTargetsOpen}
                >
                  <span>
                    Your target lengths for {weekdayDurationOverrides.length}{" "}
                    weekday
                    {weekdayDurationOverrides.length === 1 ? "" : "s"} couldn&apos;t
                    be hit exactly — see details.
                  </span>
                  <ChevronDown
                    className={cn(
                      "h-4 w-4 shrink-0 text-neutral-500 transition-transform",
                      durationTargetsOpen && "rotate-180",
                    )}
                    aria-hidden
                  />
                </button>
                {durationTargetsOpen ? (
                  <ul className="space-y-2 border-t border-neutral-200/80 px-3 py-3 text-xs text-neutral-700">
                    {weekdayDurationOverrides.map((e) => (
                      <li key={e.weekday}>
                        <span className="font-semibold text-neutral-900">
                          {e.weekday}
                        </span>
                        : preferred {e.preferred_duration} min, actual average{" "}
                        {e.actual_duration} min. {e.reason}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
          </div>

          {/* Vertical KPI strip */}
          <div
            className="hidden shrink-0 items-stretch gap-6 md:flex"
            role="group"
            aria-label="Key figures"
          >
            <Kpi
              label="Weekly"
              value={
                <AnimatedNumber
                  value={weeklyMinutes}
                  format={(v) => formatHoursMinutes(v)}
                  className="tabular"
                />
              }
              hint={`${formatInt(weeklyMinutes)} min`}
            />
            <KpiDivider />
            <Kpi label="Monthly" value={<MoneyOdometer value={scheduledMonthly} />} />
            <KpiDivider />
            <Kpi
              label={
                capStatus === "at_cap"
                  ? "Over cap (min)"
                  : capStatus === "under_cap"
                    ? "Under cap (min)"
                    : "Δ minutes"
              }
              value={
                <span className="tabular">
                  {capStatus === "at_cap"
                    ? formatInt(nonBillableOverMin)
                    : capStatus === "under_cap"
                      ? `−${formatInt(underDeliveryMin)}`
                      : "0"}
                </span>
              }
              tone={showSuccess ? "success" : "warning"}
            />
          </div>
        </motion.div>
      </AnimatePresence>
    </motion.section>
  )
}

function KpiDivider() {
  return <div className="w-px self-stretch bg-neutral-200/80" aria-hidden />
}

function Kpi({
  label,
  value,
  tone = "default",
  hint,
}: {
  label: string
  value: React.ReactNode
  tone?: "default" | "success" | "warning"
  hint?: React.ReactNode
}) {
  return (
    <div className="flex min-w-[110px] flex-col items-start justify-center">
      <div className="label-caps text-[10px]">{label}</div>
      <div
        className={cn(
          "mt-1 font-display text-3xl font-semibold tabular leading-none",
          tone === "success"
            ? "text-success"
            : tone === "warning"
              ? "text-warning"
              : "text-neutral-900",
        )}
      >
        {value}
      </div>
      {hint !== undefined && hint !== null && hint !== "" ? (
        <div className="mt-1 text-xs tabular text-neutral-500">{hint}</div>
      ) : null}
    </div>
  )
}

export default ReconciliationBanner
