#!/bin/sh
# Atomically replace only this project's chain. Never flush global/Docker chains.
set -eu
iptables -w 5 -N DOCKER-USER 2>/dev/null || true
iptables -w 5 -N PERSONAL_AGENT_FORWARD 2>/dev/null || true
{
  printf '*filter\n-F PERSONAL_AGENT_FORWARD\n'
  printf '%s\n' '-A PERSONAL_AGENT_FORWARD -s 172.30.40.0/28 -d 172.30.40.0/28 -j RETURN' '-A PERSONAL_AGENT_FORWARD -s 172.30.40.0/28 -j DROP'
  for subnet in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 192.0.0.0/24 198.18.0.0/15 224.0.0.0/4 240.0.0.0/4 2.24.115.58/32; do
    printf '%s\n' "-A PERSONAL_AGENT_FORWARD -s 172.30.41.0/28 -d $subnet -j DROP"
  done
  printf '%s\n' '-A PERSONAL_AGENT_FORWARD -j RETURN' 'COMMIT'
} | iptables-restore --wait 5 --noflush
for subnet in 172.30.40.0/28 172.30.41.0/28; do
  iptables -w 5 -C DOCKER-USER -s "$subnet" -j PERSONAL_AGENT_FORWARD 2>/dev/null || iptables -w 5 -I DOCKER-USER 1 -s "$subnet" -j PERSONAL_AGENT_FORWARD
  iptables -w 5 -C INPUT -s "$subnet" -j DROP 2>/dev/null || iptables -w 5 -I INPUT 1 -s "$subnet" -j DROP
done
