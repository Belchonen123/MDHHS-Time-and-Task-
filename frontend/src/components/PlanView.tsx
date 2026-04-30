import { useCallback, useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import { downloadFile } from "@/api/client"
import { ReconciliationBanner } from "@/components/plan/ReconciliationBanner"
import { PlanTabs, type PlanTabDef } from "@/components/plan/PlanTabs"
import { SummaryPanel } from "@/components/plan/SummaryPanel"
import { WeeklyPatternPanel } from "@/components/plan/WeeklyPatternPanel"
import { DailySchedulePanel } from "@/components/plan/DailySchedulePanel"
import { ReconciliationPanel } from "@/components/plan/ReconciliationPanel"
import { DownloadsPanel } from "@/components/plan/DownloadsPanel"
import { PlanScheduleEditor } from "@/components/plan/PlanScheduleEditor"
import {
  DOWNLOAD_NETWORK_TOAST_DESCRIPTION,
  downloadBlobFromUrl,
  isLikelyDownloadNetworkFailure,
} from "@/lib/downloadBlob"
import { WEEK_DAYS } from "@/lib/scheduleUtils"
import { normalizeWorkerAvailability } from "@/lib/workerAvailability"
import type { Client, Plan } from "@/types"

export type PlanTabId =
  | "summary"
  | "schedule"
  | "weekly"
  | "daily"
  | "reconciliation"
  | "downloads"

const TABS: readonly PlanTabDef<PlanTabId>[] = [
  { id: "schedule", label: "Schedule" },
  { id: "weekly", label: "Weekly Pattern" },
  { id: "daily", label: "Daily Schedule" },
  { id: "reconciliation", label: "Reconciliation" },
  { id: "downloads", label: "Downloads" },
  { id: "summary", label: "Summary" },
]

interface PlanViewProps {
  plan: Plan
  client: Client
  clientId: string
  /** All plans for this client — used by Summary's "Plan history" timeline. */
  plans: readonly Plan[]
  /** Switch the active plan version (called from the timeline). */
  onSelectVersion: (version: number) => void
  /** Called when the user edits the ScheduleConfig — parent refreshes data. */
  onPlanUpdated?: (plan: Plan) => void
  /** Optional tab to land on (e.g. `reconciliation` when upload had warnings). */
  initialTab?: PlanTabId
  /** Fire the confetti burst once (set when navigating from the upload flow). */
  celebrate?: boolean
}

export function PlanView({
  plan,
  client,
  clientId,
  plans,
  onSelectVersion,
  onPlanUpdated,
  initialTab,
  celebrate,
}: PlanViewProps) {
  const [tab, setTab] = useState<PlanTabId>(initialTab ?? "schedule")

  const weekdayPreferredMinutes = useMemo(() => {
    const map = normalizeWorkerAvailability(
      client.availability as Record<string, unknown> | undefined,
    )
    const out: Partial<Record<string, number>> = {}
    for (const d of WEEK_DAYS) {
      const n = map[d]?.preferred_duration_min
      if (typeof n === "number" && n > 0) out[d] = n
    }
    return out
  }, [client.availability])

  // If the initialTab hint changes (e.g. user arrived from the upload flow
  // and then the data loaded), honor it once.
  useEffect(() => {
    if (initialTab) setTab(initialTab)
  }, [initialTab])

  useEffect(() => {
    const onFocusTaskPlacement = () => {
      setTab("schedule")
      window.setTimeout(() => {
        document
          .getElementById("task-placement-editor")
          ?.scrollIntoView({ behavior: "smooth", block: "start" })
      }, 50)
    }
    window.addEventListener("app:focus-task-placement", onFocusTaskPlacement)
    return () =>
      window.removeEventListener("app:focus-task-placement", onFocusTaskPlacement)
  }, [])

  /** Same URL + filename convention as DownloadsPanel weekly card. */
  const handleDownloadWeekly = useCallback(async () => {
    if (!(plan.xlsx_path && plan.xlsx_path.trim() !== "")) {
      return
    }
    const url = downloadFile(clientId, plan.version, "weekly")
    const name = `${clientId}-v${plan.version}-weekly.xlsx`
    try {
      await downloadBlobFromUrl(url, name)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Download failed."
      if (isLikelyDownloadNetworkFailure(msg)) {
        toast.error("Can't reach the server to download this file", {
          description: DOWNLOAD_NETWORK_TOAST_DESCRIPTION,
          duration: 12_000,
        })
      } else {
        toast.error(`Could not download weekly schedule: ${msg}`)
      }
    }
  }, [clientId, plan.version, plan.xlsx_path])

  return (
    <div className="flex flex-col gap-6">
      <ReconciliationBanner
        plan={plan}
        payRate={client.pay_rate}
        celebrate={celebrate}
      />

      <PlanTabs tabs={TABS} value={tab} onChange={setTab}>
        {(active) => {
          switch (active) {
            case "summary":
              return (
                <SummaryPanel
                  plan={plan}
                  client={client}
                  plans={plans}
                  selectedVersion={plan.version}
                  onSelectVersion={onSelectVersion}
                />
              )
            case "schedule":
              return (
                <PlanScheduleEditor
                  clientId={clientId}
                  plan={plan}
                  onPlanUpdated={(p) => onPlanUpdated?.(p)}
                  weekdayPreferredMinutes={weekdayPreferredMinutes}
                />
              )
            case "weekly":
              return (
                <WeeklyPatternPanel
                  plan={plan}
                  onDownloadWeekly={handleDownloadWeekly}
                />
              )
            case "daily":
              return <DailySchedulePanel plan={plan} client={client} />
            case "reconciliation":
              return <ReconciliationPanel plan={plan} />
            case "downloads":
              return <DownloadsPanel plan={plan} clientId={clientId} />
          }
        }}
      </PlanTabs>
    </div>
  )
}

export default PlanView
