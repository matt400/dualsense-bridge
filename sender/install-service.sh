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

if [ $# -eq 0 ]; then
	if [ -f "$DIR/sender.toml" ]; then
		set -- --config "$DIR/sender.toml"
	else
		echo "Brak $DIR/sender.toml - skopiuj sender.example.toml jako sender.toml (ustaw host) albo podaj argumenty," >&2
		echo "np.: doas $0 192.168.1.4" >&2
		exit 1
	fi
fi

# Folder trafia do plików usługi bez cytowania (OpenRC: directory, systemd: WorkingDirectory) - tylko bezpieczne znaki.
case $DIR in
	*[!A-Za-z0-9/._+:-]*)
		echo "Folder nadajnika ma spację lub znak specjalny: $DIR" >&2
		echo "Przenieś repozytorium do folderu o prostej nazwie (np. ~/dualsense-bridge) i uruchom skrypt ponownie." >&2
		exit 1 ;;
esac

# Argumenty (np. klucz ze spacją, cudzysłowem, $ albo %) są cytowane osobno dla każdego systemu:
#   OpenRC  - supervise-daemon przepuszcza command_args przez eval, więc słowa w apostrofach powłoki
#   systemd - ExecStart: słowa w "...", \ i " poprzedzone \, % jako %%, $ jako $$
sq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
dq() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/%/%%/g' -e 's/\$/$$/g')"; }
NL='
'
OPENRC_ARGS=
SYSTEMD_ARGS=
for a in "$@"; do
	case $a in
		*"$NL"*) echo "Argument z nową linią nie jest obsługiwany" >&2; exit 1 ;;
	esac
	OPENRC_ARGS="${OPENRC_ARGS:+$OPENRC_ARGS }$(sq "$a")"
	SYSTEMD_ARGS="${SYSTEMD_ARGS:+$SYSTEMD_ARGS }$(dq "$a")"
done

if [ "$INIT" = openrc ]; then
	mkdir -p "$ROOT/etc/init.d" "$ROOT/etc/conf.d"
	install -m 755 "$DIR/service/$NAME.openrc" "$ROOT/etc/init.d/$NAME"
	printf 'SENDER_DIR=%s\nSENDER_ARGS=%s\n' "$(sq "$DIR")" "$(sq "$OPENRC_ARGS")" > "$ROOT/etc/conf.d/$NAME"
	chmod 600 "$ROOT/etc/conf.d/$NAME"      # może zawierać klucz HMAC
	if live; then
		rc-update add "$NAME" default
		rc-service "$NAME" restart
	fi
else
	mkdir -p "$ROOT/etc/systemd/system"
	# podstawienie bez sed: argumenty mogą zawierać |, & i \ (znaki specjalne sed)
	U_DIR=$DIR U_ARGS=$SYSTEMD_ARGS awk '
		function rep(s, a, b,   out, i) {
			out = ""
			while ((i = index(s, a)) > 0) { out = out substr(s, 1, i - 1) b; s = substr(s, i + length(a)) }
			return out s
		}
		{ print rep(rep($0, "@ARGS@", ENVIRON["U_ARGS"]), "@DIR@", ENVIRON["U_DIR"]) }
	' "$DIR/service/$NAME.service" > "$ROOT/etc/systemd/system/$NAME.service"
	chmod 600 "$ROOT/etc/systemd/system/$NAME.service"      # może zawierać klucz HMAC
	if live; then
		systemctl daemon-reload
		systemctl enable --now "$NAME"
		systemctl restart "$NAME"
	fi
fi
echo "Usługa $NAME ($INIT) działa jako root: $DIR/sender_controller.py $OPENRC_ARGS"
echo "Wyłącz swój poprzedni autostart nadajnika - dwa nadajniki naraz walczą o pady."
