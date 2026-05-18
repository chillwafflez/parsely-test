# CLAUDE.md — Document Parsing Service

All-in-one document parsing SaaS with a **correction + save-as-template** workflow. User uploads a document, Azure AI Document Intelligence extracts fields, user fixes mistakes inline or by drawing boxes over missed regions, then saves those corrections as a template that auto-applies to future uploads of the same layout. Prototype built for a demo on **~2026-05-29** (extended one month from 2026-04-29 on 2026-04-23). Owner: `marketing@taia.us` (Microsoft-ecosystem preference, C#/.NET + Azure).

## Read order for a new session

1. **This file** — rules and commands.
2. **`context/PRACTICE_PROJECT.md`** — *why* this repo is being rewritten. The developer is practicing their company's incoming tech stack (Neon, FastAPI, K8s, Keycloak, Flutter, OpenTofu, …) on this hackathon project. Read this to understand the goal + architecture decisions + phased plan before anything else makes sense.
3. **`context/PROJECT_CONTEXT.md`** — full architecture, day-by-day build log, gotchas, and roadmap for the original .NET app. Behavioral source of truth for the port.
4. **`memory/MEMORY.md`** — Claude Code's local per-machine memory cache (lives at `~/.claude/projects/<slug>/memory/`, not in the repo). Mirrors `context/PRACTICE_PROJECT.md`; the repo doc wins on conflict.
5. Code.
6. Feature-specific design docs in `context/`, loaded only when you're working on that feature:
   - `context/FASTAPI_PORT.md` — **Practice-rewrite state.** FastAPI/Pydantic/Neon replica of the .NET backend at `api-py/`. Read this BEFORE touching `api-py/` — captures gotchas the code alone won't reveal.
   - `context/VOICE_FEATURE.md` — Voice-fill feature implementation brief (Phases 1–4 shipped 2026-04-22 → 2026-04-23).
   - `context/TEMPLATES_PAGE.md` — Templates management surface (index page + edit page + duplicate). Frozen 2026-04-23, **not yet implemented**.

If `PROJECT_CONTEXT.md` and this file disagree, `PROJECT_CONTEXT.md` wins — it's the living document.

## Stack

### Backend — `api/`
- .NET 10 / ASP.NET Core 10 Web API (Controllers, not Minimal APIs)
- EF Core 10 + `Microsoft.EntityFrameworkCore.Sqlite`, `Database.EnsureCreated()` (no migrations yet)
- `Azure.AI.DocumentIntelligence` 1.0.0, `prebuilt-invoice` model
- SQLite at `api/app.db`, uploads at `api/uploads/` (both gitignored)
- Secrets via `dotnet user-secrets` — never `appsettings.json`

### Frontend — `web/`
- Next.js 15 (App Router) + React 19, TypeScript strict
- Tailwind v4 (`@theme` in `app/globals.css`, no `tailwind.config.ts`) + CSS Modules for complex component styles
- `react-pdf@^9.2.1` + `pdfjs-dist@^4.8.69` — **do not upgrade** to 10.x / 5.x (see Gotchas)
- `lucide-react` icons, `clsx` via `lib/cn.ts`
- **pnpm** only — `pnpm-lock.yaml` is source of truth

## How to run

Two terminals from repo root:

```bash
# API on http://localhost:5180
cd api && dotnet run

# Web on http://localhost:3000
cd web && pnpm dev
```

First-time setup:

```bash
cd api && dotnet restore
cd ../web && pnpm install
```

Verify secrets are set (don't print the key value):

```bash
cd api && dotnet user-secrets list
# expect: DocumentIntelligence:Endpoint = https://taia-ams-docai.cognitiveservices.azure.com/
#         DocumentIntelligence:Key      = <rotated value>
```

If missing, ask the user to run `dotnet user-secrets set "DocumentIntelligence:Key" "..."` themselves. Do not request the key value in chat.

Smoke test: upload `samples/sample-invoice.pdf` in the UI → review stage with aligned bboxes.

## Non-negotiables

- **Never read `.env*` files.** Not `.env`, not `.env.local`, not via `grep`/`cat`/Read tool/anything. Secrets live there. See `memory/feedback_no_env_reads.md`.
- **Never accept a credential in chat.** If a key, connection string, or token appears in any message (user's or a file you read), stop, flag it, and tell the user to rotate. Don't echo it back in tool calls or summaries.
- **Never auto-commit.** The user runs `git commit` / `git push` themselves unless they explicitly delegate. Surface a proposed message; let them execute.
- **Never run `npm install` in `web/`.** pnpm only. If `package-lock.json` appears, delete it — someone ran `npm` by mistake.
- **Don't re-add Fluent UI.** Rejected on Day 2 (Griffel shorthand conflicts + design fit). Tailwind v4 + CSS Modules is the answer.
- **Don't "upgrade" `react-pdf` to 10.x** or `pdfjs-dist` to 5.x without an explicit ask. The downgrade is load-bearing (see Gotchas).
- **Don't `use client` everything.** Keep the SSR-skip boundary at `PdfDocumentView` only; other client components nest inside the already-client `ReviewStage` tree.

## Engineering style

- **Prototype-grade, not toy-grade.** User wants clean, readable, well-structured code following industry best practices. Prototype tradeoffs are OK when documented as `// TODO:` with a reason.
- **Don't over-engineer.** No speculative abstractions, no feature flags for hypothetical futures, no tests unless asked. Three similar lines beats a premature helper.
- **Don't add error handling for impossible states.** Validate at boundaries (HTTP inputs, Azure SDK responses); trust internal calls.
- **Comments explain WHY, not WHAT.** Only write one when the rationale is non-obvious (hidden constraint, subtle invariant, workaround for a specific bug). Otherwise let well-named identifiers speak.
- **No markdown docs for tasks you just did.** PROJECT_CONTEXT.md is where persistent context lives — don't drop ad-hoc `NOTES.md` / `CHANGES.md` files.

## Known gotchas (from `context/PROJECT_CONTEXT.md` §7)

Short list; full context and fixes are in PROJECT_CONTEXT.md.

- **`react-pdf@10` / `pdfjs-dist@5` breaks under Next.js 15 + Webpack** — `Object.defineProperty called on non-object` at module init. Fix: stay on `react-pdf@^9.2.1` + `pdfjs-dist@^4.8.69`. Don't add `transpilePackages` for these — it makes it worse.
- **Bbox alignment requires `page.getViewport({ scale: 1 }).width`**, not `page.width` from the `onLoadSuccess` callback (which is rendered px, not native points). Math lives in `web/lib/bbox.ts` (`polygonToPercentBBox`); don't re-derive.
- **pdf.js worker: use the CDN URL**, not `import.meta.url`. Brittle under Next 15.
- **pnpm hoist for pdfjs-dist** — `web/.npmrc` has `public-hoist-pattern[]=*pdfjs-dist*`. Do not remove.
- **EF Core: build the full entity graph before a single `SaveChangesAsync`.** Calling `SaveChangesAsync` twice (once on `Analyzing`, once with extracted fields) triggers `DbUpdateConcurrencyException`.
- **`DocumentFieldType` is an extensible-enum struct, not a C# enum.** No `switch` case labels. Use `if (type == DocumentFieldType.X)` chains.
- **Next.js config cache is sticky.** After changing `next.config.ts`, stop dev and `rm -rf web/.next`.

## Commands Claude commonly runs (no approval needed — see `.claude/settings.json`)

- `pnpm install`, `pnpm dev`, `pnpm build`, `pnpm lint`
- `dotnet restore`, `dotnet build`, `dotnet run`, `dotnet test`, `dotnet user-secrets list`
- `dotnet ef migrations *`, `dotnet ef database update` (if we add migrations)
- `git status`, `git diff`, `git log`, `git show`, `git branch`, `git remote -v`, `git ls-remote`, `git blame`, `git rev-parse`

Destructive / shared-state commands (`git push`, `git commit`, `git reset --hard`, `rm -rf` anywhere outside `.next`/`bin`/`obj`, anything touching Azure resources) always require confirmation.

## Using context7

The user frequently ends prompts with `use context7` and genuinely means it: verify library-specific syntax against current docs before writing code, especially for Next.js, React, react-pdf, Azure SDKs, and EF Core. Training-era knowledge is often a minor version behind. Don't use context7 for general programming, refactoring, or internal business logic.

## Where we are

Days 1–11 feature work complete (core parse→correct→template loop, URL routing, history page, Voice-Fill feature, matured PDF export). Day 9 Tailwind migration is still partial — `bounding-box-overlay`, `inspector/*`, and `app/page.module.css` remain on CSS Modules. Next up: Templates management surface — full design spec in `context/TEMPLATES_PAGE.md`, not yet implemented. Full roadmap in `PROJECT_CONTEXT.md` §6 and §11.

Design reference is the read-only bundle at `Document Parsing Service Prototype-handoff/` — treat it as a spec, don't modify it.
