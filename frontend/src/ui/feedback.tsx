import React from "react";

type BannerTone = "error" | "success" | "info";

export function FeedbackBanner({
  tone,
  message,
}: {
  tone: BannerTone;
  message: string;
}): React.ReactElement {
  const classesByTone: Record<BannerTone, string> = {
    error: "border-red-200 bg-red-50 text-red-800",
    success: "border-emerald-200 bg-emerald-50 text-emerald-800",
    info: "border-slate-200 bg-slate-50 text-slate-700",
  };
  return <div className={`rounded-md border p-3 text-sm ${classesByTone[tone]}`}>{message}</div>;
}

export function EmptyState({
  message,
  action,
}: {
  message: string;
  action?: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="px-4 py-4 text-sm text-slate-600">
      <div>{message}</div>
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function SkeletonRows({ count = 3 }: { count?: number }): React.ReactElement {
  return (
    <>
      {Array.from({ length: count }).map((_, idx) => (
        <div key={`skeleton-${idx}`} className="px-4 py-3">
          <div className="h-4 w-3/4 animate-pulse rounded bg-slate-200" />
          <div className="mt-2 h-3 w-1/2 animate-pulse rounded bg-slate-100" />
        </div>
      ))}
    </>
  );
}
