import type { ComponentType, ReactNode } from "react"
import { FileQuestion, PartyPopper, Users, type LucideProps } from "lucide-react"

import { cn } from "@/lib/utils"

interface EmptyStateProps {
  icon: ComponentType<LucideProps>
  headline: string
  description?: ReactNode
  action?: ReactNode
  secondary?: ReactNode
  /** Tints the icon — neutral by default, success for "everything OK" variants. */
  tone?: "neutral" | "success" | "warning" | "danger"
  className?: string
}

/**
 * Generic empty-state layout. Variants below are thin wrappers that pass
 * a specific icon + copy. Use those wherever possible so empty copy stays
 * consistent across the app (the variant is part of the design system).
 */
export function EmptyState({
  icon: Icon,
  headline,
  description,
  action,
  secondary,
  tone = "neutral",
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-4 px-6 py-12 text-center",
        className,
      )}
    >
      <Icon
        className={cn(
          "h-16 w-16",
          tone === "neutral" && "text-neutral-300",
          tone === "success" && "text-success",
          tone === "warning" && "text-warning",
          tone === "danger" && "text-danger",
        )}
        strokeWidth={1.5}
        aria-hidden
      />
      <div className="flex flex-col items-center gap-1.5">
        <h3 className="font-display text-lg font-semibold tracking-tight text-neutral-900">
          {headline}
        </h3>
        {description && (
          <p className="max-w-md text-sm text-neutral-600">{description}</p>
        )}
      </div>
      {(action || secondary) && (
        <div className="mt-2 flex items-center gap-3">
          {action}
          {secondary}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Variants
// ---------------------------------------------------------------------------

/**
 * Home page — no clients yet. The upload hero is already above this slot, so
 * no action button is needed (the hero handles the CTA).
 */
export function NoClientsYet() {
  return (
    <EmptyState
      icon={Users}
      headline="No plans yet"
      description={
        <>
          Drop your first MDHHS-6064 PDF above to generate a calibrated plan
          of care. The whole workflow takes about 8 seconds.
        </>
      }
    />
  )
}

interface NoPlanVersionsProps {
  onRerun: () => void
}

/**
 * Client page — client exists but has zero plan versions (edge case, e.g.
 * all were deleted manually).
 */
export function NoPlanVersionsFoundForClient({ onRerun }: NoPlanVersionsProps) {
  return (
    <EmptyState
      icon={FileQuestion}
      headline="Plan history is empty"
      description="We still have this client on file, but there aren't any plan versions. You can rebuild from the original source PDF."
      action={
        <button
          type="button"
          onClick={onRerun}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg bg-primary-700 px-4 py-2",
            "text-sm font-medium text-white shadow-sm",
            "transition-[background-color,box-shadow] duration-150",
            "hover:bg-primary-800 hover:shadow-md",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-700 focus-visible:ring-offset-2",
          )}
        >
          Re-run from source PDF
        </button>
      }
    />
  )
}

/**
 * Editor — used inside the Issues panel when the live validator has nothing
 * to complain about. Green party popper because the moment should feel good.
 */
export function ValidationAllPassed({
  totalChecks = 11,
}: {
  totalChecks?: number
}) {
  return (
    <EmptyState
      tone="success"
      icon={PartyPopper}
      headline="All checks passed"
      description={
        <>
          All {totalChecks} cross-checks passing. Ready to save.
        </>
      }
    />
  )
}

export default EmptyState
