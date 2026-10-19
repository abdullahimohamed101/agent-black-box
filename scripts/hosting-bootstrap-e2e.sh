#!/usr/bin/env bash
# Host bootstrap E2E (Phase 23, step 8, criterion A9): runs scripts/hosting-bootstrap.sh, hosting-secrets.sh and the `--server` checks
# of hosting-verify.sh in a THROWAWAY Ubuntu 24.04 container (pinned digest). Real: apt (ufw, fail2ban, unattended-upgrades, rclone,
# Docker's own repo with its GPG fingerprint and the version pin, docker-ce and the compose plugin), useradd, sudoers, sshd and
# `sshd -t`/`sshd -T`, the real file modes. Stubbed (a container has no init, no kernel firewall rights, no swap rights): systemctl,
# ufw (a file-backed fake that prints `ufw status verbose` in ufw's format), timedatectl, swapon, the docker daemon commands,
# fail2ban-client, who and journalctl (the evidence for the ssh lockout rule).
#
# Proves: --check lists every action and changes nothing; --apply refuses without root, without a key, with a private key; the second
# run changes nothing; stage 2 REFUSES without a proven key login, without a live deploy session, without a key file, and when another
# sshd drop-in wins; it succeeds and keeps sshd listening when the evidence is there; secrets are 0600, never printed, never
# overwritten; and every --server assertion that can run here turns FAIL on an unconfigured host and PASS on the configured one.
# NOT proven (UNVERIFIED (env), needs a real server): the real ufw/iptables behaviour, fail2ban banning, unattended-upgrades running,
# systemd timers firing, the Hetzner network, a reboot, and an external port scan.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE='ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac'
docker info >/dev/null 2>&1 || { echo "HOSTING-BOOTSTRAP-E2E FAILED: docker is not reachable" >&2; exit 1; }
name="abb-hosting-bootstrap-e2e-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
docker run --rm --name "$name" -v "$root:/repo:ro" "$IMAGE" bash /repo/scripts/hosting-bootstrap-e2e-container.sh
