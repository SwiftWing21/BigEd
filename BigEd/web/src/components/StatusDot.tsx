"use client";

export function StatusDot({ status }: { status: string }) {
  const color =
    status === "online" || status === "healthy" || status === "IDLE"
      ? "bg-status-green"
      : status === "degraded" || status === "BUSY" || status === "configured"
        ? "bg-status-orange"
        : status === "offline" || status === "unhealthy" || status === "error"
          ? "bg-status-red"
          : "bg-text-dim";

  return <span className={`inline-block w-2 h-2 rounded-full ${color}`} />;
}
