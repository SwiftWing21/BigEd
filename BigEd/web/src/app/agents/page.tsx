"use client";

import { useEffect, useState } from "react";
import { api, AgentPerformance } from "@/lib/api";

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentPerformance[]>([]);

  useEffect(() => {
    const load = async () => {
      try { setAgents(await api.agents()); } catch { /* offline */ }
    };
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, []);

  return (
    <div className="space-y-6">
      <h2 className="text-gold text-lg font-bold">Agent Performance</h2>
      <div className="bg-bg-2 border border-glass-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-text-dim text-left bg-bg-3 border-b border-glass-border">
              <th className="p-3">Agent</th>
              <th className="p-3">Tasks/hr</th>
              <th className="p-3">Success Rate</th>
              <th className="p-3">Avg Latency</th>
              <th className="p-3">IQ Score</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((a) => (
              <tr key={a.agent} className="border-b border-glass-border/30 hover:bg-bg-3/50">
                <td className="p-3 font-bold">{a.agent}</td>
                <td className="p-3">{a.tasks_per_hour.toFixed(1)}</td>
                <td className="p-3">
                  <span className={
                    a.success_rate > 0.9 ? "text-status-green" :
                    a.success_rate > 0.7 ? "text-status-orange" : "text-status-red"
                  }>
                    {(a.success_rate * 100).toFixed(1)}%
                  </span>
                </td>
                <td className="p-3 text-text-dim">{Math.round(a.avg_latency_ms)}ms</td>
                <td className="p-3">
                  <span className={`px-2 py-0.5 rounded text-xs ${
                    a.avg_iq > 0.8 ? "bg-status-green/20 text-status-green" :
                    a.avg_iq > 0.5 ? "bg-status-orange/20 text-status-orange" :
                    "bg-status-red/20 text-status-red"
                  }`}>
                    {a.avg_iq.toFixed(2)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {agents.length === 0 && (
          <p className="text-center text-text-dim py-8">No agent data — fleet may be offline</p>
        )}
      </div>
    </div>
  );
}
