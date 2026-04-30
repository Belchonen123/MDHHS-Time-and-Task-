import { useState } from "react"
import { Calendar, ChevronDown, ChevronLeft, ChevronRight } from "lucide-react"

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { cn } from "@/lib/utils"

/**
 * Month-year pair. `month` is 1-12 (not 0-11) to match how humans read dates.
 */
export type MonthYear = { month: number; year: number }

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
] as const

const MONTH_ABBR = MONTH_NAMES.map((m) => m.slice(0, 3))

export function currentMonthYear(): MonthYear {
  const d = new Date()
  return { month: d.getMonth() + 1, year: d.getFullYear() }
}

export function formatMonthYear(v: MonthYear): string {
  return `${MONTH_NAMES[v.month - 1]} ${v.year}`
}

interface MonthYearPickerProps {
  value: MonthYear
  onChange: (v: MonthYear) => void
  disabled?: boolean
  className?: string
}

export function MonthYearPicker({
  value,
  onChange,
  disabled,
  className,
}: MonthYearPickerProps) {
  const [open, setOpen] = useState(false)
  // Year the grid is showing — independent of selected value so users can page.
  const [viewYear, setViewYear] = useState(value.year)
  const now = currentMonthYear()

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (next) setViewYear(value.year)
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={`Service month: ${formatMonthYear(value)}. Change`}
          className={cn(
            "inline-flex items-center gap-2 rounded-full border border-neutral-300 bg-white",
            "px-4 py-2 text-sm font-medium text-neutral-800",
            "transition-[background-color,border-color,box-shadow] duration-[160ms] ease-out",
            "hover:border-neutral-400 hover:shadow-sm",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-700 focus-visible:ring-offset-2",
            "disabled:cursor-not-allowed disabled:opacity-60",
            "data-[state=open]:border-primary-500 data-[state=open]:shadow-sm",
            className,
          )}
        >
          <Calendar className="h-4 w-4 text-neutral-500" />
          <span className="tabular">{formatMonthYear(value)}</span>
          <ChevronDown className="h-4 w-4 text-neutral-500" />
        </button>
      </PopoverTrigger>

      <PopoverContent align="center" className="w-64">
        {/* Year header */}
        <div className="mb-3 flex items-center justify-between">
          <button
            type="button"
            onClick={() => setViewYear((y) => y - 1)}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-neutral-500 transition-colors hover:bg-neutral-100 hover:text-neutral-900"
            aria-label="Previous year"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <div className="tabular text-sm font-semibold text-neutral-900">{viewYear}</div>
          <button
            type="button"
            onClick={() => setViewYear((y) => y + 1)}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-neutral-500 transition-colors hover:bg-neutral-100 hover:text-neutral-900"
            aria-label="Next year"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>

        {/* Month grid */}
        <div className="grid grid-cols-3 gap-1.5">
          {MONTH_ABBR.map((abbr, i) => {
            const m = i + 1
            const isSelected = value.year === viewYear && value.month === m
            const isCurrent = viewYear === now.year && m === now.month
            return (
              <button
                key={abbr}
                type="button"
                onClick={() => {
                  onChange({ month: m, year: viewYear })
                  setOpen(false)
                }}
                className={cn(
                  "rounded-md px-2 py-2 text-sm font-medium transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-700 focus-visible:ring-offset-1",
                  isSelected
                    ? "bg-primary-700 text-white hover:bg-primary-800"
                    : isCurrent
                      ? "text-primary-700 ring-1 ring-inset ring-primary-200 hover:bg-primary-50"
                      : "text-neutral-700 hover:bg-neutral-100",
                )}
              >
                {abbr}
              </button>
            )
          })}
        </div>
      </PopoverContent>
    </Popover>
  )
}
