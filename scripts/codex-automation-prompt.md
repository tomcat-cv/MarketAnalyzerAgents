Run the daily research briefing pipeline in this workspace.

Use the project rules in AGENTS.md. Generate today's brief with:

```bash
PYTHONPATH=src python3 -m dailyresearch run --backend zhipu
```

If ZHIPU_API_KEY is unavailable, use the Codex summary backend:

```bash
PYTHONPATH=src python3 -m dailyresearch run --backend codex
```

Both commands must use the project's independent collectors and Verified Evidence Pack.
Do not browse/search from the model backend. Report the generated HTML path, evidence
window, counts by level, configured holdings, and any collection warnings. Confirm that
the brief contains the three required market/holdings/action sections. Do not use Claude or n8n.
