"use client";

import { useEffect, useState } from "react";
import { fetchJSON } from "@/lib/api";

interface AuditEntry {
  id: number;
  timestamp: string;
  actor: string;
  action: string;
  resource: string;
  detail: string;
  cost_usd: number;
}

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");

  const load = async () => {
    try {
      const params = new URLSearchParams();
      if (actor) params.set("actor", actor);
      if (action) params.set("action", action);
      const data = await fetchJSON<{ entries: AuditEntry[] }>(
        `/api/audit?${params.toString()}`
      );
      setEntries(data.entries || []);
    } catch {
      /* offline */
    }
  };

  useEffect(() => { load(); }, [actor, action]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-gold text-lg font-bold">Audit Trail</h2>
        <div className="flex gap-2">
          <a
            href="/api/audit/export?fmt=csv"
            className="text-xs px-3 py-1.5 bg-bg-3 text-text-dim rounded hover:text-gold transition"
          >
            Export CSV
          </a>
          <a
            href="/api/audit/export?fmt=json"
            className="text-xs px-3 py-1.5 bg-bg-3 text-text-dim rounded hover:text-gold transition"
          >
            Export JSON
          </a>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-4">
        <input
          type="text"
          placeholder="Filter by actor..."
          value={actor}
          onChange={(e) => setActor(e.target.value)}
          className="bg-bg-3 border border-glass-border rounded px-3 py-1.5 text-sm text-text placeholder-text-dim focus:border-gold outline-none"
        />
        <input
          type="text"
          placeholder="Filter by action..."
          value={action}
          onChange={(e) => setAction(e.target.value)}
          className="bg-bg-3 border border-glass-border rounded px-3 py-1.5 text-sm text-text placeholder-text-dim focus:border-gold outline-none"
        />
      </div>

      {/* Table */}
      <div className="bg-bg-2 border border-glass-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-text-dim text-left bg-bg-3 border-b border-glass-border">
              <th className="p-3">Time</th>
              <th className="p-3">Actor</th>
              <th className="p-3">Action</th>
              <th className="p-3">Resource</th>
              <th className="p-3">Cost</th>
              <th className="p-3">Detail</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id} className="border-b border-glass-border/30 hover:bg-bg-3/50">
                <td className="p-3 text-text-dim text-xs whitespace-nowrap">{e.timestamp}</td>
                <td className="p-3">{e.actor}</td>
                <td className="p-3 text-gold">{e.action}</td>
                <td className="p-3 text-text-dim">{e.resource}</td>
                <td className="p-3 text-text-dim">
                  {e.cost_usd > 0 ? `$${e.cost_usd.toFixed(4)}` : "—"}
                </td>
                <td className="p-3 text-text-dim text-xs max-w-xs truncate">{e.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {entries.length === 0 && (
          <p className="text-center text-text-dim py-8">No audit entries</p>
        )}
      </div>
    </div>
  );
}
