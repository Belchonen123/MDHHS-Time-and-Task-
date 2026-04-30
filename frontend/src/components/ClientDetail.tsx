import { useCallback, useEffect, useMemo, useState } from "react"
import { useLocation, useNavigate, useParams } from "react-router-dom"
import { Loader2, RefreshCw, Trash2, User } from "lucide-react"
import { toast } from "sonner"

import { deleteClient, getClient } from "@/api/client"
import { ClientAvailabilityPanel } from "@/components/ClientAvailabilityPanel"
import { NoPlanVersionsFoundForClient } from "@/components/EmptyState"
import { PageHeader } from "@/components/layout/PageHeader"
import { useBreadcrumbLabel } from "@/components/layout/AppShell"
import { PlanView, type PlanTabId } from "@/components/PlanView"
import { ReRunDialog } from "@/components/ReRunDialog"
import { ShimmerBlock } from "@/components/skeletons/ShimmerBlock"
import { SummaryKPISkeleton } from "@/components/skeletons/SummaryKPISkeleton"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { formatMoney } from "@/lib/format"
import type { WorkerAvailabilityMap } from "@/lib/workerAvailability"
import type { Client, ClientDetail as ClientDetailT, Plan } from "@/types"

function isPlanTab(v: unknown): v is PlanTabId {
  return (
    v === "summary" ||
    v === "schedule" ||
    v === "weekly" ||
    v === "daily" ||
    v === "validation" ||
    v === "downloads"
  )
}

export function ClientDetail() {
  const { id: clientId = "" } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const decodedId = useMemo(
    () => (clientId ? decodeURIComponent(clientId) : ""),
    [clientId],
  )

  const [data, setData] = useState<ClientDetailT | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  /** Local availability while editing — overlays `client` for PlanView until Save or server refresh. */
  const [availabilityDraft, setAvailabilityDraft] = useState<
    WorkerAvailabilityMap | undefined
  >(undefined)
  const [reRunOpen, setReRunOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)

  // Initial tab hint from navigation state — upload flow sets
  // `{ focusTab: "validation" }` when validation had warnings.
  const initialTabHint = useMemo<PlanTabId | undefined>(() => {
    const maybe = (location.state as { focusTab?: unknown } | null)?.focusTab
    return isPlanTab(maybe) ? maybe : undefined
  }, [location.state])

  // `fromUpload` is set on the navigation state by the upload flow — we use
  // it to fire the one-time confetti burst on the reconciliation banner and
  // then clear it so a subsequent refresh doesn't re-celebrate.
  const [celebrate, setCelebrate] = useState<boolean>(() => {
    const fromUpload = (location.state as { fromUpload?: unknown } | null)?.fromUpload
    return fromUpload === true
  })
  useEffect(() => {
    if (!celebrate) return
    // Clear the state entry so refreshes don't re-fire.
    window.history.replaceState({}, "")
    // Then forget the flag locally after a frame — the banner reads it on mount.
    const t = window.setTimeout(() => setCelebrate(false), 100)
    return () => window.clearTimeout(t)
  }, [celebrate])

  const load = useCallback(async () => {
    if (!decodedId) return
    setData(null)
    setLoadError(false)
    try {
      const d = await getClient(decodedId)
      setData(d)
      const maxV = d.plans.length
        ? Math.max(...d.plans.map((p) => p.version))
        : null
      setSelectedVersion((v) =>
        v != null && d.plans.some((p) => p.version === v) ? v : maxV,
      )
    } catch {
      setLoadError(true)
    }
  }, [decodedId])

  useEffect(() => {
    void load()
  }, [load])

  // Command palette "Re-run current plan" bridge — see AppShortcuts.
  useEffect(() => {
    const handler = () => setReRunOpen(true)
    window.addEventListener("app:rerun-current", handler)
    return () => window.removeEventListener("app:rerun-current", handler)
  }, [])

  const selectedPlan: Plan | null = useMemo(() => {
    if (!data || selectedVersion == null) return null
    return data.plans.find((p) => p.version === selectedVersion) ?? null
  }, [data, selectedVersion])

  const latestPlanVersion = useMemo(() => {
    if (!data?.plans.length) return null
    return Math.max(...data.plans.map((p) => p.version))
  }, [data?.plans])

  const clientForPlanView: Client | null = useMemo(() => {
    if (!data) return null
    const base = data.client
    if (!availabilityDraft) return base
    return {
      ...base,
      availability: availabilityDraft as Client["availability"],
    }
  }, [data, availabilityDraft])

  // Register the client's friendly name in the breadcrumb — swaps the raw id
  // for "Ottilie Smith" in the top bar.
  const clientPath = decodedId
    ? `/clients/${encodeURIComponent(decodedId)}`
    : null
  useBreadcrumbLabel(clientPath, data?.client?.client_name || null)

  const handleRerunSuccess = (plan: Plan) => {
    void getClient(decodedId).then((d) => {
      setData(d)
      setSelectedVersion(plan.version)
    })
  }

  const confirmDelete = async () => {
    if (!decodedId) return
    setDeleting(true)
    try {
      await deleteClient(decodedId)
      toast.success("Client removed")
      setDeleteOpen(false)
      navigate("/")
    } catch {
      /* api layer already toasts */
    } finally {
      setDeleting(false)
    }
  }

  if (!decodedId) {
    return (
      <p className="text-sm text-neutral-500">Invalid client link.</p>
    )
  }

  if (loadError) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-6">
        <p className="text-sm text-red-900">
          Could not load this client. They may have been removed.
        </p>
        <Button
          type="button"
          className="mt-4"
          variant="outline"
          onClick={() => navigate("/")}
        >
          Back to list
        </Button>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex flex-col gap-6">
        {/* Page header skeleton */}
        <div className="flex flex-col gap-2">
          <ShimmerBlock className="h-3 w-16" />
          <ShimmerBlock className="h-9 w-80" />
          <ShimmerBlock className="mt-1 h-3 w-96" />
        </div>
        {/* Reconciliation banner skeleton */}
        <ShimmerBlock className="h-[140px] w-full rounded-xl" />
        {/* KPI strip skeleton */}
        <SummaryKPISkeleton />
      </div>
    )
  }

  const c = data.client
  const plansCount = data.plans.length
  const planClient = clientForPlanView ?? c
  const metaParts = [
    c.county ? `${c.county} County` : null,
    c.asw_name ? `ASW: ${c.asw_name}` : null,
    `${formatMoney(c.pay_rate)}/hr`,
    `${plansCount} plan${plansCount === 1 ? "" : "s"}`,
  ].filter(Boolean) as string[]

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow="Client"
        title={c.client_name || c.client_id || "Unnamed client"}
        subtitle={metaParts.join(" · ")}
        icon={User}
        actions={
          <>
            <Button
              type="button"
              variant="outline"
              onClick={() => setReRunOpen(true)}
              disabled={!selectedPlan}
              className="gap-2"
            >
              <RefreshCw className="h-4 w-4" />
              Re-run schedule
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => setDeleteOpen(true)}
              className="text-neutral-500 hover:bg-red-50 hover:text-danger"
              aria-label="Delete client"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </>
        }
      />

      <ClientAvailabilityPanel
        client={c}
        planVersion={selectedPlan?.version ?? null}
        latestPlanVersion={latestPlanVersion}
        onAvailabilityDraftChange={(draft) =>
          setAvailabilityDraft(draft ?? undefined)
        }
        onPlanPreview={(plan) =>
          setData((prev) =>
            prev
              ? {
                  ...prev,
                  plans: prev.plans.map((pp) =>
                    pp.version === plan.version ? plan : pp,
                  ),
                }
              : prev,
          )
        }
        onPlanRegenerated={(plan, savedAvailability) => {
          setAvailabilityDraft(undefined)
          setData((prev) => {
            if (!prev) return prev
            return {
              ...prev,
              client: {
                ...prev.client,
                availability: savedAvailability as Client["availability"],
              },
              plans: prev.plans.map((pp) =>
                pp.version === plan.version ? plan : pp,
              ),
            }
          })
        }}
      />

      {selectedPlan ? (
        <PlanView
          plan={selectedPlan}
          client={planClient}
          clientId={c.client_id}
          plans={data.plans}
          onSelectVersion={(v) => setSelectedVersion(v)}
          onPlanUpdated={(p) => {
            // ScheduleEditor PATCH response — splice the updated plan into
            // the list so banner / tabs re-render without a full refetch.
            setData((prev) =>
              prev
                ? {
                    ...prev,
                    plans: prev.plans.map((pp) =>
                      pp.version === p.version ? p : pp,
                    ),
                  }
                : prev,
            )
          }}
          initialTab={initialTabHint}
          celebrate={celebrate}
        />
      ) : data.plans.length === 0 ? (
        <div className="mt-6 rounded-xl border border-dashed border-neutral-300 bg-white">
          <NoPlanVersionsFoundForClient onRerun={() => setReRunOpen(true)} />
        </div>
      ) : (
        <p className="text-sm text-neutral-500">
          Select a plan version in the Plan History timeline.
        </p>
      )}

      <ReRunDialog
        open={reRunOpen}
        onOpenChange={setReRunOpen}
        clientId={c.client_id}
        planVersion={selectedPlan?.version ?? 0}
        initialMonth={
          selectedPlan && selectedPlan.year && selectedPlan.month
            ? { year: selectedPlan.year, month: selectedPlan.month }
            : undefined
        }
        sourcePlan={selectedPlan ?? null}
        onSuccess={handleRerunSuccess}
        workerAvailability={planClient.availability}
      />

      <AlertDialog
        open={deleteOpen}
        onOpenChange={(o) => !deleting && setDeleteOpen(o)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this client?</AlertDialogTitle>
            <AlertDialogDescription>
              Remove {c.client_name || c.client_id} and all related plans and
              files. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void confirmDelete()}
              disabled={deleting}
            >
              {deleting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Deleting…
                </>
              ) : (
                "Delete"
              )}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
