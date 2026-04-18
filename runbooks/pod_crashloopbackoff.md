# Runbook: Pod CrashLoopBackOff

**Alert:** `KubePodCrashLooping`  
**Severity:** High  
**Source:** Prometheus / Alertmanager

## Symptoms

- Pod restart count > 5 in last 10 minutes
- `kubectl get pods` shows `CrashLoopBackOff` status

## Immediate Actions

1. Identify the crashing pod: `kubectl get pods -n <namespace> | grep CrashLoop`
2. Read exit code: `kubectl describe pod <pod> -n <namespace> | grep -A3 "Last State"`
   - Exit 137 → OOMKill (memory limit too low)
   - Exit 1 → Application error (check logs)
   - Exit 139 → Segfault (likely binary or config corruption)
3. Get last logs: `kubectl logs <pod> -n <namespace> --previous`
4. Check resource limits: `kubectl describe pod <pod> -n <namespace> | grep -A6 Limits`

## Common Causes in Fintech Context

- **Configuration secret missing**: Pod starts, tries to connect to DB or vault, fails immediately
- **OOMKill**: Memory limit set too low for current transaction volume
- **Bad deployment**: New image has startup bug — rollback with `kubectl rollout undo deployment/<name> -n <namespace>`

## Splunk Query

```spl
index=k8s_events namespace="<namespace>" reason=BackOff earliest=-30m
| stats count by pod_name reason
```

## Escalation

- If payments or auth pods: immediate P1
- If non-critical service: P3, fix within 2 hours

## Runbook Link

https://wiki.internal/runbooks/pod-crashloopbackoff
