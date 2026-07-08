# Glossary: WCS (Warehouse Control System)

| Term | Definition |
|---|---|
| `WCS` | Warehouse Control System; routes totes physically through the warehouse from pick station through sortation, packing, labeling, and outbound |
| `TCP/IP socket` | Sole communication channel between WES and WCS; always TCP, never ActiveMQ or REST |
| `socket listener` | WCS component that accepts incoming TCP connections from WES |
| `hypervisor boundary` | The VMware vSphere layer; physical hardware faults below it go to field engineer; VM/OS/service faults above it go to WCS or infra subagent |
| `Windows service` | WCS runs as C and C# Windows services on a Windows VM; managed via `Restart-Service` in PowerShell |
| `MS SQL Server` | Database used by WCS; typically co-located on the same Windows VM as the WCS service |
| `VMware vSphere` | Hypervisor platform managing the Windows VM that runs WCS |
| `sorter` | Physical conveyor system that routes totes to different lanes post-pick |
| `diverter actuator` | Physical component on the sorter that directs totes to the correct lane or chute |
| `E-stop` | Emergency stop event at a physical point in the warehouse conveyor system |
| `socket_failure` | WES→WCS TCP socket dropped, half-open, or connection refused; affects all totes simultaneously |
| `wcs_service_down` | WCS Windows service not running; no routing activity for any entity |
| `ms_sql_unreachable` | WCS service running but cannot connect to MS SQL Server; silent failure — no socket errors, just no order state writes |
| `lost_ack (WCS)` | WCS completed its task and sent ack, but WES did not receive it; entity is already in correct physical state |
| `routing_error` | WCS sent a tote to the wrong chute, packing station, or outbound lane; check routing table config in client overlay |
| `hardware_fault` | Physical hardware failure at a conveyor point (sorter jam, E-stop, diverter fault); escalate to field engineer |
| `on_takeaway` | Order state: tote placed on the takeaway conveyor after picking |
| `at_sorter` | Tote state: tote is at the sorter being directed to its destination lane |
| `at_packing` | Tote state: tote has arrived at the packing station |
| `Restart-Service` | PowerShell command used to restart the WCS Windows service; confirm service name from GitHub code before using |
