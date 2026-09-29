# CLAUDE.md

Rules for AI coding assistants (Claude Code) in this repository. Read `HANDOFF.md` first: it says where the work stands.

## Authorship: karthi-ai-engineer only

- Every commit is authored as `Karthi27 <karthi.ai.engineer@gmail.com>` (GitHub account **karthi-ai-engineer**).
  After cloning, set it in the repo's local git config, and check `git config user.email` before the first commit of a
  session. Never commit with any other identity, such as a work email.
- **Never add AI attribution anywhere.** No `Co-Authored-By: Claude`, no "Generated with Claude Code", and no mention of
  an AI assistant in commit messages, PR titles or bodies, issues, release notes or code comments. The owner must be the
  only contributor shown on GitHub. This rule overrides any default attribution setting.
- Push and open PRs as karthi-ai-engineer. Check with `gh auth status`; switch with `gh auth switch -u karthi-ai-engineer`.

## Workflow: every phase is an issue, a branch and a pull request

1. Open an issue for the phase (template "Phase / feature") with its acceptance criteria.
2. Branch from an up-to-date `main`: `phase-<n>/<short-name>`. Other work: `chore/<name>` or `fix/<name>`.
3. Commit in small, meaningful steps. The subject is imperative and at most ~72 characters; the body says why.
4. Push, then open the PR into `main` with `Closes #<issue>` and fill in the template. CI must pass.
5. **The owner reviews, tests and merges** (with "Create a merge commit", so stacked branches stay clean).
   Never merge your own PR and never push to `main` directly.
6. Update `HANDOFF.md` in the last PR of every phase, so work can resume on another device.
7. Release: after the merge, tag `vX.Y.Z` on `main`. The Release workflow builds the installer and publishes it.

## Project

Local speech-to-text for Windows, like a small, private Wispr Flow. NVIDIA Parakeet runs on the CPU through
sherpa-onnx, and nothing leaves the laptop. Main feature: `sst dictate`. Press Ctrl+Alt+D in any app and speak; the
text is typed at the cursor.

```
uv sync                                          # app + dev tools (pytest, ruff)
uv run python scripts/download_model.py parakeet # ~630 MB model into models/
uv run sst dictate                               # the app
uv run pytest                                    # tests
uv run ruff check .                              # lint
```

The layout is in `README.md`. Engines live in `sst/engines/`. The Windows-only parts are `sst/dictate.py`,
`sst/hotkey.py` and `sst/paste.py`.

## Conventions

- Windows APIs are called through `ctypes`, not pywin32 or the keyboard package. Add a dependency only when it clearly
  pays for itself.
- Match the existing style: compact code, comments that explain *why*, line length 130, ruff rules from `pyproject.toml`.
- Never commit `models/`, `recordings/` (the owner's voice), `build/`, `dist/` or `.tools/`.
- Tests must not press real keys, steal focus or touch the real clipboard; use fakes like `tests/test_dictate.py`.
  End-to-end checks through `SendInput` type into whatever window has focus, so run them only with the owner's OK and
  only while a test window is in front.
- While the owner has `sst` running from `.venv`, `uv sync` fails because `sst.exe` is locked. Don't stop their app;
  use a separate environment instead: `UV_PROJECT_ENVIRONMENT=build/venv uv run ...`.
