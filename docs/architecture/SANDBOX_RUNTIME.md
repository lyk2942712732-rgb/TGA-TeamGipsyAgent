# Sandbox runtime

TGA uses Python as its control plane and a Linux-only Go daemon as the
privileged execution plane.

- `DockerSandboxProvider` calls the host-installed `sbx` CLI for offline and
  web profiles. A Task owns one outer sandbox; Solver workspaces remain
  separate.
- `SandboxdProvider` communicates with `tga-sandboxd` over a mode `0660` Unix
  socket. The daemon validates the root-owned profile again and creates only
  `runsc` containers with fixed images, limits and capabilities.
- Task, Solver, profile, configuration digest and fencing token are attached
  to every managed resource. Reconcile only removes resources carrying TGA's
  managed label.
- There is no fallback. `TGA_SANDBOX_RUNTIME=enforced` rejects missing
  profiles, local-process MCP, unavailable providers and mismatched config.
- Docker Sandboxes compatibility is intentionally limited to `sbx` 0.34.x.
  A task-scoped `shell-docker` template owns the VM, while every command runs
  in a digest-pinned, non-root inner tool container.
- The sandboxd Unix listener validates both mode `0660` group access and the
  connecting process UID through Linux `SO_PEERCRED`.

Advanced network profiles accept canonical IP/CIDR and port lists only.
Generated nftables rules deny loopback, link-local and metadata ranges before
applying grants. `raw-network` adds only `NET_RAW`; `NET_ADMIN`, `SYS_ADMIN`,
host networking, devices and the Docker socket are never client options.

Task pause and approval states retain the sandbox. Terminal tasks should mark
the instance released with `destroy_after = terminal time + 15 minutes`; the
cleanup worker passes the current valid instance set to `Reconcile`.

The committed configuration is intentionally `disabled` and contains release
digest placeholders. Enabling enforcement before replacing every selected
profile and tool image with a real `@sha256:<64 hex>` reference is rejected.
The MCP v2 migration similarly leaves existing mutable local tags unusable in
enforced mode until release engineering pins them.
