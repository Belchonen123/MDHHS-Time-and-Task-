import { useMemo } from "react"
import { format, parseISO } from "date-fns"
import { motion } from "framer-motion"
import { CheckCircle2, XCircle } from "lucide-react"

import { cn } from "@/lib/utils"
import {
  formatHoursMinutes,
  formatInt,
  formatMoney,
} from "@/lib/format"
import {
  deliveredMinutesFromPlan,
  mdhhsFormTotalsFromAuthorizedTasks,
  scheduledMonthlyMinutesFromPlan,
} from "@/lib/scheduleBuild"
import type { Client, Plan } from "@/types"

interface SummaryPanelProps {
  plan: Plan
  client: Client
  plans: readonly Plan[]
  selectedVersion: number
  onSelectVersion: (version: number) => void
}

function sourceFileName(path: string) {
  if (!path) return "—"
  const parts = path.split(/[/\\]/)
  return parts[parts.length - 1] || path
}

/* ---------- Stats strip ---------- */

function StatCard({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail?: string
}) {
  return (
    <div className="flex-1 rounded-lg border border-neutral-200 bg-white p-5 shadow-xs">
      <div className="label-caps text-[10px]">{label}</div>
      <div className="mt-2 font-display text-2xl font-semibold tabular text-neutral-900">
        {value}
      </div>
      {detail && (
        <div className="mt-1 text-xs text-neutral-500 tabular">{detail}</div>
      )}
    </div>
  )
}

/* ---------- About this client ---------- */

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] items-baseline gap-4 py-2">
      <dt className="label-caps text-[10px]">{label}</dt>
      <dd className="text-sm text-neutral-900">{children}</dd>
    </div>
  )
}

function AboutClient({ client, plan }: { client: Client; plan: Plan }) {
  const asw = [client.asw_name, client.asw_phone].filter(Boolean).join(" · ")
  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-5 shadow-xs">
      <h3 className="mb-3 font-display text-lg font-semibold tracking-tight text-neutral-900">
        About this client
      </h3>
      <dl className="divide-y divide-neutral-100">
        <InfoRow label="Client">
          <span className="font-medium">{client.client_name || "—"}</span>
        </InfoRow>
        <InfoRow label="Client ID">
          <span className="font-mono text-xs text-neutral-700">{client.client_id}</span>
        </InfoRow>
        <InfoRow label="Case #">
          <span className="font-mono text-xs text-neutral-700">
            {client.case_number || "—"}
          </span>
        </InfoRow>
        <InfoRow label="County">{client.county || "—"}</InfoRow>
        <InfoRow label="Pay rate">
          <span className="tabular">{formatMoney(client.pay_rate)}</span>
          <span className="text-neutral-500"> / hr</span>
        </InfoRow>
        <InfoRow label="ASW">{asw || "—"}</InfoRow>
        <InfoRow label="Source PDF">
          <span className="font-mono text-xs text-neutral-700">
            {sourceFileName(plan.source_pdf_path)}
          </span>
        </InfoRow>
      </dl>
    </section>
  )
}

/* ---------- Plan history ---------- */

function PlanHistory({
  plans,
  selectedVersion,
  onSelectVersion,
}: {
  plans: readonly Plan[]
  selectedVersion: number
  onSelectVersion: (v: number) => void
}) {
  const sorted = [...plans].sort((a, b) => b.version - a.version)
  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-5 shadow-xs">
      <h3 className="mb-3 font-display text-lg font-semibold tracking-tight text-neutral-900">
        Plan history
      </h3>
      {sorted.length === 0 ? (
        <p className="text-sm text-neutral-500">No plans yet.</p>
      ) : (
        <ol className="relative space-y-1 pl-4 before:absolute before:left-[7px] before:top-2 before:bottom-2 before:w-px before:bg-neutral-200">
          {sorted.map((p) => {
            const active = p.version === selectedVersion
            return (
              <li key={p.id} className="relative">
                <motion.button
                  type="button"
                  onClick={() => onSelectVersion(p.version)}
                  whileHover={{ x: 2 }}
                  className={cn(
                    "relative w-full rounded-md py-2 pl-6 pr-3 text-left transition-colors",
                    active
                      ? "bg-primary-50 ring-1 ring-primary-100"
                      : "hover:bg-neutral-50",
                  )}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "absolute left-[-9px] top-1/2 h-3 w-3 -translate-y-1/2 rounded-full border-2",
                      active
                        ? "border-primary-700 bg-white"
                        : "border-neutral-300 bg-white",
                    )}
                  />
                  <div className="flex items-baseline justify-between gap-2">
                    <div className="flex items-baseline gap-2">
                      <span
                        className={cn(
                          "inline-flex items-center rounded-md border px-1.5 py-0.5 font-mono text-[10px] font-semibold",
                          active
                            ? "border-primary-200 bg-white text-primary-800"
                            : "border-neutral-200 bg-neutral-50 text-neutral-700",
                        )}
                      >
                        v{p.version}
                      </span>
                      <span className="tabular text-sm font-medium text-neutral-900">
                        {formatMoney(p.monthly_amount)}
                      </span>
                    </div>
                    {p.validation_passed ? (
                      <CheckCircle2
                        className="h-4 w-4 text-success"
                        aria-label="All checks passed"
                      />
                    ) : (
                      <XCircle
                        className="h-4 w-4 text-danger"
                        aria-label="Some checks failed"
                      />
                    )}
                  </div>
                  <div className="mt-0.5 text-xs text-neutral-500 tabular">
                    {format(parseISO(p.created_at), "MMM d, yyyy · h:mm a")}
                  </div>
                </motion.button>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}

/* ---------- Main panel ---------- */

export function SummaryPanel({
  plan,
  client,
  plans,
  selectedVersion,
  onSelectVersion,
}: SummaryPanelProps) {
  const sched = plan.schedule
  const authRollup = useMemo(
    () =>
      mdhhsFormTotalsFromAuthorizedTasks(
        plan.tasks,
        Number(client.pay_rate) || 0,
      ),
    [plan.tasks, client.pay_rate],
  )
  const scheduledTargetMin = useMemo(
    () => deliveredMinutesFromPlan(plan),
    [plan],
  )
  const authCapMin = useMemo(
    () => scheduledMonthlyMinutesFromPlan(plan),
    [plan],
  )
  const billableMin =
    plan.billable_minutes ??
    (plan.schedule as { billable_minutes?: number } | undefined)
      ?.billable_minutes ??
    Math.min(scheduledTargetMin, authCapMin || scheduledTargetMin)
  const billableUsd =
    plan.billable_amount ??
    (plan.schedule as { billable_amount?: number } | undefined)
      ?.billable_amount
  const deliveredUsd =
    plan.delivered_amount ??
    Number((sched as Record<string, unknown>)?.delivered_amount) ??
    0
  const weeklyBudget = plan.weekly_minutes

  // "Monthly auth" = MDHHS-authorized monthly total from per-task monthly_amounts.
  const authorizedMonthly = plan.tasks.reduce((s, t) => {
    const v = Number(t.monthly_amount)
    return Number.isFinite(v) ? s + v : s
  }, 0)

  const dayCount = Object.keys(sched?.days ?? {}).length

  return (
    <div className="flex flex-col gap-6">
      {/* Stats strip */}
      <div className="flex gap-4">
        <StatCard
          label="Weekly budget"
          value={formatHoursMinutes(weeklyBudget)}
          detail={`${formatInt(weeklyBudget)} min`}
        />
        <StatCard
          label="Monthly auth"
          value={authorizedMonthly > 0 ? formatMoney(authorizedMonthly) : "—"}
          detail={`${formatHoursMinutes(authRollup.monthlyMinutes)} (${formatInt(authRollup.monthlyMinutes)} min)`}
        />
        <StatCard
          label="Delivered (calendar)"
          value={deliveredUsd > 0 ? formatMoney(deliveredUsd) : "—"}
          detail={`${formatHoursMinutes(scheduledTargetMin)} (${formatInt(scheduledTargetMin)} min)`}
        />
        <StatCard
          label="Billable (invoice)"
          value={
            billableUsd != null && billableUsd > 0
              ? formatMoney(billableUsd)
              : "—"
          }
          detail={`${formatHoursMinutes(billableMin)} (${formatInt(billableMin)} min)`}
        />
        <StatCard
          label="Days scheduled"
          value={`${formatInt(dayCount)} of 7`}
          detail={`${plan.tasks.filter((t) => (t.task_name || "").trim()).length} tasks`}
        />
      </div>

      {/* Two-panel grid */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.2fr_1fr]">
        <AboutClient client={client} plan={plan} />
        <PlanHistory
          plans={plans}
          selectedVersion={selectedVersion}
          onSelectVersion={onSelectVersion}
        />
      </div>
    </div>
  )
}

export default SummaryPanel
