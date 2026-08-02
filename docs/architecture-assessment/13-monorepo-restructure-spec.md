# 13. Dist-Stack Monorepo Restructure Spec

**Status:** Implementation-ready design (oracle-verified against all 5 repos' source).
**Date:** 2026-08-01

---

# Dist-Stack Monorepo — Restructure Spec

**Scope:** consolidate `gdm-stack`, `dist-stack`, `dist-workflow-runner`, `dist-kg`, `dist-dashboard` into one Python monorepo rooted at `/home/aadillatif/Documents/GitHub/dist-stack`.

**Verified facts:**

| Repo | Branch | Dist name | Version | Tests | Key content |
|---|---|---|---|---|---|
| gdm-stack | main | — | — | — | `docs/architecture-assessment/01–12`, `docs/mcp-wiring.md`, `opencode.json` (`.opencode/` untracked) |
| dist-stack | master | `dist-stack-model-registry` | 0.1.0 (dynamic, `src/dist_stack/version.py`) | 167 | 4 store packages + `mcp/` helpers, Jupyter Book in `docs/` |
| dist-workflow-runner | master | `dist-workflow-runner` | 0.1.0 | 71 | `workflow_runner/`, `workflows/*.json` (2), `servers.yaml.example` |
| dist-kg | master | `dist-kg` | 0.1.0 | 56 | `kg_server/` incl. vendored `gdm_client.py` |
| dist-dashboard | main | — (requirements.txt) | — | 15 | `app.py`/`data.py`/`styles.py`, `.streamlit/config.toml` |

Python 3.12.3; uv 0.12.1 installed. Total test surface: **309**.

---

## A. Workspace strategy — `uv workspace`

One root venv, one lockfile, one `uv sync`, per-package `pyproject.toml` (hatchling), four installable members. Rationale: per-package PyPI identity preserved (`dist-stack-model-registry`, `dist-workflow-runner`, `dist-kg` keep names/versions); uv is installed and workspace resolution is native (`{ workspace = true }`); one `uv run pytest` tests everything; dashboard + root are virtual members (`[tool.uv] package = false`); charter preserved (dist-stack keeps zero *required* runtime deps, `mcp` as an optional extra).

Versioning: independent per-package versions; intra-monorepo deps `{ workspace = true }`. Publish checklist swaps workspace pins → `>=0.1,<1` (uv rejects workspace pins at publish).

---

## B. Target directory layout

```
dist-stack/
├── pyproject.toml                                 # uv workspace root (virtual)
├── uv.lock
├── .python-version                                # "3.12"
├── .gitignore                                     # merged
├── README.md                                      # monorepo overview
├── opencode.json                                  # ← gdm-stack (paths rewired)
├── servers.yaml.example                           # ← dist-workflow-runner (kept at root)
├── .github/workflows/ci.yml                       # [NEW] greenfield CI
├── docs/                                          # Jupyter Book root (was dist-stack/docs)
│   ├── _config.yml  _toc.yml  intro.md  quickstart.md
│   ├── registry.md  manifest.md  runstore.md  kg.md
│   ├── conventions.md  ecosystem.md  references.md
│   ├── examples/provenance_walkthrough.ipynb
│   ├── architecture-assessment/                   # ← gdm-stack (docs 01–13, verbatim)
│   └── mcp-wiring.md                              # ← gdm-stack (paths rewired)
├── packages/
│   ├── dist-stack-model-registry/                 # was: dist-stack repo
│   │   ├── pyproject.toml                         # name unchanged; +optional mcp extra
│   │   ├── README.md
│   │   ├── src/dist_stack/
│   │   │   ├── __init__.py  version.py
│   │   │   ├── registry/  manifest/  runstore/  kg/
│   │   │   └── mcp/ (__init__.py serialization.py CONVENTIONS.md client.py[NEW])
│   │   └── tests/
│   ├── dist-workflow-runner/
│   │   ├── pyproject.toml                         # dep → { workspace = true }
│   │   ├── src/workflow_runner/  workflows/  tests/
│   └── dist-kg/
│       ├── pyproject.toml                         # dep → { workspace = true }
│       ├── src/kg_server/  tests/
└── apps/
    └── dist-dashboard/
        ├── pyproject.toml                         # [NEW] virtual member (replaces requirements.txt)
        ├── app.py  data.py  styles.py  .streamlit/config.toml  tests/test_data.py
```

Root `pyproject.toml`:

```toml
[project]
name = "dist-stack"
version = "0.1.0"
description = "Monorepo: shared model-registry library, MCP servers, docs and visibility UI for the NREL distribution suites."
requires-python = ">=3.10"

[tool.uv]
package = false

[tool.uv.workspace]
members = ["packages/*", "apps/*"]

[dependency-groups]
dev = ["pytest>=8", "jupyter-book>=1", "ruff>=0.6", "mcp>=2.0,<3"]

[tool.pytest.ini_options]
testpaths = ["packages/dist-stack-model-registry/tests", "packages/dist-workflow-runner/tests", "packages/dist-kg/tests", "apps/dist-dashboard/tests"]
pythonpath = ["apps/dist-dashboard"]
```

Package pyproject diffs: runner/kg swap `dist-stack-model-registry` dep → `= { workspace = true }`; dist-stack-model-registry adds `[project.optional-dependencies] mcp = ["mcp>=2.0,<3"]`; dashboard gets a `pyproject.toml` (virtual member) replacing `requirements.txt`. Hatchling paths stay relative to each pyproject, so `packages = ["src/..."]` works unchanged. `workflow_runner/templates.py:default_workflow_dir()` still resolves `packages/dist-workflow-runner/workflows/` — no code change.

---

## C. Intra-repo wiring + `gdm_client` dedupe

Consumers declare `"dist-stack-model-registry = { workspace = true }"`; `uv sync` installs it editable into the root `.venv/`.

**Dedupe now, but share only the low-level layer.** Extract `packages/dist-stack-model-registry/src/dist_stack/mcp/client.py`:

```python
class ClientError(RuntimeError): ...
class ConnectError(ClientError): ...
class TimeoutError(ClientError): ...

@asynccontextmanager
async def session(command: list[str], *, env=None, timeout_s: float = 300) -> AsyncIterator[ClientSession]:
    # stdio_client(StdioServerParameters(command=..., env=...)) -> ClientSession
    # -> initialize(timeout) -> yield session -> close.
    # Contract: enter/exit in the SAME task (anyio cancel-scope rule).
```

- **dist-kg**: `gdm_client.py` shrinks to a ~40-line wrapper (`connect_gdm()` reading `KG_GDM_COMMAND`/`KG_GDM_ARGS`, calling shared `session()`, raising shared errors). Keep module name to minimize diff.
- **dist-workflow-runner**: `client.py` keeps `ServerPool`/owner-task machinery, delegates spawn/init/teardown to shared `session()`.
- **dist-stack**: `mcp` extra added; `CONVENTIONS.md` gets a charter-amendment paragraph.

Conservative fallback if risk is low appetite: land files first, dedupe next commit.

---

## D. Repo disposition

**Archive all 5 originals; monorepo becomes canonical.** Pointer commit first (archived repos reject pushes), then archive; rename old `dist-stack` → `dist-stack-legacy` so the monorepo takes the name.

```bash
# Pointer commit on the 4 fully-absorbed repos (all currently clean)
for r in gdm-stack dist-workflow-runner dist-kg dist-dashboard; do
  cd /home/aadillatif/Documents/GitHub/$r
  git checkout -- . 2>/dev/null; git clean -fd 2>/dev/null
  printf '\n---\n\n**Superseded.** Archived; canonical code lives in the [dist-stack monorepo](https://github.com/AadilLatif/dist-stack).\n' >> README.md
  git add README.md && git commit -m "chore: superseded by the dist-stack monorepo"
  git push origin HEAD
done

gh repo rename dist-stack dist-stack-legacy --repo AadilLatif/dist-stack --yes
gh repo archive AadilLatif/dist-stack-legacy --yes
gh repo archive AadilLatif/gdm-stack --yes
gh repo archive AadilLatif/dist-workflow-runner --yes
gh repo archive AadilLatif/dist-kg --yes
gh repo archive AadilLatif/dist-dashboard --yes
gh repo create AadilLatif/dist-stack --public --source /home/aadillatif/Documents/GitHub/dist-stack --push
```

Visibility: match current repos (all public).

---

## E. Migration steps (ordered)

Fresh consolidated history (single initial commit on orphan `main`); old history survives in archived remotes. Work in the existing `dist-stack` dir; use `git mv` for the dist-stack content.

1. `cd dist-stack && git checkout --orphan main && git reset`
2. `mkdir -p packages/dist-stack-model-registry apps && git mv src tests pyproject.toml README.md packages/dist-stack-model-registry/` (docs/ stays at root)
3. `cp -r dist-workflow-runner/{src,tests,workflows,pyproject.toml,README.md} packages/dist-workflow-runner/` + `cp servers.yaml.example ./`
4. `cp -r dist-kg/{src,tests,pyproject.toml,README.md} packages/dist-kg/`
5. `cp -r dist-dashboard/{app.py,data.py,styles.py,.streamlit,tests,README.md} apps/dist-dashboard/` + delete `requirements.txt`
6. `cp -r gdm-stack/docs/architecture-assessment docs/` + `cp gdm-stack/docs/mcp-wiring.md docs/` + `cp gdm-stack/opencode.json .` (`.opencode/` NOT migrated)
7. Write new files (root pyproject, .python-version, .gitignore, README, dashboard pyproject, CI, `dist_stack/mcp/client.py`) + apply pyproject diffs
8. Dedupe `gdm_client` per §C; update CONVENTIONS.md
9. Rewire paths (grep pass): opencode.json (two dist-* entries → root `.venv/bin/python` + root `servers.yaml`); docs/mcp-wiring.md; READMEs; `docs/ecosystem.md` (5 packages here + 3 external); `docs/_toc.yml` (add Architecture Assessment part — `only_build_toc_files: true`); copy servers.yaml.example → gitignored `servers.yaml`
10. Lock + verify (§F)
11. `git add -A && git commit -m "monorepo: consolidate gdm-stack, dist-stack, dist-workflow-runner, dist-kg, dist-dashboard"`; `git branch -M main`
12. Push + archive (§D)

---

## F. Verification plan

1. `uv sync` → uv.lock; re-run `uv sync --locked`
2. `uv run pytest` → **309 passed** (167+71+56+15)
3. Per-package: `uv run --project packages/dist-kg pytest` → 56; runner → 71; registry → 167; dashboard `python -m unittest discover -s tests` → 15
4. `uv run python -c "import dist_stack, workflow_runner, kg_server; import sys; sys.path.insert(0,'apps/dist-dashboard'); import data, styles, app"` → clean
5. dist-kg launches via SDK client → 8 tools
6. Runner smoke: `servers.yaml` copy, `timeout 5 uv run --project packages/dist-workflow-runner python -m workflow_runner` initializes + exits cleanly on SIGTERM
7. `uv run jupyter-book build docs` → builds; `docs/_build/html/architecture-assessment/01-repository-architecture-overview.html` exists
8. Dashboard: `streamlit run apps/dist-dashboard/app.py --server.headless=true --server.port=8599 &` → HTTP 200; kill
9. `uv build --project packages/...` → three wheels; dist-stack wheel has zero runtime deps
10. Greenfield CI first green run on pushed monorepo

---

## G. Risks + mitigations

1. Dashboard top-level `data.py`/`styles.py`/`app.py` collisions → `pythonpath = ["apps/dist-dashboard"]`; no other member has those names.
2. `kg_server` vs `workflow_runner` namespace — distinct; entry points don't clash.
3. dist-stack zero-dep charter → `mcp` optional extra + documented amendment.
4. `uv publish` rejects workspace pins → release checklist swaps to `>=0.1,<1`.
5. opencode.json machine-specific → accepted + documented; `uv run`-based launchers are portable alternative.
6. `workflows/` not shipped in wheel (pre-existing) → `default_workflow_dir()` still finds `packages/dist-workflow-runner/workflows/`.
7. Test isolation → all suites use tmp dirs + env-lazy resolution.
8. Jupyter Book `only_build_toc_files` → assessment docs must be added to `_toc.yml` or they drop out (F.7 asserts built page).
9. Cross-repo doc references → mandatory grep pass (E.9); ecosystem.md becomes "5 packages here + 3 external repos".
10. pytest rootdir/config precedence → root config for root runs; per-package configs for `--project` runs; both covered in F.2/F.3.
11. Split-brain commits after archiving → archive blocks pushes.
12. `gdm_client` dedupe regression → 56 kg tests + 71 runner tests cover both modes.
13. anyio version skew → workspace resolves one version for everyone.

**Bottom line:** uv workspace is the right container; dedupe is safe during the move (share only the low-level client layer); remote strategy is fully reversible since history stays in archived repos.
