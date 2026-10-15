#!/usr/bin/env python3
"""Grade the declarative stack orchestrator the agent was asked to build.

The orchestrator is graded exclusively through its *public* behaviour, driven
against a stand-in Docker Engine.  We never inspect the agent's internal data
structures, label keys or method bodies: we hand it declarative stack
descriptions, let it talk to a fake ``APIClient`` that faithfully remembers the
resources it is told to create, and then assert the things the contract
promises -- correct ordering, idempotency, drift-driven recreation, orphan
cleanup, dependency health-gating and clean teardown.

To keep the grade honest the git-tracked source tree is restored before the
package is imported, so the only thing under test is the agent's *new*,
untracked ``docker/orchestration`` package running on top of pristine
docker-py.
"""

from __future__ import annotations

import itertools
import os
import subprocess
import sys

APP = "/app"


# ---------------------------------------------------------------------------
# Harness plumbing
# ---------------------------------------------------------------------------

def _restore_tracked_sources():
    """Revert edits to git-tracked files so only new code is graded.

    The task is to *add* a package, not to change docker-py.  Restoring
    tracked files means the orchestrator cannot be "passed" by weakening the
    library it builds on; the agent's untracked package is left in place.
    """
    try:
        subprocess.run(
            ["git", "checkout", "HEAD", "--", "."],
            cwd=APP,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print("WARNING: could not restore tracked sources: {}".format(exc))


def _load():
    """Import the agent's orchestration package and pull out its public API."""
    sys.path.insert(0, APP)
    import importlib

    try:
        pkg = importlib.import_module("docker.orchestration")
    except Exception as exc:
        raise AssertionError(
            "could not import docker.orchestration: {}: {}".format(
                type(exc).__name__, exc
            )
        )

    missing = [
        n for n in ("StackDeployer", "StackSpec")
        if getattr(pkg, n, None) is None
    ]
    if missing:
        raise AssertionError(
            "docker.orchestration is missing required names: {}".format(missing)
        )
    return pkg


# ---------------------------------------------------------------------------
# A stateful stand-in for docker.api.APIClient
# ---------------------------------------------------------------------------

class FakeEngine:
    """A minimal, faithful Docker Engine simulator for one project.

    It records every resource creation together with whatever labels the
    orchestrator asked for, and serves them back through ``containers`` /
    ``networks`` / ``volumes`` so that a *fresh* deployer -- which keeps no
    state of its own -- can recognise them on a later call.  It deliberately
    echoes labels verbatim rather than imposing a schema, so the grade does
    not depend on exactly how the orchestrator chooses to label things.

    Every call is appended to ``self.calls`` as a ``(verb, *args)`` tuple, so
    ordering assertions can read the wire-level sequence.
    """

    def __init__(self, health="healthy"):
        self.containers_store = {}   # name -> record
        self.networks_store = {}     # name -> record
        self.volumes_store = {}      # name -> record
        self.create_kwargs = {}      # container name -> create_container kwargs
        self.calls = []
        self._ids = itertools.count(1)
        self._health = health

    # -- observation -----------------------------------------------------

    def containers(self, quiet=False, all=False, trunc=False, latest=False,
                   since=None, before=None, limit=-1, size=False,
                   filters=None):
        self.calls.append(("containers",))
        return [dict(r) for r in self.containers_store.values()]

    def networks(self, names=None, ids=None, filters=None):
        self.calls.append(("networks",))
        return [dict(r) for r in self.networks_store.values()]

    def volumes(self, filters=None):
        self.calls.append(("volumes",))
        return {"Volumes": [dict(r) for r in self.volumes_store.values()]}

    def inspect_container(self, container):
        self.calls.append(("inspect_container", container))
        rec = self.containers_store.get(container)
        if rec is None:
            # Also allow lookup by id.
            for r in self.containers_store.values():
                if r.get("Id") == container:
                    rec = r
                    break
        kw = self.create_kwargs.get(rec["Names"][0].lstrip("/")) if rec else {}
        return {
            "Id": rec.get("Id") if rec else container,
            "Name": rec.get("Names", ["/" + str(container)])[0] if rec else container,
            "State": {
                "Running": True,
                "Status": "running",
                "Health": {"Status": self._health},
            },
            "Config": {
                "Image": (kw or {}).get("image"),
                "Labels": (rec or {}).get("Labels", {}),
                "Env": [
                    "{}={}".format(k, v)
                    for k, v in ((kw or {}).get("environment") or {}).items()
                ],
            },
        }

    # -- helper builders (return opaque sentinels) -----------------------

    def create_host_config(self, *args, **kwargs):
        self.calls.append(("create_host_config", kwargs))
        return {"__hostconfig__": kwargs}

    def create_endpoint_config(self, *args, **kwargs):
        self.calls.append(("create_endpoint_config", kwargs))
        return {"__endpoint__": kwargs}

    def create_networking_config(self, *args, **kwargs):
        self.calls.append(("create_networking_config", args, kwargs))
        return {"__netconfig__": (args, kwargs)}

    # -- mutation --------------------------------------------------------

    def create_network(self, name, **kwargs):
        self.calls.append(("create_network", name))
        self.networks_store[name] = {
            "Id": "net_" + name,
            "Name": name,
            "Labels": dict(kwargs.get("labels") or {}),
        }
        return {"Id": "net_" + name}

    def create_volume(self, name=None, **kwargs):
        self.calls.append(("create_volume", name))
        self.volumes_store[name] = {
            "Name": name,
            "Labels": dict(kwargs.get("labels") or {}),
        }
        return {"Name": name}

    def create_container(self, image=None, **kwargs):
        name = kwargs.get("name")
        cid = "ctr_{}".format(next(self._ids))
        self.calls.append(("create_container", name, image))
        full = dict(kwargs)
        full["image"] = image
        self.create_kwargs[name] = full
        self.containers_store[name] = {
            "Id": cid,
            "Names": ["/" + str(name)],
            "Labels": dict(kwargs.get("labels") or {}),
            "State": "running",
            "Status": "Up",
        }
        return {"Id": cid}

    def start(self, container, *args, **kwargs):
        self.calls.append(("start", container))

    def stop(self, container, *args, **kwargs):
        self.calls.append(("stop", container))

    def wait(self, container, *args, **kwargs):
        self.calls.append(("wait", container))
        return {"StatusCode": 0}

    def remove_container(self, container, *args, **kwargs):
        self.calls.append(("remove_container", container))
        for name, rec in list(self.containers_store.items()):
            if name == container or rec.get("Id") == container:
                del self.containers_store[name]
                self.create_kwargs.pop(name, None)
                break

    def remove_network(self, net_id, *args, **kwargs):
        self.calls.append(("remove_network", net_id))
        for name, rec in list(self.networks_store.items()):
            if name == net_id or rec.get("Id") == net_id:
                del self.networks_store[name]
                break

    def remove_volume(self, name, *args, **kwargs):
        self.calls.append(("remove_volume", name))
        for vname, rec in list(self.volumes_store.items()):
            if vname == name or rec.get("Name") == name:
                del self.volumes_store[vname]
                break

    # -- convenience for assertions -------------------------------------

    def verbs(self):
        return [c[0] for c in self.calls]

    def created_container_order(self):
        return [c[1] for c in self.calls if c[0] == "create_container"]

    def create_call_for(self, image):
        for c in self.calls:
            if c[0] == "create_container" and c[2] == image:
                return c
        return None


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _noop(_seconds):
    """A sleeper that never actually sleeps -- keeps the health gate fast."""


def _make_deployer(pkg, engine, project="proj"):
    """Construct a StackDeployer, tolerating either sleeper kw name."""
    try:
        return pkg.StackDeployer(engine, project, sleeper=_noop)
    except TypeError:
        # Fall back to positional / no injected sleeper.
        return pkg.StackDeployer(engine, project)


BASE_SPEC = {
    "services": {
        "db": {
            "image": "postgres:16",
            "volumes": ["pgdata:/var/lib/postgresql/data"],
        },
        "cache": {"image": "redis:7"},
        "web": {
            "image": "nginx:1.25",
            "depends_on": {"db": {"condition": "service_healthy"}},
            "ports": ["80:8080"],
            "networks": ["frontend"],
        },
    },
    "networks": {"frontend": {}},
    "volumes": {"pgdata": {}},
}


def check_spec_validation(pkg):
    """Malformed specs must be rejected with the right error family."""
    SpecError = pkg.SpecError

    # Missing image.
    try:
        pkg.StackSpec.from_dict({"services": {"a": {}}})
    except SpecError:
        pass
    else:
        raise AssertionError("a service without 'image' must raise SpecError")

    # No services at all.
    try:
        pkg.StackSpec.from_dict({"services": {}})
    except SpecError:
        pass
    else:
        raise AssertionError("an empty stack must raise SpecError")

    # Unknown dependency.
    UnknownDependencyError = getattr(pkg, "UnknownDependencyError", SpecError)
    try:
        pkg.StackSpec.from_dict(
            {"services": {"a": {"image": "x", "depends_on": ["ghost"]}}}
        )
    except SpecError:
        pass
    else:
        raise AssertionError("an unknown dependency must raise a SpecError")

    # Cyclic dependency.  The contract only says a depends_on cycle "must
    # error" -- it does not dictate *when*.  Both are valid implementations:
    #   * eagerly, at from_dict() time, or
    #   * lazily, when a dependency order is first required (topological_order).
    # Accept a SpecError raised at either point.
    CyclicDependencyError = getattr(pkg, "CyclicDependencyError", SpecError)
    cyclic = {
        "services": {
            "a": {"image": "x", "depends_on": ["b"]},
            "b": {"image": "y", "depends_on": ["a"]},
        }
    }
    try:
        spec = pkg.StackSpec.from_dict(cyclic)
    except SpecError:
        pass  # detected eagerly at parse/validate time -- acceptable.
    else:
        try:
            spec.topological_order()
        except SpecError:
            pass  # detected lazily when an order is needed -- acceptable.
        else:
            raise AssertionError("a dependency cycle must raise a SpecError")


def check_config_hash_stable(pkg):
    """The config hash must ignore key ordering but track real changes."""
    a = pkg.StackSpec.from_dict(
        {"services": {"s": {"image": "x", "environment": ["A=1", "B=2"]}}}
    ).services["s"]
    b = pkg.StackSpec.from_dict(
        {"services": {"s": {"image": "x", "environment": ["B=2", "A=1"]}}}
    ).services["s"]
    if a.config_hash() != b.config_hash():
        raise AssertionError(
            "config_hash must not depend on environment ordering"
        )

    c = pkg.StackSpec.from_dict(
        {"services": {"s": {"image": "y", "environment": ["A=1", "B=2"]}}}
    ).services["s"]
    if a.config_hash() == c.config_hash():
        raise AssertionError(
            "config_hash must change when the image changes"
        )


def check_plan_ordering(pkg):
    """plan() over an empty world creates infra before containers, in order."""
    engine = FakeEngine()
    dep = _make_deployer(pkg, engine)
    spec = pkg.StackSpec.from_dict(BASE_SPEC)

    plan = dep.plan(spec)
    if plan.is_noop:
        raise AssertionError("plan over an empty world must not be a no-op")

    # Planning must not mutate anything.
    if engine.containers_store or engine.networks_store or engine.volumes_store:
        raise AssertionError("plan() must not create any resources")

    # Extract the resource names in plan order, restricted to creates.
    def kind(a):
        return getattr(a.kind, "value", a.kind)

    def rtype(a):
        return getattr(a.resource_type, "value", a.resource_type)

    creates = [a for a in plan.actions if kind(a) == "create"]
    types_in_order = [rtype(a) for a in creates]

    # Every network/volume create must precede every container create.
    last_infra = max(
        (i for i, t in enumerate(types_in_order)
         if t in ("network", "volume")),
        default=-1,
    )
    first_container = min(
        (i for i, t in enumerate(types_in_order) if t == "container"),
        default=len(types_in_order),
    )
    if last_infra > first_container:
        raise AssertionError(
            "networks/volumes must be planned before containers: {}".format(
                types_in_order
            )
        )

    # Containers must appear in dependency order: db before web.
    container_creates = [a.resource_name for a in creates
                         if rtype(a) == "container"]
    db_i = _first_index(container_creates, "db")
    web_i = _first_index(container_creates, "web")
    if db_i is None or web_i is None:
        raise AssertionError(
            "expected db and web containers in the plan, got {}".format(
                container_creates
            )
        )
    if db_i > web_i:
        raise AssertionError(
            "web depends on db, so db must be created first: {}".format(
                container_creates
            )
        )


def _first_index(names, service_token):
    for i, n in enumerate(names):
        if service_token in n:
            return i
    return None


def check_apply_and_idempotency(pkg):
    """up() applies in order; a second up() over the same spec is a no-op."""
    engine = FakeEngine()
    dep = _make_deployer(pkg, engine)
    spec = pkg.StackSpec.from_dict(BASE_SPEC)

    dep.up(spec)

    # Networks and volumes must have been created before any container.
    verbs = engine.verbs()
    first_container_call = _index_of(verbs, "create_container")
    for infra_verb in ("create_network", "create_volume"):
        idxs = [i for i, v in enumerate(verbs) if v == infra_verb]
        for i in idxs:
            if i > first_container_call:
                raise AssertionError(
                    "{} happened after a container was created".format(
                        infra_verb
                    )
                )

    # Container creation order respects dependencies (db before web).
    order = engine.created_container_order()
    db_i = _first_index(order, "db")
    web_i = _first_index(order, "web")
    if db_i is None or web_i is None or db_i > web_i:
        raise AssertionError(
            "containers were not created in dependency order: {}".format(order)
        )

    # Each created container must be started.
    if verbs.count("start") < 3:
        raise AssertionError(
            "expected every created container to be started (>=3 starts), "
            "saw {}".format(verbs.count("start"))
        )

    # The web container must be created with the right image.
    if engine.create_call_for("nginx:1.25") is None:
        raise AssertionError("web was not created from image nginx:1.25")

    # --- idempotency: the second plan must be a no-op ---
    plan2 = dep.plan(spec)
    if not plan2.is_noop:
        raise AssertionError(
            "re-planning an unchanged, fully-deployed stack must be a no-op; "
            "got:\n{}".format(plan2.summary())
        )

    engine.calls.clear()
    dep.up(spec)
    mutating = [v for v in engine.verbs() if v.startswith(("create_container",
                "remove_container", "start", "stop"))]
    if mutating:
        raise AssertionError(
            "a second up() over an unchanged stack must not create, start, "
            "stop or remove containers; saw {}".format(mutating)
        )


def _index_of(seq, value):
    try:
        return seq.index(value)
    except ValueError:
        return len(seq)


def check_drift_recreates(pkg):
    """Changing a service's config must make the next plan recreate it."""
    engine = FakeEngine()
    dep = _make_deployer(pkg, engine)

    v1 = pkg.StackSpec.from_dict(
        {"services": {"api": {"image": "app:v1"}}}
    )
    dep.up(v1)

    # Same spec again: no-op (proves recognition works).
    if not dep.plan(v1).is_noop:
        raise AssertionError(
            "re-planning the identical single-service stack must be a no-op"
        )

    # Changed image: the plan must now contain a change for 'api'.
    v2 = pkg.StackSpec.from_dict(
        {"services": {"api": {"image": "app:v2"}}}
    )
    plan = dep.plan(v2)
    if plan.is_noop:
        raise AssertionError(
            "changing a service's image must produce a non-empty plan"
        )

    # Applying it must end with a container built from the new image, and the
    # old one must have been torn down (stop/remove) along the way.
    engine.calls.clear()
    dep.up(v2)
    verbs = engine.verbs()
    if "create_container" not in verbs:
        raise AssertionError("drift apply did not create a new container")
    if not ("remove_container" in verbs or "stop" in verbs):
        raise AssertionError(
            "recreating a drifted container must stop/remove the old one"
        )
    if engine.create_call_for("app:v2") is None:
        raise AssertionError("the recreated container did not use image app:v2")

    # Final state: exactly one api container, and re-planning is a no-op again.
    if not dep.plan(v2).is_noop:
        raise AssertionError(
            "after recreation, re-planning the new spec must be a no-op"
        )


def check_orphan_removal(pkg):
    """Dropping a service from the spec must plan (and apply) its removal."""
    engine = FakeEngine()
    dep = _make_deployer(pkg, engine)

    two = pkg.StackSpec.from_dict({
        "services": {
            "api": {"image": "app:v1"},
            "worker": {"image": "app:v1"},
        }
    })
    dep.up(two)

    one = pkg.StackSpec.from_dict(
        {"services": {"api": {"image": "app:v1"}}}
    )
    plan = dep.plan(one)

    def kind(a):
        return getattr(a.kind, "value", a.kind)

    removes = [a for a in plan.actions if kind(a) == "remove"]
    if not any("worker" in a.resource_name for a in removes):
        raise AssertionError(
            "removing 'worker' from the spec must plan its removal; plan:\n{}"
            .format(plan.summary())
        )

    engine.calls.clear()
    dep.up(one)
    # The worker container must be gone from the engine.
    if any("worker" in name for name in engine.containers_store):
        raise AssertionError("orphaned 'worker' container was not removed")
    # The api container must survive.
    if not any("api" in name for name in engine.containers_store):
        raise AssertionError("removing an orphan must not remove live services")


def check_health_gate(pkg):
    """A service_healthy dependency must gate on inspect_container.

    With a dependency that never becomes healthy, up() must not hang forever;
    it must give up and raise (a DeploymentError-family error), having polled
    the dependency's health at least once.
    """
    engine = FakeEngine(health="unhealthy")
    dep = _make_deployer(pkg, engine)

    spec = pkg.StackSpec.from_dict({
        "services": {
            "db": {"image": "postgres:16"},
            "web": {
                "image": "nginx:1.25",
                "depends_on": {"db": {"condition": "service_healthy"}},
            },
        }
    })

    DeploymentError = getattr(pkg, "DeploymentError", Exception)
    try:
        dep.up(spec)
    except DeploymentError:
        pass
    except Exception as exc:
        raise AssertionError(
            "an unhealthy gated dependency should raise a DeploymentError, "
            "got {}: {}".format(type(exc).__name__, exc)
        )
    else:
        raise AssertionError(
            "up() must not succeed while a service_healthy dependency is "
            "never healthy"
        )

    if not any(c[0] == "inspect_container" for c in engine.calls):
        raise AssertionError(
            "the health gate must poll inspect_container on the dependency"
        )


def check_health_gate_success(pkg):
    """When the dependency is healthy, the gated service comes up fine."""
    engine = FakeEngine(health="healthy")
    dep = _make_deployer(pkg, engine)
    spec = pkg.StackSpec.from_dict({
        "services": {
            "db": {"image": "postgres:16"},
            "web": {
                "image": "nginx:1.25",
                "depends_on": {"db": {"condition": "service_healthy"}},
            },
        }
    })
    dep.up(spec)  # must not raise
    if not any("web" in n for n in engine.containers_store):
        raise AssertionError("web should be running once db is healthy")


def check_down_reverse_order(pkg):
    """down() must remove containers before the networks/volumes they use."""
    engine = FakeEngine()
    dep = _make_deployer(pkg, engine)
    spec = pkg.StackSpec.from_dict(BASE_SPEC)
    dep.up(spec)

    engine.calls.clear()
    dep.down(spec)

    verbs = engine.verbs()
    last_container_remove = max(
        (i for i, v in enumerate(verbs) if v == "remove_container"),
        default=-1,
    )
    first_infra_remove = min(
        (i for i, v in enumerate(verbs)
         if v in ("remove_network", "remove_volume")),
        default=len(verbs),
    )
    if last_container_remove > first_infra_remove:
        raise AssertionError(
            "down() must remove all containers before networks/volumes: {}"
            .format(verbs)
        )

    # Everything must be gone.
    if engine.containers_store or engine.networks_store or engine.volumes_store:
        raise AssertionError("down() must leave no managed resources behind")


def check_ps_reports_drift(pkg):
    """ps(spec) must flag containers whose config has drifted."""
    engine = FakeEngine()
    dep = _make_deployer(pkg, engine)
    v1 = pkg.StackSpec.from_dict({"services": {"api": {"image": "app:v1"}}})
    dep.up(v1)

    # Same spec: up to date.
    statuses = dep.ps(v1)
    if not statuses:
        raise AssertionError("ps() must report the running container")
    if not all(s.up_to_date for s in statuses):
        raise AssertionError("ps() must report an unchanged container as up to date")

    # Drifted spec: not up to date.
    v2 = pkg.StackSpec.from_dict({"services": {"api": {"image": "app:v2"}}})
    statuses2 = dep.ps(v2)
    if any(s.up_to_date for s in statuses2):
        raise AssertionError("ps() must flag a drifted container as not up to date")


def check_scaling(pkg):
    """replicas: N must produce N containers for that service."""
    engine = FakeEngine()
    dep = _make_deployer(pkg, engine)
    spec = pkg.StackSpec.from_dict(
        {"services": {"worker": {"image": "app:v1", "replicas": 3}}}
    )
    dep.up(spec)
    worker_containers = [n for n in engine.containers_store if "worker" in n]
    if len(worker_containers) != 3:
        raise AssertionError(
            "replicas: 3 must create 3 containers, got {}: {}".format(
                len(worker_containers), sorted(worker_containers)
            )
        )
    # Distinct names.
    if len(set(worker_containers)) != 3:
        raise AssertionError("replica container names must be distinct")


def check_interpolation_and_loader(pkg):
    """Optional but contract-level: ${VAR} interpolation + override merge.

    These are graded only if the package exposes them (they are part of the
    documented API surface); a package that omits them fails here.
    """
    for name in ("interpolate", "load_stack", "deep_merge", "select_profiles"):
        if getattr(pkg, name, None) is None:
            raise AssertionError(
                "docker.orchestration must expose {!r}".format(name)
            )

    env = {"TAG": "v9", "PORT": "8080", "EMPTY": ""}
    if pkg.interpolate("img:${TAG}", env) != "img:v9":
        raise AssertionError("${VAR} interpolation is wrong")
    if pkg.interpolate("${MISSING:-fb}", env) != "fb":
        raise AssertionError(":- default not applied for missing var")
    if pkg.interpolate("${EMPTY:-fb}", env) != "fb":
        raise AssertionError(":- default not applied for empty var")
    if pkg.interpolate("$$x", env) != "$x":
        raise AssertionError("$$ must escape to a literal $")

    # Deep merge: overrides win on scalars, append-lists extend.
    merged = pkg.deep_merge(
        {"services": {"web": {"image": "nginx", "ports": ["80:80"]}}},
        {"services": {"web": {"image": "nginx:1.25", "ports": ["443:443"]}}},
    )
    web = merged["services"]["web"]
    if web["image"] != "nginx:1.25":
        raise AssertionError("override must replace a scalar (image)")
    if "80:80" not in web["ports"] or "443:443" not in web["ports"]:
        raise AssertionError("override must extend an append-list (ports)")

    # Profiles: services outside the active profile are dropped.
    doc = {"services": {
        "web": {"image": "nginx"},
        "debug": {"image": "busybox", "profiles": ["dev"]},
    }}
    prod = pkg.select_profiles(doc, [])
    if "debug" in prod["services"] or "web" not in prod["services"]:
        raise AssertionError("inactive-profile services must be dropped")
    dev = pkg.select_profiles(doc, ["dev"])
    if "debug" not in dev["services"]:
        raise AssertionError("active-profile services must be kept")

    # Full pipeline feeds StackSpec.
    resolved = pkg.load_stack(
        [{"services": {"web": {"image": "nginx:${TAG}",
                               "ports": ["${PORT}:80"]}}}],
        environment=env,
    )
    spec = pkg.StackSpec.from_dict(resolved)
    if spec.services["web"].image != "nginx:v9":
        raise AssertionError("load_stack did not interpolate the image")


# ---------------------------------------------------------------------------

CHECKS = [
    check_spec_validation,
    check_config_hash_stable,
    check_plan_ordering,
    check_apply_and_idempotency,
    check_drift_recreates,
    check_orphan_removal,
    check_health_gate,
    check_health_gate_success,
    check_down_reverse_order,
    check_ps_reports_drift,
    check_scaling,
    check_interpolation_and_loader,
]


def main():
    _restore_tracked_sources()
    pkg = _load()

    for check in CHECKS:
        check(pkg)
        print("ok - {}".format(check.__name__))

    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
