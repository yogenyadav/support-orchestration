# Glossary: Infrastructure

| Term | Definition |
|---|---|
| `infra` | Infrastructure domain; owns VM/hypervisor/OS-layer issues that cause software symptoms in any domain subagent |
| `hypervisor boundary` | VMware vSphere layer; the dividing line between infra responsibility (VM and above) and field engineer responsibility (physical hardware below) |
| `VMware vSphere` | Hypervisor platform managing client VMs at warehouse sites |
| `ESXi host` | Physical VMware server that runs one or more client VMs; ESXi host failure can take down all VMs on that machine |
| `guest VM` | Virtual machine running a client application (WCS Windows VM, WES Linux VM, etc.) |
| `oom_crash` | Most common infra failure: guest VM ran out of memory; JVM service was killed by the OS |
| `java.lang.OutOfMemoryError` | JVM log signal indicating an OOM crash; the service process will be absent with no clean shutdown entry |
| `JVM heap` | Java Virtual Machine memory pool; configured via `-Xmx` in the client overlay; may need tuning if OOM is recurring |
| `disk_full` | VM disk partition reached 100%; services fail to write logs, DB files, or temp data — often a silent failure |
| `network_partition` | VM cannot reach a specific other service; may be VM-scoped (virtual NIC, vSwitch) or site-wide (switch/VLAN) |
| `vsphere_host_issue` | ESXi host is failing or disconnected; multiple VMs on the same host show issues simultaneously |
| `vm_memory_balloon` | VMware memory ballooning is reclaiming guest memory under host pressure; causes high GC pause times without a hard crash |
| `balloon driver` | VMware guest driver that returns memory to the hypervisor under overcommit conditions |
| `clock_skew` | VM clock has drifted from other hosts; causes auth token expiry, TLS cert errors, and message correlation failures |
| `NTP` | Network Time Protocol; keeps VM clocks synchronized; if stopped, clock skew accumulates |
| `chronyc makestep` | Command to force-synchronize a Linux VM clock via chrony NTP client |
| `OOM kill` | OS action of forcibly terminating a process when the VM runs out of memory |
| `memory overcommit` | vSphere host configuration where the sum of VM memory reservations exceeds physical RAM; triggers ballooning |
| `infra finding` | Structured output the infra subagent produces: host, issue type, evidence, fix, and which domain subagent to resume after the fix |
