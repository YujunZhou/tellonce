# Colleague Rollout

## Install

```bash
git clone <repo> ~/.codex/skills/preference-tracker
cd /path/to/project
bash ~/.codex/skills/preference-tracker/install.sh
bash ~/.codex/skills/preference-tracker/doctor.sh
```

Doctor should end with a line like:

```text
Preference Tracker status: local=PASS, skill=PASS, state=PASS, plain_codex_hooks=DEGRADED, wrapper=NOT_USED, shadow=DISABLED, install=OBSERVE_ONLY
```

`DEGRADED` for hooks is acceptable in v1 because hooks are experimental. `state=FAIL` or `install=FAILED` is not acceptable.

## Basic Smoke

```bash
python -m codex_preftrack scan --project-root . --message "from now on, always use tabs not spaces"
python -m codex_preftrack dashboard --project-root .
python -m codex_preftrack exec --project-root . -- definitely-missing-codex-binary
```

The missing binary command should create a run artifact and return a degraded runtime failure, not a fake pass.

## What To Report

Send back:

- doctor output;
- dashboard output;
- `.codex/preference-tracker/registration.json`;
- `.codex/preference-tracker/mode.json`;
- whether normal Codex usage was through wrapper or plain Codex.

Do not send full transcripts or secrets.
