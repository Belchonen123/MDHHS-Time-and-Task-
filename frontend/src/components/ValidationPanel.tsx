import { Fragment, useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { cn } from "@/lib/utils"
import { displayExpectedActual, stripLeadingCheckNumber } from "@/lib/scheduleUtils"
import type { ValidationReport } from "@/types"

type ValidationPanelProps = {
  report: ValidationReport
}

export function ValidationPanel({ report }: ValidationPanelProps) {
  const checks = report.checks
  const total = checks.length
  const passCount = checks.filter((c) => c.passed).length
  const allPassed = report.all_passed
  const [expanded, setExpanded] = useState<number | null>(null)

  return (
    <div className="space-y-4">
      {allPassed ? (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          All {total} cross-check{total === 1 ? "" : "s"} passed. Authorization is the billing cap;
          delivered minutes follow the weekly pattern across the real calendar; billable = min(delivered,
          authorized) per ASM 144.
        </div>
      ) : (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          Validation failed — {total - passCount} of {total} check{total === 1 ? "" : "s"} did not
          pass. Review before submitting to billing.
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Cross-checks</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">#</TableHead>
                <TableHead>Check</TableHead>
                <TableHead>Expected</TableHead>
                <TableHead>Actual</TableHead>
                <TableHead>Tolerance</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {checks.map((c, i) => (
                <Fragment key={c.name + i}>
                  <TableRow
                    className={cn(
                      !c.passed && "cursor-pointer hover:bg-muted/50",
                    )}
                    onClick={() => {
                      if (c.passed) return
                      setExpanded((e) => (e === i ? null : i))
                    }}
                  >
                    <TableCell className="font-mono text-muted-foreground">{i + 1}</TableCell>
                    <TableCell className="max-w-[220px] text-sm">
                      {stripLeadingCheckNumber(c.name)}
                    </TableCell>
                    <TableCell className="max-w-[180px] break-words text-xs text-muted-foreground">
                      {displayExpectedActual(c.expected, c.name)}
                    </TableCell>
                    <TableCell className="max-w-[180px] break-words text-xs text-muted-foreground">
                      {displayExpectedActual(c.actual, c.name)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {c.tolerance}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        {c.passed ? (
                          <Badge className="bg-emerald-100 font-normal text-emerald-800 hover:bg-emerald-200">
                            ✓ PASS
                          </Badge>
                        ) : (
                          <Badge
                            variant="outline"
                            className="border-red-200 bg-red-50 font-normal text-red-800 hover:bg-red-100"
                          >
                            ✗ FAIL
                          </Badge>
                        )}
                        {!c.passed && (
                          <span className="text-muted-foreground">
                            {expanded === i ? (
                              <ChevronDown className="h-4 w-4" />
                            ) : (
                              <ChevronRight className="h-4 w-4" />
                            )}
                          </span>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                  {!c.passed && expanded === i && c.detail && (
                    <TableRow>
                      <TableCell colSpan={6} className="bg-muted/30 text-sm text-foreground">
                        <p className="font-medium text-foreground/80">Detail</p>
                        <p className="mt-1 whitespace-pre-wrap text-muted-foreground">{c.detail}</p>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
