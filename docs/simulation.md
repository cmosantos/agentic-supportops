# Deterministic Contoso simulation

The Contoso environment is a local JSON fixture validated into typed Pydantic models. Every tool reads this fixture through `SimulationRepository`; it never inspects the developer machine or calls a network service.

## Resources

- Identity: Alice is healthy; Bob has shared mailbox access with automapping disabled; Carol has FullAccess without SendAs; David is disabled; Erin lacks a license and an expected group.
- Messaging: the support shared mailbox has per-user permissions; Alice's mailbox is nearly full and rejects external mail; the sales distribution group rejects external mail.
- Endpoints: WS-001 is healthy; WS-002 has critical disk and degraded services; WS-003 has working IP connectivity with broken DNS; WS-004 has no gateway or external connectivity.
- Infrastructure: APP-01 is healthy; APP-02 has critical CPU; API-01 and SUPPORT-API are degraded; DB-01 has a near-exhausted application connection pool; FILE-01 has critical storage and a stopped service.
- Monitoring: active alerts correspond to the intentionally unhealthy hosts.

## Incident catalog

The fixture contains `INC-001` through `INC-025`, spanning identity, messaging, endpoint, network, and infrastructure scenarios. Each record has an explicit affected resource and a small investigation context. Six playbooks are deeply supported:

| Incident | Evidence collected |
| --- | --- |
| `INC-002` | Account, mailbox, FullAccess, SendAs, automapping |
| `INC-003` | Account, mailbox, FullAccess, SendAs, automapping |
| `INC-014` | Device details and critical disk percentage |
| `INC-019` | Network config, gateway, external connectivity, DNS resolution |
| `INC-021` | Host status, CPU/memory/disk metrics, active alerts |
| `INC-023` | Application health, latency, error rate, host context, alerts |

Other catalog incidents are coherent fixture scenarios but intentionally return `investigation_not_supported` until a later mission adds their playbooks.

## Tool catalog

- Identity: `get_user`, `get_account_status`, `get_user_groups`, `get_user_licenses`, `get_mailbox`, `get_mailbox_permissions`.
- Endpoint: `get_device`, `get_cpu_usage`, `get_memory_usage`, `get_disk_usage`, `get_network_config`, `get_service_status`.
- Network: `check_gateway_connectivity`, `check_external_connectivity`, `check_dns_resolution`.
- Monitoring: `get_host_status`, `get_recent_alerts`, `get_metrics`, `get_service_health`, `get_application_health`.

All return the same typed `ToolResult` envelope with tool name, resource, success, structured data, or an explicit error such as `resource_not_found`, `mailbox_not_found`, `service_not_found`, or `invalid_argument`.

## Reset behavior

`python -m simulation.seed --reset` is CLI-only and requires the destructive `--reset` flag. It drops local tables and restores the fixture baseline. Tests use temporary SQLite databases and never touch the development database.

## Architectural boundary

```text
Deterministic playbooks or optional AI investigation
                         |
                         v
                Investigation tools
                         |
                         v
              Simulation repository
                         |
                         v
                Typed local fixture
```

The tools contain no prompt, LLM, MCP, or vendor-specific types. Mission 04 keeps OpenAI Responses API integration behind a gateway and passes only validated function calls into these tools. MCP, Agents SDK, RAG, approval workflows, and OpenTelemetry are not implemented.
