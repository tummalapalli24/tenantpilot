# Eval results - offline-heuristic - 2026-09-21 19:44 UTC

| metric | value |
|---|---|
| cases | 10 |
| pass_rate | 1.0 |
| tool_selection_recall | 1.0 |
| forbidden_tool_calls | 0 |
| fact_recall | 1.0 |
| unsupported_identifiers | 0 |
| citation_errors | 0 |
| tool_success_rate | 0.889 |
| latency_ms_p50 | 86 |
| latency_ms_max | 193 |
| avg_llm_calls | 2.6 |

| case | pass | tools called | tool recall | fact recall | unsupported ids | latency ms | notes |
|---|---|---|---|---|---|---|---|
| acme-install-failure | PASS | get_customer, list_open_jobs | 1.0 | 1.0 | - | 193 | - |
| acme-firmware-stuck | PASS | get_customer, list_open_jobs | 1.0 | 1.0 | - | 100 | - |
| acme-sensor-drift | PASS | get_customer, list_open_jobs | 1.0 | 1.0 | - | 92 | - |
| acme-refrigerant-ticket-approved | PASS | get_customer, list_open_jobs, create_ticket | 1.0 | 1.0 | - | 181 | - |
| acme-ticket-rejected | PASS | get_customer, list_open_jobs, create_ticket | 1.0 | 1.0 | - | 149 | - |
| acme-suspended-account | PASS | get_customer | 1.0 | 1.0 | - | 74 | - |
| acme-unknown-customer | PASS | get_customer | 1.0 | 1.0 | - | 56 | - |
| acme-policy-question | PASS | - | 1.0 | 1.0 | - | 29 | - |
| betacorp-account-status | PASS | get_customer | 1.0 | 1.0 | - | 80 | - |
| betacorp-ticket-governance | PASS | get_customer | 1.0 | 1.0 | - | 79 | - |
