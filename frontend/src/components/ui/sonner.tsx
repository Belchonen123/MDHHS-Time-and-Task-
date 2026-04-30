import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from "lucide-react"
import { Toaster as Sonner } from "sonner"
import type { ToasterProps } from "sonner"

/**
 * Toast host configured to match the design system.
 *
 * - Position: top-right, 3 toasts stacked, rest queued.
 * - Duration: 4000ms default, 8000ms on errors (applied per-call via `toast.error`).
 * - Icons come from lucide (Success / Warning / Error / Info / Loading).
 * - Classnames hook into tokens (success-bg / warning-bg / danger-bg / info-bg).
 * - `richColors` is OFF so our className overrides are authoritative.
 * - `closeButton` is ON for every toast.
 * - Swipe/drag to dismiss is a sonner default.
 */
export function Toaster(props: ToasterProps) {
  return (
    <Sonner
      className="toaster group"
      position="top-right"
      visibleToasts={3}
      closeButton
      duration={4000}
      icons={{
        success: <CheckCircle2 className="h-4 w-4" />,
        error: <XCircle className="h-4 w-4" />,
        warning: <AlertTriangle className="h-4 w-4" />,
        info: <Info className="h-4 w-4" />,
        loading: <Loader2 className="h-4 w-4 animate-spin" />,
      }}
      toastOptions={{
        classNames: {
          toast: [
            "!rounded-lg !border !shadow-md !font-sans",
            "!bg-white !text-neutral-900 !border-neutral-200",
            "group-[.toaster]:py-3 group-[.toaster]:px-3.5",
          ].join(" "),
          title: "!text-sm !font-medium !tracking-tight",
          description: "!text-xs !text-neutral-600",
          closeButton:
            "!bg-white !text-neutral-500 hover:!text-neutral-900 !border-neutral-200",
          icon: "!shrink-0",
          success: [
            "!bg-[color:var(--success-bg)] !border-[color:color-mix(in_srgb,var(--success)_25%,transparent)]",
            "!text-[color:color-mix(in_srgb,var(--success)_90%,var(--neutral-900))]",
            "[&_[data-icon]]:!text-success",
            "!border-l-4 !border-l-success",
          ].join(" "),
          error: [
            "!bg-[color:var(--danger-bg)] !border-[color:color-mix(in_srgb,var(--danger)_25%,transparent)]",
            "!text-[color:color-mix(in_srgb,var(--danger)_90%,var(--neutral-900))]",
            "[&_[data-icon]]:!text-danger",
            "!border-l-4 !border-l-danger",
          ].join(" "),
          warning: [
            "!bg-[color:var(--warning-bg)] !border-[color:color-mix(in_srgb,var(--warning)_25%,transparent)]",
            "!text-[color:color-mix(in_srgb,var(--warning)_90%,var(--neutral-900))]",
            "[&_[data-icon]]:!text-warning",
            "!border-l-4 !border-l-warning",
          ].join(" "),
          info: [
            "!bg-[color:var(--info-bg)] !border-[color:color-mix(in_srgb,var(--info)_25%,transparent)]",
            "!text-[color:color-mix(in_srgb,var(--info)_90%,var(--neutral-900))]",
            "[&_[data-icon]]:!text-info",
            "!border-l-4 !border-l-info",
          ].join(" "),
        },
      }}
      {...props}
    />
  )
}
