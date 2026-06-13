# Contributing to Dubdeck

Welcome. Dubdeck controls real virtual machines over SSH and HTTP APIs, so correctness
matters — a bad command can lock a network gateway. This guide covers how to set up a
dev environment, how to run the gate, and the two design patterns every contributor
needs to know.

---

## Dev setup

### Backend

Python 3.12+ and [`uv`](https://docs.astral.sh/uv/) are required.

```bash
# Dev server (auto-reloads on save)
cd backend && uv run fastapi dev app/main.py

# Run the full test suite
cd backend && uv run pytest

# Lint + format
cd backend && uv run ruff check --fix . && uv run ruff format .
```

### Frontend

Node and npm are required.

```bash
cd frontend && npm install   # first time only

npm run dev     # Vite dev server
npm run lint    # ESLint
npm run test    # Vitest (watch mode)
npm run build   # Production build
```

---

## The gate: `./scripts/check.sh`

Every contribution — bug fix, refactor, new feature — must leave the gate script green
before opening a PR.

```bash
./scripts/check.sh
```

Run it from the repo root. It executes the full suite in order:

| Step | What it runs |
|---|---|
| `backend: ruff check` | Linter; fails on any rule violation |
| `backend: ruff format --check` | Formatter; fails if any file would change |
| `backend: pytest` | Full backend test suite |
| `frontend: lint` | ESLint |
| `frontend: tsc` | TypeScript compiler (`tsc -b`); zero type errors required |
| `frontend: vitest` | Frontend unit tests |
| `frontend: build` | Production Vite build; catches import/bundle errors |

The script uses `set -euo pipefail` and exits non-zero at the first failure. There is
no partial credit — fix everything before opening a PR.

Forgejo CI runs the same script. A red gate means the PR won't be merged.

---

## Cardinal rule: tests never touch real infrastructure

Dubdeck issues SSH commands to Parallels and KVM hosts, and HTTP calls to Tailscale.
**No test is allowed to make a real SSH connection or network call.** Not even to
localhost. Not even in a CI container. Not ever.

### How the isolation works

Every remote call in the backend goes through the `SSHRunner` protocol defined in
`backend/app/sshlayer.py`. The production path uses `AsyncSSHRunner`, which manages
real SSH connections. Tests inject `FakeRunner` — a pure in-memory test double that
holds a dictionary of canned responses keyed by `(host_address, command_string)`.

`FakeRunner` records every call it receives. It raises `LookupError` if a test issues
a command it wasn't set up to expect — so an accidental real-command path fails loudly
rather than silently passing.

The `conftest.py` `fake` fixture wires up a complete, healthy-lab `FakeRunner` and
passes it into the FastAPI `wire()` call instead of `AsyncSSHRunner`. No SSH daemon,
no Parallels, no KVM needed.

**A PR whose tests require real infrastructure will not be merged.** If you find
yourself wanting to `ssh` in a test, the answer is: add a `FakeRunner.respond()` call
for that command and a fixture file for its output.

---

## The parser-fixture pattern

Dubdeck's provider parsers are **pure functions over captured command output**. They
take a `str`, return structured data, and have no side effects. This makes them trivially
testable without any mocked subprocess or SSH machinery.

### How it works

1. Run the real command on a real host once. Save the raw stdout to
   `backend/tests/fixtures/`.
2. Write a parser function in `backend/app/hypervisors.py` that takes that string and
   returns typed data.
3. In the test, read the fixture file and pass its contents directly to the parser.
   Assert on the result.

The fixture file is the permanent record of what real hardware produces. If the command
output format ever changes, update the fixture and the parser together.

### Worked example: `parse_list`

**Fixture** — `backend/tests/fixtures/prlctl_list.txt` (captured output of
`prlctl list --all -o name,status` on the Parallels host):

```
NAME                             STATUS
NixOS                            stopped
OpenClaw                         stopped
Safe Browsing v2                 suspended
...
gateway-01                       stopped
lab-03-test-01                   stopped
```

**Parser** — `backend/app/providers/parallels.py`:

```python
def parse_list(output: str) -> dict[str, ResourceState]:
    """NAME ... STATUS columns; names may contain spaces, status is the last token."""
    states: dict[str, ResourceState] = {}
    for line in output.splitlines()[1:]:
        if not line.strip():
            continue
        name, _, status = line.rstrip().rpartition(" ")
        states[name.rstrip()] = _STATES.get(status.strip(), ResourceState.UNKNOWN)
    return states
```

**Test** — `backend/tests/test_providers.py`:

```python
FIXTURES = Path(__file__).parent / "fixtures"

def test_parallels_parse_list():
    states = pl.parse_list((FIXTURES / "prlctl_list.txt").read_text())
    assert states["gateway-01"] == ResourceState.STOPPED
    # VM names with spaces must survive column parsing
    assert states["Safe Browsing v2"] == ResourceState.SUSPENDED
    assert len(states) == 13
```

The test reads the fixture file, feeds it to the parser, and asserts on the result.
No subprocess, no SSH, no Parallels — just string in, dict out.

A second test exercises the edge-case path that every parser must handle:

```python
def test_unknown_states_dont_crash():
    out = "NAME STATUS\nweird-vm exploding\n"
    assert pl.parse_list(out)["weird-vm"] == ResourceState.UNKNOWN
```

This is an inline string (no fixture file needed) because the input is synthetic, not
captured from real hardware.

The same pattern applies to `parse_virsh_list`, `parse_prlctl_snapshots`,
`parse_virsh_snapshots`, and every other provider parser. If you add a new hypervisor
command, follow this pattern: capture once, parse purely, test both the happy path
(fixture file) and the failure path (inline string).

---

## PR flow

1. Create a branch off `main` with a short descriptive name.
2. Make your change. Add or update tests. **Do not skip the fixture pattern** for any
   new parser.
3. Run `./scripts/check.sh` from the repo root. All checks must pass.
4. Open a PR against `main`. Fill in the PR template. Keep changes small and focused —
   one logical change per PR.

PRs that add production code without tests, or that require real infrastructure to
pass tests, will be sent back for revision.
