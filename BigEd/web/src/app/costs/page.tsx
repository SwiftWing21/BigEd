"use client";

import { useEffect, useState } from "react";
import { fetchJSON } from "@/lib/api";

interface CostEntry {
  skill?: string;
  model?: string;
  calls: number;
  total_input: number;
  total_output: number;
  total_cost: number;
}

export default function CostsPage() {
  const [bySkill, setBySkill] = useState<CostEntry[]>([]);
  const [byModel, setByModel] = useState<CostEntry[]>([]);
  const [period, setPeriod] = useState("week");

  useEffect(() => {
    const load = async () => {
      try {
        const [s, m] = await Promise.all([
          fetchJSON<CostEntry[]>(`/api/usage?period=${period}&group_by=skill`),
          fetchJSON<CostEntry[]>(`/api/usage?period=${period}&group_by=model`),
        ]);
        setBySkill(s);
        setByModel(m);
      } catch { /* offline */ }
    };
    load();
  }, [period]);

  const totalCost = bySkill.reduce((s, e) => s + (e.total_cost || 0), 0);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-gold text-lg font-bold">Cost Dashboard</h2>
        <div className="flex gap-1 bg-bg-3 rounded p-0.5">
          {["day", "week", "month"].map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1 text-xs rounded transition ${
                period === p ? "bg-gold text-bg font-bold" : "text-text-dim hover:text-text"
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* Total */}
      <div className="bg-bg-2 border border-glass-border rounded-lg p-6 text-center">
        <p className="text-text-dim text-sm">Total Spend ({period})</p>
        <p className="text-3xl font-bold text-gold mt-1">${totalCost.toFixed(4)}</p>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* By Skill */}
        <div className="bg-bg-2 border border-glass-border rounded-lg p-4">
          <h3 className="text-sm font-bold text-gold mb-3 uppercase">By Skill</h3>
          <div className="space-y-2">
            {bySkill.slice(0, 10).map((e) => (
              <div key={e.skill} className="flex justify-between text-sm">
                <span>{e.skill}</span>
                <span className="text-text-dim">
                  ${(e.total_cost || 0).toFixed(4)}
                  <span className="ml-2 text-xs">({e.calls} calls)</span>
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* By Model */}
        <div className="bg-bg-2 border border-glass-border rounded-lg p-4">
          <h3 className="text-sm font-bold text-gold mb-3 uppercase">By Model</h3>
          <div className="space-y-2">
            {byModel.map((e) => (
              <div key={e.model} className="flex justify-between text-sm">
                <span>{e.model}</span>
                <span className="text-text-dim">
                  ${(e.total_cost || 0).toFixed(4)}
                  <span className="ml-2 text-xs">({e.calls} calls)</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
