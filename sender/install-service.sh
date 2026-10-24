#!/bin/sh
# Autostart for the sender as a systemd service (root) - instead of an own autostart as a normal user, who has no
# access to /dev/hidraw (rumble: "no permission") or to the low-latency mode.
#
#   doas ./install-service.sh                     sender arguments: --config sender.toml (the file next to the script)
#   doas ./install-service.sh 192.168.1.4 --key X  your own sender arguments (the key goes to the environment file)
#   doas ./install-service.sh --own-root ...      make root the owner of the sender code (see below)
#   doas ./install-service.sh --remove            remove the service
#
# Security (docs/SECURITY.md):
# - the key from --key does not go to ExecStart (any user can see it in ps / /proc/PID/cmdline), only to
#   /etc/dualsense-sender.env (0600, root) as PADKEY;
# - the service has systemd restrictions (NoNewPrivileges, a read-only file system outside the repository...);
# - the service runs as root while the code sits in an ordinary user's folder: that user (and any program of theirs) can
#   change the code that runs as root. --own-root hands the repository to root (the update from the window keeps working -
#   git pull then runs as root; run ./setup-update-key.sh through doas in that case).
#
# Log: journalctl -u dualsense-sender
# Tests: ROOT=directory - the files go under ROOT, the service is not started.
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
		echo "Run as root: doas $0 $*" >&2
		exit 1
	fi
	if [ ! -d /run/systemd/system ]; then
		echo "This system does not run systemd - the sender service needs systemd." >&2
		exit 1
	fi
fi

if [ "${1:-}" = "--remove" ]; then
	live && { systemctl disable --now "$NAME" || true; }
	rm -f "$UNIT" "$ENVF"
	live && systemctl daemon-reload
	echo "Removed the $NAME service"
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

# The folder goes into the unit unquoted (WorkingDirectory, the script path, ReadWritePaths) - safe characters only.
case $REPO in
	*[!A-Za-z0-9/._+:-]*)
		echo "The sender folder has a space or a special character: $DIR" >&2
		echo "Move the repository to a folder with a plain name (e.g. ~/dualsense-bridge) and run the script again." >&2
		exit 1 ;;
esac

# Arguments (e.g. a key with a space, a quote, $ or %) follow the ExecStart rules: each word in "...", \ and " preceded by \,
# % jako %%, $ jako $$.
dq() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/%/%%/g' -e 's/\$/$$/g')"; }
# A value in the systemd environment file: in "...", with \ " $ and ` preceded by \ (literally, no variable expansion).
eq() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\$/\\$/g' -e 's/`/\\`/g')"; }
NL='
'
ARGS=
KEY=
HAS_KEY=0
NEXT_KEY=0
for a in "$@"; do
	case $a in
		*"$NL"*) echo "An argument with a newline is not supported" >&2; exit 1 ;;
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
	echo "--key without a value" >&2
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
# substitution without sed: the arguments may contain |, & and \ (special characters for sed)
U_DIR=$DIR U_REPO=$REPO U_ARGS=$ARGS awk '
	function rep(s, a, b,   out, i) {
		out = ""
		while ((i = index(s, a)) > 0) { out = out substr(s, 1, i - 1) b; s = substr(s, i + length(a)) }
		return out s
	}
	{ print rep(rep(rep($0, "@ARGS@", ENVIRON["U_ARGS"]), "@DIR@", ENVIRON["U_DIR"]), "@REPO@", ENVIRON["U_REPO"]) }
' "$DIR/service/$NAME.service" > "$UNIT"
chmod 600 "$UNIT"
# the configuration file may contain the HMAC key - owner only (the service reads it as root)
[ -f "$DIR/sender.toml" ] && chmod go-rwx "$DIR/sender.toml"
if [ $OWN_ROOT -eq 1 ]; then
	live && chown -R 0:0 "$REPO"
	live && chmod -R go-w "$REPO"
	echo "Repository $REPO is now owned by root (git pull from the window runs as root)"
elif [ "$(stat -c %u "$REPO")" -ne 0 ]; then
	echo "WARNING: the sender code is owned by user $(stat -c %U "$REPO") but the service runs as root - that user" \
		"(and any program of theirs) can change code that runs as root. To prevent it: doas $0 --own-root ..." >&2
fi
if live; then
	systemctl daemon-reload
	systemctl enable --now "$NAME"
	systemctl restart "$NAME"
fi
echo "Service $NAME (systemd) runs as root: $DIR/sender_controller.py $*"
echo "Log: journalctl -u $NAME -f"
echo "Disable any previous sender autostart - two senders at once fight over the pads."
