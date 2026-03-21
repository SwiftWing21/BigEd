import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BigEd CC — Fleet Dashboard",
  description: "Web-based fleet management for BigEd Compute Command",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-bg">
        <nav className="flex items-center h-14 px-6 bg-bg-3 border-b border-glass-border">
          <span className="text-gold font-bold text-lg tracking-wide">BigEd CC</span>
          <span className="ml-3 text-xs text-text-dim bg-glass-nav px-2 py-0.5 rounded">
            v0.170
          </span>
          <div className="flex-1" />
          <div className="flex gap-4 text-sm">
            <a href="/" className="text-text-dim hover:text-gold transition">Fleet</a>
            <a href="/agents" className="text-text-dim hover:text-gold transition">Agents</a>
            <a href="/mcp" className="text-text-dim hover:text-gold transition">MCP</a>
            <a href="/costs" className="text-text-dim hover:text-gold transition">Costs</a>
            <a href="/audit" className="text-text-dim hover:text-gold transition">Audit</a>
            <a href="/settings" className="text-text-dim hover:text-gold transition">Settings</a>
          </div>
        </nav>
        <main className="p-6">{children}</main>
      </body>
    </html>
  );
}
