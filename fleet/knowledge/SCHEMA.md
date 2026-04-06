# Knowledge Wiki Schema

> Based on Karpathy's LLM Wiki pattern. This file defines how the knowledge
> directory is structured, what conventions agents must follow, and what
> workflows maintain the wiki's integrity.

## Three Layers

1. **Raw outputs** — immutable agent-generated artifacts in subdirectories
   (code_reviews/, evaluations/, security/, etc.). Agents write here. Never modified after creation.

2. **Wiki pages** — LLM-maintained summaries, entity pages, and concept pages in `wiki/`.
   Updated on every ingest. Cross-linked via markdown links.

3. **This schema** — the rules agents follow when creating or updating wiki content.

## Directory Structure

```
knowledge/
├── SCHEMA.md              ← this file (conventions, rules)
├── index.md               ← content catalog: every page with link + one-line summary
├── log.md                 ← append-only: what happened and when
├── wiki/                  ← LLM-maintained summary pages
│   ├── overview.md        ← high-level project health synthesis
│   ├── agents.md          ← agent roster, specializations, performance
│   ├── skills.md          ← skill catalog with quality metrics
│   ├── security.md        ← security posture synthesis
│   ├── architecture.md    ← architecture decisions and patterns
│   └── {topic}.md         ← additional topic pages as needed
├── code_reviews/          ← raw agent outputs (immutable)
├── code_discussion/
├── evaluations/
├── security/
├── stability/
├── changelogs/
├── quality/
├── refactors/
├── marathon/
└── code_drafts/
```

## Conventions

### File naming
- Wiki pages: `wiki/{topic}.md` — lowercase, hyphens for spaces
- Raw outputs: `{category}/{filename}_{date}_{agent}.md`
- Index: `index.md` at knowledge root
- Log: `log.md` at knowledge root

### Cross-linking
- Use relative markdown links: `[security posture](wiki/security.md)`
- Every wiki page must link back to `index.md`
- Every wiki page should link to related wiki pages

### Index entries
One line per page, format:
```
- [Page Title](path/to/file.md) — one-line summary (updated YYYY-MM-DD)
```

### Log entries
Append-only, format:
```
- YYYY-MM-DD HH:MM — {agent} — {action}: {description}
```

## Workflows

### On skill output (code_review, evaluation, security_review, etc.)
1. Write raw output to appropriate subdirectory (existing behavior)
2. Append entry to `log.md`
3. Update relevant wiki page(s) if findings are significant
4. Update `index.md` if a new file was created

### On lint (periodic, triggered by doc_freshness skill)
1. Scan wiki pages for broken cross-links
2. Flag contradictions between wiki pages
3. Identify raw outputs not referenced in any wiki page (orphans)
4. Report stale wiki pages (last updated > 7 days with new raw data since)

### On query (knowledge search)
1. Search wiki pages first (higher-quality, synthesized)
2. Fall back to raw outputs via RAG if wiki doesn't cover the topic
3. Cite sources: wiki page + underlying raw outputs
