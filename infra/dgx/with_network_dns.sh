#!/usr/bin/env bash
# Run a command with application-scoped DNS; never change the host resolver.
set -euo pipefail

if [[ $# -eq 0 ]]; then
    echo "Usage: bash infra/dgx/with_network_dns.sh COMMAND [ARG ...]" >&2
    exit 64
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESOLVER_SOURCE="${FOOTYLEVELS_RESOLV_CONF:-${SCRIPT_DIR}/resolv.conf.example}"
if [[ ! -r "$RESOLVER_SOURCE" ]]; then
    echo "Cannot read resolver configuration: $RESOLVER_SOURCE" >&2
    exit 66
fi
for utility in unshare mount mktemp; do
    if ! command -v "$utility" >/dev/null 2>&1; then
        echo "Required Linux utility is unavailable: $utility" >&2
        exit 69
    fi
done

# Snapshot the configuration in a private directory for this invocation.
umask 077
RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/footylevels-dns.XXXXXX")"
trap 'rm -f "$RUNTIME_DIR/resolv.conf"; rmdir "$RUNTIME_DIR"' EXIT
cp "$RESOLVER_SOURCE" "$RUNTIME_DIR/resolv.conf"

# Root is mapped only inside the new user namespace. Mount propagation is
# explicitly private so the bind mount cannot alter other processes' DNS.
unshare --user --map-root-user --mount --propagation private bash -c '
    set -euo pipefail
    mount --bind "$1" /etc/resolv.conf
    shift
    exec "$@"
' -- "$RUNTIME_DIR/resolv.conf" "$@"
