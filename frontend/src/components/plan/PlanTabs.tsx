import { useId, type ReactNode } from "react"
import { AnimatePresence, motion } from "framer-motion"

import { cn } from "@/lib/utils"
import { easeOutSoft } from "@/lib/motion"

export interface PlanTabDef<T extends string = string> {
  id: T
  label: string
  badge?: ReactNode
}

interface PlanTabsProps<T extends string> {
  tabs: readonly PlanTabDef<T>[]
  value: T
  onChange: (id: T) => void
  children: (activeId: T) => ReactNode
}

/**
 * Custom tab navigation with a physically-sliding 2px underline
 * (framer-motion `layoutId`).
 *
 * Panels are mounted one at a time; `AnimatePresence mode="wait"` gives a
 * 6px slide-up fade between them — feels faster than a full slideUp.
 *
 * Tab panel content (e.g. weekly schedule download actions) is composed by
 * the parent via the `children` render prop — see `PlanView`.
 */
export function PlanTabs<T extends string>({
  tabs,
  value,
  onChange,
  children,
}: PlanTabsProps<T>) {
  const uid = useId()
  const underlineId = `plan-tab-underline-${uid}`

  return (
    <div className="w-full">
      <div
        role="tablist"
        className="relative flex items-end gap-1 border-b border-neutral-200"
      >
        {tabs.map((t) => {
          const active = t.id === value
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={`plan-tab-panel-${uid}-${t.id}`}
              id={`plan-tab-${uid}-${t.id}`}
              onClick={() => onChange(t.id)}
              className={cn(
                "relative px-4 py-3 text-sm transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-700 focus-visible:ring-offset-2 focus-visible:rounded-sm",
                active
                  ? "font-semibold text-primary-800"
                  : "font-medium text-neutral-600 hover:text-neutral-900",
              )}
            >
              <span className="inline-flex items-center gap-2">
                {t.label}
                {t.badge}
              </span>

              {active && (
                <motion.span
                  layoutId={underlineId}
                  transition={{
                    type: "spring",
                    stiffness: 380,
                    damping: 30,
                  }}
                  className="absolute inset-x-0 -bottom-px h-[2px] bg-primary-700"
                />
              )}
            </button>
          )
        })}
      </div>

      <div className="py-6">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={value}
            role="tabpanel"
            id={`plan-tab-panel-${uid}-${value}`}
            aria-labelledby={`plan-tab-${uid}-${value}`}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.2, ease: easeOutSoft }}
          >
            {children(value)}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}

export default PlanTabs
