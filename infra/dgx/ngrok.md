# FootyLevels ngrok endpoint

Kunal supplied the reserved URL
`https://ranting-pesticide-thespian.ngrok-free.dev` and the dashboard command:

```sh
ngrok http 8080 --url https://ranting-pesticide-thespian.ngrok-free.dev
```

The [configured endpoint setup guide](https://ngrok.com/agent-setup/endpoint-setup.md)
was read before installation. No additional traffic policy, authentication action
or rate limit is authorized by this setup request. Never print, copy into chat or
commit the agent authtoken. If it is absent or rejected, Kunal must run
`ngrok config add-authtoken` himself on the server.

## Verified installation — 9 October 2026

- Linux x86_64; standalone ngrok **3.39.11** installed at `~/.local/bin/ngrok` under
  Kunal's account, with owner-only execution permissions. No sudo or system package
  changes.
- Downloaded using certificate-validated HTTPS through the application-scoped DNS
  wrapper from the [official Linux download](https://ngrok.com/download/linux):
  `https://bin.ngrok.com/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz`.
- Downloaded archive SHA256:
  `cec0b4997fcc5f529dfc74bac89050354d11a915f968720600039738fdf330cf`.
  Installed binary SHA256:
  `e4e0f05c3b016699daa3fc4ff03d0870e49f705171d2dac5f16d00c55c10dee5`.
  These are recorded hashes of the downloaded files, not a comparison to a separately
  published vendor checksum.
- Existing `~/.config/ngrok/ngrok.yml` contains an authtoken and has mode `0600`;
  `ngrok config check` exits successfully. No token value was printed or copied.
  Online authentication still needs verification when the agent starts.
- Existing Skill Lab API responds at `http://127.0.0.1:8010/api/health` with HTTP 200,
  local CUDA ready and motion ready. Port `8080` has no listener.

## Port decision and launch

The exact supplied command forwards to `8080`. Kunal has been asked whether to use
the existing API port `8010` instead, or retain `8080` with a local forwarder. The
public URL must remain unchanged. No tunnel has been started while this choice is
pending. Do not start another model runtime or restart the existing API just to move
its port.

Launch the approved command through [with_network_dns.sh](with_network_dns.sh),
with `~/.local/bin` on PATH. The installed helper is under
`~/.local/share/footylevels/fp50-dns/`. Run the agent in the background with private
logs, record its specific PID, verify the local API first, then compare the public
`/api/health` response with the local response. A live endpoint or agent process by
itself does not prove that requests reach the app.

Leave the verified agent running for testing, and record how to stop that particular
process. A public health response establishes connectivity/readiness; it does not
prove a new GPU generation job or authenticated Firebase/Supabase data writes.
