# Project development guidance

- Python 3.12+; Yandex Direct/Metrika/Webmaster/Audience/Search.
- Keep OAuth custody, account/sandbox separation, request-bound approvals and Direct write policy.
- Checks: `uv run python scripts/verify.py`; `python scripts/check_engineering.py --check`. Run install checks before release.

Read only the task-relevant public documentation. Personal workspace notes are optional and remain outside Git.

- [README](../../README.md): supported features and installation.
- [Contributing](../../CONTRIBUTING.md): development and release checks.

- Repository maintenance, release checks and safety: read [maintenance rules](maintenance.md); preserve the listed checks, licenses and publication gates.
