# IntelliSREBot — Use Case Examples

Real-world triage scenarios showing inputs, agent reasoning, and outputs.

---

## Use Case 1: Pod CrashLoopBackOff (Payments Service)

**Situation:** Alertmanager fires `KubePodCrashLooping` on the payments namespace.

### Webhook Input
```bash
curl -X POST http://localhost:8080/webhook/alertmanager \
  -H "Content-Type: application/json" \
  -d '{
    "alerts": [{
      "labels": {
        "alertname": "KubePodCrashLooping",
        "severity": "high",
        "namespace": "payments",
        "service": "payments-api",
        "env": "production"
      },
      "annotations": {
        "description": "Pod payments-api-7f9d has restarted 8 times in 10 minutes"
      },
      "startsAt": "2026-04-18T14:23:00Z"
    }]
  }'
```

### Triage Output
```json
{
  "alert_id": "KubePodCrashLooping_2026-04-18T14:23:00",
  "status": "investigating",
  "severity": "critical",
  "summary": "payments-api pods crash-looping in production — likely OOMKill or missing secret after deploy",
  "likely_cause": "Exit code 137 (OOMKill) is most probable given payments-api memory profile under transaction load. Second candidate: missing environment variable from a recent deploy.",
  "recommended_actions": [
    "kubectl get pods -n payments --sort-by='.status.startTime'",
    "kubectl describe pod payments-api-7f9d -n payments | grep -A5 'Last State'",
    "If exit 137: kubectl set resources deployment/payments-api -n payments --limits=memory=768Mi",
    "If exit 1 (config): kubectl logs payments-api-7f9d -n payments --previous | head -50",
    "If bad deploy: kubectl rollout undo deployment/payments-api -n payments"
  ],
  "runbook_url": "https://wiki.internal/runbooks/pod-crashloopbackoff",
  "follow_up_queries": [
    "kubectl top pods -n payments -l app=payments-api",
    "index=k8s_events namespace=payments reason=OOMKilling earliest=-30m | stats count by pod_name"
  ],
  "confidence": "high"
}
```

---

## Use Case 2: High Error Rate — Fintech Payment API

### CLI Input (plain English)
```
You: payments-api is throwing 5xx errors, error rate is above 15% for the last 5 minutes

IntelliSREBot:

Root cause: 15% sustained 5xx on payments-api in production is a P1.
Most likely causes in order of probability:

1. Database connection pool exhausted (most common after traffic spike or deploy)
2. Upstream dependency (fraud-detection or ledger-service) timing out
3. New deploy introduced a bug in the request path

Immediate actions:
1. Check pod health: kubectl get pods -n payments -l app=payments-api
2. Check DB connections — look for "pool exhausted" in logs:
   kubectl logs -n payments -l app=payments-api --tail=200 | grep -i "pool\|connection\|timeout"
3. Splunk query for error breakdown:
   index=app_logs service=payments-api level=ERROR earliest=-15m
   | stats count by exception_class
   | sort -count
4. Check upstream: kubectl get pods -n fraud -l app=fraud-detection
5. If DB pool: rolling restart to flush connections:
   kubectl rollout restart deployment/payments-api -n payments

Confidence: medium — severity re-classified to CRITICAL because payments-api
is a payment-critical service in production.
```

---

## Use Case 3: Database Connection Pool Exhausted

### JSON Payload via CLI
```
You: {"title": "DBConnectionPoolExhausted", "source": "datadog", "severity": "critical",
     "labels": {"service": "ledger-service", "namespace": "finance", "env": "production"},
     "description": "HikariCP pool size 50 — all connections in use for >2 minutes"}

[Alert loaded: DBConnectionPoolExhausted / critical]

IntelliSREBot:

Root cause: HikariCP connection pool fully saturated on ledger-service.
All 50 connections in use for 2+ minutes means either a slow/stuck query
is holding connections open, or traffic volume has outgrown pool configuration.

Immediate actions:
1. Find long-running queries (run on your RDS/Aurora):
   SELECT pid, now() - query_start AS duration, state, left(query,100)
   FROM pg_stat_activity
   WHERE state != 'idle' AND query_start < now() - interval '2 minutes'
   ORDER BY duration DESC;

2. If stuck queries found: SELECT pg_terminate_backend(pid) FROM pg_stat_activity
   WHERE state = 'idle in transaction' AND query_start < now() - interval '5 minutes';

3. Rolling restart to flush pool (last resort, causes brief disruption):
   kubectl rollout restart deployment/ledger-service -n finance

4. Splunk to confirm pattern:
   index=app_logs service=ledger-service level=ERROR "connection pool" earliest=-30m
   | timechart span=1m count

5. If traffic-driven: temporarily increase pool size in config and redeploy
   (max safe = RDS max_connections / replica_count × 0.8)

Runbook: https://wiki.internal/runbooks/db-connection-exhausted
Confidence: high — alert title and description match runbook exactly.
```

---

## Use Case 4: False Positive Detection

### Input
```
You: {"title": "HighMemoryUsage", "source": "prometheus", "severity": "warning",
     "labels": {"service": "batch-processor", "namespace": "data-jobs", "env": "staging"},
     "description": "Memory usage at 82% of limit"}

IntelliSREBot:

Root cause assessment: This is likely a false positive or a non-urgent item.

Reasons:
- Environment is staging, not production — downgraded from warning to LOW effective severity
- batch-processor is not a payment-critical service
- 82% memory usage on a batch job is expected behavior — batch workloads
  naturally spike during processing windows and release memory on completion

Recommended actions:
1. Verify this is not a persistent leak: check if memory drops after the batch run completes
   kubectl top pod -n data-jobs -l app=batch-processor --containers

2. If memory does NOT drop after the job: check for object accumulation:
   index=app_logs service=batch-processor level=WARN "memory" earliest=-2h

3. If staging consistently hits 82%: increase memory limit by 20% in staging
   to understand true production headroom before the next prod deploy

Status: FALSE_POSITIVE candidate — recommend closing after confirming
memory releases post-job. Do not page anyone for this.

Confidence: high
```

---

## Use Case 5: Kubernetes Node Pressure

```
You: {"title": "KubeNodeNotReady", "source": "prometheus", "severity": "critical",
     "labels": {"node": "ip-10-0-1-45.ec2.internal", "env": "production"},
     "description": "Node has been NotReady for 3 minutes"}

IntelliSREBot:

Root cause: Node ip-10-0-1-45.ec2.internal is NotReady — could be kubelet crash,
disk pressure, memory pressure, or network partition from the control plane.

Immediate actions (run in order, stop when you find the cause):

1. Check node status:
   kubectl describe node ip-10-0-1-45.ec2.internal | grep -A20 Conditions

2. Check kubelet on the node (requires SSH or SSM):
   sudo systemctl status kubelet
   sudo journalctl -u kubelet --since "10 minutes ago" | tail -50

3. Check disk pressure (most common silent killer):
   df -h  — look for any filesystem at >90%
   du -sh /var/lib/docker/*  — Docker overlay2 is a frequent culprit

4. If the node is unrecoverable: cordon and drain to move workloads:
   kubectl cordon ip-10-0-1-45.ec2.internal
   kubectl drain ip-10-0-1-45.ec2.internal --ignore-daemonsets --delete-emptydir-data

5. Terminate the EC2 instance and let the ASG replace it (EKS managed node group):
   aws ec2 terminate-instances --instance-ids <instance-id>

PromQL to check which pods were on this node:
kube_pod_info{node="ip-10-0-1-45.ec2.internal"}

Confidence: medium — root cause requires node access to confirm.
```

---

## Use Case 6: Composing with SplunkBot via MCP

IntelliSREBot exposes an MCP server. When composed with SplunkBot in the same
MCP host, the agent can triage an alert AND run Splunk queries in one pipeline.

**MCP config (`claude_desktop_config.json`):**
```json
{
  "mcpServers": {
    "intelli-sre": {
      "command": "python",
      "args": ["-m", "src.mcp_server"],
      "cwd": "/path/to/intelli-sre-bot",
      "env": { "ANTHROPIC_API_KEY": "sk-ant-..." }
    },
    "splunk": {
      "command": "python",
      "args": ["-m", "src.mcp_server"],
      "cwd": "/path/to/splunk-ai-bot",
      "env": {
        "SPLUNK_HOST": "splunk.company.com",
        "SPLUNK_USERNAME": "svc_splunkbot",
        "SPLUNK_PASSWORD": "...",
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

**What the combined pipeline does for a single alert:**
1. `intelli-sre: triage_alert` → identifies likely cause, generates diagnostic queries
2. `splunk: splunk_search` → executes the SPL queries from step 1 automatically
3. Returns enriched triage with actual log evidence, not just recommendations

---

## Running Examples Locally

```bash
# Interactive CLI — paste any JSON payload or describe in plain English
python -m src.cli

# Webhook server — send real Alertmanager payloads
uvicorn src.webhook_server:app --host 0.0.0.0 --port 8080

# Test the webhook with the Use Case 1 payload
curl -X POST http://localhost:8080/webhook/alertmanager \
  -H "Content-Type: application/json" \
  -d @docs/sample_alertmanager_payload.json

# Poll for result
curl http://localhost:8080/triage/<alert_id>
```
