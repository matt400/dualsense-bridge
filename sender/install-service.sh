#!/bin/sh
# Autostart nadajnika jako usługa systemd (root) - zamiast własnego autostartu jako zwykły użytkownik, który nie ma
# dostępu do /dev/hidraw (wibracja: "brak uprawnień") ani do trybu niskich opóźnień.
#
#   doas ./install-service.sh                     argumenty nadajnika: --config sender.toml (plik obok skryptu)
#   doas ./install-service.sh 192.168.1.4 --key X  własne argumenty nadajnika
#   doas ./install-service.sh --remove            usuwa usługę
#
# Log: journalctl -u dualsense-sender
# Testy: ROOT=katalog - pliki trafiają pod ROOT, bez uruchamiania usługi.
set -eu
NAME=dualsense-sender
DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=${ROOT:-}
UNIT="$ROOT/etc/systemd/system/$NAME.service"

live() { [ -z "$ROOT" ]; }           # prawdziwa instalacja (nie test)

if live; then
	if [ "$(id -u)" -ne 0 ]; then
		echo "Uruchom jako root: doas $0 $*" >&2
		exit 1
	fi
	if [ ! -d /run/systemd/system ]; then
		echo "Ten system nie działa na systemd - usługa nadajnika wymaga systemd." >&2
		exit 1
	fi
fi

if [ "${1:-}" = "--remove" ]; then
	live && { systemctl disable --now "$NAME" || true; }
	rm -f "$UNIT"
	live && systemctl daemon-reload
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

# Folder trafia do unitu bez cytowania (WorkingDirectory, ścieżka skryptu) - tylko bezpieczne znaki.
case $DIR in
	*[!A-Za-z0-9/._+:-]*)
		echo "Folder nadajnika ma spację lub znak specjalny: $DIR" >&2
		echo "Przenieś repozytorium do folderu o prostej nazwie (np. ~/dualsense-bridge) i uruchom skrypt ponownie." >&2
		exit 1 ;;
esac

# Argumenty (np. klucz ze spacją, cudzysłowem, $ albo %) wg reguł ExecStart: każde słowo w "...", \ i " poprzedzone \,
# % jako %%, $ jako $$.
dq() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/%/%%/g' -e 's/\$/$$/g')"; }
NL='
'
ARGS=
for a in "$@"; do
	case $a in
		*"$NL"*) echo "Argument z nową linią nie jest obsługiwany" >&2; exit 1 ;;
	esac
	ARGS="${ARGS:+$ARGS }$(dq "$a")"
done

mkdir -p "$ROOT/etc/systemd/system"
# podstawienie bez sed: argumenty mogą zawierać |, & i \ (znaki specjalne sed)
U_DIR=$DIR U_ARGS=$ARGS awk '
	function rep(s, a, b,   out, i) {
		out = ""
		while ((i = index(s, a)) > 0) { out = out substr(s, 1, i - 1) b; s = substr(s, i + length(a)) }
		return out s
	}
	{ print rep(rep($0, "@ARGS@", ENVIRON["U_ARGS"]), "@DIR@", ENVIRON["U_DIR"]) }
' "$DIR/service/$NAME.service" > "$UNIT"
chmod 600 "$UNIT"                     # może zawierać klucz HMAC
if live; then
	systemctl daemon-reload
	systemctl enable --now "$NAME"
	systemctl restart "$NAME"
fi
echo "Usługa $NAME (systemd) działa jako root: $DIR/sender_controller.py $*"
echo "Log: journalctl -u $NAME -f"
echo "Wyłącz swój poprzedni autostart nadajnika - dwa nadajniki naraz walczą o pady."
