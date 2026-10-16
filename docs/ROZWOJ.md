# Rozwój

## Struktura

```
receiver/   Windows: receiver_controller.py (start), rx_*.py (moduły), rx_qt*.py (okno)
sender/     Linux:   sender_controller.py (start), sender_*.py (moduły)
tests/      testy na Linuksie z zaślepkami Windows i urządzeń evdev
```

`proto.py` (format pakietów) musi być identyczny w `receiver/` i `sender/`. Testy to sprawdzają.

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
  tylko master (`test_trigfx.py`, `test_trigfx_tx.py`);
- regresje z przeglądu bugów (`test_review.py`, `test_review_tx.py`, `test_hunt*.py`) - m.in. start / stop skanu
  Bluetooth naraz, palec leżący na touchpadzie przy połączeniu, klik touchpada puszczony w widoku Test, skróty z PS
  w trybie TV, rejestracja wibracji z gry w ViGEm (sygnatura callbacku z vgamepad, kolor lightbara jako struktura),
  receiver bez ViGEmBus, obiekty COM audio zwalniane przed CoUninitialize; skróty, akcje mastera i przycisk trybu
  konsoli na granicy widoku Test liczone od trzymanych wejść, Bluetooth nadajnika bez busctl / uprawnień, autobias
  po kalibracji z okna (`test_hunt9.py`, `test_hunt9_tx.py`);
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
- panel skrótów pada (`test_keys_gui.py`) i autostart przez Harmonogram zadań z migracją wpisu Run (`test_autostart.py`).

Działanie z prawdziwym padem, ViGEm, `DisplaySwitch.exe`, przełączaniem dźwięku (pycaw) i powiadomieniami Windows trzeba
sprawdzić ręcznie.
