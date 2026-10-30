# Bezpieczeństwo

[English](SECURITY.md) | **Polski**

Receiver działa na PC **jako administrator**, nadajnik **jako root**, a rozmawiają przez UDP w sieci lokalnej. Bez
klucza każdy w tej sieci może im wysyłać pakiety. W domu, we własnej sieci, to może wystarczyć; w sieci wspólnej
(współlokatorzy, Wi-Fi dla gości, niezaufane urządzenia „smart”) ustaw klucz.

## Przed czym chroni klucz

Bez `key` każdy w sieci lokalnej może:
- ruszać myszą i klikać na PC (touchpad jako mysz) oraz wywołać akcje pada master (uśpienie, wyłączenie),
- wysyłać polecenia do nadajnika: aktualizacja i restart, parowanie urządzeń Bluetooth, wyłączanie padów.

Z tym samym `key` po obu stronach każdy pakiet jest podpisany (HMAC-SHA256). Oba programy same przechodzą wtedy na
**protokół v3**: każdy pakiet ma też znacznik świeżości, więc nagrany pakiet wysłany ponownie później jest odrzucany
(ochrona przed replay). Przejście jest zapamiętane w `auth.json` obok każdego programu; zmiana klucza zaczyna je od
nowa.

## Ustawienie klucza

1. Wygeneruj: Ustawienia → Bezpieczeństwo → **Kopiuj nowy klucz** albo
   `python -c "import secrets; print(secrets.token_urlsafe(24))"`.
2. Receiver: `key = "..."` w `receiver.toml` (albo zmienna środowiskowa `PADKEY`).
3. Nadajnik: `key = "..."` w `sender.toml` albo `doas ./install-service.sh <IP PC> --key "..."` - instalator wpisuje
   klucz do `/etc/dualsense-sender.env` (prawa 0600), a nie do polecenia usługi, które każdy użytkownik widzi w `ps`.
4. Uruchom oba ponownie. Inny klucz po obu stronach okno zgłasza w dzienniku zdarzeń.

Po powrocie po jednej stronie do wersji bez protokołu v3 usuń `auth.json` po drugiej.

## Inne ustawienia

| Ustawienie | Gdzie | Domyślnie | Po co |
|---|---|---|---|
| `allow` | receiver | "" | przyjmuj pakiety tylko z tego IP (nadajnika) |
| `dsu_bind` | receiver | `127.0.0.1` | serwer DSU (żyroskop dla emulatorów) tylko dla tego komputera; `0.0.0.0` = emulator na innym urządzeniu, ale wtedy każdy w sieci widzi ruchy i przyciski padów |
| `dsu_max_clients` | receiver | 4 | najwyżej tylu subskrybentów DSU (każdy dostaje strumień do `dsu_hz` pakietów/s) |
| `hm_min_age_days` | receiver | 7 | nieużywane, dopóki HIDMaestro jest przypięty (domyślnie: stała wersja i link w `PINNED_TAG` w `rx_hm_update.py`, sprawdzany jest SHA-256 każdego pliku z zipa, nowsze wydanie nigdy nie instaluje się samo, a przesunięcie przypięcia to przejrzany commit). Bez przypięcia nowe wydanie HIDMaestro instaluje się dopiero tyle dni po publikacji (nie ma podpisu, a działa w procesie administratora); pierwsza instalacja nie czeka; `0` = od razu; **Ustawienia → Pady → Zainstaluj teraz** pomija czekanie dla oczekującego wydania, jednorazowo |

## Pliki i uprawnienia

- **Folder receivera.** Receiver (i zadanie autostartu z najwyższymi uprawnieniami) uruchamia kod ze swojego folderu
  jako administrator. Jeśli folder należy do Twojego zwykłego konta, każdy uruchomiony przez Ciebie program może ten kod
  zmienić. Ustawienia → Bezpieczeństwo → **Zabezpiecz folder** oddaje folder Administratorom (zwykli użytkownicy
  tylko czytają); receiver dalej zapisuje ustawienia i się aktualizuje. Gdy receiver działa z klona gita, obejmuje to
  całe repozytorium razem z `.git`: aktualizacja uruchamia tam git jako administrator, więc `.git` zapisywalny dla
  Ciebie (hooki, konfiguracja) uruchomiłby kod jako administrator przy następnym sprawdzeniu aktualizacji. Python zainstalowany „tylko dla tego
  użytkownika” ma ten sam problem - zainstaluj go dla wszystkich użytkowników.
- **Folder nadajnika.** Usługa działa jako root, a kod leży w folderze użytkownika. `doas ./install-service.sh
  --own-root ...` oddaje repozytorium rootowi (przycisk Aktualizuj dalej działa; `setup-update-key.sh` uruchamiaj
  potem przez `doas`).
- **systemd.** Usługa działa z `NoNewPrivileges`, systemem plików tylko do odczytu poza repozytorium
  (`ProtectSystem=strict`), `/home` tylko do odczytu, prywatnym `/tmp`. Coś przestało działać w Twoim systemie?
  `doas systemctl edit dualsense-sender` i wyzeruj winną opcję.

## Bluetooth z okna

Parowanie z okna działa tylko dla padów do gier; tylko pady dostają flagę „zaufany” (zaufane urządzenie może łączyć
się samo). Po parowaniu adapter przestaje przyjmować parowanie. Urządzenie wciąż może udawać pada - paruj tylko,
patrząc na listę.

## Zgłaszanie problemu

Załóż zgłoszenie (issue) na GitHubie. Coś, co nie powinno być publiczne: prywatne zgłoszenie podatności na GitHubie
(zakładka Security → Report a vulnerability).
