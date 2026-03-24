# r/ClaudeAI Post Draft

**Title:** Pro trial → Max 20x in 48 hours. It's Sunday morning and I'm 70% through the week's usage. Here's what I built.

---

Started last week just wanting to play with the tools. Grabbed a 7-day Pro trial on Monday, maxed it out by Wednesday morning. Immediately upgraded to Max 20x. It's Sunday and we're sitting at just past 70% for the week. I've been busy.

Background: I'm not a professional dev. I'm a tinkerer who wanted to see how far you could push Claude Code + occasional Gemini Code reviews as a second set of eyes. Turns out — pretty far.

I've been building **BigEd CC**, a local-first AI fleet manager (92 skills, Ollama + Claude/Gemini fallback, dual-supervisor architecture). But the real surprise was how naturally the plugin system clicked. Over the past few days I've assembled 4 Claude Code plugins from the patterns I was building anyway:

**1. model-manager** (v1.0.0 — just shipped)
The big one. 6 skills, 2 autonomous agents, 10 reference docs. Covers three-tier model architecture (heartbeat/conductor/primary), VRAM-aware device routing, thermal-aware scaling, zombie handle cleanup, circuit breakers for provider fallback, cost-aware inference routing. 13-category audit checklist, 14 anti-patterns. Framework-agnostic (PyTorch, Ollama, llama.cpp, vLLM). Searched the plugin marketplace — nothing else in this space. Zero competing plugins.

**2. github-manager**
6 skills + 2 agents for repo lifecycle. Branch tracking/cleanup, issue management, PR workflows, label/milestone organization, release notes generation, and secrets safety auditing. The repo-health-checker agent runs a full autonomous audit across issues, PRs, branches, milestones, and labels.

**3. oss-reviewer**
Evaluate any open-source project across 4 lenses before adding it to your stack: security, performance, architecture, and compliance. Give it a GitHub URL, get back a structured assessment.

**4. swarm-consensus**
Multi-perspective debate tool. Give it a decision ("should we use X or Y?"), it structures arguments from multiple viewpoints, stress-tests the reasoning, then synthesizes. Basically devil's advocate as a service.

**What surprised me:**

- The plugin system is genuinely powerful once you understand skills + agents + reference docs. Skills teach Claude *how* to do something. Agents let it do it autonomously. Reference docs are the domain knowledge.
- Gemini as a code review second opinion is underrated. Different blind spots from Claude.
- Most of my token usage isn't writing code — it's Claude reading my codebase to understand context before making changes. The more structured your CLAUDE.md and project docs are, the less you burn.
- Building plugins from patterns you've already battle-tested in your own codebase is the move. Don't theorize — extract from working code.

All of these are on my GitHub: https://github.com/SwiftWing21

If anyone's interested in any of these I'm happy to share the .plugin files or walk through how they're structured. The model-manager especially — if you're running local models and fighting VRAM/thermal issues, it might save you some pain.

70% through the week and no regrets on Max 20x. This is the most productive I've been on a project in years.
