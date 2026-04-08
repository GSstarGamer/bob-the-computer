# Remote Local-LLM Ops

This folder contains the deployment assets for Bob's remote OpenAI-compatible `vLLM` server.

## Intended Shape

- primary endpoint: `https://llm.rionnag.net/gpt-oss/v1`
- local bind: `127.0.0.1:8001`
- served model name: `gpt-oss-20b`
- auth: none by default
- systemd template: `bob-llm@.service`
- Caddy terminates TLS and strips the route prefix before proxying to `vLLM`

## Files

- `bootstrap_remote.sh`: installs base packages, creates `/opt/bob-llm` and `/etc/bob-llm`, and installs the systemd template
- `systemd/bob-llm@.service`: generic `vLLM` service for named instances such as `gpt-oss`
- `examples/gpt-oss.env.example`: sample instance env file for `/etc/bob-llm/gpt-oss.env`
- `Caddyfile`: path-based routing for `llm.rionnag.net`
- `start_ssh_tunnel.ps1`: fallback helper that starts a local SSH port-forward to `http://127.0.0.1:18001/v1`

## Notes

- The audited `ssh.rionnag.net` server has one 24 GB RTX 3090, so the practical default model is `gpt-oss-20b`.
- Additional coding or deep-thinking endpoints are left as optional follow-up work unless GPU capacity increases.
- The public hostname `https://llm.rionnag.net/gpt-oss/v1` is the intended Bob runtime path.
- The default deployment is keyless for local-model testing. Add `API_KEY=...` in an instance env file only if you intentionally want the endpoint protected.
- Bob uses a neutral custom `User-Agent` so Cloudflare does not block direct SDK-style `POST /responses` requests on this hostname.
- The SSH tunnel helper is only for troubleshooting.

## Bootstrap SSH

Use password auth only for the first interactive login:

```powershell
ssh girish@ssh.rionnag.net
```

Type the password manually when prompted. Do not hardcode or commit the password in scripts, env files, docs, or service definitions. Once connected, install an SSH key and switch the deployment flow to key-based auth.
