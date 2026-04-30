import { ShimmerBlock } from "@/components/skeletons/ShimmerBlock"
import { TableCell, TableRow } from "@/components/ui/table"

/**
 * Skeleton row for the clients table. Column widths are tuned to roughly
 * match real data so the transition from loading → loaded doesn't pop.
 * Caller renders N of these (default 4) while the list is fetching.
 */
export function ClientRowSkeleton() {
  return (
    <TableRow className="border-b border-neutral-100">
      <TableCell className="py-4">
        <ShimmerBlock className="h-4 w-32" />
      </TableCell>
      <TableCell>
        <ShimmerBlock className="h-3 w-24" />
      </TableCell>
      <TableCell>
        <ShimmerBlock className="h-5 w-10 rounded-full" />
      </TableCell>
      <TableCell>
        <ShimmerBlock className="h-4 w-20" />
      </TableCell>
      <TableCell>
        <ShimmerBlock className="h-5 w-8 rounded-full" />
      </TableCell>
      <TableCell>
        <ShimmerBlock className="h-3 w-28" />
      </TableCell>
      <TableCell className="text-right">
        <div className="inline-flex items-center justify-end gap-1.5">
          <ShimmerBlock className="h-8 w-14 rounded-md" />
          <ShimmerBlock className="h-8 w-8 rounded-md" />
        </div>
      </TableCell>
    </TableRow>
  )
}

export function ClientTableSkeletonRows({ count = 4 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <ClientRowSkeleton key={i} />
      ))}
    </>
  )
}

export default ClientRowSkeleton
