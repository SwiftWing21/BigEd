---
name: oss-review
description: Review open-source projects for quality, security, and maintainability
---

# OSS Project Reviewer

When the user asks to review an open-source project, search for projects, or evaluate a GitHub repository:

## If a GitHub URL is provided:

1. Fetch repo metadata from the GitHub API (`https://api.github.com/repos/{owner}/{repo}`)
2. Pre-rate: stars, forks, open issues, last commit age, license
3. Check for known vulnerabilities via OSV.dev (`https://api.osv.dev/v1/query`)
4. Show the traffic light rating:
   - GREEN: >1000 stars, 0 critical CVEs, recent commits
   - YELLOW: 100-1000 stars, minor CVEs, moderately active
   - RED: low activity, critical CVEs, or abandoned
5. Ask: "Pre-rating is [GREEN/YELLOW/RED]. Proceed with full review?"
6. If yes, fetch README and file tree, then analyze:
   - Security: dependency risks, auth patterns, input validation
   - Performance: complexity, resource usage, async patterns
   - Architecture: modularity, test coverage, API design, docs
   - Compliance: license, data handling, supply chain
7. Output a report card with letter grades (A-F) per dimension + key findings

## If a search query is provided:

1. Search GitHub for repositories matching the query
2. Pre-rate the top 5 results
3. Present ranked candidates with traffic light ratings
4. Ask which one to review in detail

## Output format:

Use this report card format:

| Dimension | Grade | Key Finding |
|-----------|-------|-------------|
| Security | B+ | No critical CVEs, 2 medium dependency issues |
| Performance | A- | Clean async patterns |
| Architecture | B | Good modularity, 72% test coverage |
| Compliance | A | MIT license, clean SBOM |
| **Overall** | **B+** | |

## Important:
- Always show the pre-rating before doing a full review
- Include known issues from the project's issue tracker when available
- Flag any critical CVEs prominently
- Note if the project appears abandoned (no commits >90 days)
