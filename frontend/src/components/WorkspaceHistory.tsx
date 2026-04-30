import { useCallback, useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { CheckCircle2, Loader2, XCircle } from "lucide-react"

import { listClients } from "@/api/client"
import { formatBackendLocal, parseBackendUtcInstant } from "@/lib/format"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"
import type { ClientSummary } from "@/types"

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
})

/**
 * Cross-client timeline of the **latest** saved plan per client, newest first.
 * (Full per-client version lists live on each client’s Summary tab.)
 */
export function WorkspaceHistory() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<ClientSummary[] | undefined>(undefined)

  const load = useCallback(() => {
    setRows(undefined)
    void listClients()
      .then(setRows)
      .catch(() => setRows([]))
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const sortedWithPlan = useMemo(() => {
    if (!rows) return []
    return [...rows]
      .filter((r) => r.latest_plan != null)
      .sort(
        (a, b) =>
          parseBackendUtcInstant(b.latest_plan!.created_at).getTime() -
          parseBackendUtcInstant(a.latest_plan!.created_at).getTime(),
      )
  }, [rows])

  const withoutPlan = useMemo(
    () => (rows ?? []).filter((r) => r.latest_plan == null),
    [rows],
  )

  const loading = rows === undefined

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-display text-2xl font-semibold tracking-tight text-neutral-900">
          Plan history
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-neutral-600">
          Latest saved plan for each client, ordered by when that plan was
          created. Open a client to see every version in the Summary tab.
        </p>
      </div>

      <div className="overflow-hidden rounded-xl border border-neutral-200 bg-white shadow-xs">
        <Table>
          <TableHeader>
            <TableRow className="bg-neutral-50/60 hover:bg-neutral-50/60">
              <TableHead>Client</TableHead>
              <TableHead>Plan</TableHead>
              <TableHead>Monthly $</TableHead>
              <TableHead>Validation</TableHead>
              <TableHead>Plan created</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading && (
              <TableRow>
                <TableCell colSpan={6} className="py-12 text-center text-neutral-500">
                  <Loader2 className="mx-auto h-6 w-6 animate-spin opacity-50" />
                </TableCell>
              </TableRow>
            )}
            {!loading &&
              sortedWithPlan.map((r) => {
                const p = r.latest_plan!
                return (
                  <TableRow
                    key={r.client_id}
                    className="border-neutral-100 hover:bg-neutral-50/80"
                  >
                    <TableCell className="max-w-[220px]">
                      <div className="truncate font-medium" title={r.client_name}>
                        {r.client_name || "—"}
                      </div>
                      <div className="font-mono text-[11px] text-neutral-500">
                        {r.client_id}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-sm">v{p.version}</TableCell>
                    <TableCell className="tabular">
                      {money.format(p.monthly_amount)}
                    </TableCell>
                    <TableCell>
                      {p.validation_passed ? (
                        <span className="inline-flex items-center gap-1 text-success">
                          <CheckCircle2 className="h-4 w-4" />
                          <span className="text-xs font-medium">Passed</span>
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-danger">
                          <XCircle className="h-4 w-4" />
                          <span className="text-xs font-medium">Issues</span>
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="tabular text-sm text-neutral-600">
                      {formatBackendLocal(p.created_at, "MMM d, yyyy · h:mm a")}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          navigate(`/clients/${encodeURIComponent(r.client_id)}`)
                        }
                      >
                        Open client
                      </Button>
                    </TableCell>
                  </TableRow>
                )
              })}
            {!loading && sortedWithPlan.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="py-12 text-center text-sm text-neutral-500"
                >
                  No plans yet. Upload a PDF from{" "}
                  <button
                    type="button"
                    className="font-medium text-primary-700 underline-offset-2 hover:underline"
                    onClick={() => navigate("/")}
                  >
                    Clients
                  </button>
                  .
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {!loading && withoutPlan.length > 0 && (
        <section className="rounded-lg border border-dashed border-neutral-200 bg-white p-4">
          <h2 className="text-sm font-semibold text-neutral-800">
            Clients without a plan
          </h2>
          <ul className="mt-2 space-y-1 text-sm text-neutral-600">
            {withoutPlan.map((r) => (
              <li key={r.client_id}>
                <button
                  type="button"
                  className={cn(
                    "text-left text-primary-700 underline-offset-2 hover:underline",
                  )}
                  onClick={() =>
                    navigate(`/clients/${encodeURIComponent(r.client_id)}`)
                  }
                >
                  {r.client_name || r.client_id}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

export default WorkspaceHistory
