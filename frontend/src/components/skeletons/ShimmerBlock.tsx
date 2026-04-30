import { cn } from "@/lib/utils"

interface ShimmerBlockProps {
  className?: string
  /** Hint to screen readers that content is loading. */
  label?: string
}

/**
 * Base primitive for every skeleton in the app — a shimmer-animated block.
 * Uses the `.shimmer` utility class defined in `index.css` so the keyframes
 * live in one place.
 */
export function ShimmerBlock({ className, label = "Loading" }: ShimmerBlockProps) {
  return (
    <div
      className={cn("shimmer", className)}
      role="status"
      aria-label={label}
    />
  )
}

export default ShimmerBlock
