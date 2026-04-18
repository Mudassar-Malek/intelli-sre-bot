# Runbook: HighErrorRate

**Alert:** `HighErrorRate`  
**Severity:** High / Critical  
**Services:** payments-api, auth-service, transaction-processor

## Symptoms

- HTTP 5xx rate > 5% sustained for 3+ minutes
- Typically surfaces in Prometheus alert or Datadog monitor

## Immediate Actions

1. Check pod health: `kubectl get pods -n payments --sort-by='.status.startTime'`
2. Inspect recent logs: `kubectl logs -n payments -l app=payments-api --tail=200 --previous`
3. Verify upstream dependencies are healthy (database, downstream APIs)
4. If OOMKill is present: `kubectl describe pod <pod-name> -n payments | grep -A5 OOMKill`
5. Scale up if load-driven: `kubectl scale deployment payments-api -n payments --replicas=8`

## Splunk Queries

```spl
index=app_logs service=payments-api level=ERROR earliest=-15m
| stats count by exception_class host
| sort -count
```

```spl
index=app_logs service=payments-api earliest=-1h
| timechart span=1m count by level
```

## Escalation

- P1 (>20% error rate): Page on-call lead immediately
- P2 (5-20%): Notify #sre-incidents Slack channel
- Link to runbook: https://wiki.internal/runbooks/high-error-rate

## Postmortem Template

- Timeline of events
- Root cause
- Customer impact (# transactions affected)
- Mitigation taken
- Action items (with owner and due date)
