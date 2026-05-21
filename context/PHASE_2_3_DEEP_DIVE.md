# Phase 2 + 3 deep dive — how we used the new tech stack

This document is a beginner-friendly walkthrough of every step we took in
Phases 2 and 3 of the practice rewrite, written so a future reader who's
never touched Docker / Kubernetes / Helm / Keycloak can follow along.

**Scope:** the work between 2026-05-19 and 2026-05-20.

**Tech covered:** Docker, .dockerignore, multi-stage builds, container
registries, AWS ECR, Kubernetes (control plane vs workers, Pods,
Deployments, StatefulSets, Services, ConfigMaps, Secrets, Namespaces,
readiness probes, imagePullSecrets), `kind`, `kubectl`, Helm (charts,
templates, values, releases, install/upgrade/rollback), Keycloak (realms,
clients, users, JWTs, JWKS), NextAuth.js (Auth.js v5) with Keycloak
provider, PyJWT with PyJWKClient, FastAPI dependency injection for auth,
the BFF (Backend-for-Frontend) proxy pattern for binary content.

**How to use this doc:** read top-to-bottom once to get the mental
model, then come back to specific sections as a reference. The
"Gotchas log" near the bottom is the most useful section if you're
hitting an error and want to know if we already solved it.

---

## Mental models cheat sheet

Before the step-by-step, four mental models that everything else builds on:

### What's a container?

A **container** is a way to package a program plus everything it needs
to run (Python interpreter, libraries, your code) into one portable
bundle. You can hand the bundle to any machine with a container runtime
installed (Docker Desktop, containerd, etc.) and the program runs the
same way it ran on your laptop. No "works on my machine" problems
because the *machine* is part of the bundle.

The bundle, when frozen on disk, is called an **image**. When you run
it, the running instance is called a **container**. One image, many
containers. (Image = recipe, container = the cooked meal.)

A **Dockerfile** is the recipe text file that tells Docker how to build
the image, step by step.

### What's a container registry?

A registry is a private cloud-hosted file store for container images.
You `docker push` images to it; other machines `docker pull` them. AWS
ECR, Docker Hub, GitHub Container Registry (ghcr.io), Azure Container
Registry — all the same shape, different vendors.

Auth varies: ECR uses AWS IAM credentials traded for short-lived Docker
tokens; Docker Hub uses username/password.

### What's Kubernetes?

Kubernetes (K8s) is a system that runs containers across one or more
machines on your behalf and keeps them healthy. You tell it "run 3
copies of this image, expose them on port 8080, restart any that die,
let me roll out new versions without downtime" — it does the work.

A K8s cluster has two layers: the **control plane** (the brain, makes
all decisions, stores state) and the **worker nodes** (the muscles,
actually run containers). For local development we use **kind**, which
is "Kubernetes-in-Docker" — both layers fit into one Docker container
on your machine, and `kubectl` (the CLI) talks to it the same way it
would talk to a 100-node cloud cluster.

### What's an OIDC JWT and why do we care?

When you sign in to a service via Keycloak (or Google, or Microsoft),
Keycloak gives your browser a **JWT (JSON Web Token)** — a long
base64-encoded string in three parts separated by dots:
`header.payload.signature`.

- The **payload** is plain JSON describing who you are: your user ID,
  email, when the token expires.
- The **signature** is computed over header+payload using Keycloak's
  private RSA key. Only Keycloak has that key.

When your browser later calls a backend with this token in the
`Authorization: Bearer <token>` header, the backend verifies the
signature using Keycloak's **public** key (which Keycloak publishes at
a JWKS endpoint). If the signature checks out, the backend trusts the
claims in the payload without ever asking Keycloak again. Stateless,
fast, and cryptographically strong.

OIDC (OpenID Connect) is the standard wrapper around all of this —
defines the login flow, the URLs, the token shape, everything.

---

# Phase 2 — Container delivery pipeline

**Goal:** take the FastAPI app at `api-py/`, package it into a portable
container, run it on a local Kubernetes cluster, and publish the image
to AWS ECR so a real cluster could pull it.

**End state:**
- A Dockerfile + .dockerignore in `api-py/`
- Raw K8s manifests (Namespace, ConfigMap, Secret, Deployment, Service)
  in `api-py/k8s/`
- A hand-written Helm chart in `api-py/charts/parsely-api/` that
  produces the same K8s objects from a values file
- An ECR repository `parsely-api` hosting the image as tag `v1`
- A kind cluster pulling `parsely-api:v1` from ECR using an
  imagePullSecret

## Step 2.1 — Containerize the FastAPI service with Docker

### The big idea — multi-stage builds

A naive Dockerfile installs Python + your dependencies + your code on
one big image. You ship the whole thing. Result: bloated image (~700
MB), build tools and package indexes baked in, larger attack surface.

A **multi-stage** Dockerfile defines two (or more) build environments
inside one file. Each stage starts from its own base image. You can
`COPY --from=<stage>` files between stages. The final image is only
the *last* stage; everything before is discarded.

We use this so the **builder stage** has `uv` and the build toolchain
(which it needs to install Python deps), but the **runtime stage** is
plain Python with no build tools — just the pre-built virtualenv and
our app code. Result: ~80 MB compressed image (versus ~700 MB).

### Walking `api-py/Dockerfile`

```dockerfile
# syntax=docker/dockerfile:1.9
```
Lets us use modern Dockerfile features (cache mounts, bind mounts).

```dockerfile
FROM python:3.12-slim-bookworm AS builder
RUN pip install --no-cache-dir uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
```
Builder stage starts from official Python 3.12 slim image (Debian-based
~120 MB), installs `uv` (the fast Rust-based Python package manager),
sets uv env vars:
- `UV_COMPILE_BYTECODE=1` — precompile `.py` to `.pyc` so cold start
  is faster
- `UV_LINK_MODE=copy` — copy files instead of hardlinking (hardlinks
  break across stages)
- `UV_NO_DEV=1` — don't install dev-only deps
- `UV_PYTHON_DOWNLOADS=never` — don't let uv silently fetch a different
  Python

Why pip install uv? We originally tried `FROM ghcr.io/astral-sh/uv:...`
(the prebuilt image with uv already inside), but ghcr.io denied
anonymous pulls on this machine. Falling back to a plain Python image +
`pip install uv` sidesteps the registry, costs ~10s extra build time
once, and produces a byte-identical uv binary.

```dockerfile
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-install-project
```
The `--mount=type=cache` line is the layer-caching superpower:
- `type=cache` mounts a folder that persists *across builds*. uv's
  download cache lives at `/root/.cache/uv`. First build: downloads
  every dep from PyPI. Subsequent builds with the same `uv.lock`: pulls
  straight from the cache.
- `type=bind` mounts a file from the build context *without copying it
  into a layer*. We need `pyproject.toml` and `uv.lock` to run `uv sync`,
  but we don't want them baked into the image (their hash would
  invalidate downstream layers every time we edit them).

`--locked` makes uv fail if the lockfile is stale. `--no-install-project`
skips trying to install the project itself as a package (our pyproject
says `package = false`).

```dockerfile
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
```
App source last. **This ordering is the layer-caching trick:** deps
change rarely, code changes constantly. If we COPYed source first, every
code edit would invalidate the dep install layer and re-resolve every
dep. Putting source last means edits only re-run the COPY step.

```dockerfile
FROM python:3.12-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*
```
Runtime stage starts fresh. No uv, no build tools. We install **tini**
(a tiny init system, ~250 KB). In a container, the first process started
becomes PID 1 with special kernel-level responsibilities: forwarding
signals to children, reaping zombie processes. Python wasn't designed
for that role. Without tini, `docker stop` (SIGTERM) sometimes hangs
for 10s until escalated to SIGKILL. With tini, shutdowns are clean.

```dockerfile
RUN groupadd --system --gid 1001 app && useradd --system --uid 1001 --gid app --shell /sbin/nologin app
USER app
```
Create a non-root user and switch to them. Most production K8s clusters
refuse to run root containers via Pod Security admission policies. Bake
the constraint in now.

```dockerfile
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 5181
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5181"]
```
Bring the venv + source from the builder. Set `PATH` so the venv's
`uvicorn` resolves to the right binary. `PYTHONUNBUFFERED=1` is
critical for container logs — without it, Python buffers stdout and
`docker logs` shows nothing for minutes.

`ENTRYPOINT ["/usr/bin/tini", "--"]` runs tini as PID 1 which then
execs uvicorn. **Exec form (JSON array), not shell form** — without
the `sh -c` wrapper, SIGTERM reaches uvicorn directly.

`--host 0.0.0.0` is mandatory inside a container. The default
`127.0.0.1` only accepts traffic from inside the container, so
`docker run -p` would have nothing to connect to.

### `.dockerignore` — defense in depth

Same idea as `.gitignore`. When you `docker build .`, Docker tarballs
the entire current directory and sends it to the daemon as the "build
context". `.dockerignore` skips files. Critical entries for us:

- `.venv/` — your local venv is ~500 MB. The image builds its own.
- `.env`, `.env.*` — secrets. CLAUDE.md forbids reading them; this is
  double belt and suspenders.
- `__pycache__/`, `.pytest_cache/`, etc. — junk.

### Commands you'll use

```powershell
# Build
cd api-py
docker build -t parsely-api:dev .

# Run (smoke test against your local Neon)
docker run --rm -p 5181:5181 --env-file .env parsely-api:dev

# Inspect
docker images parsely-api:dev    # size, hash
docker logs <container-id>       # stdout/stderr from the running container
```

---

## Step 2.2 — Local Kubernetes with kind + raw manifests

### What Kubernetes actually does

A K8s cluster runs containers across machines for you, restarts them
when they die, replaces them when you change their spec, and gives them
stable network identities so other services can find them. You declare
the *desired state* ("3 copies of this image, with these env vars, on
port 5181") and K8s constantly reconciles reality to match.

### The control plane vs nodes

Every cluster has two layers:

```
CONTROL PLANE (the brain)
├── kube-apiserver — HTTP API everyone talks to
├── etcd — key-value DB holding ALL cluster state
├── kube-controller-manager — runs control loops (Deployment, ReplicaSet, ...)
└── kube-scheduler — picks which node a new Pod runs on

WORKER NODES (the muscles, can be 1 or thousands)
├── kubelet — agent that runs containers on this node
├── kube-proxy — programs the network for Services
└── containerd — actual container runtime
```

`kubectl apply -f mything.yaml` is just: kubectl reads your file,
POSTs it to the API server, API server validates + stores in etcd,
controllers notice, do their work, kubelets enact it on a node.

### Why kind, not EKS/GKE/AKS

EKS control plane alone costs ~$73/month. For practice we don't need
HA across regions. **kind** ("Kubernetes IN Docker") spins up the
entire cluster — control plane + one node — as a Docker container on
your laptop. From kubectl's perspective it's a real cluster. From
your laptop it's a Docker container. Zero cloud cost. Same K8s API.

Install: `winget install Kubernetes.kind`. Create cluster:
`kind create cluster --name parsely`.

### Core K8s primitives we used

| Primitive | What it is |
|---|---|
| **Namespace** | A folder inside the cluster. Resources in different namespaces don't see each other. We used `parsely`. |
| **Pod** | The smallest deployable unit. Usually wraps one container. Disposable — if it dies, K8s makes a new one with a new IP. |
| **Deployment** | "I want N copies of this Pod template, keep them alive, roll updates safely." You don't create Pods directly; you create Deployments which create Pods (via ReplicaSets). |
| **Service** | A stable virtual IP + DNS name in front of a set of Pods. Pods come and go; the Service stays. Lookup via label selector. |
| **ConfigMap** | Bag of non-secret key-value strings (URLs, feature flags). Mounted into Pods as env vars or files. |
| **Secret** | Like ConfigMap but for sensitive values. Base64-encoded on disk (not encrypted by default — fine for kind, not fine for prod without extra setup). |

### Walking `api-py/k8s/*.yaml`

**`namespace.yaml`** — three lines. Creates the `parsely` namespace so
we can `kubectl delete namespace parsely` to nuke everything later.

**`configmap.yaml`** — non-secret runtime config: `AZURE_DI_ENDPOINT`,
`AZURE_BLOB_CONTAINER`, `CORS_ALLOWED_ORIGINS`. These get injected as
env vars on every Pod that references this ConfigMap via `envFrom`.

**`deployment.yaml`** — the meaty file:
- `apiVersion: apps/v1, kind: Deployment` — controller type
- `spec.replicas: 1` — one Pod
- `spec.selector.matchLabels` — how the Deployment finds the Pods it
  owns (must match the template labels, immutable after create)
- `spec.template.spec.containers[0]` — the actual container spec:
  - `image: parsely-api:dev` — no registry prefix because the image
    was loaded directly into kind via `kind load docker-image`
  - `imagePullPolicy: IfNotPresent` — use cached image, don't try to
    fetch from a registry
  - `envFrom: [configMapRef, secretRef]` — inject every key in those
    objects as an env var
  - `readinessProbe: httpGet /health` — K8s hits /health every 10s
    before sending traffic; if it returns non-200, Pod is marked
    NotReady and traffic skips it
  - `resources.requests` — minimum CPU/memory needed to schedule
  - `resources.limits` — kill if exceeded

**`service.yaml`** — fronts the Pod with a stable name:
- `type: ClusterIP` — internal-only (kind doesn't have a real load
  balancer; we'll use `kubectl port-forward` to reach it)
- `selector: app.kubernetes.io/name: parsely-api` — finds Pods with
  this label
- `ports[0].port: 5181` — Service listens here
- `ports[0].targetPort: http` — forwards to the named port on the Pod
  (we named the container port `http` so renaming the number doesn't
  require Service edits)

**`create-secret.ps1`** — the Secret is NOT a YAML file because we
don't want sensitive values committed to git. The script reads your
local `api-py/.env`, parses the three secret keys (`DATABASE_URL`,
`AZURE_DI_KEY`, `AZURE_BLOB_CONNECTION_STRING`), and creates a K8s
Secret via `kubectl create secret generic --from-literal=`.

### The image-loading dance

```powershell
kind load docker-image parsely-api:dev --name parsely
```
This copies the image from your host's Docker into the kind node's
internal container cache (kind nodes have their own cache, separate
from `docker images`). Without this, the Deployment would try to pull
from a remote registry and get `ErrImagePull` because `parsely-api:dev`
doesn't exist on Docker Hub.

### Commands you'll use

```powershell
# Apply everything (skip the Secret — that's the script)
kubectl apply -f api-py/k8s/namespace.yaml
kubectl apply -f api-py/k8s/configmap.yaml
./api-py/k8s/create-secret.ps1
kubectl apply -f api-py/k8s/deployment.yaml
kubectl apply -f api-py/k8s/service.yaml

# Watch pods come up
kubectl get pods -n parsely -w

# Logs / inspect
kubectl logs -n parsely <pod-name>
kubectl describe pod -n parsely <pod-name>   # events, mounts, env, restart count

# Reach the Service from your laptop
kubectl port-forward -n parsely svc/parsely-api 5181:5181

# Nuke everything in the namespace
kubectl delete namespace parsely
```

---

## Step 2.3 — Helm chart for repeatability

### What Helm is, in plain English

Helm is a **templating system + release manager** for K8s manifests.
Two things it gives you:

1. **Templates.** The same chart deploys to dev/staging/prod with
   different values. Want 1 replica in dev, 5 in prod? One value
   override.
2. **Releases.** Every `helm install` is a tracked release with a
   number. `helm upgrade` does a rolling update. `helm rollback`
   reverts. `helm uninstall` deletes everything the chart created.

But the bigger win is **distribution**: complex K8s software (nginx,
cert-manager, Prometheus, Keycloak) ships as Helm charts. Two commands
deploy any of them with parameterized config. We benefit from that in
Phase 3 when we install Keycloak.

### Why hand-write the chart instead of `helm create`?

`helm create` scaffolds a ~12-file chart with placeholders for nearly
every K8s primitive ever. For a simple app it's 90% noise. Hand-writing
a chart means each file exists for a reason and the author understands
it.

### Walking `api-py/charts/parsely-api/`

```
Chart.yaml          chart metadata (name, version, appVersion)
values.yaml         default values (image, replicas, resources, ...)
.helmignore         files to skip when packaging
templates/
  _helpers.tpl      reusable template fragments (labels, names)
  configmap.yaml    parameterized ConfigMap
  deployment.yaml   parameterized Deployment (+ checksum/config trick)
  service.yaml      parameterized Service
```

Notably **not** in the chart:
- `Namespace` — Helm installs into a namespace specified at install
  time (`--namespace parsely --create-namespace`), not via a chart
  template.
- `Secret` — still external (created by `create-secret.ps1`). Keeps
  sensitive values out of the chart entirely. The chart references the
  Secret by name (`existingSecret: parsely-api-secrets`).

### Template language essentials

Helm uses Go's text/template engine. Inside `{{ }}`:

- `{{ .Values.image.tag }}` — read from values.yaml
- `{{ .Release.Name }}` — the release name you typed (`helm install
  parsely ...` → `.Release.Name = "parsely"`)
- `{{ .Chart.Name }}` — from Chart.yaml
- `{{ include "parsely-api.labels" . }}` — invoke a named template,
  passing `.` (the whole context) as the argument
- `{{- ... -}}` — the `-` strips whitespace on that side (essential to
  avoid blank lines in rendered YAML)
- `{{ ... | nindent 4 }}` — pipe through "newline + 4-space indent"
- `{{ ... | toYaml }}` — render a value as YAML (useful for nested
  blocks like `resources:`)

### `_helpers.tpl` — named templates

Files starting with `_` are not rendered into K8s manifests. They hold
**named templates** defined with `{{- define "name" }}...{{- end }}`
and invoked with `{{ include "name" . }}`. We use them for:

- `parsely-api.name` — chart name, possibly overridden, truncated to
  K8s's 63-char DNS label limit
- `parsely-api.fullname` — release name + chart name, used as the base
  name for every created resource
- `parsely-api.labels` — full label set including chart and app
  versions. Goes on every resource for queryability
  (`kubectl get all -l app.kubernetes.io/instance=parsely`)
- `parsely-api.selectorLabels` — minimal stable subset used in Service
  selectors and Deployment selectors. Selectors are **immutable** —
  K8s won't let you change them after creation. So selectorLabels
  excludes anything that changes on upgrade (version, chart-version).

### The `checksum/config` annotation

Inside `deployment.yaml`:

```yaml
template:
  metadata:
    annotations:
      checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
```

What this does: at template-render time, Helm computes the SHA-256 hash
of the rendered ConfigMap and writes it as an annotation on the Pod
template. **Why:** when you change a value in `values.yaml` and `helm
upgrade`, Helm updates the ConfigMap but K8s doesn't notice the
Deployment template needs to roll (the Deployment itself looks
unchanged). With the checksum annotation, any ConfigMap content change
changes the annotation, which forces a Pod template hash change, which
triggers a rolling restart automatically.

Without this trick, ConfigMap changes silently leave Pods running with
stale env vars. Classic gotcha.

### Commands you'll use

```powershell
# Render templates without applying (dry run, great for debugging)
helm template parsely api-py/charts/parsely-api

# Install a release named `parsely`
helm install parsely api-py/charts/parsely-api --namespace parsely

# Upgrade — change values, push a new image, etc.
helm upgrade parsely api-py/charts/parsely-api --namespace parsely --set replicaCount=2

# Roll back to a specific revision
helm rollback parsely 1 --namespace parsely

# What revisions exist and their status
helm history parsely -n parsely

# What values were used for the current release
helm get values parsely -n parsely

# Uninstall everything the chart created
helm uninstall parsely -n parsely
```

---

## Step 2.4 — Push the image to AWS ECR

### What ECR is

ECR (Elastic Container Registry) is AWS's private Docker registry.
Same API as Docker Hub — `docker push` and `docker pull` work — but
authenticated via your AWS IAM credentials instead of a separate
username/password.

Free tier: 500 MB private storage for one repo, 12 months. After that
~$0.10/GB/month. Push traffic is free; data transfer out of AWS costs
a tiny amount.

### The login dance

ECR doesn't accept long-lived passwords. Instead:

```powershell
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com
```

What happens:
- `aws ecr get-login-password` uses your AWS IAM creds to ask ECR for
  a token (a JWT-like blob, valid 12 hours).
- The pipe sends that token as the password to `docker login`.
- `--password-stdin` reads it from stdin so it never lives on the
  command line / in shell history.
- The username is the literal string `AWS` (hardcoded by ECR).

After this, `docker push` / `docker pull` to that ECR host works for
12 hours. Then you re-run the login.

### Tagging and pushing

ECR repo URIs follow `<account>.dkr.ecr.<region>.amazonaws.com/<repo>`.
A Docker image can have multiple tag names pointing at the same hash:

```powershell
# Create the ECR repo (one-time)
aws ecr create-repository --repository-name parsely-api --region us-east-1

# Tag the local image with the ECR URI
docker tag parsely-api:dev <account>.dkr.ecr.us-east-1.amazonaws.com/parsely-api:v1

# Push
docker push <account>.dkr.ecr.us-east-1.amazonaws.com/parsely-api:v1
```

Use deliberate version tags (`v1`, `v2`, …), not `:latest`. With
`:latest` you can never tell what's actually deployed.

### "Why are there three entries in ECR for one push?"

Modern Docker (Buildx, default in Docker Desktop 23+) creates a
**manifest list / image index** for every build, even single-platform
ones. The index is a tiny JSON blob pointing at the actual
platform-specific image. Buildx also adds an **attestation manifest**
(SBOM + provenance metadata for supply-chain scanning). One push →
three entries:

| Type | Size | Purpose |
|---|---|---|
| Image Index (tagged) | ~rolled-up | Points at the platform image. Pull-time clients use this to pick the right binary. |
| Image (untagged) | ~80 MB | The actual linux/amd64 container. |
| Image (untagged) | ~1.5 KB | Attestation (in-toto JSON SBOM + provenance). |

Confusingly, both the real image and the attestation manifest share
the same `artifactMediaType: application/vnd.oci.image.config.v1+json`.
The differentiator is **size** (81 MB vs 1.5 KB) and an annotation
buried inside (`vnd.docker.reference.type: attestation-manifest`).

To disable attestations: `docker build --provenance=false --sbom=false`.
We left them on — they're free, tiny, and useful for security scans.

---

## Step 2.5 — kind pulls from ECR via imagePullSecrets

### The architecture problem

After Step 2.4 the image lives in ECR. After Step 2.3 our Helm chart
points at a local `parsely-api:dev`. To close the loop, we update the
chart to pull from ECR. But the kind cluster has zero AWS credentials —
the kubelet inside the kind node will try to pull from ECR and hit
HTTP 401.

### The fix

K8s has a Secret type called `kubernetes.io/dockerconfigjson` that
holds registry credentials in the same format as `~/.docker/config.json`.
When a Pod references it via `spec.imagePullSecrets`, the kubelet uses
those creds when pulling the image.

```powershell
kubectl create secret docker-registry parsely-ecr-creds \
  --namespace parsely \
  --docker-server=<account>.dkr.ecr.us-east-1.amazonaws.com \
  --docker-username=AWS \
  --docker-password=<ecr-token>
```

### Three files we changed

**`api-py/charts/parsely-api/templates/deployment.yaml`** — added the
imagePullSecrets block, gated on a value so the default (kind-loaded)
case still works:

```yaml
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 8 }}
{{- end }}
```

The `{{- with X }}` block only renders if `X` is non-empty. Default
`imagePullSecrets: []` → block is skipped, no `imagePullSecrets:` key
in the rendered Pod spec. Override with a non-empty list → block
renders.

**`api-py/k8s/values-ecr.yaml`** — Helm values *overlay* file. Applied
on top of the chart's defaults via `helm upgrade ... -f
values-ecr.yaml`. Contents:

```yaml
image:
  repository: <YOUR_ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/parsely-api
  tag: v1
  pullPolicy: IfNotPresent

imagePullSecrets:
  - name: parsely-ecr-creds
```

**`api-py/k8s/refresh-ecr-secret.ps1`** — automates the
`kubectl create secret docker-registry ...` dance. Since the ECR token
expires every 12 hours, you re-run this script periodically. The script
calls `aws sts get-caller-identity` to resolve your account ID
automatically — no hard-coding.

### In a production EKS cluster you'd skip all this

EKS has **IRSA** (IAM Roles for Service Accounts). A Pod's Service
Account can be bound to an IAM role with ECR pull permissions, and the
kubelet uses *that* — no long-lived secret, no 12-hour refresh, no
script. We're not using EKS (cost), so manual refresh is the practice
substitute.

---

# Phase 3 — End-to-end authentication

**Goal:** add login + token-based authorization to the app. Users sign
in via Keycloak, get a JWT, and that token gets validated on every
FastAPI call.

**End state:**
- Keycloak running in the kind cluster, using a dedicated Neon database
  for its own state
- A `parsely` realm with a confidential `parsely-web` client and a test
  user (`alice`)
- Next.js wired via NextAuth.js v5 with the Keycloak provider; sign-in
  flow at `/api/auth/signin`, session cookie carries the Keycloak access
  token
- FastAPI validates incoming Bearer tokens against Keycloak's JWKS
  endpoint via PyJWT
- Every data router (documents, templates, aggregations, document_types)
  requires a valid token; `/health` and `/me` are public/test-only
- The browser embeds `/api/documents/{id}/file` via a Next.js BFF route
  handler that proxies to FastAPI with the user's token attached
  server-side (so the embed/iframe doesn't need to know about auth)

## Step 3.1 — Install Keycloak via Helm

### What Keycloak does

Keycloak is an **identity server**. One process that handles:
- Sign-in (with passwords, MFA, social/SAML/LDAP, ...)
- User storage (with hashed passwords, password resets, lockout)
- OAuth2 / OIDC token issuance
- Admin UI for managing realms / clients / users
- Role-based access control

Your apps don't store user passwords or implement login forms — they
redirect users to Keycloak, Keycloak handles the auth, your apps just
validate the resulting JWTs.

The company's actual deployment will use **Red Hat Build of Keycloak
(RHBK)** — upstream Keycloak + Red Hat support contract. Same APIs,
same env vars, same Helm chart structure. Practice on upstream, deploy
RHBK in prod.

### Why we used `codecentric/keycloakx`

The codecentric Helm chart is the most popular community chart and the
canonical way to deploy Keycloak on K8s outside of the (newer, more
complex) Keycloak Operator. Operator pattern uses CRDs to declaratively
manage realms/clients — closer to GitOps, but more pieces to learn.
For first contact with Keycloak on K8s, the plain Helm chart is right.

### Why a dedicated Neon database (not the chart's bundled Postgres)

The chart can bring its own in-cluster Postgres as a dependency, but
that DB dies when you delete the cluster. Using Neon (which the user
already has for the main app) means:
- Free (Neon free tier supports multiple databases per project)
- Persistent across cluster rebuilds
- Mirrors how prod actually works — in-cluster service talking to a
  managed cloud database

### Creating the Keycloak database in Neon

Two ways. The SQL editor approach is the most reliable across Neon UI
versions:

```sql
CREATE DATABASE keycloak;
```

Run that from Neon's SQL Editor (connected to any existing database in
the project — you can't `CREATE DATABASE` while connected to the same
DB).

### The critical detail: direct vs pooler endpoint

Neon exposes two hostnames per project:
- `ep-<id>.<region>.aws.neon.tech` — direct connection (real Postgres)
- `ep-<id>-pooler.<region>.aws.neon.tech` — pooled (PgBouncer in
  transaction mode)

For Keycloak, **use the direct endpoint.** Keycloak's startup runs
Liquibase migrations which use Postgres **advisory locks** and
**prepared statements**. PgBouncer in transaction mode breaks both.
You'd see weird Liquibase errors after an hour of debugging. Direct
endpoint is a normal Postgres connection — everything works.

### Walking `api-py/k8s/keycloak-values.yaml`

```yaml
image:
  repository: quay.io/keycloak/keycloak
  tag: "26.0"
```
Pin a specific Keycloak minor. The chart deploys whatever tag we
specify here. Bump deliberately.

```yaml
replicas: 1
```
Single replica. We're not clustering (that's the whole reason we use
`start-dev` below — see gotcha #2 in the gotchas log).

```yaml
cache:
  stack: custom
```
This was the key fix to the JGroups crash loop. The chart's default
`cache.stack` ("default") makes it mount a `cache-ispn.xml` config
file referencing the `jdbc-ping` JGroups discovery stack, which
Keycloak 26.5+ ships but the chart 7.1.x doesn't register correctly.
`cache.stack: custom` tells the chart NOT to mount that file. Combined
with `start-dev` below, Keycloak uses local-only cache and never tries
to load JGroups.

```yaml
command:
  - "/opt/keycloak/bin/kc.sh"
args:
  - "start-dev"
```
`start-dev` is Keycloak's "single-node development" mode. It
implicitly:
- Sets `--cache=local` (skips Infinispan clustering)
- Enables HTTP (no TLS required)
- Relaxes hostname validation
- Disables several production-only checks

Not for production. For our single-replica kind deploy, it's correct.

```yaml
dbchecker:
  enabled: false
```
Disables the chart's "wait for DB" init container. It sometimes races
Neon's serverless compute cold-start. Keycloak's own startup retries
the DB connection plenty.

```yaml
extraEnv: |
  - name: KC_DB
    value: postgres
  - name: KC_DB_URL
    value: "jdbc:postgresql://<neon-direct-host>/keycloak?sslmode=require"
  - name: KC_DB_USERNAME
    valueFrom:
      secretKeyRef:
        name: keycloak-db
        key: username
  - name: KC_DB_PASSWORD
    valueFrom:
      secretKeyRef:
        name: keycloak-db
        key: password
  - name: KC_BOOTSTRAP_ADMIN_USERNAME
    valueFrom:
      secretKeyRef:
        name: keycloak-admin
        key: username
  - name: KC_BOOTSTRAP_ADMIN_PASSWORD
    valueFrom:
      secretKeyRef:
        name: keycloak-admin
        key: password
```
We bypass the chart's structured `database:` block and set `KC_*` env
vars directly. Reason: the chart can't render `?sslmode=require` into
the JDBC URL (Neon requires SSL). The `KC_BOOTSTRAP_ADMIN_*` vars are
Keycloak 26's modern names for the initial admin user (older versions
used `KEYCLOAK_ADMIN_*`).

```yaml
health.enabled: true
metrics.enabled: true
```
Required — the chart's liveness/readiness probes hit `/health/ready`
and `/health/live`. These endpoints only exist when Keycloak's
management interface is enabled, which is what these flags do.

### The helper script

`api-py/k8s/create-keycloak-secrets.ps1` — prompts for the Neon DB
password and a Keycloak admin password (both as SecureString so they're
not echoed), then creates two K8s Secrets (`keycloak-db`,
`keycloak-admin`) via `kubectl create secret generic --from-literal=`.

### Install + verify

```powershell
helm repo add codecentric https://codecentric.github.io/helm-charts
helm repo update
./api-py/k8s/create-keycloak-secrets.ps1
helm install keycloak codecentric/keycloakx --namespace parsely -f api-py/k8s/keycloak-values.yaml

# Watch
kubectl get pods -n parsely -w

# Reach the admin console
kubectl port-forward -n parsely svc/keycloak-keycloakx-http 8080:80
# Browser → http://localhost:8080 → Administration Console
```

---

## Step 3.2 — Realm, client, and test user (Keycloak admin work)

### Realm vs client vs user

- **Realm** = a tenant. Has its own users, clients, roles. Resources
  in different realms can't see each other. The default `master` realm
  is for managing *Keycloak itself* — never put app users there.
- **Client** = an application that uses Keycloak for auth. Each app
  (or each app instance) gets one. The Next.js frontend is one client.
  FastAPI doesn't need its own client — it just *validates* tokens
  issued to other clients.
- **User** = a human (or service account) who can log in.

### Public vs confidential clients

When you create a client, Keycloak asks "Client authentication: ON or
OFF?"
- **OFF (public)** — no client secret. Used by true SPAs and mobile
  apps that can't keep a secret. Auth happens via PKCE (Proof Key for
  Code Exchange) instead.
- **ON (confidential)** — has a client secret. Used when a server can
  safely hold the secret.

**Next.js is a backend.** The auth-code-to-token exchange happens in
Next.js's server runtime (Auth.js v5's route handlers), not in the
browser. So `parsely-web` should be **confidential**. We initially set
it as public, then flipped it in Step 3.3 when wiring NextAuth.

### The /auth URL prefix

The codecentric chart 7.x sets `KC_HTTP_RELATIVE_PATH=/auth` for
backward compat with old Keycloak (pre-17 had `/auth/` in all URLs;
17+ dropped it). Modern Keycloak doesn't use the prefix. Our URLs end
up with it:

- Admin console: `http://localhost:8080/auth/admin/master/console/`
- Realm OIDC discovery: `http://localhost:8080/auth/realms/parsely/.well-known/openid-configuration`
- Token endpoint: `http://localhost:8080/auth/realms/parsely/protocol/openid-connect/token`
- JWKS: `http://localhost:8080/auth/realms/parsely/protocol/openid-connect/certs`

Could be removed by overriding the env var. Tolerable for the practice.

### Things to remember when configuring the client

In the Keycloak admin → `parsely` realm → Clients → `parsely-web`:
- **Client type:** OpenID Connect
- **Client authentication:** ON (confidential)
- **Standard flow:** enabled (= Authorization Code flow)
- **Direct access grants:** enabled (= password grant; useful for
  testing with curl/PowerShell)
- **Valid redirect URIs:** `http://localhost:3000/api/auth/callback/keycloak`
  (NextAuth's convention)
- **Valid post logout redirect URIs:** `http://localhost:3000`
- **Web origins:** `http://localhost:3000` (for CORS)

### The user trap

When you create a user and "Set password", **toggle Temporary OFF**
before saving. Otherwise Keycloak treats it as a one-time password and
forces an "Update Password" required action, which means `password`
grant requests fail with "Account is not fully set up." The fix if you
hit this: User → Details → remove all chips from "Required user
actions" → Save → re-set password with Temporary OFF.

### Direct password grant for testing

Once user exists, mint a token without going through the browser:

```powershell
$body = @{
  client_id     = "parsely-web"
  client_secret = 'YOUR_CLIENT_SECRET'    # confidential client requires this
  username      = "alice"
  password      = 'YOUR_PASSWORD'
  grant_type    = "password"
  scope         = "openid"
}
$tokenResp = Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8080/auth/realms/parsely/protocol/openid-connect/token" `
  -ContentType "application/x-www-form-urlencoded" `
  -Body $body
$token = $tokenResp.access_token
```

Paste `$token` into [jwt.io](https://jwt.io) to inspect. Required
fields: `iss = http://localhost:8080/auth/realms/parsely`, `azp =
parsely-web`, `preferred_username = alice`, `exp` ~5 min ahead.

---

## Step 3.3 — Wire Next.js login via NextAuth.js (Auth.js v5)

### Auth.js v5 design philosophy

NextAuth.js v5 (now branded "Auth.js") is convention-over-configuration.
The whole thing is two files for our use case.

### File 1: `web/auth.ts`

```typescript
import NextAuth from "next-auth";
import Keycloak from "next-auth/providers/keycloak";

declare module "next-auth" {
  interface Session {
    accessToken?: string;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string;
  }
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [Keycloak],
  callbacks: {
    async jwt({ token, account }) {
      if (account?.access_token) {
        token.accessToken = account.access_token;
      }
      return token;
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken;
      return session;
    },
  },
});
```

What's happening:
- **`Keycloak`** provider — automatically reads `AUTH_KEYCLOAK_ID`,
  `AUTH_KEYCLOAK_SECRET`, `AUTH_KEYCLOAK_ISSUER` from env. No config
  object needed.
- **`module declare`** blocks — TypeScript module augmentation.
  Extends Auth.js's built-in `Session` and `JWT` types with our
  `accessToken` field so the rest of the codebase is type-safe.
- **`callbacks.jwt`** — fires every time the session is retrieved.
  `account` is only populated on the initial sign-in callback (when
  Keycloak first hands us tokens). We capture the access_token onto
  our internal JWT (encrypted session cookie).
- **`callbacks.session`** — fires every time the session object is
  surfaced (server or client). We copy accessToken from JWT to Session
  so it's visible to callers.

`handlers` is the HTTP handler bundle. `auth()` is a server-side helper
to read the current session. `signIn` / `signOut` are server actions
for triggering those flows.

### File 2: `web/app/api/auth/[...nextauth]/route.ts`

```typescript
import { handlers } from "@/auth";
export const { GET, POST } = handlers;
```

Re-exports the handler functions under `/api/auth/*`. Auth.js handles
~10 sub-routes (signin, signout, callback, session, providers, csrf,
…) all behind this one wildcard route. The `[...nextauth]` syntax is
Next.js's catch-all dynamic route.

The destructuring (`const { GET, POST } = handlers`) is important — we
can't `export { GET, POST } from "@/auth"` because `auth.ts` exports
`handlers` as a single object, not individual functions.

### Environment variables in `web/.env.local`

```
AUTH_SECRET=<long random string>
AUTH_KEYCLOAK_ID=parsely-web
AUTH_KEYCLOAK_SECRET=<client secret from Keycloak admin>
AUTH_KEYCLOAK_ISSUER=http://localhost:8080/auth/realms/parsely
```

- `AUTH_SECRET` — used to encrypt the session cookie. Generate with
  `pnpm dlx auth secret` (writes it into `.env.local` automatically).
- `AUTH_KEYCLOAK_*` — three env vars the Keycloak provider auto-reads.
  No prefix override needed if you stick to these names.

**Next.js does not hot-reload env vars.** Restart `pnpm dev` after
editing `.env.local`.

### Where the token lives

After sign-in:
1. Keycloak returns an access_token (and a refresh_token + id_token).
2. Auth.js encrypts them and stores them in an HTTP-only cookie named
   `authjs.session-token` scoped to localhost:3000.
3. On every request, Auth.js can decrypt the cookie via `auth()`
   (server) or `useSession()` (client) to expose the session.
4. We surface `accessToken` on the session so the rest of our code can
   pull it out for backend API calls.

### Smoke test

Open `http://localhost:3000/api/auth/signin` → click Keycloak → log in
as alice → land back on localhost:3000. Then visit
`http://localhost:3000/api/auth/session` — JSON with `accessToken`
field. Same token claims as the direct password grant.

---

## Step 3.4 — FastAPI JWT validation

### The library: PyJWT

`pyjwt[crypto]` — the `[crypto]` extra pulls in `cryptography`, which
provides RSA primitives. Keycloak signs JWTs with RS256
(RSA-SHA256), so we need RSA verification.

PyJWT 2.6+ ships **`PyJWKClient`** — a built-in helper that fetches
JWKS from a URL, caches keys, and automatically refetches when a token
has a `kid` (key ID) header that's not in the cache. Saves us writing
the cache layer ourselves.

### `api-py/app/config.py` addition

```python
keycloak_issuer: str  # required env var

@property
def keycloak_jwks_url(self) -> str:
    return f"{self.keycloak_issuer.rstrip('/')}/protocol/openid-connect/certs"
```

The OIDC standard says JWKS lives at `<issuer>/protocol/openid-connect/certs`.
We derive it from the issuer rather than requiring it as a separate
env var. (When in-cluster FastAPI needs a different host for fetching
than for `iss` validation, we'll split this into two fields. For now,
local FastAPI sees both at `localhost:8080`.)

### `api-py/app/security.py` — the validator

The pipeline:
1. Extract `Authorization: Bearer <token>` from the request (FastAPI's
   `HTTPBearer` security scheme).
2. Hand the token to `PyJWKClient.get_signing_key_from_jwt(token)` —
   it reads the token's `kid` header and returns the matching key from
   the cached JWKS.
3. Call `jwt.decode(token, signing_key, algorithms=["RS256"],
   issuer=...)`. This:
   - Verifies the RS256 signature against the signing key
   - Verifies the `iss` claim matches our configured issuer
   - Verifies `exp` hasn't passed
4. Build a typed `CurrentUser` Pydantic model from the validated
   claims.
5. Convert exceptions to 401s with appropriate WWW-Authenticate
   headers.

### Why `verify_aud: False`

Keycloak's default `aud` (audience) claim for password-grant tokens is
the string `"account"` (the built-in account-management client). It's
not a useful security boundary — we'd be checking "the token is for
the account console" which doesn't help. The real security boundary
is `iss` (which we DO verify) plus the signature (which we DO verify).

If we ever set up explicit audience mappers in Keycloak (so tokens for
`parsely-web` have `aud: parsely-api`), we'd flip this on.

### The FastAPI dependency pattern

```python
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
```

Then route handlers use it like:

```python
@app.get("/me")
async def me(user: CurrentUserDep) -> CurrentUser:
    return user
```

FastAPI sees the `Depends(...)` in the type annotation, runs the
dependency before the handler body, injects the result. If the
dependency raises an HTTPException, FastAPI handles the 401 response
automatically — handler body never runs.

### CurrentUser is frozen

```python
class CurrentUser(BaseModel):
    model_config = ConfigDict(frozen=True)
    ...
```

Pydantic's `frozen=True` makes the model immutable. Once the dep
returns it, route handlers can't mutate the user object mid-request.
Prevents subtle bugs.

### Testing

Restart uvicorn after adding `KEYCLOAK_ISSUER` to `api-py/.env`. Three
scenarios:

```powershell
# No token → 401
curl http://localhost:5181/me

# Valid token → 200 with user info
Invoke-RestMethod -Uri "http://localhost:5181/me" `
  -Headers @{ Authorization = "Bearer $token" }

# Tampered token → 401
$bad = $token + "x"
Invoke-RestMethod -Uri "http://localhost:5181/me" `
  -Headers @{ Authorization = "Bearer $bad" }
```

The `/me` endpoint exists specifically to verify the dep before
applying it to real routes (Step 3.5).

---

## Step 3.5 — Protect the existing endpoints

### Two halves

1. **Server-side:** every data router gets `dependencies=[Depends(get_current_user)]`
   at the constructor level. Every endpoint inside the router now
   requires a valid Bearer token.
2. **Client-side:** every API call from Next.js attaches the
   `Authorization: Bearer <token>` header.

### Router-level vs endpoint-level dependencies

We chose router-level because we're not yet using the user object in
handlers (per-user data scoping is deferred to Phase 3.5+):

```python
router = APIRouter(
    prefix="/api/documents",
    tags=["documents"],
    dependencies=[Depends(get_current_user)],
)
```

The dep runs before every endpoint in the router. If validation fails,
the handler never runs. If we ever need the user object inside a
handler, we can ALSO add `user: CurrentUserDep` to that specific
endpoint — the dep is idempotent (FastAPI caches dependency results
per-request).

Applied to: `documents`, `templates`, `aggregations`, `document_types`.

Left public: `/health` (K8s probes), `/me` (already uses the dep
directly).

### The client-side wrapper

`web/lib/api-client.ts` was the single chokepoint — all 22 `fetch()`
calls live there. We added two helpers and replaced every call:

```typescript
let cachedToken: { value: string; expiresAt: number } | null = null;

async function getAccessToken(): Promise<string> {
  if (cachedToken && cachedToken.expiresAt > Date.now()) {
    return cachedToken.value;
  }
  // globalThis.fetch — calling authedFetch here would recurse
  const res = await globalThis.fetch("/api/auth/session", { cache: "no-store" });
  const session = await res.json();
  if (!session?.accessToken) throw new Error("Not authenticated");
  const expiresAt = session.expires
    ? new Date(session.expires).getTime() - 60_000
    : Date.now() + 60_000;
  cachedToken = { value: session.accessToken, expiresAt };
  return session.accessToken;
}

async function authedFetch(input, init) {
  const token = await getAccessToken();
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return globalThis.fetch(input, { ...init, headers });
}
```

How the token comes in: when a client component (the api-client is
called only from `"use client"` files) fetches `/api/auth/session`,
Next.js's NextAuth route reads the session cookie, decrypts it, and
returns the session JSON including `accessToken`. We cache it briefly
to avoid round-tripping per call.

Then `replace_all` swapped `await fetch(` → `await authedFetch(` in
api-client.ts. The deliberate `globalThis.fetch` inside the helpers
avoids the replace catching itself.

### Known limitation hit immediately

`fileUrl(id)` returns a plain URL string used in `<embed src=...>` for
PDF preview. Browser embeds can't attach custom headers. Once the
documents router required auth, the embed got 401. We fixed this in
Step 3.6.

---

## Step 3.6 — BFF proxy for binary content

### The fundamental problem

JWT auth works for AJAX/fetch calls (you control the headers). It does
NOT work for browser-driven loads of:
- `<embed src="...">`, `<iframe src="...">`, `<object data="...">`
- `<img src="...">`, `<video src="...">`, `<audio src="...">`
- `window.open(...)`, regular `<a href>` clicks
- Anything else the browser fetches from an attribute

Because: the browser doesn't let JS specify headers for those.

### Three solution shapes

| Approach | What it does | Trade-off |
|---|---|---|
| **A. Open the endpoint up** | Don't require auth on `/file`. Anyone with a doc UUID can read. | Simplest. But documents become effectively public if IDs leak. |
| **B. Signed URLs / one-time tokens** | Backend issues a short-lived signed URL per file fetch. Browser embeds the signed URL. | Common in S3-style setups. Extra round trip + extra endpoint to implement. |
| **C. BFF proxy** | Browser embeds a same-origin URL on the Next.js host. Next.js Route Handler reads session cookie, fetches FastAPI with Bearer, streams response back. | Token never leaves the server. Browser sees same-origin URL → cookies sent automatically. Production-shaped. |

We chose **C**. Most secure, production-shaped, and reuses our existing
NextAuth session.

### `web/app/api/documents/[id]/file/route.ts`

The proxy:

```typescript
import { auth } from "@/auth";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:5180";

const PASSTHROUGH_HEADERS = [
  "content-type", "content-length", "content-disposition",
  "accept-ranges", "content-range", "etag", "last-modified",
];

export async function GET(req, { params }) {
  // 1. Verify the user is signed in (server-side, via cookie)
  const session = await auth();
  if (!session?.accessToken) {
    return new Response("Unauthorized", { status: 401 });
  }

  const { id } = await params;

  // 2. Forward the browser's Range header — PDF viewers request byte ranges
  const upstreamHeaders = new Headers({
    Authorization: `Bearer ${session.accessToken}`,
  });
  const range = req.headers.get("range");
  if (range) upstreamHeaders.set("Range", range);

  // 3. Server-to-server fetch with the Bearer token
  const upstream = await fetch(`${API_BASE}/api/documents/${id}/file`, {
    headers: upstreamHeaders,
    cache: "no-store",
  });

  // 4. Stream the body back, preserving status (200/206) and headers
  const responseHeaders = new Headers();
  for (const name of PASSTHROUGH_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}
```

Key things to understand:

- **`auth()` from `@/auth`** is the server-side helper that reads the
  encrypted session cookie. Returns `null` if not signed in. We bail
  with 401 immediately — never hit FastAPI for an unauth'd request.
- **Range header forwarding** matters for PDFs. Browsers request
  documents in byte ranges (bytes 0-65535 first, then jump to bytes
  8M-9M for page 5, etc.). HTTP standard is `Range:` header + `206
  Partial Content` responses. Forwarding it through the proxy means
  byte-range support keeps working.
- **`cache: "no-store"`** on the upstream fetch — Next.js's data cache
  shouldn't hold onto a user's private document.
- **Streaming via `new Response(upstream.body, ...)`** — Node's fetch
  returns the body as a `ReadableStream`. Passing it into a new
  Response streams it through without buffering the whole file into
  memory. Important for big PDFs.
- **`PASSTHROUGH_HEADERS` allowlist** — we don't blindly copy all
  upstream headers because:
  - Hop-by-hop headers (Connection, Keep-Alive) shouldn't cross
  - Content-Encoding could double-up if Next.js re-encodes
  - Server identification headers leak info
  We copy: Content-Type (so browser knows it's a PDF), Content-Length
  (sizing), Content-Disposition (filename), Accept-Ranges /
  Content-Range (range support), ETag / Last-Modified (caching).

### The api-client.ts change

```typescript
// Before
export function fileUrl(id: string): string {
  return `${API_BASE}/api/documents/${id}/file`;
}

// After
export function fileUrl(id: string): string {
  return `/api/documents/${id}/file`;
}
```

Relative URL. The browser fetches it from the same origin (Next.js,
port 3000), which sends the session cookie automatically (no header
manipulation needed for cookies — they're tied to origin). Next.js
Route Handler picks it up, authenticates via cookie, proxies to
FastAPI on port 5181 with a Bearer header.

### Why this is the production pattern

The BFF (Backend-for-Frontend) shape solves three problems at once:
1. **Auth gateway** — single place where the token lives; the browser
   doesn't need to know how to attach it to non-fetch resources.
2. **Content shaping** — could trim/filter data on the way through.
3. **Network boundary** — in production the FastAPI service can be on
   a private network with only the Next.js BFF exposed publicly.

Same pattern scales to: image streams, video, file downloads,
server-sent events. Any case where the browser does the fetch but the
upstream wants a token.

---

# File index

Files we created or significantly changed in Phases 2 and 3.

## api-py/

- `Dockerfile` — multi-stage build (uv builder → slim runtime)
- `.dockerignore` — exclude .venv, .env, junk
- `pyproject.toml` — added `pyjwt[crypto]>=2.10`
- `app/config.py` — added `keycloak_issuer` + computed `keycloak_jwks_url`
- `app/security.py` — JWT validator + `CurrentUser` + `CurrentUserDep`
- `app/main.py` — added `/me` endpoint for testing the auth dep
- `app/routers/documents.py` — router-level auth dep
- `app/routers/templates.py` — router-level auth dep
- `app/routers/aggregations.py` — router-level auth dep
- `app/routers/document_types.py` — router-level auth dep

## api-py/k8s/

- `namespace.yaml` — Namespace
- `configmap.yaml` — ConfigMap (parsely-api-config)
- `deployment.yaml` — raw Deployment
- `service.yaml` — raw Service
- `create-secret.ps1` — creates parsely-api-secrets from .env
- `values-ecr.yaml` — Helm overlay pointing at ECR + pull secret
- `refresh-ecr-secret.ps1` — refreshes ECR auth Secret (12h cadence)
- `keycloak-values.yaml` — Helm overlay for keycloakx chart
- `create-keycloak-secrets.ps1` — creates keycloak-db + keycloak-admin Secrets

## api-py/charts/parsely-api/

- `Chart.yaml` — chart metadata
- `values.yaml` — defaults
- `.helmignore` — exclude junk when packaging
- `templates/_helpers.tpl` — named templates (labels, names)
- `templates/configmap.yaml` — templated ConfigMap
- `templates/deployment.yaml` — templated Deployment (with checksum/config + imagePullSecrets)
- `templates/service.yaml` — templated Service

## web/

- `auth.ts` — NextAuth.js (Auth.js v5) central config
- `app/api/auth/[...nextauth]/route.ts` — re-exports the auth handlers
- `app/api/documents/[id]/file/route.ts` — BFF proxy for FastAPI /file
- `lib/api-client.ts` — added `authedFetch` wrapper; all calls authed

## web/.env.local (not committed)

- `AUTH_SECRET`
- `AUTH_KEYCLOAK_ID`
- `AUTH_KEYCLOAK_SECRET`
- `AUTH_KEYCLOAK_ISSUER`

## api-py/.env (not committed)

- Existing: `DATABASE_URL`, `AZURE_*`
- Added: `KEYCLOAK_ISSUER`

---

# Gotchas log (lessons learned the hard way)

Listed in the order we hit them. If you re-encounter any of these,
this is the section to consult.

## 1. `ghcr.io` denying anonymous pulls

**Symptom:** `docker build` failed early with
`failed to fetch oauth token: denied: denied` against
`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`.

**Cause:** Docker Desktop on this machine sends stale / bad
credentials to ghcr.io, and unauthenticated pulls are rate-limited or
denied. Could be corporate network filter, could be a credential
helper misconfig.

**Fix:** stop using ghcr.io as the base image source. Switch to plain
`python:3.12-slim-bookworm` from Docker Hub and install uv inside.
Same end state, no ghcr dependency.

## 2. uv standalone installer needs curl/wget

**Symptom:** `RUN /uv-installer.sh && rm /uv-installer.sh` failed with
`ERROR: need 'curl or wget' (command not found)`.

**Cause:** The `python:3.12-slim` image ships neither curl nor wget.
The astral install script needs one of them internally.

**Fix:** `RUN pip install --no-cache-dir uv`. pip ships with the
slim Python image so we use it. uv installed via pip is byte-identical
to uv installed via the script.

## 3. Keycloak ISPN000540 / jdbc-ping JGroups stack

**Symptom:** Keycloak pod CrashLoopBackOff with
`ISPN000540: No such JGroups stack 'jdbc-ping'`.

**Cause:** Keycloak 26+ defaults to clustered Infinispan caching with
the jdbc-ping JGroups discovery stack. The codecentric chart 7.1.x
doesn't register that stack properly with Keycloak 26.5+. Even adding
`--cache=local` to args didn't help because the chart was mounting its
own `cache-ispn.xml` config file that references the missing stack.

**Fix:** two changes in `keycloak-values.yaml`:
- `cache.stack: custom` — tells the chart NOT to mount its cache config file
- `args: ["start-dev"]` — explicitly use development mode, which uses
  local cache only and bypasses all clustering code paths

For a single-replica practice deploy, both are correct anyway.

## 4. `helm upgrade` not propagating to StatefulSets

**Symptom:** We changed values, ran `helm upgrade`, but the pod kept
running with the old args. `kubectl get pod -o jsonpath` showed args
from before our changes.

**Cause:** Helm's diff against StatefulSet specs is sometimes
conservative — if it decides "nothing meaningful changed at the
template level" it skips rolling pods. Our value changes only updated
volume mounts (cache-ispn.xml removed) and args, which apparently
didn't trigger the rollout heuristic.

**Fix:** `helm uninstall keycloak -n parsely` + `helm install ...`.
For stateful workloads with external state (Neon held all the data),
this is cheap. For data-bearing in-cluster workloads, you'd
`kubectl rollout restart sts/<name>` after the upgrade.

**Lesson:** when iterating on StatefulSet values, prefer
`helm upgrade && kubectl rollout restart sts/<name>` or just nuke +
reinstall.

## 5. `/auth` URL prefix surprise

**Symptom:** `http://localhost:8080/realms/parsely/.well-known/openid-configuration`
returned "Resource not found" but `http://localhost:8080/auth/realms/...`
worked.

**Cause:** codecentric chart 7.x defaults `KC_HTTP_RELATIVE_PATH=/auth`
for backward compat with old Keycloak (pre-17 had `/auth/` everywhere;
17+ removed it). The chart honors the old default.

**Fix:** accept the prefix in all our URLs. Could override the env
var to `/` if we wanted modern Keycloak shape, but not worth another
helm upgrade cycle for a cosmetic change.

## 6. NextAuth `export GET doesn't exist`

**Symptom:** Turbopack build error,
`Export GET doesn't exist in target module ./auth`.

**Cause:** I wrote `export { GET, POST } from "@/auth"` in the route
handler, but `auth.ts` exports `handlers` (a single object containing
.GET and .POST). The named re-export only works if the names exist
at the top level of the source module.

**Fix:** `import { handlers } from "@/auth"; export const { GET, POST } = handlers;`
The destructuring is the canonical Auth.js v5 pattern.

## 7. `unauthorized_client: Invalid client or Invalid client credentials`

**Symptom:** PowerShell password-grant request failed with that error.

**Cause:** We'd flipped `parsely-web` to a confidential client in Step
3.3 to support NextAuth. Confidential clients require `client_secret`
in token requests; public clients don't.

**Fix:** add `client_secret = '<secret>'` to the request body.

## 8. `Account is not fully set up`

**Symptom:** password grant returned this error even though the
password was correct.

**Cause:** When we created `alice` in Keycloak, the password was set
with **Temporary: ON** (the default toggle). Keycloak adds an "Update
Password" required action, which blocks token issuance until the user
goes through a UI flow to set a permanent password.

**Fix:** in Keycloak admin → Users → alice → Details → remove all
chips from "Required user actions" → Save. Then Credentials tab → if
Temporary still says Yes, re-set the password with Temporary toggled
OFF.

## 9. PDF preview returning 401 after protecting `/file`

**Symptom:** After Step 3.5, in-browser PDF preview broke. The Network
tab showed a 401 on `/api/documents/{id}/file`.

**Cause:** The browser fetches `/file` via `<embed src=...>`. Browsers
don't let JS attach custom headers to embed/iframe src loads. Our
`authedFetch` wrapper doesn't help — embed loads bypass JS entirely.

**Fix:** BFF proxy. `web/app/api/documents/[id]/file/route.ts` reads
the session cookie server-side, refetches FastAPI with a Bearer header,
streams the result back. Browser sees a same-origin URL → cookie sent
automatically → no header manipulation needed.

---

# Commands cheat sheet

## Docker

```powershell
docker build -t parsely-api:dev api-py        # build
docker run --rm -p 5181:5181 --env-file api-py/.env parsely-api:dev   # run
docker images parsely-api:dev                  # info
docker logs <container>                        # logs
```

## kind

```powershell
kind create cluster --name parsely             # create
kind load docker-image parsely-api:dev --name parsely   # load image
kind delete cluster --name parsely             # delete
```

## kubectl

```powershell
kubectl get pods -n parsely -w                 # watch pods
kubectl describe pod -n parsely <pod>          # full state, events
kubectl logs -n parsely <pod> --previous       # previous crash logs
kubectl port-forward -n parsely svc/parsely-api 5181:5181
kubectl exec -it -n parsely <pod> -- /bin/sh   # shell into pod
kubectl rollout restart sts/<name> -n <ns>     # force pod recreate
kubectl delete namespace parsely               # nuke everything
```

## Helm

```powershell
helm install <release> <chart> -n <ns> -f values.yaml
helm upgrade <release> <chart> -n <ns> --set key=value
helm rollback <release> <rev> -n <ns>
helm history <release> -n <ns>
helm get values <release> -n <ns>
helm get manifest <release> -n <ns>            # full rendered YAML
helm template <release> <chart> -f values.yaml # render without applying
helm uninstall <release> -n <ns>
helm show values <chart>                       # default values
```

## AWS / ECR

```powershell
aws sts get-caller-identity                    # who am I, account ID
aws ecr create-repository --repository-name parsely-api --region us-east-1
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <acct>.dkr.ecr.us-east-1.amazonaws.com
aws ecr list-images --repository-name parsely-api --region us-east-1
aws ecr describe-images --repository-name parsely-api --region us-east-1 --output table
```

## Keycloak token via curl/PowerShell

```powershell
$body = @{
  client_id = "parsely-web"
  client_secret = '<secret>'
  username = "alice"
  password = '<password>'
  grant_type = "password"
  scope = "openid"
}
$tokenResp = Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8080/auth/realms/parsely/protocol/openid-connect/token" `
  -ContentType "application/x-www-form-urlencoded" `
  -Body $body
$tokenResp.access_token
```

## Service paths to remember

| What | URL |
|---|---|
| Keycloak admin console | `http://localhost:8080/auth/admin/master/console/` (via port-forward) |
| OIDC discovery | `http://localhost:8080/auth/realms/parsely/.well-known/openid-configuration` |
| Token endpoint | `http://localhost:8080/auth/realms/parsely/protocol/openid-connect/token` |
| JWKS | `http://localhost:8080/auth/realms/parsely/protocol/openid-connect/certs` |
| NextAuth sign-in | `http://localhost:3000/api/auth/signin` |
| NextAuth session | `http://localhost:3000/api/auth/session` |
| FastAPI /me (test) | `http://localhost:5181/me` |
| FastAPI /health (no auth) | `http://localhost:5181/health` |

---

# Citations — docs we consulted (via context7)

Everything in this document was cross-checked against current official
docs at session time (May 2026). If you re-verify later, these are the
sources that mattered:

- **uv** Docker patterns — `astral-sh/uv-docker-example` (canonical
  multi-stage pattern, `--mount=type=cache` for build cache)
- **kind** — `kubernetes-sigs/kind` (install, create cluster, kind load
  docker-image)
- **Kubernetes** — `kubernetes/website` (Deployment / Service / ConfigMap
  / Secret manifests, envFrom secretRef, readiness probes)
- **Helm** — `helm/helm-www` (Chart.yaml apiVersion v2, named templates
  in _helpers.tpl, .Values / .Release / .Chart objects, checksum/config
  pattern)
- **Keycloak** — `keycloak/keycloak` (`KC_*` env vars, `KC_BOOTSTRAP_ADMIN_*`,
  start-dev / start, --cache=local vs jdbc-ping, JGroups stack
  configuration)
- **codecentric/keycloakx chart** — official values.yaml on GitHub
  (`cache.stack: custom`, `database:` block vs `extraEnv` for SSL JDBC URLs)
- **NextAuth.js (Auth.js v5)** — `nextauthjs/next-auth` (Keycloak
  provider, JWT/session callbacks, App Router handler pattern,
  environment variables, exposeAccessToken pattern)
- **BuildKit attestations** — `moby/buildkit` (attestation manifest
  format, why we see three entries per push in ECR)

For Phase 4 (OpenTofu), check `hashicorp/terraform` and the Neon
Terraform provider docs.
