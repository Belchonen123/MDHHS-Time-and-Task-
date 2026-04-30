import type { ReactNode } from "react"
import type { LucideIcon } from "lucide-react"

import { cn } from "@/lib/utils"

export interface PageHeaderProps {
  title: string
  subtitle?: string
  icon?: LucideIcon
  actions?: ReactNode
  eyebrow?: string
  className?: string
}

/**
 * Standard page header — use at the top of every route-level page.
 *
 *   <PageHeader
 *     eyebrow="CLIENT"
 *     title={client.name}
 *     subtitle="12 plans • last updated 2 days ago"
 *     icon={User}
 *     actions={<Button>Edit</Button>}
 *   />
 */
export function PageHeader({
  title,
  subtitle,
  icon: Icon,
  actions,
  eyebrow,
  className,
}: PageHeaderProps) {
  return (
    <header className={cn("mb-6", className)}>
      <div className="flex items-start justify-between gap-6">
        <div className="flex min-w-0 items-start gap-4">
          {Icon && (
            <div className="mt-1 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-700">
              <Icon className="h-5 w-5" />
            </div>
          )}
          <div className="min-w-0">
            {eyebrow && (
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-primary-600">
                {eyebrow}
              </div>
            )}
            <h1 className="truncate font-display text-3xl font-semibold tracking-tight text-neutral-900">
              {title}
            </h1>
            {subtitle && (
              <p className="mt-1 text-base text-neutral-600">{subtitle}</p>
            )}
          </div>
        </div>
        {actions && (
          <div className="flex shrink-0 items-center gap-2 pt-1">{actions}</div>
        )}
      </div>
      <div className="mt-6 border-t border-neutral-200" />
    </header>
  )
}

export default PageHeader
