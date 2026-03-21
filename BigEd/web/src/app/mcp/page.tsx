"use client";

import { useEffect, useState } from "react";
import { api, MCPServer } from "@/lib/api";
import { StatusDot } from "@/components/StatusDot";

export default function MCPPage() {
  const [configured, setConfigured] = useState<MCPServer[]>([]);
  const [available, setAvailable] = useState<MCPServer[]>([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const data = await api.mcp.list();
      setConfigured(data.configured);
      setAvailable(data.available);
    } catch {
      /* offline */
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const enable = async (name: string) => {
    await api.mcp.enable(name);
    load();
  };

  const disable = async (name: string) => {
    await api.mcp.disable(name);
    load();
  };

  if (loading) return <p className="text-text-dim">Loading MCP servers...</p>;

  return (
    <div className="space-y-6">
      <h2 className="text-gold text-lg font-bold">MCP Servers</h2>

      {/* Configured */}
      <div className="bg-bg-2 border border-glass-border rounded-lg p-4">
        <h3 className="text-sm font-bold text-gold mb-3 uppercase">Configured</h3>
        {configured.length === 0 ? (
          <p className="text-text-dim text-sm">No MCP servers configured</p>
        ) : (
          <div className="space-y-2">
            {configured.map((s) => (
              <div
                key={s.name}
                className="flex items-center justify-between bg-bg-3 rounded p-3 border border-glass-border"
              >
                <div className="flex items-center gap-3">
                  <StatusDot status={s.status} />
                  <div>
                    <span className="font-bold text-sm">{s.name}</span>
                    <span className="ml-2 text-xs text-text-dim">{s.type}</span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-text-dim">{s.category}</span>
                  <button
                    onClick={() => disable(s.name)}
                    className="text-xs px-3 py-1 bg-accent/20 text-accent rounded hover:bg-accent/40 transition"
                  >
                    Disable
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Available */}
      <div className="bg-bg-2 border border-glass-border rounded-lg p-4">
        <h3 className="text-sm font-bold text-gold mb-3 uppercase">Available</h3>
        <div className="space-y-2">
          {available.map((s) => (
            <div
              key={s.name}
              className="flex items-center justify-between bg-bg-3 rounded p-3 border border-glass-border"
            >
              <div>
                <span className="font-bold text-sm">{s.name}</span>
                <span className="ml-2 text-xs text-text-dim">{s.type}</span>
                {s.description && (
                  <p className="text-xs text-text-dim mt-0.5">{s.description}</p>
                )}
                {s.requires_key && (
                  <span className="text-xs text-status-orange">
                    Requires: {s.requires_key}
                  </span>
                )}
              </div>
              <button
                onClick={() => enable(s.name)}
                className="text-xs px-3 py-1 bg-status-green/20 text-status-green rounded hover:bg-status-green/40 transition"
              >
                Enable
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
