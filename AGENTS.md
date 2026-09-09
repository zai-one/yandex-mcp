# Yandex MCP maintenance

Independent owner of Direct, Metrika, Search/Wordstat and Webmaster integrations.
Follow the ZAI task protocol when under that workspace; runtime must work outside it.
Use Python and synthetic offline fixtures. Never call live/paid Yandex APIs in tests.
Preserve scope, approval, budgets, idempotency, job ownership and uncertain-write state.

Use Python for scripts. Preserve existing MCP names/schemas and server-owned
authorization, account boundaries, budgets, approvals and unknown-outcome state.
Do not use live provider accounts or credentials in tests. Keep setup local and explicit.
Run scripts/verify.py and scripts/verify_install.py before releases.
Use Issues for requested changes. Preserve LICENSE, NOTICE and third-party licenses.
Never publish operational handoffs, private extraction refs, credentials or runtime state.
Production deployment is a separate action.
When operating in a workspace with a task/verifier protocol, follow that protocol.
