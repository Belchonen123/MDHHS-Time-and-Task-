import { CheckCircle2, Loader2, Trash2, XCircle } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { deleteClient, downloadFile, listClients } from "@/api/client"
import { NoClientsYet } from "@/components/EmptyState"
import { ClientTableSkeletonRows } from "@/components/skeletons/ClientRowSkeleton"
import { UploadHero } from "@/components/upload/UploadHero"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { formatBackendLocal } from "@/lib/format"
import type { ClientSummary } from "@/types"
import { cn } from "@/lib/utils"

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
})

/** When the display name is missing, link the em dash to the stored authorization PDF (review source). */
function sourceReviewHref(row: ClientSummary): string | null {
  if (row.client_name?.trim()) return null
  const p = row.latest_plan
  if (!p?.has_source_pdf) return null
  return downloadFile(row.client_id, p.version, "source")
}

/** Open client detail — land on Reconciliation when the latest plan needs review (“Review” badge). */
function clientRowNavigateState(row: ClientSummary): {
  focusTab: "reconciliation"
} | undefined {
  if (
    row.latest_plan != null &&
    !row.latest_plan.validation_passed
  ) {
    return { focusTab: "reconciliation" }
  }
  return undefined
}

export function ClientList() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<ClientSummary[] | undefined>(undefined)
  const [loadTick, setLoadTick] = useState(0)
  const [pendingDelete, setPendingDelete] = useState<ClientSummary | null>(null)
  const [deleting, setDeleting] = useState(false)
  const tableRef = useRef<HTMLTableElement>(null)

  const load = useCallback(() => {
    setLoadTick((n) => n + 1)
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      if (loadTick === 0) setRows(undefined)
      try {
        const data = await listClients()
        if (!cancelled) setRows(data)
      } catch {
        if (!cancelled) setRows([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [loadTick])

  const openClient = useCallback(
    (row: ClientSummary) => {
      const state = clientRowNavigateState(row)
      navigate(`/clients/${encodeURIComponent(row.client_id)}`, state ? { state } : undefined)
    },
    [navigate],
  )

  const confirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await deleteClient(pendingDelete.client_id)
      toast.success("Client removed")
      setPendingDelete(null)
      load()
    } catch {
      // deleteClient toasts
    } finally {
      setDeleting(false)
    }
  }

  const loading = rows === undefined
  const empty = !loading && rows.length === 0

  const handleRowKeyDown = useMemo(
    () => (e: KeyboardEvent<HTMLTableRowElement>, row: ClientSummary, index: number) => {
      const rowsCount = rows?.length ?? 0
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault()
        openClient(row)
      } else if (e.key === "ArrowDown") {
        e.preventDefault()
        const next = tableRef.current?.querySelector<HTMLTableRowElement>(
          `tr[data-row-index="${Math.min(rowsCount - 1, index + 1)}"]`,
        )
        next?.focus()
      } else if (e.key === "ArrowUp") {
        e.preventDefault()
        const prev = tableRef.current?.querySelector<HTMLTableRowElement>(
          `tr[data-row-index="${Math.max(0, index - 1)}"]`,
        )
        prev?.focus()
      }
    },
    [rows, openClient],
  )

  return (
    <div className="flex flex-col gap-6">
      <UploadHero
        onUploaded={load}
        variant={rows && rows.length > 0 ? "compact" : "hero"}
      />

      <div className="flex flex-col gap-3">
        {!loading && rows && rows.length > 0 ? (
          <h2 className="text-sm font-semibold text-neutral-900">
            Previously run
          </h2>
        ) : null}
        {empty ? (
          <div className="rounded-xl border border-dashed border-neutral-300 bg-white">
            <NoClientsYet />
          </div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-neutral-200 bg-white shadow-xs">
            <Table ref={tableRef} className="[&_tr]:border-neutral-100">
              <TableHeader>
                <TableRow className="bg-neutral-50/60 hover:bg-neutral-50/60">
                  <TableHead>Client name</TableHead>
                  <TableHead>Client ID</TableHead>
                  <TableHead>Latest plan</TableHead>
                  <TableHead>Monthly $</TableHead>
                  <TableHead>Validation</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading && <ClientTableSkeletonRows count={4} />}
                {!loading &&
                  !empty &&
                  rows.map((r, index) => (
                    <TableRow
                      key={r.client_id}
                      data-row-index={index}
                      tabIndex={0}
                      className={cn(
                        "cursor-pointer transition-colors",
                        "hover:bg-neutral-50",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-700 focus-visible:ring-offset-[-2px]",
                      )}
                      onClick={() => openClient(r)}
                      onKeyDown={(e) => handleRowKeyDown(e, r, index)}
                      aria-label={`${r.client_name || r.client_id} — press Enter to open`}
                    >
                      <TableCell
                        className="max-w-[200px] truncate font-medium"
                        title={
                          r.client_name?.trim()
                            ? r.client_name
                            : sourceReviewHref(r)
                              ? "Open authorization PDF"
                              : ""
                        }
                      >
                        {(() => {
                          const href = sourceReviewHref(r)
                          return href ? (
                            <a
                              href={href}
                              target="_blank"
                              rel="noopener noreferrer"
                              className={cn(
                                "rounded-sm text-primary underline decoration-primary/50 underline-offset-2",
                                "outline-none focus-visible:ring-2 focus-visible:ring-primary-700",
                              )}
                              aria-label={`Open authorization PDF (${r.client_id})`}
                              onClick={(e) => e.stopPropagation()}
                            >
                              —
                            </a>
                          ) : (
                            <>{r.client_name?.trim() || "—"}</>
                          )
                        })()}
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {r.client_id}
                      </TableCell>
                      <TableCell>
                        {r.latest_plan ? (
                          <Badge variant="secondary" className="font-mono">
                            v{r.latest_plan.version}
                          </Badge>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell>
                        {r.latest_plan
                          ? money.format(r.latest_plan.monthly_amount)
                          : "—"}
                      </TableCell>
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        {r.latest_plan ? (
                          <span
                            className={cn(
                              "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
                              r.latest_plan.validation_passed
                                ? "border-success/20 bg-success-bg text-success"
                                : "border-danger/20 bg-danger-bg text-danger",
                            )}
                            aria-label={
                              r.latest_plan.validation_passed
                                ? "Validation passed"
                                : "Validation failed"
                            }
                          >
                            {r.latest_plan.validation_passed ? (
                              <CheckCircle2 className="h-3 w-3" />
                            ) : (
                              <XCircle className="h-3 w-3" />
                            )}
                            {r.latest_plan.validation_passed ? "Passed" : "Review"}
                          </span>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                        {formatBackendLocal(r.updated_at)}
                      </TableCell>
                      <TableCell
                        className="text-right"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <div className="inline-flex items-center justify-end gap-1">
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={(e) => {
                              e.stopPropagation()
                              openClient(r)
                            }}
                          >
                            View
                          </Button>
                          <Button
                            type="button"
                            size="icon"
                            variant="ghost"
                            className="text-muted-foreground hover:text-destructive"
                            onClick={() => setPendingDelete(r)}
                            aria-label={`Delete client ${r.client_id}`}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      <AlertDialog
        open={!!pendingDelete}
        onOpenChange={(open) => {
          if (!open && !deleting) setPendingDelete(null)
        }}
      >
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this client?</AlertDialogTitle>
            <AlertDialogDescription>
              This will remove {pendingDelete?.client_name || "this client"} and
              all plans and files stored for them. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void confirmDelete()}
              disabled={deleting}
              className="min-w-[100px]"
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
