#!/bin/sh

set -eu

nameserver="$(awk '/^[[:space:]]*nameserver[[:space:]]+/ { print $2; exit }' /etc/resolv.conf)"
nameserver="${nameserver:-127.0.0.11}"

case "$nameserver" in
    *:*) export NGINX_RESOLVER="[$nameserver] ipv6=on" ;;
    *) export NGINX_RESOLVER="$nameserver" ;;
esac
