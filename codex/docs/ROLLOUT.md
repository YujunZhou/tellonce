# Colleague Rollout

## Install

```bash
git clone <repo> ~/.codex/skills/tellonce
cd /path/to/project
bash ~/.codex/skills/tellonce/codex/install.sh
bash ~/.codex/skills/tellonce/codex/doctor.sh
```

(The Codex installer lives under `codex/`; the repo-root `install.sh` is the
Claude Code variant and would register Claude Code hooks instead.)

Doctor should end with a line like:

```text
Tellonce status: state=PASS, private_paths=PASS, wrapper=NOT_USED, hooks=PASS, shadow=DISABLED, install=OBSERVE_ONLY
```

`wrapper=NOT_USED` is normal before the first `tellonce_codex exec` run. `state=FAIL` or `install=FAILED` is not acceptable.

## Basic Smoke

```bash
python -m tellonce_codex scan --project-root . --message "from now on, always use tabs not spaces"
python -m tellonce_codex dashboard --project-root .
python -m tellonce_codex exec --project-root . -- definitely-missing-codex-binary
```

The missing binary command should create a run artifact and return a degraded runtime failure, not a fake pass.

## What To Report

Send back:

- doctor output;
- dashboard output;
- `.codex/tellonce/registration.json`;
- `.codex/tellonce/mode.json`;
- whether normal Codex usage was through wrapper or plain Codex.

Do not send full transcripts or secrets.
