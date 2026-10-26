#!/bin/sh
# Access to the private repository for updating the sender from the PC window (the "Update" button).
#
# The sender service runs as root and starts git as the owner of this folder, but without an SSH agent and without a terminal:
# an ordinary SSH key (with a passphrase / in an agent) does not work there ("Could not read from remote repository"). This
# script creates a separate deploy key (read-only, no passphrase) in .git/ - it never enters the repository,
# and git pull leaves it alone.
#
#   ./setup-update-key.sh      (as the folder owner - usually the user WITHOUT doas; after --own-root: doas)
#
# Run it, add the printed key on GitHub, run it again - the second run only checks access. It can be repeated.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
KEY="$REPO/.git/dualsense-deploy-key"
KNOWN="$REPO/.git/dualsense-known-hosts"

if [ ! -d "$REPO/.git" ]; then
	echo "$REPO is not a git clone - clone the repository first: git clone git@github.com:matt400/dualsense-bridge.git" >&2
	exit 1
fi
if [ "$(id -u)" -ne "$(stat -c %u "$REPO")" ]; then
	if [ "$(stat -c %u "$REPO")" -eq 0 ]; then       # install-service.sh --own-root: root-owned repository
		echo "Repository $REPO is owned by root (install-service.sh --own-root) - run: doas $0" >&2
	else
		echo "Run as the owner of $REPO (user $(stat -c %U "$REPO")), without doas." >&2
	fi
	exit 1
fi
for cmd in git ssh ssh-keygen; do
	command -v "$cmd" >/dev/null 2>&1 || { echo "Missing program: $cmd (doas apk add git openssh-client)" >&2; exit 1; }
done

# a deploy key works over SSH only - an https://github.com/... address is turned into git@github.com:...
URL=$(git -C "$REPO" remote get-url origin)
case $URL in
	https://github.com/*)
		SSH_URL="git@github.com:${URL#https://github.com/}"
		git -C "$REPO" remote set-url origin "$SSH_URL"
		echo "Repository URL: $URL -> $SSH_URL (a deploy key works over SSH only)"
		URL=$SSH_URL ;;
	git@github.com:* | ssh://git@github.com/*) ;;
	*)
		echo "Unknown repository URL: $URL - the script supports GitHub only" >&2
		exit 1 ;;
esac
SLUG=${URL#git@github.com:}
SLUG=${SLUG#ssh://git@github.com/}
SLUG=${SLUG%.git}

if [ ! -f "$KEY" ]; then
	ssh-keygen -q -t ed25519 -N "" -C "dualsense-bridge sender@$(hostname)" -f "$KEY"
	chmod 600 "$KEY"
	echo "Created key $KEY"
fi

# the same ssh command the sender uses when updating (proto.ssh_command)
if ERR=$(GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15 -i '$KEY' -o IdentitiesOnly=yes \
-o UserKnownHostsFile='$KNOWN' -o StrictHostKeyChecking=accept-new" git -C "$REPO" ls-remote origin HEAD 2>&1 >/dev/null); then
	echo "OK - the sender can reach $SLUG. The Update button in the PC window will now update the sender too."
	exit 0
fi
case $ERR in
	*"Permission denied"* | *"publickey"* | *"Repository not found"* | *"not found"*) ;;
	*)                                        # not a missing key (network, DNS, host key...) - instructions would not help
		echo "Could not connect to GitHub:" >&2
		printf '%s\n' "$ERR" | grep -v '^$' | head -5 >&2
		exit 1 ;;
esac

cat <<EOF

Add this key on GitHub (one time):
  1. open https://github.com/$SLUG/settings/keys/new
  2. Title: dualsense-bridge sender
  3. Key: paste the line below
  4. do NOT tick "Allow write access" (read-only is enough)
  5. Add key, then run this script again - it will check the access

$(cat "$KEY.pub")

EOF
exit 2
