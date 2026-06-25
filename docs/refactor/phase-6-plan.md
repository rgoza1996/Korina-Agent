# Phase 6 — pytest + CI

## Status
Not started. Stub seeded by prep cron (`korina-phase6-prep`, 2026-06-25).

## Deployment sync strategy
- For automated tests / CI: runs from `/home/roggoz/Korina-Agent`, no rsync needed.
- For manual live regression on roggoz: MUST rsync from source → live before testing.
  - Concrete sync commands:
    ```
    rsync -a --delete /home/roggoz/Korina-Agent/korina/ /home/roggoz/Korina/korina/
    rsync -a /home/roggoz/Korina-Agent/Korina/index.html /home/roggoz/Korina/index.html
    rsync -a /home/roggoz/Korina-Agent/Korina/styles.css /home/roggoz/Korina/styles.css
    rsync -a /home/roggoz/Korina-Agent/tests/regression_smoke.py /home/roggoz/Korina/tests/regression_smoke.py
    ```
  - Then: `systemctl --user restart korina-voice-lab.service` + poll `/api/health`.

## TODO
- [ ] Full task list TBD
