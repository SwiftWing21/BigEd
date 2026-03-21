/** BigEd CC Fleet API client */

const API_BASE = "";

export async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// Fleet API types
export interface FleetHealth {
  status: string;
  uptime_seconds: number;
  fleet_db: string;
  ollama: string;
  supervisor: string;
  dashboard: string;
}

export interface AgentPerformance {
  agent: string;
  tasks_per_hour: number;
  success_rate: number;
  avg_latency_ms: number;
  avg_iq: number;
}

export interface MCPServer {
  name: string;
  type: string;
  status: string;
  category: string;
  description?: string;
  requires_key?: string;
  config?: Record<string, unknown>;
}

export interface MCPServersResponse {
  configured: MCPServer[];
  available: MCPServer[];
}

export interface SLAData {
  overall: { avg_completion_ms: number; success_rate: number; total_24h: number };
  by_skill: Array<{ skill: string; avg_ms: number; success_rate: number; count: number }>;
}

// API functions
export const api = {
  health: () => fetchJSON<FleetHealth>("/api/fleet/health"),
  agents: () => fetchJSON<AgentPerformance[]>("/api/agents/performance"),
  mcp: {
    list: () => fetchJSON<MCPServersResponse>("/api/mcp/servers"),
    enable: (name: string, apiKey?: string) =>
      postJSON("/api/mcp/enable/" + name, apiKey ? { api_key: apiKey } : undefined),
    disable: (name: string) => postJSON("/api/mcp/disable/" + name),
    probe: (name: string) => postJSON("/api/mcp/probe/" + name),
    addCustom: (data: { name: string; type: string; url?: string; command?: string; args?: string[] }) =>
      postJSON("/api/mcp/add", data),
  },
  sla: () => fetchJSON<SLAData>("/api/sla"),
};
