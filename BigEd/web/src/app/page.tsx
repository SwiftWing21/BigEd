"use client";

import { useEffect, useState } from "react";
import { api, FleetHealth, AgentPerformance, SLAData } from "@/lib/api";
import { StatusDot } from "@/components/StatusDot";

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-bg-2 border border-glass-border rounded-lg p-4">
      <h3 className="text-gold text-sm font-bold mb-3 uppercase tracking-wide">{title}</h3>
      {children}
    </div>
  );
}

export default function FleetPage() {
  const [health, setHealth] = useState<FleetHealth | null>(null);
  const [agents, setAgents] = useState<AgentPerformance[]>([]);
  const [sla, setSla] = useState<SLAData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [h, a, s] = await Promise.all([api.health(), api.agents(), api.sla()]);
        setHealth(h);
        setAgents(a);
        setSla(s);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Connection failed");
      }
    };
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, []);

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <p className="text-status-red text-lg">Fleet Offline</p>
          <p className="text-text-dim text-sm mt-2">{error}</p>
          <p className="text-text-dim text-xs mt-1">
            Start the fleet: <code className="text-gold">python fleet/supervisor.py</code>
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Health overview */}
      <div className="grid grid-cols-4 gap-4">
        <Card title="Fleet">
          <div className="flex items-center gap-2">
            <StatusDot status={health?.status ?? "unknown"} />
            <span className="text-xl font-bold">{health?.status ?? "..."}</span>
          </div>
          {health && (
            <p className="text-text-dim text-xs mt-1">
              Uptime: {Math.floor((health.uptime_seconds ?? 0) / 3600)}h
            </p>
          )}
        </Card>
        <Card title="Ollama">
          <div className="flex items-center gap-2">
            <StatusDot status={health?.ollama ?? "unknown"} />
            <span className="capitalize">{health?.ollama ?? "..."}</span>
          </div>
        </Card>
        <Card title="Supervisor">
          <div className="flex items-center gap-2">
            <StatusDot status={health?.supervisor ?? "unknown"} />
            <span className="capitalize">{health?.supervisor ?? "..."}</span>
          </div>
        </Card>
        <Card title="SLA (24h)">
          {sla ? (
            <>
              <p className="text-xl font-bold">
                {(sla.overall.success_rate * 100).toFixed(1)}%
              </p>
              <p className="text-text-dim text-xs">
                {sla.overall.total_24h} tasks, avg {Math.round(sla.overall.avg_completion_ms)}ms
              </p>
            </>
          ) : (
            <p className="text-text-dim">...</p>
          )}
        </Card>
      </div>

      {/* Agent cards */}
      <Card title="Agents">
        {agents.length === 0 ? (
          <p className="text-text-dim text-sm">No agent data</p>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            {agents.map((a) => (
              <div
                key={a.agent}
                className="bg-bg-3 rounded p-3 border border-glass-border"
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-bold">{a.agent}</span>
                  <span
                    className={`text-xs px-1.5 py-0.5 rounded ${
                      a.avg_iq > 0.8
                        ? "bg-status-green/20 text-status-green"
                        : a.avg_iq > 0.5
                          ? "bg-status-orange/20 text-status-orange"
                          : "bg-status-red/20 text-status-red"
                    }`}
                  >
                    IQ {a.avg_iq.toFixed(2)}
                  </span>
                </div>
                <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-text-dim">
                  <div>
                    <span className="block text-text">{a.tasks_per_hour.toFixed(1)}</span>
                    tasks/hr
                  </div>
                  <div>
                    <span className="block text-text">
                      {(a.success_rate * 100).toFixed(0)}%
                    </span>
                    success
                  </div>
                  <div>
                    <span className="block text-text">{Math.round(a.avg_latency_ms)}</span>
                    ms avg
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* SLA by skill */}
      {sla && sla.by_skill.length > 0 && (
        <Card title="SLA by Skill">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-dim text-left border-b border-glass-border">
                  <th className="pb-2">Skill</th>
                  <th className="pb-2">Count</th>
                  <th className="pb-2">Avg ms</th>
                  <th className="pb-2">Success</th>
                </tr>
              </thead>
              <tbody>
                {sla.by_skill.slice(0, 15).map((s) => (
                  <tr key={s.skill} className="border-b border-glass-border/30">
                    <td className="py-1.5">{s.skill}</td>
                    <td className="text-text-dim">{s.count}</td>
                    <td className="text-text-dim">{Math.round(s.avg_ms)}</td>
                    <td>
                      <span
                        className={
                          s.success_rate > 0.9
                            ? "text-status-green"
                            : s.success_rate > 0.7
                              ? "text-status-orange"
                              : "text-status-red"
                        }
                      >
                        {(s.success_rate * 100).toFixed(0)}%
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
