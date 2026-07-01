# 1 · Warehouse Operations — Systems & How They Work Together

How a supplier delivery becomes a shipped order, and the software systems (mostly built by our company, a systems integrator for 125+ client warehouses) that make each step possible.

---

## 1.1 The end-to-end flow

```mermaid
flowchart TD
    A[Supplier delivery arrives] --> B[DECANT software<br/>decides item→bin placement]
    B --> C{Storage backend<br/>per client}
    C -->|some clients| D[AutoStore]
    C -->|other clients| E[Alternate storage<br/>e.g. Knapp]
    F[Customer places order] --> G[Client WMS<br/>owned by client]
    G -->|order sent in| H[ESB<br/>ActiveMQ + Apache Camel]
    H --> I[WES / Orchestrator Engine OE<br/>prioritizes orders]
    I --> J[PICKING ENGINE]
    J --> D
    J --> E
    D --> K[Bins to ports / stations]
    E --> K
    K --> L[Operator picks items into tote/cart<br/>consolidates from many bins]
    L --> M[Tote onto takeaway / conveyor]
    M --> N[WCS<br/>Warehouse Control Software]
    N --> O[Sorter + divert hardware → chute]
    O --> P[Packing station]
    P --> Q[Labeling station<br/>print-and-apply printers]
    Q --> R[Outbound conveyor]
    R --> S[Outbound trucks]

    style B fill:#e8f0fe
    style I fill:#fce8e6
    style J fill:#e8f0fe
    style N fill:#e8f0fe
    style G fill:#fef7e0
```

> **WMS is always the client's** (shown in yellow). Everything else in the spine is our software. The two storage backends depend on the client.

### Stage-by-stage

| Stage | What happens | Owning software |
|---|---|---|
| Inbound / decant | Supplier items placed into bins | **Decant** |
| Storage | Bins stored, retrieved on demand | **AutoStore** or **alternate (Knapp)** per client |
| Order intake | Customer orders enter from the client's WMS | **WMS (client-owned)** → into our stack |
| Orchestration | Orders prioritized; execution coordinated | **WES / OE** |
| Picking | Storage instructed to bring bins; operators pick into totes | **Picking engine** + storage |
| Control | Totes routed physically across the floor | **WCS** |
| Sortation | Divert to the right chute | **WCS** + sorter/divert hardware |
| Pack & label | Order packed, label printed and applied | **WCS**-directed packing & labeling stations |
| Outbound | Package to truck | **WCS** |

---

## 1.2 WES is the conductor for the whole second half

WES (also called the Orchestrator Engine / OE) decides order **priority**, then keeps instructing WCS at each physical stage. There are several **flavors** of WES depending on the client.

```mermaid
sequenceDiagram
    participant WMS as Client WMS
    participant ESB as ESB (ActiveMQ+Camel)
    participant WES as WES / OE
    participant PICK as Picking Engine
    participant STORE as AutoStore / Knapp
    participant WCS as WCS
    participant HW as Sorter / Divert HW

    WMS->>ESB: order
    ESB->>WES: routed order
    Note over WES: prioritize orders
    WES->>PICK: release pick (prioritized)
    PICK->>STORE: retrieve bins
    STORE-->>PICK: bins to ports
    Note over PICK: operator picks into tote
    WES->>WCS: tote onto takeaway
    WES->>WCS: divert to chute
    WCS->>HW: fire diverter
    WES->>WCS: route to packing
    WES->>WCS: route to labeling (print + apply)
    WES->>WCS: route to outbound conveyor
    Note over WCS: → trucks
```

---

## 1.3 Transport layer — how systems talk (this matters for diagnosis)

Communication is **not uniform**. Each transport fails differently and is diagnosed differently.

```mermaid
flowchart LR
    WES[WES / OE]
    subgraph transports
      direction TB
      MQ[ActiveMQ + ESB<br/>messages on channels<br/>Camel routes to consumers]
      REST[RESTful APIs]
      SOCK[TCP/IP sockets]
    end
    WES -->|most downstream, our systems| MQ
    WES -->|sometimes| REST
    WES -->|sometimes| SOCK
    WES ===|ALWAYS sockets| WCS[WCS]
    MQ --> DOWN[Downstream consumers]
    REST --> DOWN
    SOCK --> WCS

    note1[Each downstream system ACKs<br/>back to WES when its task completes]
    DOWN -.ack.-> WES
    WCS -.ack.-> WES
```

- **WES → our downstream systems:** mostly **ActiveMQ + ESB**; sometimes **REST**; sometimes **sockets**.
- **WES ⇄ WCS:** **always TCP/IP sockets**.
- **Acknowledgment path:** every system **acks back to WES** when its task completes. A task that completed but whose ack never returned leaves WES thinking work is still pending — a classic, maddening failure mode.

**ESB internals:** built on **ActiveMQ** for transport (messages on channels) with **Apache Camel** layered on top to route messages from channels to the right consumer.

| Transport | Typical failure signature |
|---|---|
| ActiveMQ / ESB | stuck queue, dead consumer, backed-up channel, poison/dead-letter |
| REST | timeout, 500, auth error, retry storm |
| TCP/IP sockets (WCS) | dropped/hung/half-open connection, framing mismatch |
| Ack path | work done but ack lost → WES stuck waiting |

---

## 1.4 IMS — the integrity gate that can stop everything

**IMS (Inventory Management System, ours)** keeps inventory counted correctly via **cycle counting** as orders are fulfilled. If a cycle count is wrong, **IMS halts fulfillment at that point** for correction.

```mermaid
flowchart TD
    FLOW[Fulfillment in progress] --> CC{IMS cycle count<br/>correct?}
    CC -->|yes| CONT[Continue fulfillment]
    CC -->|no — discrepancy| HALT[IMS HALTS fulfillment<br/>awaiting correction]
    HALT --> FIX[Counts corrected] --> CONT

    style HALT fill:#fce8e6
```

> **Key insight for support:** a stalled order is *not always a failure*. IMS may be **deliberately holding** it because counts didn't reconcile. A naive diagnosis chases a phantom bug; the right move is to check whether IMS flagged a discrepancy.

---

## 1.5 The software is structured as base + per-client customization

```mermaid
flowchart TD
    BASE[BASE software<br/>base GitHub org<br/>common across all clients] --> T[Target overlay<br/>target org]
    BASE --> M[Mr Price overlay<br/>mr price org]
    BASE --> D[... 125+ client overlays<br/>one org each]

    T --> TDEP[Deployed on-prem at Target]
    M --> MDEP[Deployed on-prem at Mr Price]
```

- A **base software** plus a **customization per client** built on top, applied across all domains.
- All of it lives in **GitHub**: base in the **base org**, each client in its **own org** (Target org, Mr Price org, …).
- Any real diagnosis reads **base + the relevant client overlay**.

---

## 1.6 The deep truth: it's a state machine of domain entities

Every order, tote, bin, and inventory record is a **domain entity** with a lifecycle: it moves from an **initial state**, through **intermediate states**, to an **end state**.

```mermaid
stateDiagram-v2
    [*] --> received
    received --> validated
    validated --> prioritized
    prioritized --> released
    released --> picking
    picking --> picked
    picked --> on_takeaway
    on_takeaway --> sorted
    sorted --> packed
    packed --> labeled
    labeled --> loaded
    loaded --> shipped
    shipped --> [*]

    note right of prioritized
      Each transition is OWNED by a domain,
      has an expected TRIGGER, and a set of
      BLOCKERS (incl. IMS halts, lost acks,
      stuck messages, dropped sockets).
    end note
```

> **A production incident is, almost by definition, an entity stuck in an intermediate state it should have left** — "order is late" really means "the order entity is stuck in `picking` when it should be `packed`." Entities also **depend on each other** (an order can't advance until its bins arrive), so diagnosis means tracing the dependency web until you find the real blocker.

---

## 1.7 One-page mental model

```mermaid
flowchart LR
    subgraph INBOUND
      S[Supply] --> DEC[Decant] --> ST[Storage]
    end
    subgraph DEMAND
      WMS[Client WMS] --> ESB[ESB] --> WES[WES/OE]
    end
    subgraph EXECUTION
      WES --> PICK[Picking] --> ST
      ST --> TOTE[Totes] --> WCS[WCS] --> SORT[Sort/Pack/Label] --> OUT[Outbound]
    end
    IMS[IMS integrity gate] -.can halt.-> WES
    IMS -.can halt.-> PICK
    IMS -.can halt.-> WCS
```

**Three intertwined layers to hold in mind:** the **flow** (inbound→outbound), the **transport** (ActiveMQ/REST/sockets + ack path), and the **integrity/control** layer (IMS can stop the machine).

---

## 1.8 WCS hardware and software stack

WCS runs on a well-defined on-prem stack. Understanding it is critical for discriminating between software bugs, infrastructure failures, and physical hardware faults.

```mermaid
flowchart TD
    subgraph "Physical hardware (field team)"
      HW[Sorter / divert / conveyor mechanics]
    end
    subgraph "Infrastructure layer (infra subagent)"
      DELL[x86 Dell server]
      VMWARE[VMware vSphere + hypervisor]
      VM[Windows VM]
    end
    subgraph "WCS software layer (WCS subagent)"
      SVC[C / C# Windows service]
      MSSQL[MS SQL database<br/>usually co-located on same VM]
    end

    HW -.physical failure.-> DELL
    DELL --> VMWARE --> VM --> SVC --> MSSQL

    style HW fill:#fce8e6
    style SVC fill:#e8f0fe
    style MSSQL fill:#e8f0fe
```

**The discrimination rule — where the hypervisor boundary sits:**

| Symptom | Owning team |
|---|---|
| Divert / sorter / conveyor mechanics jammed | Field engineer (hardware) — outside our software stack |
| VM down, hypervisor issue, disk/memory exhaustion, vSphere fault | **Infra subagent** |
| Windows service crashed or hung, MS SQL issue, C/C# code bug | **WCS subagent** |

- WCS software is **C and C# code** running as **Windows services**.
- The **MS SQL database** (WCS's only DB type) typically runs on the **same Windows VM** as the service.
- When the WCS subagent exhausts its software-layer hypotheses and the symptom points below the hypervisor, it routes to **infra**. When the root cause is clearly physical mechanics, the orchestrator escalates to a **field engineer**.

> **Key for diagnosis:** check whether the Windows service is alive and MS SQL is reachable before assuming hardware. Most WCS incidents are software-layer; physical hardware failures are relatively rare and have distinct signatures (sensor faults, E-stop events, mechanical alerts in WCS logs).
