#!/bin/sh
# Autostart nadajnika jako usługa systemowa (root) - zamiast własnego autostartu jako zwykły użytkownik, który nie ma
# dostępu do /dev/hidraw (wibracja: "brak uprawnień") ani do trybu niskich opóźnień.
#
#   doas ./install-service.sh                     argumenty nadajnika: --config sender.toml (plik obok skryptu)
#   doas ./install-service.sh 192.168.1.4 --key X  własne argumenty nadajnika
#   doas ./install-service.sh --remove            usuwa usługę
#
# OpenRC (postmarketOS / Alpine) albo systemd - wykrywane automatycznie. Log: /var/log/dualsense-sender.log (OpenRC)
# albo journalctl -u dualsense-sender (systemd).
# Testy: ROOT=katalog INIT=openrc|systemd - pliki trafiają pod ROOT, bez uruchamiania usługi.
set -eu
NAME=dualsense-sender
DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=${ROOT:-}

if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
	echo "Uruchom jako root: doas $0 $*" >&2
	exit 1
fi
if [ -z "${INIT:-}" ]; then
	if [ -d /run/systemd/system ]; then INIT=systemd
	elif command -v openrc-run >/dev/null 2>&1 || [ -x /sbin/openrc-run ]; then INIT=openrc
	else echo "Nie znaleziono OpenRC ani systemd" >&2; exit 1
	fi
fi
live() { [ -z "$ROOT" ]; }           # prawdziwa instalacja (nie test)

if [ "${1:-}" = "--remove" ]; then
	if [ "$INIT" = openrc ]; then
		live && { rc-service "$NAME" stop || true; rc-update del "$NAME" default || true; }
		rm -f "$ROOT/etc/init.d/$NAME" "$ROOT/etc/conf.d/$NAME"
	else
		live && { systemctl disable --now "$NAME" || true; }
		rm -f "$ROOT/etc/systemd/system/$NAME.service"
		live && systemctl daemon-reload
	fi
	echo "Usunięto usługę $NAME"
	exit 0
fi

if [ $# -gt 0 ]; then
	ARGS=$*
elif [ -f "$DIR/sender.toml" ]; then
	ARGS="--config $DIR/sender.toml"
else
	echo "Brak $DIR/sender.toml - skopiuj sender.example.toml jako sender.toml (ustaw host) albo podaj argumenty," >&2
	echo "np.: doas $0 192.168.1.4" >&2
	exit 1
fi

if [ "$INIT" = openrc ]; then
	mkdir -p "$ROOT/etc/init.d" "$ROOT/etc/conf.d"
	install -m 755 "$DIR/service/$NAME.openrc" "$ROOT/etc/init.d/$NAME"
	printf 'SENDER_DIR="%s"\nSENDER_ARGS="%s"\n' "$DIR" "$ARGS" > "$ROOT/etc/conf.d/$NAME"
	if live; then
		rc-update add "$NAME" default
		rc-service "$NAME" restart
	fi
else
	mkdir -p "$ROOT/etc/systemd/system"
	sed -e "s|@DIR@|$DIR|g" -e "s|@ARGS@|$ARGS|g" "$DIR/service/$NAME.service" > "$ROOT/etc/systemd/system/$NAME.service"
	if live; then
		systemctl daemon-reload
		systemctl enable --now "$NAME"
		systemctl restart "$NAME"
	fi
fi
echo "Usługa $NAME ($INIT) działa jako root: $DIR/sender_controller.py $ARGS"
echo "Wyłącz swój poprzedni autostart nadajnika - dwa nadajniki naraz walczą o pady."
