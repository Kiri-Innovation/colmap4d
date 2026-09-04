# AGENTS.md

Operating conventions for an AI agent (or any contributor) working in this repository. These are
the disciplines this project has followed and expects to keep; they live here so they survive
across sessions and agents instead of in chat context. Human-facing contribution rules are in
[`CONTRIBUTING.md`](CONTRIBUTING.md) — this file references it rather than repeating it.

## Green gate

Before any merge or push: `ruff check .` && `ruff format --check .` && `pytest` (identical to
`.github/workflows/ci.yml`). GitHub Actions re-runs it on `main` and on PRs.

## Branch / push discipline

Two tiers, defined in [`CONTRIBUTING.md`](CONTRIBUTING.md): **Tier 1** (ordinary code/docs/tests)
may push to `main` after the green gate; **Tier 2** — `spec/**`, `conformance/golden/**`, and
publishing workflows (`.github/workflows/publish.yml`) — MUST go branch → PR → CI-green → merge,
never a direct push. When unsure, treat as Tier 2. This repo has no `gh`/token in some
environments; the faithful substitute is branch → local-CI-green → `--no-ff` merge → push, and
each PR/merge message names the category (see below).

## Spec is normative; the goldens are its constitution

- `spec/colmap4d-v1.0.md` **Part I** is the sole normative surface. Everything else (this file, the
  skill, the reference impl, docstrings) is a derived view — if it conflicts with the spec, the spec
  wins.
- `conformance/golden/**` is the **executable definition** of Part I. Editing a golden's *normative*
  content (bytes, documented ids/timestamps, pass/fail behavior) **is a protocol change** and goes
  through the spec-PR process. Goldens are **hand-authored**, never dumped from the implementation
  (that would make the impl, not the spec, the source of truth).
- Every `spec/**` PR carries a `spec/CHANGELOG.md` entry tagged **`clarification`** (no behavior
  change) or **`normative change`**. Merge/commit messages state the category.

## How the format evolves after freeze (the `points_t_method` precedent)

The spec is frozen at v1.0. Post-freeze changes are **additive only**: a new field/layer is added
as **OPTIONAL** and relies on the standing Part I rule that *readers MUST tolerate unknown fields*,
so old readers ignore it and old files stay valid. **Never change or remove an existing rule, and
never add a required field.** `points_t_method` (D3) is the template: MAY-level, ignorable, with a
CHANGELOG entry. A protocol-breaking need would be a new spec major version, not an edit to v1.0.

## Two version axes (do not conflate)

- **Package version** — `pyproject.toml` / `src/colmap4d/__init__.py __version__` / `CITATION.cff`;
  bumps for any code/packaging change. Keep the three in sync.
- **Spec version** — the `spec/` title and `time_meta.colmap4d_spec`; bumps only on a protocol
  change. The package can release many times against one frozen spec (e.g. 1.0.1 implements spec 1.0).

## Release flow

Tag on the merged commit → publish a GitHub Release → `.github/workflows/publish.yml` builds the
tagged tree and uploads to PyPI via **Trusted Publishing** (OIDC, `id-token: write`,
`environment: pypi`, no stored token). Lessons already paid for (see
[`docs/freeze-checklist.md`](docs/freeze-checklist.md) "Process lessons"):

- **Pre-tag audit:** `grep` the whole tree for placeholder URLs / org names before tagging — a tag
  is immutable and the build comes from the tagged tree, so dead links bake in permanently.
- **Release-env config** (Trusted-Publishing environment, PyPI pending publisher) must match on both
  sides before the first run.
- Hardening commits that fix release config land **after** the tag they fix — expected; **do not
  move or re-cut tags.** Ship the fix on the package axis (next patch release).

## OPEN questions: record → decide → archive

Ambiguities are tracked in `docs/open-questions.md`: state the question, record the decision with
rationale, mark status. Settled decisions are folded into the spec's normative text; the entry
stays as provenance (nothing "provisional" remains after freeze). Rejected proposals are kept as
**`rejected-unless-new-evidence`** with the full reasoning, so they are not silently re-opened
(e.g. OPEN-7, the redundant `NAME` column).

## Scope boundaries

This repo is the format: spec + reference implementation + converters + conformance. The viewer
(`ColmapUtil`) lives in its own repo. Keep the skill (`skills/colmap4d/SKILL.md`) thin — a derived
view, not a second spec.
