# Contributing

## Branch & PR discipline — two tiers

The green gate for every change is local CI: `ruff check .` + `ruff format --check .` + `pytest`
(the same steps as `.github/workflows/ci.yml`).

**Tier 1 — ordinary changes** (code fixes, converters, viewer-host work, docs, tests, tooling)
MAY be committed directly to `main` once local CI is green.

**Tier 2 — protected files** MUST go through a branch → PR → CI-green → merge flow (never a
direct push to `main`), because they are outward-facing commitments:

- `spec/**` — the frozen protocol. Every change carries a `spec/CHANGELOG.md` entry tagged
  `clarification` or `normative change`, and follows the `points_t_method` precedent (new optional
  fields only; never change or remove an existing rule).
- `conformance/golden/**` — the executable definition of the format. Editing a golden's normative
  content is a protocol change (see `conformance/golden/README.md`) and moves in lockstep with the
  spec PR.
- `.github/workflows/**` that touch **publishing** (e.g. `publish.yml`) — a change here directly
  touches the release / PyPI promise, so it is the same protection tier as `spec`/`golden`: branch
  + PR, never a direct push. (Non-publishing CI-workflow tweaks are Tier 1.)
- `skills/**` (notably `SKILL.md`) — a derived restatement of the spec that is distributed to
  downstream agents as their behavior guide, so a semantic error propagates with the same force as
  a wrong rule in the spec. Same tier as `spec`/`golden`: the PR must check the restatement line by
  line against the spec for accuracy. (`AGENTS.md` and `CONTRIBUTING.md` themselves stay Tier 1.)

When in doubt, treat it as Tier 2.

> **Bootstrap note.** These rules were established by direct pushes to `main` during bootstrap —
> including the commit that first defined this protected list (a rule cannot gate its own
> introduction). Once GitHub branch protection is enabled (require-PR on `main`), the direct-push
> path is closed at the mechanism level and this note is historical.
