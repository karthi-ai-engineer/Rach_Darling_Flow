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
5. **The owner reviews, tests and merges** (with "Create a merge commit", so stacked branches stay clean). Merge
   yourself only when the owner explicitly asks. Then retarget the next stacked PR to `main` *before* deleting the
   merged branch: deleting a base branch closes the PRs stacked on it. Never push to `main` directly.
6. Update `HANDOFF.md` in the last PR of every phase, so work can resume on another device.
7. Release, only when the owner says so: raise `__version__` in `sst/__init__.py` in the phase's PR; after the merge,
   tag `vX.Y.Z` on `main`. The Release workflow publishes `Rflow-Setup.exe` and `Rflow-Setup.exe.sha256`. Every
   installed Rflow offers that release as an update, and the website links to it. Keep those two asset names: the
   updater and the website depend on them.

## Project

**Rflow**: dictation for Windows, like a small, private Wispr Flow. NVIDIA Parakeet recognises speech on the CPU
through sherpa-onnx, so the voice never leaves the laptop. The text can optionally be cleaned up by a model from the
provider the user chooses (OpenAI, Anthropic, Google Gemini, Groq, Ollama, vLLM or another OpenAI-compatible server).
Each person can have a profile with their own setup. Hold Ctrl+Win in any app and speak; the text is typed at the cursor.
The public download site is `site/` (Vercel). The Python package keeps its internal name `sst`.

```
uv sync                                          # app + dev tools (pytest, ruff)
uv run python scripts/download_model.py parakeet # ~630 MB model into models/
uv run sst app                                   # the tray app (uv run sst dictate: the same in a console)
uv run pytest                                    # tests
uv run ruff check .                              # lint
```

The layout is in `README.md`. Engines live in `sst/engines/`. The dictation logic is `Dictation` in `sst/dictate.py`,
shared by the app (`sst/app.py`, Qt: tray, pill, `TrayApp`) and the console command. The window is `sst/window.py`: it
keeps no state and calls `TrayApp`, or `PreviewApp` in tests and screenshots. In-app updates are `sst/updates.py`, and
the text cleanup is `sst/gateway.py` (`PROVIDERS`: each provider's request format). Settings, keys, history, stats and
reading tests belong to a profile (`Profiles` in `sst/settings.py`): read and write them through the profile's paths
(`TrayApp.profile.settings_file`...), never the module's default paths. The Windows-only parts are `sst/app.py`, `sst/dictate.py`, `sst/hotkey.py`,
`sst/paste.py`, `sst/settings.py` and `sst/gateway.py` (DPAPI). UI tests run Qt off-screen (`tests/test_app.py`,
`tests/test_window.py`). To see a UI change, render it with the Windows platform but without showing it, e.g. with
`scripts/make_site_screenshots.py`, and look at the image in both themes.

## Conventions

- Windows APIs are called through `ctypes`, not pywin32 or the keyboard package. Add a dependency only when it clearly
  pays for itself.
- Match the existing style: compact code, comments that explain *why*, line length 130, ruff rules from `pyproject.toml`.
- Never commit `models/`, `recordings/` (the owner's voice), `build/`, `dist/` or `.tools/`.
- The owner's company AI gateway (its address and key) is private. Both live only in the first profile's
  `%APPDATA%\sst\gateway.json` (provider "vLLM or another OpenAI-compatible server"), and the key is encrypted with
  DPAPI. Never put either into code, docs, tests, logs or commits; the repository is public. Load them with
  `GatewayConfig.load(profile.gateway_file)`, whose repr hides the key. Test the gateway one model at a time (a small
  server), and use `tests/test_gateway.py`'s local fake for automated tests.
- Tests must not press real keys, steal focus or touch the real clipboard; use fakes like `tests/test_dictate.py`.
  End-to-end checks through `SendInput` type into whatever window has focus, so run them only with the owner's OK and
  only while a test window is in front.
- While the owner has `sst` running from `.venv`, `uv sync` fails because `sst.exe` is locked. Don't stop their app;
  use a separate environment instead: `UV_PROJECT_ENVIRONMENT=build/venv uv run ...`.
