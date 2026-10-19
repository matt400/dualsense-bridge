# Rozwój

[English](DEVELOPMENT.md) | **Polski**

## Struktura

```
receiver/   Windows: receiver_controller.py (start), rx_*.py (moduły), rx_qt*.py (okno)
sender/     Linux:   sender_controller.py (start), sender_*.py (moduły)
tests/      testy na Linuksie z zaślepkami Windows i urządzeń evdev
```

`proto.py` (format pakietów) musi być identyczny w `receiver/` i `sender/`. Testy to sprawdzają.

## Zrzuty ekranu

```
pip install PySide6-Essentials
python3 tools/screenshots.py          # -> docs/img/*.png (albo: python3 tools/screenshots.py KATALOG)
```

Rysuje okno receivera bez ekranu (Qt offscreen, bez Windowsa) na zaślepkach z testów i danych-atrapie: dwa pady,
bateria, opóźnienie, profil z remapem, widok Test, panel Bluetooth i dwie zakładki ustawień. UI po angielsku.
Uruchom po zmianach w oknie i dodaj nowe PNG do commita.

## Testy

```
pip install evdev ruff PySide6-Essentials
./tests/run_tests.sh
```

`ruff` (reguły w `pyproject.toml`) sprawdza cały kod przed testami. GitHub Actions uruchamia to samo przy każdym
pushu i PR (`.github/workflows/tests.yml`).

Testy obejmują:
- logikę obu stron: touchpad, gesty, limiter, przepełnienie bufora, skróty, profile, gyro, kształtowanie gałek;
- komunikację end-to-end przez UDP;
- start prawdziwych procesów oraz czyste zamknięcie przy restarcie z ustawień (porty UDP, wirtualne DS4, blokada instancji);
- tryb konsoli z zaślepionymi poleceniami ekranu, dźwięku i powiadomień (config, podwójne kliknięcie, auto-OFF, błędy);
- pad master (uprawnienia, akcje PS + przycisk, przejęcie), auto-ON trybu konsoli, R2 z histerezą,
  stabilizację kliku touchpada, `slots.json`;
- okno receivera (Qt offscreen, bez ekranu);
- kopie ustawień, pomiar opóźnienia (state2 / pong2, zegar PC, Bluetooth) i opór spustów w raporcie HID
  (`test_features.py`, `test_features_tx.py`);
- efekty spustów: klik, drgania, wibracja z gry, trig2 / trig wg wersji nadajnika, dziedziczenie w profilu gry,
  tylko master (`test_trigfx.py`, `test_trigfx_tx.py`); efekty spustów z gry (HIDMaestro, trig3): odczyt z raportu
  wyjściowego, profil wygrywa per spust, zwolnienie po zamknięciu programu (`test_gametrig.py`, `test_gametrig_tx.py`);
- regresje z przeglądu bugów (`test_review.py`, `test_review_tx.py`, `test_hunt*.py`) - m.in. start / stop skanu
  Bluetooth naraz, palec leżący na touchpadzie przy połączeniu, klik touchpada puszczony w widoku Test, skróty z PS
  w trybie TV, rejestracja wibracji z gry w ViGEm (sygnatura callbacku z vgamepad, kolor lightbara jako struktura),
  receiver bez ViGEmBus, obiekty COM audio zwalniane przed CoUninitialize; skróty, akcje mastera i przycisk trybu
  konsoli na granicy widoku Test liczone od trzymanych wejść, Bluetooth nadajnika bez busctl / uprawnień, autobias
  po kalibracji z okna (`test_hunt9.py`, `test_hunt9_tx.py`); nowy klient DSU od razu dostaje stan, zapis plików
  ustawień atomowy z fsync - `proto.write_file` / `write_json` (`test_hunt10.py`, `test_hunt10_tx.py`); HIDMaestro
  pobierany jednym zapytaniem Range na plik (odpowiedź 200 = błąd), start jako administrator bez okna UAC dla drugiej
  kopii i bez pętli przy folderze tylko do odczytu, blokada receivera-administratora widziana z procesu bez uprawnień,
  zadania autostartu z własnymi argumentami (`test_hunt11.py`);
  bez mastera restart przy wyłączonym trybie konsoli nie włącza go identyfikatorem pada (`test_console.py`), okno
  wraca na środek po wyjściu z gry, która zmieniła rozdzielczość (`test_settings_gui.py`), polecenia Bluetooth jako
  ten sam pakiet powtórzony (`test_bt.py`);
  bateria wirtualnego DualSense w skali 0-10 (pełny pad bez kabla to 100 %, nie 85 %) (`test_vpad.py`), wybór
  kontrolera Bluetooth odporny na błąd rfkill w połowie - wybrany odblokowany przed blokowaniem reszty, nieudana
  zmiana ponawiana (`test_btadapter_tx.py`);
  przy kilku kontrolerach Bluetooth (wbudowany zablokowany + dongle) skan z okna na wybranym kontrolerze
  (`select` w bluetoothctl, limit czasu po naszej stronie) i wyłączanie pada przez D-Bus na kontrolerze, z którym
  jest połączony (`test_hunt15_tx.py`);
  wybór kontrolera Bluetooth i link policy bez SNIFF także bez receivera (PC wyłączony, Wake-on-LAN), SNIFF
  poprawiany po każdym włączeniu kontrolera - układ `hci_dev_info` jak w jądrze (`test_hunt16_tx.py`); okno na wierzch
  tylko przy nowym połączeniu pada, nie po chwilowej przerwie w sygnale (`test_settings_gui.py`); aktualizacja
  HIDMaestro w trakcie pracy nie usuwa folderu wersji załadowanej w procesie (zależności DLL wczytują się leniwie) -
  znika dopiero po restarcie (`test_hm_update.py`);
- przycisk → gra, dziennik zdarzeń (też zdarzenia nadajnika end-to-end), lightbar od baterii, ustawienia dla nadajnika
  (`test_extras.py`, `test_extras_gui.py`) oraz Wake-on-LAN po stronie nadajnika - kombinacja, ARP, broadcast, magic
  packet, `wol.json` (`test_wol_tx.py`);
- widok Test: pomiar dryfu i propozycja strefy martwej, korekcja dryfu we wszystkich profilach pada, blokada wyjścia
  do gry (DS4, DSU, mysz, skróty), kalibracja żyroskopu z okna i jej zapis na nadajniku, gałki bez strefy martwej
  nadajnika na czas pomiaru, wyłączone skróty i wyjście Options + Create (`test_padtest.py`, `test_padtest_gui.py`,
  `test_calib_tx.py`);
- Bluetooth nadajnika z okna: protokół, BlueZ przez podstawiony `busctl`, skan na prawdziwym procesie z limitem,
  błędy adaptera / uprawnień / programów, lista rundami, całość przez UDP (`test_bt.py`) i panel w oknie z obsługą
  padem (`test_bt_gui.py`);
- panel skrótów pada (`test_keys_gui.py`) i autostart przez Harmonogram zadań z migracją wpisu Run (`test_autostart.py`);
- wydajność i niezawodność: SCHED_FIFO na musl przez syscall (`test_rt_tx.py`), wykrywanie pada przez inotify
  na `/dev/input` i powtórki bye / poleceń / spustów jako ten sam pakiet rozłożony w czasie (`test_devwatch_tx.py`,
  `test_rx2.py`, `test_e2e.py`), odczyt tylko potrzebnych bajtów raportu wyjściowego HIDMaestro (`test_vpad.py`),
  limit czasu odbioru w jądrze (SO_RCVTIMEO) zamiast select() przed każdym pakietem (`test_rx2.py`);
- wirtualne pady: backend HIDMaestro / ViGEmBus i ich zmiana w trakcie, pad zastępczy dla stałej kolejności graczy,
  raport DualSense, feedback z raportu wyjściowego DualSense (`test_vpad.py`, zakładka Pady w `test_settings_gui.py`),
  pobieranie HIDMaestro przez HTTP Range, wersje, błędy zipa / sieci i uruchamianie jako administrator (`test_hm_update.py`);
- bezpieczeństwo: protokół v3 z kluczem - samoczynne przejście (próbny ping), znacznik świeżości, dokładne powtórki,
  podpisane v2 po przejściu, zapamiętanie w `auth.json`, zegar PC nadajnika po restarcie receivera, powtórzony ping nie
  cofa zegara nadajnika, koniec do końca przez UDP z ponownie wysłanym nagraniem (`test_security_tx.py`,
  `test_security.py`, `test_e2e_key.py`); DSU tylko na 127.0.0.1 z limitem klientów, nowe wydanie HIDMaestro czeka,
  lista w zakładce Bezpieczeństwo, zwykły tekst w etykietach okna (`test_security.py`); parowanie z okna tylko padów,
  adapter nie zostaje otwarty na parowanie, zaufanie tylko dla padów (`test_bt.py`); klucz w
  `/etc/dualsense-sender.env` zamiast `ExecStart`, ograniczenia systemd (`test_service_tx.py`).

Działanie z prawdziwym padem, ViGEm, HIDMaestro (pythonnet, sterownik), `DisplaySwitch.exe`, przełączaniem dźwięku (pycaw) i powiadomieniami Windows trzeba
sprawdzić ręcznie.
