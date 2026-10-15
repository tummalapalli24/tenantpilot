"""Connector config presets used by one-step onboarding (Create Customer form) and seeds.

A preset is just a starting point: after onboarding, an admin can edit the operation
paths and field maps per customer without code changes.
"""

FIELD_SERVICE_REST_V1 = {
    "auth_header": "X-API-Key",
    "health_path": "/health",
    "timeout_seconds": 5,
    "max_attempts": 3,
    "operations": {
        "get_customer": {
            "method": "GET",
            "path": "/customers/{customer_id}",
            "result_path": "customer",
            "field_map": {
                "customer_id": "customer_ref",
                "name": "full_name",
                "status": "account_status",
                "tier": "plan",
                "region": "site.region",
                "primary_contact": "contact.name",
                "open_ticket_count": "open_tickets",
                "notes": "account_notes",
            },
        },
        "list_open_jobs": {
            "method": "GET",
            "path": "/customers/{customer_id}/work-orders",
            "query": {"state": "open"},
            "result_path": "data.work_orders",
            "list_result": True,
            "field_map": {
                "job_id": "wo_number",
                "customer_id": "customer_ref",
                "job_type": "category",
                "status": "state",
                "scheduled_for": "window_start",
                "technician": "tech.name",
                "error_code": "fault_code",
                "notes": "tech_notes",
            },
        },
        "create_ticket": {
            "method": "POST",
            "path": "/tickets",
            "body": {
                "customer_ref": "{customer_id}",
                "subject": "{summary}",
                "severity": "{priority}",
                "channel": "tenantpilot-agent",
            },
            "result_path": "ticket",
            "field_map": {
                "ticket_id": "ticket_no",
                "customer_id": "customer_ref",
                "summary": "subject",
                "priority": "severity",
                "status": "state",
                "created_at": "opened_at",
            },
        },
    },
}

# BetaCorp keeps customer accounts in a Postgres CRM schema; the connector runs one named,
# parameterized read-only query and maps its columns onto the canonical Customer model.
BETACORP_POSTGRES_CRM = {
    "statement_timeout_ms": 3000,
    "operations": {
        "get_customer": {
            "sql": (
                "SELECT account_ref, org_name, lifecycle_state, service_tier, hq_region, account_manager, "
                "open_cases, notes FROM betacorp_crm.accounts WHERE upper(account_ref) = upper($1)"
            ),
            "params": ["customer_id"],
            "field_map": {
                "customer_id": "account_ref",
                "name": "org_name",
                "status": "lifecycle_state",
                "tier": "service_tier",
                "region": "hq_region",
                "primary_contact": "account_manager",
                "open_ticket_count": "open_cases",
                "notes": "notes",
            },
        }
    },
}
