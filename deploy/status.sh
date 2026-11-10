#!/bin/sh
set -eu
cd /srv/personal-agent/current
docker compose ps
docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}} {{.PIDs}}' personal-agent-hermes-1 personal-agent-web-1 personal-agent-egress-1
df -h /srv/personal-agent/workspace
systemctl is-active personal-agent.service personal-agent-edge-link.service
systemctl list-timers personal-agent-backup.timer --no-pager
curl --fail --silent --show-error https://alvaro.2.24.115.58.sslip.io/health
