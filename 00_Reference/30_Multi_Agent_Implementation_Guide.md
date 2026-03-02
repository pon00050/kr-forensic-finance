# Multi-Agent Implementation Guide

> **Scope:** Where Claude multi-agents fit in this pipeline, what the Claude Agent SDK
>   enables, and the implementation sequence for Phases 3–5.
> **Canonical for:** Agent SDK adoption; orchestrator/subagent design; SEIBRO MCP pattern.
> **See also:** `10_Multi_Agent_Architecture.md` (agent roster design), `08_Continuous_Monitoring_System.md`
>   (3-way match architecture), `09_Claude_Cost_Optimization.md` (cost patterns)
> **Builds on:** `10_Multi_Agent_Architecture.md` — the 12-agent roster defined there is the
>   conceptual design; this doc is the implementation path using the Claude Agent SDK.

*Created: March 2, 2026.*

---

## TL;DR

The pipeline's Layer 2 (AI-assisted review) was designed for multi-agent orchestration.
The **Claude Agent SDK** (released Sep 2025, formerly Claude Code SDK) is now the right
implementation vehicle — it exposes the same agentic loop that powers Claude Code itself,
with built-in tools, subagent spawning, MCP server integration, session continuity, and
audit hooks.

Three tasks in this pipeline are natural fits for agents and cannot be replicated by
plain Python: **Leg 3 real-time monitoring**, **narrative inconsistency detection**, and
**SEIBRO scraping via Playwright MCP**. Everything else (extraction, Beneish math,
pricing) stays as deterministic Python — agents orchestrate on top, they don't replace it.

---

## Section 1 — What the Claude Agent SDK Provides

```bash
pip install claude-agent-sdk
```

The SDK wraps Claude's agentic loop — the same loop that powers Claude Code — as a
programmable Python/TypeScript library. Key capabilities relevant to this project:

| Capability | What it means here |
|---|---|
| **Built-in tools** (Read, Bash, Grep, WebFetch) | Agents call existing Python scripts as subprocesses — no rewrite needed |
| **Subagents** | Orchestrator spawns Haiku (classification) and Sonnet (synthesis) agents in parallel |
| **MCP servers** | Playwright MCP plugs into SEIBRO scraping natively |
| **Sessions** | Orchestrator holds context across multi-step company investigations |
| **Hooks** | Audit log every Claude decision — required for litigation-support provenance |
| **Batch API** | Non-urgent tasks (narrative analysis, entity resolution) at ~50% cost reduction |

### Agent SDK vs. direct Anthropic Client SDK

The Client SDK requires you to implement the tool execution loop manually. The Agent SDK
handles it:

```python
# Client SDK — you implement the loop
response = client.messages.create(...)
while response.stop_reason == "tool_use":
    result = your_tool_executor(response.tool_use)
    response = client.messages.create(tool_result=result, **params)

# Agent SDK — Claude handles tools autonomously
async for message in query(prompt="Classify this DART filing", options=options):
    print(message)
```

For this project, use the Agent SDK for all Layer 2 work.

---

## Section 2 — The Three Natural Fits

### Fit 1: Leg 3 Real-Time Monitoring (Phase 5)

The DART RSS + news classification loop maps directly to an orchestrator/subagent pattern:

```
Orchestrator (Sonnet 4.6)
  └── Receives DART RSS and Korean financial news items
  └── Monitoring subagent (Haiku 4.5)
        └── Classifies each item: A/B/C/D/E/F (single letter, no explanation)
        └── Parallel execution — multiple items classified simultaneously
  └── Match engine (Python tool)
        └── Checks if classification + price signal (Leg 2) + Beneish flag (Leg 1) align
  └── Alert subagent (Sonnet 4.6)
        └── Fires only on 3-way match; drafts structured alert summary
        └── Output: {"company": ..., "ticker": ..., "legs_matched": [...], "summary": ...}
```

This is the most immediately valuable agent integration — it operationalizes the
3-way match framework described in `08_Continuous_Monitoring_System.md`.

**Implementation sketch:**

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition
import asyncio

async def run_leg3_monitor(rss_items: list[dict]):
    async for message in query(
        prompt=f"Classify these DART/news items and check for 3-way matches: {rss_items}",
        options=ClaudeAgentOptions(
            allowed_tools=["Bash", "Task"],
            agents={
                "classifier": AgentDefinition(
                    description="Classifies DART/news items A-F. Returns single letter only.",
                    prompt="You are a DART filing classifier. Return only: A, B, C, D, E, or F.",
                    tools=["Bash"],  # calls match_engine.py
                )
            },
        ),
    ):
        if hasattr(message, "result"):
            yield message.result
```

### Fit 2: Narrative Inconsistency Detection (Phase 3+)

A Sonnet 4.6 agent reads the full 사업보고서 (annual report) in a single 200K-token
context and flags contradictions between:

- MD&A text claims vs. reported financial numbers
- Officer holding disclosures vs. DART filing records
- CB/BW prospectus terms vs. actual exercise history
- Prior-year restatements buried in footnotes

**This is the task where Claude's large context window is genuinely irreplaceable.**
No Python script can do semantic cross-referencing across a 300-page Korean document.

Output schema (from `CLAUDE.md`):
```json
[{"source_quote": "...", "flag_type": "...", "severity": "low|medium|high"}]
```

Cost management: use Batch API (non-urgent, ~50% discount) + `cache_control: ephemeral`
on system prompts. See `09_Claude_Cost_Optimization.md`.

**Won benchmark caveat:** Open-ended Korean financial *reasoning* is near-zero accuracy.
Narrative inconsistency is *flagging* (is X consistent with Y?) — a binary classification
task the benchmark confirms is reliable. Do not ask the agent to explain what a flag
*means* financially; ask only whether a contradiction exists.

### Fit 3: SEIBRO Scraping via Playwright MCP (Phase 2+)

SEIBRO (`seibro.or.kr`) has no API and uses a WebSquare JavaScript app — the primary
reason it was deferred. The **Playwright MCP server** changes this calculus entirely:
a Haiku agent with Playwright MCP can navigate the WebSquare UI, extract repricing
history and CB/BW exercise events, and write structured output to parquet.

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async def scrape_seibro_cb(cb_issue_code: str):
    async for message in query(
        prompt=f"Navigate SEIBRO and extract repricing history for CB issue {cb_issue_code}",
        options=ClaudeAgentOptions(
            mcp_servers={
                "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}
            },
            allowed_tools=["mcp__playwright__navigate", "mcp__playwright__click",
                           "mcp__playwright__fill", "mcp__playwright__snapshot"],
        ),
    ):
        if hasattr(message, "result"):
            return message.result
```

This replaces what would otherwise be ~500 lines of brittle custom Playwright scraping
code with a short agent definition and a natural-language task description.

**Risk:** WebSquare apps are dynamically rendered — the agent's DOM navigation may need
prompt tuning as SEIBRO updates its UI. Snapshot-based extraction (Playwright's
accessibility tree) is more stable than CSS selector scraping.

---

## Section 3 — What Agents Do NOT Replace

Per `07_Automation_Assessment.md` and the Won benchmark (arXiv 2503.17963):

| Task | Keep as | Reason |
|---|---|---|
| DART/KRX data extraction | Plain Python | Deterministic; no reasoning needed |
| Beneish M-Score calculation | Plain Python | Pure arithmetic |
| PyKRX price/volume fetch | Plain Python | API wrapper; no reasoning needed |
| Open-ended Korean financial analysis | Neither | Near-zero accuracy on all models |
| Final fraud determination | Layer 3 (human) | Legal liability; agents produce hypotheses only |
| Filtering, math, sorting | Plain Python | Never use Claude for tasks Python handles |

The architecture principle from `10_Multi_Agent_Architecture.md` stands: **Python provides
the deterministic foundation; agents add orchestration and reasoning on top.**

---

## Section 4 — Entity Resolution (Cross-Cutting)

A Haiku 4.5 subagent clusters officer name variants across filings into unified
person-entities for the `officer_network.py` graph:

```
Input:  ["홍길동", "홍 길동", "Hong Gil-dong", "H. Gil-dong", "홍길동(대표이사)"]
Output: {"cluster_id": "P-0041", "names": [...], "confidence": 0.94,
          "match_basis": "phonetic + context"}
```

This is pure classification — exactly the task the Won benchmark confirms Haiku handles
reliably. Run via Batch API (non-urgent, asynchronous). One batch job per pipeline run
after officer holdings extraction is complete.

---

## Section 5 — Implementation Sequence

Given the current state (Phase 1 complete, Phase 2 scaffold in place):

```
Now → Phase 2
  Run --stage cb_bw to produce the 3 parquets (plain Python, no agents needed)
  Implement SEIBRO scraping (Playwright MCP agent — first agent SDK touchpoint)

Phase 3 → Leg 3 monitoring daemon
  Implement orchestrator + Haiku classifier subagent for DART RSS + news
  This is where the Agent SDK becomes the architecture, not an add-on
  Deploy on Mac Mini (Korean IP) or Oracle VPS (Legs 1+3 only)

Phase 3+ → Narrative inconsistency
  Sonnet agent over full 사업보고서
  Batch API for cost control
  Deploy after Phase 3 validates the pipeline end-to-end

Phase 4 → Entity resolution
  Haiku Batch API subagent over officer holdings data
  Feeds directly into officer_network.py (networkx graph)
```

**Do not implement agents prematurely.** The Agent SDK adds value only after the
underlying Python data pipeline produces reliable inputs. Agents reasoning over bad
data produce confidently wrong outputs.

---

## Section 6 — Audit and Trust Boundaries

The Agent SDK's **hooks** system enables a full audit trail — required for
litigation-support use cases:

```python
async def log_claude_decision(input_data, tool_use_id, context):
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "tool": input_data.get("tool_name"),
        "input": input_data.get("tool_input"),
        "tool_use_id": tool_use_id,
    }
    with open("agent_audit.jsonl", "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {}

options = ClaudeAgentOptions(
    hooks={"PostToolUse": [HookMatcher(matcher=".*", hooks=[log_claude_decision])]}
)
```

Every classification, every tool call, every subagent invocation is logged with a
`tool_use_id` that ties back to the specific Claude API call. This is the provenance
chain that makes Layer 2 outputs defensible.

**Trust boundary rule (from `10_Multi_Agent_Architecture.md`):** Agents may read any
processed parquet and call any read-only Python tool. Agents may NOT directly write
to `01_Data/raw/` or modify pipeline configuration. All agent writes go to
`01_Data/processed/agent_outputs/` with the audit log entry alongside.

---

## Appendix — Related Documents

| Document | Relevance |
|---|---|
| `10_Multi_Agent_Architecture.md` | Conceptual 12-agent roster; design principles |
| `08_Continuous_Monitoring_System.md` | 3-way match architecture; Leg 2/3 trigger logic |
| `07_Automation_Assessment.md` | Won benchmark; automation ceiling; what Claude reliably does |
| `09_Claude_Cost_Optimization.md` | Batch API patterns; cache_control; model routing |
| `29_Railway_Infrastructure_Analysis.md` | Where the monitoring daemon runs (Railway/Mac Mini/VPS) |
