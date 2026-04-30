import { ShimmerBlock } from "@/components/skeletons/ShimmerBlock"

/**
 * Skeleton for the Daily Schedule tab — 8 rows with alternating widths so
 * the loading state doesn't read as obviously mechanical.
 */
const ROW_WIDTHS = [
  "w-[60%]",
  "w-[75%]",
  "w-[55%]",
  "w-[80%]",
  "w-[65%]",
  "w-[70%]",
  "w-[58%]",
  "w-[72%]",
] as const

export function DailyScheduleSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      <ShimmerBlock className="mb-1 h-4 w-48" /> {/* week header */}
      {ROW_WIDTHS.map((w, i) => (
        <div
          key={i}
          className="flex items-center gap-3 rounded-lg border border-neutral-200 bg-white px-4 py-3"
        >
          {/* date chip */}
          <ShimmerBlock className="h-10 w-10 rounded-md" />
          {/* day name + details */}
          <div className="flex flex-1 flex-col gap-1.5">
            <ShimmerBlock className={`h-3 ${w}`} />
            <ShimmerBlock className="h-2.5 w-1/3" />
          </div>
          {/* status/amount */}
          <ShimmerBlock className="h-4 w-16" />
        </div>
      ))}
    </div>
  )
}

export default DailyScheduleSkeleton
