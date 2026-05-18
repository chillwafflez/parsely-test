# Practice project — goal, architecture, and phased plan

This is the **why** behind everything in this repo. The original .NET app at `api/` is the source-of-truth spec; everything else (the FastAPI port at `api-py/`, the upcoming Docker/K8s work, the future Flutter companion) exists to give the developer hands-on practice with their company's new tech stack.

This file is the **canonical** statement of the project's purpose. Claude Code's auto-memory at `~/.claude/projects/<slug>/memory/` is a per-machine cache of the same information — fast to recall in-session, but local-only. **If the two diverge, this file wins.**

---

## Goal

The developer's company is adopting: **Neon, ECR, Keycloak RHBK, Flutter, FastAPI, Pydantic, OpenTofu, Kubernetes, Helm, Docker.** The company hasn't shipped any of these to production yet — this practice gets the developer ahead of the curve before the rollout hits. The hackathon project (originally .NET / Next.js / TypeScript + Azure Document Intelligence) is the substrate.

**Constraints:**

- **Self-funded.** Free tiers only — Neon free, AWS free tier for ECR, Azure free tier for Document Intelligence (already wired). Local K8s (`kind` / `k3d`) — **no managed K8s** (EKS ≈ $73/mo control plane is off the table).
- **Proof-of-concept, not production parity.** Cutting scope to learn the pattern is fine.
- **Cost target: < $2/month total spend.**

**Developer background:** strong with C# / .NET / Next.js / TypeScript. Limited Python; zero Flutter / K8s / Helm / OpenTofu / Keycloak experience at the start. Comfortable with the underlying *concepts* (containers, auth, IaC) but new to the specific tools — pitch explanations accordingly.

---

## Architecture decisions

**Frontend:** keep the existing Next.js 15 app as-is. **No** Flutter rewrite of the main UI.

**Flutter scope:** a *separate, small* mobile companion app — camera → upload → view extracted fields. ~3 screens. Practice scope, not feature parity with the web UI.

**Backend rewrite:** C# / .NET → **FastAPI + Pydantic + SQLModel** (Python).

**Persistence:** SQL Server → **Neon Postgres**.

**Auth:** Keycloak RHBK (added — the current app has no auth). Validate JWTs in the Next.js BFF *and* in FastAPI.

**Microservices split — Option A** (BFF inside Next.js, no separate gateway service):

| Deployable  | Role |
|-------------|------|
| **web**     | Next.js 15 — UI + BFF route handlers under `app/api/*` |
| **api**     | FastAPI — documents, templates, extraction, aggregations, (voice deferred) |
| **keycloak**| RHBK Helm chart |

**Why this split:**

- Documents + templates are **one bounded context.** Templates *are* saved field rules from documents — shared transaction boundary, shared data. Splitting them would force either a shared-DB anti-pattern or chatty cross-service calls.
- With only one downstream backend, a dedicated gateway service is theatrical. Next.js's `app/api/*` route handlers are the BFF.
- Future split point: when adding a 2nd backend (e.g. a voice service), promote the BFF into a real gateway.

**How to apply this when asked about cross-cutting concerns** (auth, request shaping, service-to-service calls): assume Option A is in effect. Don't suggest a separate gateway service until there's a 2nd backend.

---

## Phased plan

Each phase is sized to teach **one new tool at a time**. Don't bleed work across phases (no Helm before Phase 2; no Keycloak before Phase 3).

1. ✅ **Phase 1 — FastAPI + Neon backend.** Port the .NET API to FastAPI; swap SQL Server for Neon Postgres. Frontend keeps pointing at localhost. **Complete.** See `context/FASTAPI_PORT.md` for current state, gotchas, and quick commands.
2. ⏭ **Phase 2 — Dockerize + local K8s.** Dockerfile for the FastAPI app, Helm chart, run on `kind` or `k3d`. Push images to AWS ECR. **No EKS** (see cost target).
3. **Phase 3 — Keycloak + auth.** Keycloak in the same cluster via the official Helm chart. `/login` flow + JWT validation in Next.js BFF and FastAPI.
4. **Phase 4 — OpenTofu.** Codify the Neon project, ECR repo, and Keycloak realm / client. Tear-down + re-apply as the practice exercise.
5. **Phase 5 — Flutter mobile companion.** Separate small app — camera → upload → view fields. Three screens, mobile-first.

(Optional **Phase 1.5** — port the `voice/*` router from the .NET side if the user wants full feature parity. Two endpoints. Needs Azure Speech REST + OpenAI Python SDK. Currently deferred.)

---

## When asked "what's next"

Anchor to the **current phase** above. Phase 1 is done; Phase 2 is the next milestone. Don't propose work from later phases unless the user explicitly asks.

---

## Pointers

- `CLAUDE.md` — rules for working in this repo (read first every session)
- `context/PROJECT_CONTEXT.md` — original .NET project spec (behavior reference for the port)
- `context/FASTAPI_PORT.md` — current state of the Phase 1 FastAPI port (gotchas, parity decisions, commands)
- `~/.claude/projects/<slug>/memory/` — Claude Code's local auto-memory (per-machine cache; this file overrides it on conflict)
