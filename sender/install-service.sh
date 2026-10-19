#!/bin/sh
# Autostart nadajnika jako usługa systemd (root) - zamiast własnego autostartu jako zwykły użytkownik, który nie ma
# dostępu do /dev/hidraw (wibracja: "brak uprawnień") ani do trybu niskich opóźnień.
#
#   doas ./install-service.sh                     argumenty nadajnika: --config sender.toml (plik obok skryptu)
#   doas ./install-service.sh 192.168.1.4 --key X  własne argumenty nadajnika (klucz trafia do pliku środowiska)
#   doas ./install-service.sh --own-root ...      kod nadajnika na własność roota (patrz niżej)
#   doas ./install-service.sh --remove            usuwa usługę
#
# Bezpieczeństwo (docs/SECURITY.md):
# - klucz z --key nie trafia do ExecStart (widać go w ps / /proc/PID/cmdline dla każdego użytkownika), tylko do
#   /etc/dualsense-sender.env (0600, root) jako PADKEY;
# - usługa ma ograniczenia systemd (NoNewPrivileges, system plików tylko do odczytu poza repozytorium...);
# - usługa działa jako root, a kod leży w folderze zwykłego użytkownika: ten użytkownik (i każdy jego program) może
#   zmienić kod wykonywany jako root. --own-root oddaje repozytorium rootowi (aktualizacja z okna dalej działa - git
#   pull idzie wtedy jako root; ./setup-update-key.sh uruchamiaj wtedy przez doas).
#
# Log: journalctl -u dualsense-sender
# Testy: ROOT=katalog - pliki trafiają pod ROOT, bez uruchamiania usługi.
set -eu
NAME=dualsense-sender
DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=${ROOT:-}
UNIT="$ROOT/etc/systemd/system/$NAME.service"
ENVF="$ROOT/etc/$NAME.env"
REPO=$(cd "$DIR/.." && pwd)

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
	rm -f "$UNIT" "$ENVF"
	live && systemctl daemon-reload
	echo "Usunięto usługę $NAME"
	exit 0
fi

OWN_ROOT=0
if [ "${1:-}" = "--own-root" ]; then
	OWN_ROOT=1
	shift
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

# Folder trafia do unitu bez cytowania (WorkingDirectory, ścieżka skryptu, ReadWritePaths) - tylko bezpieczne znaki.
case $REPO in
	*[!A-Za-z0-9/._+:-]*)
		echo "Folder nadajnika ma spację lub znak specjalny: $DIR" >&2
		echo "Przenieś repozytorium do folderu o prostej nazwie (np. ~/dualsense-bridge) i uruchom skrypt ponownie." >&2
		exit 1 ;;
esac

# Argumenty (np. klucz ze spacją, cudzysłowem, $ albo %) wg reguł ExecStart: każde słowo w "...", \ i " poprzedzone \,
# % jako %%, $ jako $$.
dq() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/%/%%/g' -e 's/\$/$$/g')"; }
# Wartość w pliku środowiska systemd: w "...", \ " $ i ` poprzedzone \ (dosłownie, bez rozwijania zmiennych).
eq() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\$/\\$/g' -e 's/`/\\`/g')"; }
NL='
'
ARGS=
KEY=
HAS_KEY=0
NEXT_KEY=0
for a in "$@"; do
	case $a in
		*"$NL"*) echo "Argument z nową linią nie jest obsługiwany" >&2; exit 1 ;;
	esac
	if [ $NEXT_KEY -eq 1 ]; then
		KEY=$a; HAS_KEY=1; NEXT_KEY=0; continue
	fi
	case $a in
		--key) NEXT_KEY=1; continue ;;
		--key=*) KEY=${a#--key=}; HAS_KEY=1; continue ;;
	esac
	ARGS="${ARGS:+$ARGS }$(dq "$a")"
done
if [ $NEXT_KEY -eq 1 ]; then
	echo "--key bez wartości" >&2
	exit 1
fi

mkdir -p "$ROOT/etc/systemd/system"
if [ $HAS_KEY -eq 1 ]; then
	(umask 077; printf 'PADKEY=%s\n' "$(eq "$KEY")" > "$ENVF")
	chmod 600 "$ENVF"
	echo "Klucz HMAC w $ENVF (0600) - nie w ExecStart (widocznym w ps)"
else
	rm -f "$ENVF"
fi
# podstawienie bez sed: argumenty mogą zawierać |, & i \ (znaki specjalne sed)
U_DIR=$DIR U_REPO=$REPO U_ARGS=$ARGS awk '
	function rep(s, a, b,   out, i) {
		out = ""
		while ((i = index(s, a)) > 0) { out = out substr(s, 1, i - 1) b; s = substr(s, i + length(a)) }
		return out s
	}
	{ print rep(rep(rep($0, "@ARGS@", ENVIRON["U_ARGS"]), "@DIR@", ENVIRON["U_DIR"]), "@REPO@", ENVIRON["U_REPO"]) }
' "$DIR/service/$NAME.service" > "$UNIT"
chmod 600 "$UNIT"
# plik konfiguracji może zawierać klucz HMAC - tylko właściciel (usługa czyta go jako root)
[ -f "$DIR/sender.toml" ] && chmod go-rwx "$DIR/sender.toml"
if [ $OWN_ROOT -eq 1 ]; then
	live && chown -R 0:0 "$REPO"
	live && chmod -R go-w "$REPO"
	echo "Repozytorium $REPO należy teraz do roota (git pull z okna działa jako root)"
elif [ "$(stat -c %u "$REPO")" -ne 0 ]; then
	echo "UWAGA: kod nadajnika należy do użytkownika $(stat -c %U "$REPO"), a usługa działa jako root - ten użytkownik" \
		"(i każdy jego program) może zmienić kod wykonywany jako root. Zabezpieczenie: doas $0 --own-root ..." >&2
fi
if live; then
	systemctl daemon-reload
	systemctl enable --now "$NAME"
	systemctl restart "$NAME"
fi
echo "Usługa $NAME (systemd) działa jako root: $DIR/sender_controller.py $*"
echo "Log: journalctl -u $NAME -f"
echo "Wyłącz swój poprzedni autostart nadajnika - dwa nadajniki naraz walczą o pady."
