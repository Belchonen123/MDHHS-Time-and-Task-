import { ShimmerBlock } from "@/components/skeletons/ShimmerBlock"

/**
 * Skeleton variant of the Summary tab's 4-up KPI strip. Keeps the same
 * card chrome (p-5, rounded-lg, border) so the layout doesn't shift when
 * real data arrives.
 */
export function SummaryKPISkeleton() {
  return (
    <div className="grid grid-cols-4 gap-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="rounded-lg border border-neutral-200 bg-white p-5 shadow-xs"
        >
          <ShimmerBlock className="h-3 w-20" />
          <div className="mt-2">
            <ShimmerBlock className="h-8 w-24" />
          </div>
          <div className="mt-1.5">
            <ShimmerBlock className="h-3 w-14" />
          </div>
        </div>
      ))}
    </div>
  )
}

export default SummaryKPISkeleton
