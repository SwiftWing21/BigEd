"use client";

import { useEffect, useState } from "react";
import { fetchJSON } from "@/lib/api";

export default function SettingsPage() {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    fetchJSON<Record<string, unknown>>("/api/web/config")
      .then(setConfig)
      .catch(() => {});
  }, []);

  return (
    <div className="space-y-6">
      <h2 className="text-gold text-lg font-bold">Settings</h2>

      <div className="bg-bg-2 border border-glass-border rounded-lg p-4">
        <h3 className="text-sm font-bold text-gold mb-3 uppercase">Web Configuration</h3>
        {config ? (
          <pre className="text-sm text-text-dim bg-bg-3 rounded p-4 overflow-auto">
            {JSON.stringify(config, null, 2)}
          </pre>
        ) : (
          <p className="text-text-dim">Loading...</p>
        )}
      </div>

      <div className="bg-bg-2 border border-glass-border rounded-lg p-4">
        <h3 className="text-sm font-bold text-gold mb-3 uppercase">Quick Links</h3>
        <div className="grid grid-cols-3 gap-3">
          <a href="/api/fleet/health" className="bg-bg-3 rounded p-3 text-sm hover:border-gold border border-glass-border transition">
            Fleet Health API
          </a>
          <a href="/api/agents/performance" className="bg-bg-3 rounded p-3 text-sm hover:border-gold border border-glass-border transition">
            Agent Performance API
          </a>
          <a href="/api/mcp/servers" className="bg-bg-3 rounded p-3 text-sm hover:border-gold border border-glass-border transition">
            MCP Servers API
          </a>
        </div>
      </div>
    </div>
  );
}
