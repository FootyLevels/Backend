# FootyLevels ngrok endpoint

## Current routing — 12 October 2026

The same approved agent, HTTPS URL, upstream port 8010 and Host rewrite now reach
the authenticated Development application API. The GPU HTTP listener moved to
private loopback 8013; workers still use its private Unix bridge. See
[current deployment and recovery](public-api.md). The installation and earlier
compatibility-service observations below are historical; the public address no
longer exposes the legacy shared project/media routes.

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
  Online authentication succeeded when the agent connected.
- Existing Skill Lab API responds at `http://127.0.0.1:8010/api/health` with HTTP 200,
  local CUDA ready and motion ready. Port `8080` has no listener.

## Approved launch and public verification

Kunal approved port `8010` and, after the API rejected the public Host header with
HTTP 400, approved `--host-header=localhost`. The existing API accepts localhost.
No traffic policy, authentication action or rate limit was added.

```sh
PATH="$HOME/.local/bin:$PATH" \
  "$HOME/.local/share/footylevels/fp50-dns/with_network_dns.sh" \
  ngrok http 8010 --url https://ranting-pesticide-thespian.ngrok-free.dev \
  --host-header=localhost
```

The running agent reports upstream `http://localhost:8010`. Requests from the Mac to
the public `/api/health` and `/api/projects` both returned HTTP 200 and application
JSON. Health reports local CUDA and motion ready; the project route returned a list.
No existing API/model process was restarted. Public health does not prove a fresh
GPU generation or authenticated Firebase/Supabase writes.

Set the app's Skill Lab server address to
`https://ranting-pesticide-thespian.ngrok-free.dev`. A read-only test is:

```sh
curl https://ranting-pesticide-thespian.ngrok-free.dev/api/health
```

The agent is left running under Kunal's account. Its private log is
`~/.local/share/footylevels/ngrok/agent.log`; launch PID/process group and endpoint
are recorded in `endpoint.json` beside it. The verified launch process group on
9 October was `2462717`. To stop this launch only, after checking it still belongs
to this endpoint, run `kill -- -2462717` on the DGX. Do not kill every ngrok process.
For a later launch use its recorded process group, not this historical PID.

If a token is exposed, revoke it in the dashboard and configure the replacement
privately on the DGX. Do not send the replacement through chat or put it in source.

## Owned upstream — 10 October 2026

After the teammate service was removed, Kunal authorized the owned runtime now
listening on 8010. Backend source `c865730` runs under Kunal's account with body
weights warm. The existing ngrok agent/URL/Host rewrite are unchanged. Fresh public
uploads, two keyframe generations and three-frame body motion decoded correctly;
exact test projects were removed with 204/read 404. Optional football/ground stages
report partial completion when GPU headroom is insufficient. See [runtime](runtime.md).
This remains the Debug compatibility service until cloud ownership/storage/job
integration is complete.
