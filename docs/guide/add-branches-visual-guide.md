# add_branches Visual Guide

How to connect RoutingClass instances into hierarchies with the instance form
of `add_branches`.

## Core Concept

`add_branches` lives on **RoutingClass** (not on Router). Every RoutingClass
owns exactly one router (`self.route`). Its **instance form**
(`{"name": alias, "instance": child}`) attaches an already-built child eagerly
and does two things:

1. Sets the parent-child relationship (`child._routing_parent = self`)
2. Links the child's router into the parent's router (`parent.route._children[alias] = child.route`, via `include()`)

```mermaid
graph LR
    subgraph "RoutingClass (parent)"
        P["self"]
        PR["route (Router)"]
        P --> PR
    end
    subgraph "RoutingClass (child)"
        C["child"]
        CR["route (Router)"]
        C --> CR
    end
    P -- "_routing_parent" --> C
    PR -- "_children['sales']" --> CR
    style P fill:#e1f5fe
    style C fill:#fff3e0
```

---

## Scenario 1: Attaching a Child

Parent and child each own one router. The child's router is linked under the alias.

```mermaid
graph TB
    subgraph "Parent"
        PA["route (Router)"]
        PA --- E1["health &bull; entry"]
        PA --- E2["status &bull; entry"]
        PA === S1["[sales]"]
    end
    subgraph "Child (vendite)"
        CA["route (Router)"]
        CA --- E3["ordini &bull; entry"]
        CA --- E4["fatture &bull; entry"]
    end
    S1 --> CA

    style PA fill:#bbdefb
    style CA fill:#ffe0b2
    style S1 fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
```

**Syntax:**

```python
self.add_branches({"name": "sales", "instance": vendite})
```

**Access paths:**

```python
self.route.node("health")()         # local entry
self.route.node("sales/ordini")()   # child entry
self.route.node("sales/fatture")()  # child entry
```

**Rule:** the `"name"` key is the alias in the parent's router. Every
RoutingClass has exactly one router, so there is nothing else to map.

---

## Scenario 2: Child with Its Own Children

An attached child brings its whole sub-tree along.

```mermaid
graph TB
    subgraph "Parent"
        PA["route (Router)"]
        PA --- E1["health &bull; entry"]
        PA === S1["[sales]"]
    end
    subgraph "Child (vendite)"
        CA["route (Router)"]
        CA --- E3["ordini &bull; entry"]
        CA --- E4["fatture &bull; entry"]
        CA === SS["[statistiche]"]
    end
    subgraph "Grandchild"
        GR["route (Router)"]
        GR --- E6["mensili &bull; entry"]
        GR --- E7["annuali &bull; entry"]
    end
    S1 --> CA
    SS --> GR

    style PA fill:#bbdefb
    style CA fill:#ffe0b2
    style S1 fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    style SS fill:#c8e6c9
    style GR fill:#f3e5f5
```

**Syntax:**

```python
vendite.add_branches({"name": "statistiche", "instance": statistiche})
self.add_branches({"name": "sales", "instance": vendite})
```

**Access paths:**

```python
self.route.node("sales/ordini")()              # child entry
self.route.node("sales/statistiche/mensili")() # grandchild entry
```

---

## Scenario 3: Multiple Surfaces — Composition

One class exposes one router. A service that needs several surfaces (public API,
admin, ...) is split into **one class per surface**, composed under grouping
nodes. `Section` provides an empty grouping node without a dedicated class.

```mermaid
graph TB
    subgraph "Application"
        PA["route (Router)"]
        PA --- E1["health &bull; entry"]
        PA === S1["[api]"]
        PA === S2["[admin]"]
    end
    subgraph "Section (Public API)"
        SA["route (Router)"]
        SA === S3["[orders]"]
    end
    subgraph "Section (Admin area)"
        SB["route (Router)"]
        SB === S4["[orders]"]
    end
    subgraph "OrdersApi"
        CA["route (Router)"]
        CA --- E3["get_data &bull; entry"]
    end
    subgraph "OrdersAdmin"
        CB["route (Router)"]
        CB --- E4["manage &bull; entry"]
    end
    S1 --> SA
    S2 --> SB
    S3 --> CA
    S4 --> CB

    style PA fill:#bbdefb
    style SA fill:#c5cae9
    style SB fill:#c5cae9
    style CA fill:#ffe0b2
    style CB fill:#ffe0b2
    style S1 fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    style S2 fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
```

**Syntax:**

```python
from genro_routes import Section

api = Section("Public API")
admin = Section("Admin area")
self.add_branches({"name": "api", "instance": api})
self.add_branches({"name": "admin", "instance": admin})
api.add_branches({"name": "orders", "instance": OrdersApi()})
admin.add_branches({"name": "orders", "instance": OrdersAdmin()})
```

**Access paths:**

```python
self.route.node("api/orders/get_data")()   # public surface
self.route.node("admin/orders/manage")()   # admin surface
```

> **Note:** earlier versions supported multiple routers per class with a
> `router_*` cross-mapping DSL. That feature was removed: composition (one
> class per surface, `Section` for grouping) covers the same use cases with a
> single calling style.

---

## Syntax Reference

### `add_branches({"name": alias, "instance": child})`

```python
self.add_branches({"name": "alias", "instance": child})
```

- The child's router is linked under `alias` in the parent's router (eager: at the call)
- Sets `child._routing_parent = self`
- `params` is not allowed together with `instance` (`ValueError`)
- `instance` must be a `RoutingClass` (`TypeError` otherwise)
- Raises `ValueError` on alias collision or if the child is already bound to another parent

### `include` (on Router — low level)

Direct router-to-router or entry-alias linking.

**Include a Router:**

```python
self._sys.route.include(swagger.route, name="swagger")
```

- Links the source router as a child of this router
- On the **primary** attachment (source has no parent yet) it sets
  `_routing_parent` on the source's owner and triggers plugin inheritance
- Subsequent includes of the same router are navigational shortcuts only

**Include a RouterNode (entry alias):**

```python
fatture.route.include(
    pagamenti.route.node("collega_a_fattura"),
    name="collega_pagamento",
)
```

- Creates an alias: same handler, visible from two paths
- No copy — the original MethodEntry is shared
- `name` is required for RouterNode sources

### `detach_instance` (on Router)

```python
self.route.detach_instance(child)
```

- Removes every alias of `child`'s router from this router's `_children`
- Clears `child._routing_parent`
- Stays on **Router**, not RoutingClass

---

## Scenario 4: Entry Alias

The same handler declared in one service, visible in another's tree.

```mermaid
graph TB
    subgraph "Pagamenti"
        PA["route (Router)"]
        PA --- E1["lista_pagamenti &bull; entry"]
        PA --- E2["collega_a_fattura &bull; entry"]
    end
    subgraph "Fatture"
        FA["route (Router)"]
        FA --- E3["lista_fatture &bull; entry"]
        FA -.- E4["collega_pagamento &bull; alias"]
    end
    E4 -.->|"same handler"| E2

    style PA fill:#bbdefb
    style FA fill:#ffe0b2
    style E4 fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,stroke-dasharray: 5 5
```

**Syntax:**

```python
fatture.route.include(
    pagamenti.route.node("collega_a_fattura"),
    name="collega_pagamento",
)
```

**Access paths:**

```python
pagamenti.route.node("collega_a_fattura")(1, 2)   # original
fatture.route.node("collega_pagamento")(1, 2)     # alias — same handler
```

---

## Decision Guide

```mermaid
flowchart TD
    A["What do you need?"] --> B["Expose a child service\nunder an alias"]
    A --> C["A pure grouping level\n(no handlers)"]
    A --> D["Several surfaces\n(api, admin, ...)"]
    A --> E["Make one entry visible\nfrom another tree"]

    B --> F["add_branches(name='alias', instance=child)"]
    C --> G["add_branches(name='group', instance=Section('...'))"]
    D --> H["One RoutingClass per surface,\ncomposed with add_branches"]
    E --> I["router.include(node, name='alias')"]

    style F fill:#c8e6c9,stroke:#2e7d32
    style G fill:#c8e6c9,stroke:#2e7d32
    style H fill:#fff3e0,stroke:#ef6c00
    style I fill:#f3e5f5,stroke:#7b1fa2
```

---

## Real-World Example

```python
from genro_routes import RoutingClass, route

class AuthService(RoutingClass):
    @route()
    def login(self, username: str, password: str):
        return {"token": "..."}

class UserService(RoutingClass):
    @route()
    def list_users(self):
        return ["alice", "bob"]

class Application(RoutingClass):
    def __init__(self):
        self.route.plug("logging")
        self.auth = AuthService()
        self.users = UserService()

        self.add_branches({"name": "auth", "instance": self.auth})
        self.add_branches({"name": "users", "instance": self.users})

app = Application()

app.route.node("auth/login")("alice", "secret")
app.route.node("users/list_users")()
```
