#!/bin/sh
# Dostęp do prywatnego repozytorium dla aktualizacji nadajnika z okna na PC (przycisk "Aktualizuj").
#
# Usługa nadajnika działa jako root i uruchamia git jako właściciel tego folderu, ale bez agenta SSH i bez terminala:
# zwykły klucz SSH (z hasłem / w agencie) tam nie działa ("Could not read from remote repository"). Ten skrypt tworzy
# osobny klucz wdrożeniowy (deploy key, tylko do odczytu, bez hasła) w .git/ - nigdy nie trafia do repozytorium,
# a git pull go nie rusza.
#
#   ./setup-update-key.sh      (jako zwykły użytkownik - właściciel folderu, BEZ doas)
#
# Uruchom, dodaj wypisany klucz na GitHubie, uruchom ponownie - drugi raz tylko sprawdza dostęp. Można powtarzać.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
KEY="$REPO/.git/dualsense-deploy-key"
KNOWN="$REPO/.git/dualsense-known-hosts"

if [ ! -d "$REPO/.git" ]; then
	echo "$REPO nie jest klonem git - sklonuj repozytorium: git clone git@github.com:matt400/dualsense-bridge.git" >&2
	exit 1
fi
if [ "$(id -u)" -ne "$(stat -c %u "$REPO")" ]; then
	echo "Uruchom jako właściciel folderu $REPO (użytkownik $(stat -c %U "$REPO")), bez doas." >&2
	exit 1
fi
for cmd in git ssh ssh-keygen; do
	command -v "$cmd" >/dev/null 2>&1 || { echo "Brak programu $cmd (doas apk add git openssh-client)" >&2; exit 1; }
done

# klucz wdrożeniowy działa tylko przez SSH - adres https://github.com/... zamieniamy na git@github.com:...
URL=$(git -C "$REPO" remote get-url origin)
case $URL in
	https://github.com/*)
		SSH_URL="git@github.com:${URL#https://github.com/}"
		git -C "$REPO" remote set-url origin "$SSH_URL"
		echo "Adres repozytorium: $URL -> $SSH_URL (klucz wdrożeniowy działa tylko przez SSH)"
		URL=$SSH_URL ;;
	git@github.com:* | ssh://git@github.com/*) ;;
	*)
		echo "Nieznany adres repozytorium: $URL - skrypt obsługuje GitHub" >&2
		exit 1 ;;
esac
SLUG=${URL#git@github.com:}
SLUG=${SLUG#ssh://git@github.com/}
SLUG=${SLUG%.git}

if [ ! -f "$KEY" ]; then
	ssh-keygen -q -t ed25519 -N "" -C "dualsense-bridge sender@$(hostname)" -f "$KEY"
	chmod 600 "$KEY"
	echo "Utworzono klucz $KEY"
fi

# to samo polecenie ssh, którego używa nadajnik przy aktualizacji (proto.ssh_command)
if ERR=$(GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15 -i '$KEY' -o IdentitiesOnly=yes \
-o UserKnownHostsFile='$KNOWN' -o StrictHostKeyChecking=accept-new" git -C "$REPO" ls-remote origin HEAD 2>&1 >/dev/null); then
	echo "OK - nadajnik ma dostęp do $SLUG. Przycisk Aktualizuj w oknie na PC zaktualizuje też telefon."
	exit 0
fi
case $ERR in
	*"Permission denied"* | *"publickey"* | *"Repository not found"* | *"not found"*) ;;
	*)                                        # to nie brak klucza (sieć, DNS, host key...) - instrukcja nic nie da
		echo "Nie udało się połączyć z GitHubem:" >&2
		printf '%s\n' "$ERR" | grep -v '^$' | head -5 >&2
		exit 1 ;;
esac

cat <<EOF

Dodaj ten klucz na GitHubie (jednorazowo):
  1. otwórz https://github.com/$SLUG/settings/keys/new
  2. Title: dualsense-bridge telefon
  3. Key: wklej linię poniżej
  4. NIE zaznaczaj "Allow write access" (wystarczy odczyt)
  5. Add key, potem uruchom ten skrypt jeszcze raz - sprawdzi dostęp

$(cat "$KEY.pub")

EOF
exit 2
