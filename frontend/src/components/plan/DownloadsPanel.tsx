import { useState, type ReactNode } from "react"
import { AnimatePresence, motion } from "framer-motion"
import {
  CalendarDays,
  Check,
  Download,
  FileSpreadsheet,
  FileText,
  Files,
  Loader2,
} from "lucide-react"
import { toast } from "sonner"

import { downloadFile, type DownloadFileType } from "@/api/client"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  DOWNLOAD_NETWORK_TOAST_DESCRIPTION,
  downloadBlobFromUrl,
  isLikelyDownloadNetworkFailure,
} from "@/lib/downloadBlob"
import { easeOutSoft } from "@/lib/motion"
import { cn } from "@/lib/utils"
import type { Plan } from "@/types"

interface DownloadsPanelProps {
  plan: Plan
  clientId: string
}

type DownloadConfig = {
  key: DownloadFileType
  title: string
  description: string
  icon: ReactNode
  iconClass: string
  buttonLabel: string
}

/** True when API would return 404 "File not available" for download (missing path). */
function isArtifactUnavailable(
  key: DownloadFileType,
  plan: Plan,
): boolean {
  const trimmed = (s: string | undefined) => Boolean(s && String(s).trim() !== "")
  switch (key) {
    case "xlsx":
    case "weekly":
      return !trimmed(plan.xlsx_path)
    case "pdf":
      return !trimmed(plan.pdf_path)
    case "source":
      return !trimmed(plan.source_pdf_path)
    default:
      return true
  }
}

const DOWNLOADS: readonly DownloadConfig[] = [
  {
    key: "xlsx",
    title: "Plan of Care — XLSX",
    description: "7-tab workbook with summary, weekly grid, and full reconciliation.",
    icon: <FileSpreadsheet className="h-7 w-7" />,
    iconClass: "bg-[color:var(--success-bg)] text-success",
    buttonLabel: "Download XLSX",
  },
  {
    key: "pdf",
    title: "Plan of Care — PDF",
    description: "Print-ready plan pack for submission.",
    icon: <FileText className="h-7 w-7" />,
    iconClass: "bg-[color:var(--danger-bg)] text-danger",
    buttonLabel: "Download PDF",
  },
  {
    key: "source",
    title: "Original MDHHS-6064",
    description: "Uploaded authorization PDF copy.",
    icon: <Files className="h-7 w-7" />,
    iconClass: "bg-neutral-100 text-neutral-600",
    buttonLabel: "Download source",
  },
  {
    key: "weekly",
    title: "Weekly Schedule",
    description: "Opens the Weekly Schedule tab in the workbook (same file as XLSX).",
    icon: <CalendarDays className="h-7 w-7" />,
    iconClass: "bg-sky-50 text-sky-700",
    buttonLabel: "Download Weekly",
  },
]

function DownloadCard({
  config,
  plan,
  clientId,
}: {
  config: DownloadConfig
  plan: Plan
  clientId: string
}) {
  const [confirmed, setConfirmed] = useState(false)
  const [pending, setPending] = useState(false)

  const unavailable = isArtifactUnavailable(config.key, plan)

  const offlineTooltip =
    config.key === "source"
      ? "No authorization PDF stored for this plan — upload again."
      : "No generated file recorded for this plan. Re-run the plan from the client page, or re-upload."

  const onDownload = async () => {
    if (pending || unavailable) return
    const url = downloadFile(clientId, plan.version, config.key)
    const ext =
      config.key === "pdf" || config.key === "source" ? "pdf" : "xlsx"
    const name = `${clientId}-v${plan.version}-${config.key}.${ext}`
    setPending(true)
    try {
      await downloadBlobFromUrl(url, name)
      setConfirmed(true)
      window.setTimeout(() => setConfirmed(false), 2000)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Download failed."
      if (isLikelyDownloadNetworkFailure(msg)) {
        toast.error("Can't reach the server to download this file", {
          description: DOWNLOAD_NETWORK_TOAST_DESCRIPTION,
          duration: 12_000,
        })
      } else {
        toast.error(`Could not download ${config.title}: ${msg}`)
      }
    } finally {
      setPending(false)
    }
  }

  return (
    <motion.article
      initial={false}
      whileHover={{ y: -2 }}
      transition={{ duration: 0.18, ease: easeOutSoft }}
      className={cn(
        "group relative flex h-[200px] flex-col rounded-xl border border-neutral-200 bg-white p-5 shadow-xs",
        "transition-shadow duration-200 hover:shadow-lg",
      )}
    >
      <div
        className={cn(
          "mb-4 flex h-16 w-16 items-center justify-center rounded-xl",
          config.iconClass,
        )}
      >
        {config.icon}
      </div>
      <h3 className="font-display text-base font-semibold tracking-tight text-neutral-900">
        {config.title}
      </h3>
      <p className="mt-1 text-sm text-neutral-600">{config.description}</p>

      <div className="mt-auto flex items-center gap-2">
        {unavailable ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex">
                <Button
                  type="button"
                  size="default"
                  disabled
                  className="gap-2"
                >
                  <Download className="h-4 w-4" />
                  {config.buttonLabel}
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">{offlineTooltip}</TooltipContent>
          </Tooltip>
        ) : (
          <Button
            type="button"
            size="default"
            onClick={onDownload}
            disabled={pending}
            className="gap-2"
          >
            {pending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Download className="h-4 w-4" />
            )}
            {pending ? "Preparing…" : config.buttonLabel}
          </Button>
        )}

        <AnimatePresence>
          {confirmed && (
            <motion.span
              key="confirm"
              initial={{ opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -4 }}
              transition={{ duration: 0.18, ease: easeOutSoft }}
              className="inline-flex items-center gap-1 text-xs font-medium text-success"
            >
              <Check className="h-3.5 w-3.5" strokeWidth={3} />
              Downloaded
            </motion.span>
          )}
        </AnimatePresence>
      </div>
    </motion.article>
  )
}

export function DownloadsPanel({ plan, clientId }: DownloadsPanelProps) {
  return (
    <TooltipProvider delayDuration={200}>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        {DOWNLOADS.map((c) => (
          <DownloadCard key={c.key} config={c} plan={plan} clientId={clientId} />
        ))}
      </div>
    </TooltipProvider>
  )
}

export default DownloadsPanel
