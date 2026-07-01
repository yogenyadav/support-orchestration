# Skill: infra-diagnosis

Infrastructure domain diagnosis guide (VM/hypervisor/OS layer — cross-cutting).

## What infra owns

Infra diagnoses issues **at or below the hypervisor** that cause software-layer symptoms in any domain. It is a cross-cutting domain — any subagent that exhausts its software-layer hypotheses and suspects the underlying VM, OS, disk, memory, or network routes here.

Infra does **not** diagnose:
- Physical hardware faults (sorter mechanics, conveyor jams) → field engineer
- Application-level issues in WCS, WES, ESB, etc. → those domain subagents

## The hypervisor boundary

```
Physical hardware (field engineer)
    ↓
x86 Dell server (field engineer if physically failed)
    ↓
VMware vSphere hypervisor ← INFRA BOUNDARY
    ↓
Guest VM (Windows or Linux)    ← INFRA
    ↓
OS (Windows/Linux services)    ← INFRA
    ↓
Application (WCS, WES, ESB…)   ← Domain subagent
```

When a domain subagent suspects infrastructure (OOM crash, disk full, network partition at the VM/host level), it reroutes to infra with the evidence that led to that suspicion.

## Common infra failure modes

### oom_crash (most common infra incident)
The service VM ran out of memory. Java/JVM services (WES, ESB, picking engine) are common OOM victims.

Evidence:
- Log: `java.lang.OutOfMemoryError` or OOM kill in system logs
- Service process absent (killed) without a clean shutdown log entry
- Multiple services on the same VM affected simultaneously
- VM monitoring shows memory at or near 100% before the crash

Fix:
- Restart the affected service(s) after confirming memory has reclaimed (not still exhausted)
- Investigate heap size configuration in the client overlay — may need JVM `-Xmx` adjustment
- Alert engineer to monitor and consider VM memory upgrade if recurring

### disk_full
VM disk reached 100%; services fail to write logs, DB files, or temp data.

Evidence:
- Log write failures (log rotation stopped, "no space left on device" in syslog)
- MS SQL or Oracle refusing writes (WCS, database-backed services)
- Service still running but not processing — silent failure because writes are failing

Fix:
- Identify which partition is full: `/`, `/var`, `/tmp`, or the DB data partition
- Clear stale logs, temp files, or rotate logs
- Alert engineer for long-term disk capacity planning

### network_partition
VM cannot reach another service (e.g., WCS can't reach ESB host, picking engine can't reach ASRS API).

Evidence:
- Socket connection refused or timeout to a specific host/port
- Other services on the same VM can reach the target (rules out host-wide network failure)
- Or: all services on the VM lost network (switch/VLAN issue)

Fix:
- If single-host isolation: check VM's virtual NIC, vSwitch config in vSphere
- If client-site network: escalate to client network team (outside our scope)
- Reroute back to the domain subagent once network is confirmed working

### vsphere_host_issue
vSphere host (ESXi) is failing — VMs on that host may be degraded or paused.

Evidence:
- Multiple VMs on the same ESXi host showing issues simultaneously
- vSphere shows host in "disconnected", "not responding", or "error" state
- Physical server hardware alert (RAID degraded, PSU failure, etc.)

Fix: VMware vSphere admin intervention. Migrate VMs to another host if possible. Field engineer for physical server hardware.

### vm_memory_balloon
VMware memory ballooning is stealing memory from the guest, causing GC pressure or performance degradation without a hard crash.

Evidence:
- Services running but very slow / high GC pause times in JVM logs
- vSphere shows balloon driver active for this VM
- Memory overcommit on the ESXi host

Fix: vSphere admin must adjust memory reservation or reduce overcommit. Alert engineer.

### clock_skew
VM clock drifted significantly from other hosts — causes message correlation failures, token expiry, and TLS cert errors.

Evidence:
- Auth errors with "token expired" despite recent login
- Message timestamps look wrong relative to other logs
- NTP service on the VM stopped or unreachable

Fix: Sync VM clock via `ntpdate` or `chronyc makestep`. Restart NTP service.

## What infra delivers

Infra is a **diagnostic and routing** subagent. It identifies the specific VM, resource, and failure type, and hands back to the domain subagent once the infrastructure issue is resolved (or escalates to the engineer for hands-on intervention):

```
Infra finding:
  Host:    {VM hostname / IP}
  Issue:   {oom_crash / disk_full / network_partition / vsphere_host / ...}
  Evidence: {specific log line or metric}
  Fix:      {what engineer does at the VM or vSphere level}
  After fix: {which domain subagent to resume in}
```

## Evidence gathering order
1. `history_search` — "{client_id} VM OOM" or "infra crash {hostname}"
2. `phoenix_resolve` — connectivity tier (infra incidents may prevent direct connect)
3. `log_read` — syslog / OS-level logs for OOM kill, disk errors, network failures
4. `log_read` — vSphere / ESXi host logs if accessible
5. `github_read` — client overlay for VM specs, JVM heap config, service names
6. `db_state_read` — not typically useful for infra; skip unless checking DB-specific disk issue
