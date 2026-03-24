# Third-Party Vendor ToS & Compliance Reference

**Purpose:** Track every external API, OAuth provider, and third-party service used by BigEd CC.
Links to authoritative ToS (never copied — always current at source). Review when integrating new features, rotating keys, or before a release.

**Last full review:** 2026-03-21
**Owner:** Max / SwiftWing21

---

## How to use this file
- Update **Last Reviewed** whenever you re-read a vendor's ToS
- Add a note in **Key Constraints** for anything that directly affects fleet behavior (rate limits, data retention, attribution, prohibited uses)
- Flag anything that changed with `⚠️ CHANGED` until the code is verified compliant

---

## AI / LLM Providers

| Vendor | ToS / Usage Policy | Last Reviewed | Key Constraints |
|--------|-------------------|---------------|-----------------|
| **Anthropic (Claude)** | [Usage Policy](https://www.anthropic.com/legal/aup) · [API Terms](https://www.anthropic.com/legal/api-service-terms) | 2026-03-21 | No automated harmful content; prompt caching permitted; no reselling raw API access; rate-limit throttle at 20% per CLAUDE.md |
| **Google Gemini** | [Terms](https://ai.google.dev/gemini-api/terms) · [Prohibited Use](https://policies.google.com/terms/generative-ai/use-policy) | 2026-03-21 | Cannot use output to train competing models; SAFETY/RECITATION finish reasons must be handled (already done in providers.py); free tier quota limits apply |
| **MiniMax** | [Terms of Service](https://www.minimaxi.chat/en/term-of-use) | 2026-03-21 | Output attribution not required; no use for illegal content; circuit breaker (3 fail/5min) already implemented |
| **Ollama (local)** | [MIT License](https://github.com/ollama/ollama/blob/main/LICENSE) | 2026-03-21 | No ToS risk — local inference only; model weights (qwen3) subject to their own licenses (Apache 2.0) |
| **Qwen3 model weights** | [Qwen License](https://huggingface.co/Qwen/Qwen3-8B/blob/main/LICENSE) | 2026-03-21 | Apache 2.0 — commercial use permitted; no restrictions on fleet use |

---

## Web Search APIs

| Vendor | ToS | Last Reviewed | Key Constraints |
|--------|-----|---------------|-----------------|
| **Brave Search API** | [Terms](https://brave.com/search/api/) · [Data Policy](https://search.brave.com/help/privacy) | 2026-03-21 | 2000 free queries/month; results cannot be stored long-term or re-served to third parties; attribution required in UI |
| **Tavily Search** | [Terms](https://tavily.com/terms) | 2026-03-21 | 1000 free queries/month; results for end-user research use only; no scraping/mass storage |
| **Jina AI (s.jina.ai)** | [Terms](https://jina.ai/legal/) | 2026-03-21 | Free no-auth tier; fair use only; rate limits enforced server-side |
| **DuckDuckGo** | [Terms](https://duckduckgo.com/terms) | 2026-03-21 | No commercial scraping; Instant Answer API is unofficial — production use should migrate to a supported tier |
| **arXiv API** | [Terms of Use](https://arxiv.org/help/api/tou) | 2026-03-21 | Free for non-commercial and research use; no bulk harvesting; 3-second delay between requests required |

---

## GitHub

| Vendor | ToS | Last Reviewed | Key Constraints |
|--------|-----|---------------|-----------------|
| **GitHub OAuth (Device Flow)** | [OAuth Terms](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service) · [Developer Policy](https://docs.github.com/en/site-policy/github-terms/github-developer-terms-of-service) | 2026-03-21 | `repo` scope token stored in `~/.secrets` — must not be committed; device flow requires user interaction (already compliant); no automation that mimics human users at scale |
| **GitHub REST API** | [Acceptable Use](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies) | 2026-03-21 | 5000 req/hr authenticated; PAT must be least-privilege; no automated spam/issue flooding; GITHUB_PAT must stay out of repo |

---

## Image & Video Generation

| Vendor | ToS / Use Policy | Last Reviewed | Key Constraints |
|--------|-----------------|---------------|-----------------|
| **Stability AI** | [Terms](https://stability.ai/terms-of-service) · [Use Policy](https://stability.ai/use-policy) | 2026-03-21 | Cannot generate CSAM, non-consensual deepfakes, or real-person images for deception; generated images may be used commercially; watermarking not required but recommended for AI-generated content |
| **Replicate** | [Terms](https://replicate.com/terms) | 2026-03-21 | Usage billed per-second compute; model-specific licenses apply (Minimax, LTX-Video, Weaver — review each); no guaranteed SLA on free tier |

---

## Communication & Notifications

| Vendor | ToS / Developer Policy | Last Reviewed | Key Constraints |
|--------|----------------------|---------------|-----------------|
| **Discord Bot API** | [Developer Terms](https://discord.com/developers/docs/policies-and-agreements/developer-terms-of-service) · [Developer Policy](https://discord.com/developers/docs/policies-and-agreements/developer-policy) | 2026-03-21 | Bot must identify as a bot; no DM spam; slash commands preferred over message commands; hardcoded channel ID (1483720731014594560) should be moved to fleet.toml |
| **Slack API (token rotation)** | [API Terms](https://slack.com/terms-of-service/api) | 2026-03-21 | Bot tokens must be rotated via tooling.tokens.rotate; no storing user message content beyond immediate use; `SLACK_BOT_TOKEN` + `SLACK_REFRESH_TOKEN` must stay out of repo |

---

## Cloud & Infrastructure

| Vendor | ToS | Last Reviewed | Key Constraints |
|--------|-----|---------------|-----------------|
| **AWS IAM (secret rotation)** | [Service Terms](https://aws.amazon.com/service-terms/) | 2026-03-21 | IAM key rotation via boto3 is compliant; do not log `AWS_SECRET_ACCESS_KEY` values; least-privilege principle required |
| **HuggingFace Hub** | [Terms](https://huggingface.co/terms-of-service) | 2026-03-21 | Free tier rate-limited; `HF_TOKEN` required for gated models; GGUF model weights subject to their individual model card licenses |
| **Docker** | [Terms](https://www.docker.com/legal/docker-terms-service/) | 2026-03-21 | Docker Desktop requires a paid license for commercial use at companies >250 employees or >$10M revenue; Docker Engine (Linux) remains free; Playwright MCP container runs fine |

---

## Local / Self-Hosted (Low Risk)

| Vendor | License | Notes |
|--------|---------|-------|
| **Home Assistant REST API** | [Apache 2.0](https://github.com/home-assistant/core/blob/dev/LICENSE.md) | Self-hosted; no ToS exposure. HA Cloud (Nabu Casa) has separate [terms](https://www.nabucasa.com/tos/) if used |
| **UniFi Controller** | [Ubiquiti ToS](https://www.ui.com/legal/termsofservice/) | Self-hosted controller; API is unofficial/undocumented — no guarantee of stability |
| **MQTT Broker** | Protocol standard (OASIS) | Self-hosted Mosquitto (EPL 2.0); no external ToS risk |
| **PostgreSQL MCP** | [PostgreSQL License](https://www.postgresql.org/about/licence/) | Self-hosted; no ToS risk |

---

## MCP Servers (@modelcontextprotocol)

| Package | License | Notes |
|---------|---------|-------|
| `server-filesystem` | [MIT](https://github.com/modelcontextprotocol/servers/blob/main/LICENSE) | No ToS risk |
| `server-sequential-thinking` | MIT | No ToS risk |
| `server-memory` | MIT | No ToS risk |
| `server-github` | MIT | GitHub API ToS applies (see GitHub row above) |
| `server-brave-search` | MIT | Brave Search API ToS applies (see above) |
| `server-fetch` | MIT | No ToS risk — just HTTP fetching |
| `server-slack` | MIT | Slack API ToS applies (see above) |
| `server-postgres` | MIT | No ToS risk |

---

## Action Items

| Item | Priority | Status |
|------|----------|--------|
| Move Discord hardcoded channel ID `1483720731014594560` to `fleet.toml` | Low | [ ] |
| Confirm DuckDuckGo usage is within acceptable use (no commercial API) | Medium | [ ] |
| Verify Replicate model card licenses for Minimax/LTX-Video/Weaver | Medium | [ ] |
| Confirm Docker Engine vs Docker Desktop usage on dev machines | Low | [ ] |
| Add `("docs", "*.md")` to `SCAN_PATHS` in `fleet/rag.py` so this file gets indexed | Low | [ ] |

---

*This file tracks links and notes only — no ToS text is reproduced here.*
