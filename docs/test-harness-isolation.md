# Test-harness Docker isolation — findings & plan

**Status:** marker + fail-closed guard IMPLEMENTED (`dev`, 2026-07-04); rootless deferred ·
**Written:** 2026-07-04 · **Owner:** josh

## TL;DR

The deploy-integration tests spin up real `docker` containers on whatever daemon the `docker` CLI
points at. On the homelab box that is the **production** daemon, so a local `./scripts/test.sh` run
floods it with ephemeral `shekel-test-*` containers — which spammed the `wud` update-notifier (now
mitigated) and pollutes `cadvisor`/`alloy` metrics. CI is unaffected (its own throwaway daemon).

**Fix:** run those tests against an **isolated rootless Docker daemon** selected via `DOCKER_HOST`,
and **gate them behind a `docker` pytest marker** that is excluded from routine local runs. No
production-facing test-code logic changes — the suite already drives the plain `docker` CLI, which
honours `DOCKER_HOST`.

---

## Implementation status (2026-07-04)

Items 1 and 2 shipped to `dev`; the churn problem is solved:

- **Marker + default-exclude (commit `56e48204`).** A `docker` pytest marker tags the eight
  daemon-touching classes across `tests/test_deploy/` (28 tests); `scripts/test.sh` defaults to
  `-m "not docker"`, so a routine local run spawns zero containers. CI calls `pytest` directly, so
  it still runs the full set. This alone removes the local churn this doc set out to fix.
- **Fail-closed guard (commit `5df43d4f`).** `tests/test_deploy/conftest.py` skips the docker-marked
  tests when `DOCKER_HOST` is the system socket, UNLESS the run is sanctioned: CI (GitHub sets
  `CI=true`), a non-default `DOCKER_HOST`, or `SHEKEL_ALLOW_HOST_DOCKER=1`. The CI exemption
  corrects the snippet under "Fail-closed guard" below, which as written would have skipped these
  tests in CI too and stripped them from the merge gate.

Deferred or corrected:

- **Rootless daemon (Item 4): not done, optional.** Only needed to run the container tests locally.
  The "One-time host setup" block below is inaccurate for this Arch host -- `rootlesskit`,
  `slirp4netns`, and `dockerd-rootless-setuptool.sh` are not installed, so
  `pacman -S docker-rootless-extras` must run first.
- **Leftover sweep (Item 3): dropped as a standalone.** The fixtures already use `--rm` plus a
  `finally: docker rm -f`; verified zero leftovers in practice, including after a hard mass-error
  run. Fold a sweep into the rootless work if it happens, where it targets the isolated daemon.
- **Inventory correction:** the "Background" claim of *two* container-spawning files was wrong; the
  corrected list is below.

---

## Background — what the harness is

- Test entrypoint: `scripts/test.sh` → execs `pytest`; restarts the test-db container first only
  when `RESTART_TEST_DB` is set to `1`/`true`/`yes`/`on` (opt-in since 2026-09-04; an unrecognised
  value is refused rather than guessed). `pytest.ini` sets `addopts = ... --dist=loadgroup -n 12`,
  so **every** local run fans out across 12 pytest-xdist workers.
- Most tests are pure Python / static config assertions. **Eight classes across six files** touch
  the docker daemon (all gated by `_docker_available()`, all now `@pytest.mark.docker`). Of those:
  - **Three actually spawn containers.**
    `test_proxy_trust_and_headers.py::TestSharedNginxRuntimeHeaders` (network + stub + nginx; the
    main source of churn) and `::TestSharedVhostNginxParse` (`nginx -t`), plus
    `test_deploy_configs.py::TestDeployNginxConfigParses` (`nginx -t`). The original draft named
    `test_container_hardening.py` as the second spawner and missed `test_deploy_configs.py`; that
    was wrong.
  - **Five only run `docker compose config`,** which renders merged YAML and creates **no**
    container: `test_container_hardening.py::TestMergedComposeHardeningSurvivesOverride`,
    `test_deploy_configs.py::TestDeployComposeParses`,
    `test_internal_tls_and_access.py::TestMergedComposeCarriesTLS`,
    `test_image_supply_chain.py::TestComposeOverrideRequiresDigest`, and
    `test_docker_secrets_and_env_hygiene.py::TestProdComposeMergedConfig`. They touch the daemon but
    do not pollute `cadvisor`/`alloy`; they carry the marker for a clean "no daemon interaction by
    default" local run.

## The problem (symptoms observed 2026-07-04)

- Hundreds of `shekel-test-nginx-c33-<pid>` / `shekel-test-stub-c33-<pid>` create/die events on the
  production daemon (exit 137 = teardown SIGKILL).
- `wud` logged continuous `Unable to parse Docker event` (NDJSON bursts) and reacted to every event;
  its store went stale/unreliable.
- The churn also lands in `cadvisor` / `alloy` per-container metrics.

This is **not a test bug** — it is a "dev machine == production docker host" problem. The tests are
correct; they just have nowhere isolated to run locally.

## Root-cause mechanism (with citations)

| What | Where |
|---|---|
| Containers driven via the **`docker` CLI** (`subprocess.run(["docker", *args])`) | `test_proxy_trust_and_headers.py:849` (`_docker()` helper) |
| PID-namespaced names → 12 workers × their own stack | `test_proxy_trust_and_headers.py:835` (`suffix = f"c33-{os.getpid()}"`) |
| Creates network + stub + nginx, `class`-scoped fixture | `test_proxy_trust_and_headers.py:820` (`running_stack`) |
| Images pulled onto the daemon | `NGINX_TEST_IMAGE = "nginx:1.27-alpine"` (:83); stub `python:3.14-alpine` (:891) |
| Publishes port, curls host loopback | `-p 0:443` (:909) → `docker port` (:925) → `curl https://localhost:<port>` (:947, :980) |
| Teardown = `docker rm -f` (→ exit 137) | `test_proxy_trust_and_headers.py:966-968` (`finally`) |
| Skips when docker absent (`docker info`) | `_docker_available()` :87 (and `test_container_hardening.py:56`) |

**Two facts drive the whole solution:**

1. Everything goes through the `docker` CLI → the suite **honours `DOCKER_HOST`**. Redirect it and
   every test container/network moves, with zero test-code changes.
2. The tests curl **`https://localhost:<mapped_port>/`** — they depend on the published port being
   reachable on the *pytest process's* loopback.

## Why CI is already fine

`.github/workflows/ci.yml` runs `runs-on: ubuntu-latest` (:39) and invokes `pytest` directly (:224)
on an **ephemeral runner with its own throwaway docker daemon**. Containers there are created and
discarded on infrastructure that no homelab observability watches. So the collision is
**local-only**.

---

## Recommendation

### Primary: a dedicated **rootless** Docker daemon, selected via `DOCKER_HOST`

Rootless — **not** docker-in-docker — because of driving-fact #2 (`curl localhost`):

- **Rootless** publishes ports into *your user's* network namespace (RootlessKit), so
  `curl localhost:<port>` from pytest keeps working unchanged. ✅
- **dind** publishes into the privileged dind container's namespace — the host's `localhost:<port>`
  wouldn't reach it without extra plumbing, and it needs `--privileged` (against the whole
  `/opt/docker` hardening posture). ❌

Rootless gives a **completely separate daemon + socket + container/network namespace** that the
production `wud`/`cadvisor`/`alloy` (which watch the *system* socket) never see — true isolation,
mirroring the CI model. The C-33 tests only use `network create`, `run`, `port`, `exec`, `logs`,
`rm`, bind-mounts of repo-owned paths, and a random high host port — all fully supported rootless.

**One-time host setup (run as `josh` yourself, via `!`):**

```bash
sudo pacman -S docker-rootless-extras     # PREREQ on Arch: rootlesskit + slirp4netns + the setuptool
dockerd-rootless-setuptool.sh install     # runs dockerd as josh (systemd --user)
systemctl --user enable --now docker
loginctl enable-linger josh               # daemon survives logout
# resulting socket: unix:///run/user/1000/docker.sock
docker -H unix:///run/user/1000/docker.sock info | grep -i 'rootless\|Server Version'   # verify
```

**Wire the suite to it** — in `scripts/test.sh`, before invoking pytest:

```bash
# Route all test-spawned containers to the isolated rootless daemon so they
# never touch the production daemon that the homelab stack + wud/cadvisor/alloy
# share. Fail-closed: if the rootless daemon is down, `docker info` fails and the
# docker-gated tests SKIP rather than falling back to the production socket.
export DOCKER_HOST="unix://${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/docker.sock"
```

CI is unaffected: it calls `pytest` directly (not `scripts/test.sh`), so its own `DOCKER_HOST` /
default socket keeps working.

### Complementary hardening (do regardless of rootless)

**1. Gate the container-spawning tests behind a `docker` marker + default-exclude locally.** This is
the single biggest churn reduction — routine local TDD then spins up **zero** containers.

- Register the marker in `pytest.ini` (extend the existing `markers =` block, :47):

  ```ini
  markers =
      docker: test stands up real containers via the docker CLI; needs an
              isolated daemon (see docs/test-harness-isolation.md)
  ```

- Apply it to the spawning tests (module-level in `test_proxy_trust_and_headers.py`, or class-level
  on the `running_stack` class; and the docker-gated cases in `test_container_hardening.py`):

  ```python
  pytestmark = pytest.mark.docker
  ```

- Default-exclude in **`scripts/test.sh`** (NOT `pytest.ini addopts`, so CI — which calls pytest
  directly — still runs the full set):

  ```bash
  # Local default: skip container-spawning tests unless explicitly requested.
  PYTEST_MARKER_EXPR="${PYTEST_MARKER_EXPR:-not docker}"
  # ... pytest -m "$PYTEST_MARKER_EXPR" "$@"
  # Opt in with:  PYTEST_MARKER_EXPR=docker ./scripts/test.sh   (runs against rootless)
  ```

**2. Fail-closed guard (belt-and-suspenders).** A `conftest.py` hook so a bare `pytest` can never
touch the production daemon even if someone bypasses the wrapper: skip `docker`-marked tests unless
`DOCKER_HOST` is set to a non-default endpoint (or an explicit `SHEKEL_ALLOW_HOST_DOCKER=1` override
is present).

```python
# IMPLEMENTED in tests/test_deploy/conftest.py (commit 5df43d4f).  The
# condition MUST exempt CI, or the guard skips these tests on the merge
# gate too (GitHub sets CI=true; its daemon is a throwaway no observer watches):
import os
def pytest_collection_modifyitems(items):
    dh = os.environ.get("DOCKER_HOST", "")
    system_sock = dh in ("", "unix:///var/run/docker.sock", "unix:///run/docker.sock")
    sanctioned = os.environ.get("CI") or os.environ.get("SHEKEL_ALLOW_HOST_DOCKER") == "1"
    if system_sock and not sanctioned:
        skip = pytest.mark.skip(reason="docker tests need an isolated DOCKER_HOST; "
                                       "see docs/test-harness-isolation.md")
        for item in items:
            if "docker" in item.keywords:
                item.add_marker(skip)
```

**3. Leftover sweep.** Crashed runs leak containers today (seen in wud's event log). Add a
`docker`-scoped `pytest_sessionfinish` (or extend the existing DB one at `tests/conftest.py:2711`)
that force-removes any stray `shekel-test-*` on the active daemon:

```bash
docker ps -aq --filter 'name=shekel-test-' | xargs -r docker rm -f
docker network ls -q --filter 'name=shekel-test-net-' | xargs -r docker network rm
```

---

## Implementation checklist (for a fresh session)

Work happens in **this repo** (`/home/josh/projects/Shekel`), branch `dev`.

- [x] **Quick win** (pure repo change, kills routine churn) -- commit `56e48204`:
  - [x] Register `docker` marker in `pytest.ini`.
  - [x] Apply `@pytest.mark.docker` at class level to all eight daemon-touching classes across
        `tests/test_deploy/` (spawners AND `docker compose config` cases, including the
        `test_deploy_configs.py` one the draft missed).
  - [x] `scripts/test.sh`: default `-m "not docker"`, with a `PYTEST_MARKER_EXPR` opt-in. Verified:
        default run deselects the 28 marked tests and spawns **no** containers; CI's direct `pytest`
        still runs them.
- [x] **Fail-closed guard** -- commit `5df43d4f`:
  - [x] `tests/test_deploy/conftest.py` skips docker tests on the system socket, exempting CI and
        the `SHEKEL_ALLOW_HOST_DOCKER=1` override. Verified: system-daemon skips, `CI=1` runs,
        override runs, default path unchanged.
- [x] Point `docs/testing-standards.md` at this doc.
- [ ] **Isolated rootless daemon (Item 4, optional -- only to run the container tests locally):**
  - [ ] `sudo pacman -S docker-rootless-extras` (PREREQ), then
        `dockerd-rootless-setuptool.sh install` + `systemctl --user enable --now docker` +
        `loginctl enable-linger josh` (host steps, run via `!`).
  - [ ] `scripts/test.sh`: `export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock`.
  - [ ] With `SHEKEL_ALLOW_HOST_DOCKER` unset, run
        `PYTEST_MARKER_EXPR=docker ./scripts/test.sh tests/test_deploy/…` and confirm
        `shekel-test-*` containers appear on the **rootless** daemon and **never** on the system
        daemon. Fold the leftover-`shekel-test-*` sweep in here.

## Open decisions for you

**Resolved 2026-07-04:** (1) marker + default-exclude adopted, rootless deferred as optional; (2)
the default lives in the wrapper, so CI keeps running the full set; (3) class-level marker on each
daemon-touching class. Original framing kept below for context.

1. **Rootless daemon vs CI-only.** If you'd rather not maintain a rootless daemon, the marker +
   default-exclude alone removes all local churn and you rely on CI (already isolated) for that
   coverage — those are stable deploy-config tests that change rarely. Rootless is only worth it if
   you want to iterate on the C-33 tests *locally*.
2. **Where the `-m "not docker"` default lives** — wrapper only (recommended, keeps CI running the
   full set) vs `pytest.ini` (then CI must opt back in explicitly).
3. **Marker granularity** — module-level on the whole C-33 file vs class-level on just the
   `running_stack` class (the static-assertion tests in that file don't need docker and could keep
   running locally).

## Already done (context)

On 2026-07-04 the production `wud` was flipped to **opt-in watching** (`WATCHBYDEFAULT=false`,
`WATCHEVENTS=false`, `WATCHATSTART=true`; `wud.watch=true` on real `/opt/docker` services). So even
a stray prod-daemon test run no longer spams wud — but that only shields wud. Rootless is what
actually keeps test containers off the production daemon (and out of cadvisor/alloy). See
`/opt/docker/monitoring/docker-compose.yml` (wud service comments).

## References

- Spawning fixture: `tests/test_deploy/test_proxy_trust_and_headers.py:820-968`
- Docker helper / gate: `…:849` (`_docker`), `…:87` (`_docker_available`)
- Test config: `pytest.ini` (`addopts … -n 12`, `markers =` :47)
- Entrypoint: `scripts/test.sh`
- CI (already isolated): `.github/workflows/ci.yml:39` (`ubuntu-latest`), `:224` (`pytest`)
- Rootless Docker: <https://docs.docker.com/engine/security/rootless/>
