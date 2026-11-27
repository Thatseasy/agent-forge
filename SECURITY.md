# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in `agent-forge`, please report it privately rather than opening a public issue:

- Use GitHub Security Advisories (`Report a vulnerability` under the **Security** tab of `Thatseasy/agent-forge`), or contact the maintainer team directly.
- Include reproduction steps, affected component (`hooks_global`, `gate`, `mcp_server`, `units`, or CLI), and any proof-of-concept payload.

We aim to acknowledge reports within 48 hours and coordinate a fix before public disclosure.

## Security Boundaries & Guarantees

1. **No Secrets in Workspaces:** API keys belong strictly in `AGENT_FORGE_HOME/secrets.env` (`~/.agent-forge/secrets.env`, `chmod 0600`) outside every Git repository.
2. **Stdlib-Only Global Hooks:** `hooks.json` and `src/forge/hooks_global/` execute exclusively with the Python standard library and exit safely on unexpected errors.
3. **Stage-2 Gate Enforcement:** `agent-forge unit install` and `agent-forge gate --strict` reject prompt-injection markers, secret literals, unsafe command vectors, and unsanitized PII before skills or units are installed.
4. **Zero-Default Exfiltration:** Telemetry and external `cloud-sync` exports remain disabled (`enabled = false`) unless explicitly invoked by the user.

## Supported Versions

| Version | Supported |
| :--- | :--- |
| `1.x` | Yes |
| `< 1.0` | Upgrade to `1.0.0+` |
