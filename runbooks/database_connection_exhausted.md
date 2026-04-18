# Runbook: Database Connection Pool Exhausted

**Alert:** `DBConnectionPoolExhausted`  
**Severity:** Critical  
**Services:** payments-api, ledger-service, reporting

## Symptoms

- `connection pool exhausted` errors in application logs
- API latency spikes (p99 > 2000ms)
- DB CPU may appear normal — this is a connection count issue, not CPU

## Immediate Actions

1. Check current connections: query your RDS / Aurora monitoring for active connections
2. Find which pods are holding connections:
   ```spl
   index=app_logs level=ERROR "connection pool" earliest=-15m
   | stats count by host service
   ```
3. Identify leaked connections — look for long-running transactions:
   ```sql
   SELECT pid, now() - pg_stat_activity.query_start AS duration, query, state
   FROM pg_stat_activity
   WHERE state != 'idle' AND query_start < now() - interval '5 minutes';
   ```
4. Terminate long-running idle connections if safe: `SELECT pg_terminate_backend(pid) FROM ...`
5. Rolling restart of the affected service to flush connection pool: `kubectl rollout restart deployment/<name> -n <namespace>`

## Root Cause Patterns

- **Slow query holding transaction open**: Usually a missing index on a new code path
- **Connection leak after exception**: Error path doesn't return connection to pool
- **Sudden traffic spike**: Pool size tuned for steady-state, not burst

## Prevention

- Set connection pool max size = (RDS max_connections / number_of_replicas) × 0.8
- Always set pool timeout and idle eviction
- Use PgBouncer in transaction mode for high-concurrency fintech workloads

## Runbook Link

https://wiki.internal/runbooks/db-connection-exhausted
