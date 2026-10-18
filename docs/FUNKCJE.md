# Funkcje

- **Wirtualny DualSense** (HIDMaestro) albo **DS4** (ViGEmBus) z przyciskami, gałkami, spustami, touchpadem,
  żyroskopem, wibracjami i kolorem lightbara z gry. Ustawienia → Pady: `auto` (HIDMaestro, gdy jest DLL i receiver
  działa jako administrator, inaczej ViGEmBus), `hidmaestro` (to samo, ale brak HIDMaestro to ostrzeżenie w dzienniku)
  albo `vigem`. HIDMaestro pobiera się i aktualizuje sam z GitHuba (tylko DLL z zipa wydania, przez HTTP Range), a
  receiver uruchamia się jako administrator (UAC; zadanie autostartu z najwyższymi uprawnieniami). Zmiana działa od razu (pady powstają od nowa). Błąd HIDMaestro przy tworzeniu pada = ten pad przez
  ViGEmBus i ostrzeżenie w dzienniku. Karta pada (ikona touchpada) pokazuje, jakim padem jest slot.
- **Serwer DSU (Cemuhook)** z ruchem i dotykiem dla emulatorów (Cemu, Yuzu/Ryujinx, Dolphin).
- **Touchpad jako mysz:** kursor, tap = lewy przycisk, przewijanie dwoma palcami, tap dwoma palcami = prawy przycisk.
  Przełączanie: PS + klik touchpada. Fizyczny klik nie przesuwa kursora: ruch z ostatnich ~80 ms przed kliknięciem
  jest cofany, przez ~150 ms po wciśnięciu i puszczeniu kursor stoi, a przeciąganie z wciśniętym klikiem zaczyna się
  dopiero po wyraźnym ruchu palca. Czułość i te czasy: Ustawienia → Mysz (działa od razu).
- **Jedno okno do wszystkiego** (PySide6, ikona w zasobniku):
  - po lewej karty padów: bateria, **prawdziwe opóźnienie pad → gra** (średnia, „zwykle ≤” = 95 na 100 pomiarów i max z 5 s, z paskiem odcinków:
    Bluetooth / nadajnik / sieć / PC), pod nim **przycisk → gra** (to samo tylko dla naciśnięć - ostatnie 100, z resetem),
    aktywny profil, test wibracji, wyłączenie pada, korona mastera; ping, utrata pakietów i Hz w podpowiedzi;
    niepodłączony pad to sama nazwa;
  - **dziennik zdarzeń** na dole okna (ikona listy w nagłówku): połączenia i rozłączenia padów, nadajnik online /
    offline, zmiana mastera, tryb konsoli, błędy, aktualizacje, niska bateria; filtr poziomu (wszystko / ostrzeżenia i
    błędy / tylko błędy), limit linii, kopiowanie, czyszczenie (dwa kliknięcia). Ikona nadajnika włącza **zdarzenia z
    nadajnika** (domyślnie wyłączone): połączenie pada z nadajnikiem, wysłany Wake-on-LAN, utrata kontaktu z receiverem,
    błędy - nadajnik trzyma je, póki receiver nie odpowiada, więc np. Wake-on-LAN pojawia się po starcie komputera
    z prawdziwą godziną. Dziennik jest tylko w pamięci (restart go czyści), wysokość panelu da się przeciągnąć;
  - na środku rysunek pada na żywo: kliknij przycisk, spust albo gałkę, żeby ją zremapować lub dostroić;
  - po prawej profil (domyślny / per gra), zakładki **Selected**, **Gyro**, **Changes** i przycisk **Save** (Ctrl+S);
  - remapowanie przycisków i gałek, celowanie żyroskopem (prawa gałka / mysz); strefy martwe, krzywe i
    anti-deadzone w zwijanej sekcji „Zaawansowane” (otwiera się sama, gdy coś w niej zmieniono);
  - **efekty spustów** (adaptive triggers) L2 / R2 w profilu: **opór** (siła 1-8, od którego miejsca skoku),
    **klik** jak spust broni (punkt 20-70 % skoku i siła) albo **drgania** (od którego miejsca, siła,
    częstotliwość). Gra przez wirtualnego DS4 tego nie wyśle, więc ustawia to nadajnik raportem HID. Profil gry
    może je nadpisać albo zostawić „jak w profilu pada”; w Ustawienia → Master można je zastrzec tylko dla mastera;
  - **wibracja z gry na spustach** (namiastka): L2 drga jak duży silnik, R2 jak mały, z ustawianą siłą
    i częstotliwością; na czas wibracji zastępuje efekt spustu, po chwili ciszy efekt wraca. Liczy to nadajnik
    z tych samych pakietów wibracji - bez dodatkowego ruchu w sieci. Klik, drgania i wibracja z gry wymagają
    nadajnika v8 (starszy dostaje sam opór);
  - **efekty spustów z gry**: gra z obsługą DualSense ustawia je na wirtualnym DualSense (HIDMaestro), a receiver
    przekazuje je do pada bez zmian (każdy tryb, jaki wyśle gra). Na spuście z efektem w profilu wygrywa profil;
    gdy gra sama steruje spustami, wibracja z gry na spustach jest wyłączona. Działają w każdym programie (także
    Steam), zostają przy alt-tab i znikają po zamknięciu programu, który je ustawił, albo po odłączeniu pada.
    Funkcja mastera „Efekty spustów” obejmuje też je. Wymagają nadajnika v11; pierwszy efekt gry trafia do dziennika;
  - profile per gra (`gra.exe`), które włączają się same, gdy gra jest aktywnym oknem;
  - zmiany działają na padzie od razu, zapis do `remap.json` jest osobno;
  - akcje nieodwracalne (wyłączenie pada, usunięcie profilu, reset, restart receivera) wymagają drugiego kliknięcia;
  - nagłówek z samymi ikonami (pełny ekran, ustawienia, aktualizuj, restart, zakończ - opis w podpowiedzi);
    **Restart** jest też w menu zasobnika;
  - **pełny ekran = tryb TV:** większy interfejs (czytelny z kanapy) i sterowanie oknem padem, gdy okno jest
    aktywne: D-pad - poruszanie się, ✕ - wybór, ○ - wstecz / wyjście z pełnego ekranu, L1 / R1 - poprzedni / następny
    pad, PS trzymane - zwykłe skróty;
  - przyciski pada pokazane jako ikony (z pełną nazwą w podpowiedzi);
  - **Skróty pada** (ikona pada w nagłówku): wysuwany z prawej panel ze wszystkimi skrótami pada, zbudowany z
    bieżących ustawień (przycisk trybu konsoli, akcje mastera, długie PS); korona = tylko pad master. W trybie TV:
    D-pad do ikony, ✕ otwiera, D-pad przewija, ○ zamyka;
  - **Bluetooth nadajnika z okna** (nadajnik za telewizorem nie potrzebuje ekranu): ikona Bluetooth w nagłówku z kropką
    stanu (zielona - działa, niebieska - skanuje, żółta - wyłączony, czerwona - błąd, szara - brak danych) otwiera
    wysuwany z prawej panel: **skanowanie** (z odliczaniem, samo kończy się po 30 s - skan zabiera radio połączonym
    padom), lista urządzeń (pady na górze, siła sygnału w trakcie skanu, stan) i przyciski **Sparuj** (parowanie,
    zaufanie i połączenie), **Połącz**, **Rozłącz**, **Zapomnij**, a przy wyłączonym adapterze **Włącz Bluetooth**.
    W trybie TV: D-pad do ikony, ✕ otwiera, D-pad chodzi tylko po panelu, ✕ klika, ○ zamyka. Tryb parowania
    DualSense: Create + PS, aż lightbar szybko miga. Wymaga nadajnika v10 (systemd: `busctl`, BlueZ: `bluetoothctl`);
    **Kontroler Bluetooth**: lista wszystkich modułów nadajnika z nazwą urządzenia (np. „Qualcomm WCN3990”,
    „Realtek TP-Link UB5A Adapter”), rodzajem (wbudowany / dongle USB), numerem hciN i stanem; **Użyj** wybiera
    moduł na stałe (zapamiętany po urządzeniu, nie po numerze). **Auto** - dongle USB przed wbudowanym; **Ręcznie** -
    nadajnik nic nie zmienia. Niewybrane są wyłączone (rfkill), wybrany działa bez trybu SNIFF (pad nie usypia
    łącza); wpięcie / wyjęcie dongla bez restartu. Domyślny tryb: `bt_adapter` w `sender.toml`;
  - **widok Test** (trzeci przełącznik pod padem: Wynik / Fizycznie / Test) dla wybranego pada:
    - na rysunku na żywo przyciski, wartości spustów (0-255), gałki, palce i klik touchpada (także w trybie myszy);
    - po prawej wykresy gyro [°/s] i akcelerometru [g] (3 osie, ostatnie 5 s, skala sama się dopasowuje) i
      położenie obu gałek w skali pierwiastkowej (dryf rzędu 1-2 % jest dobrze widoczny);
    - **dryf gałek:** „Zmierz dryf” - puszczone gałki przez 3 s (nadajnik na ten czas wyłącza swoją strefę martwą,
      inaczej przyciąłby odchyłki do ~2 %), wynik: największe wychylenie w spoczynku i propozycja strefy martwej
      (×1,25, w górę do 1 %, min. 3 %); „Zastosuj” zapisuje ją od razu jako **korekcję dryfu pada**
      (`receiver/padcal.json`, per MAC). Działa we wszystkich profilach tego pada, także w profilach gier, jako
      minimum strefy martwej (większa z profilu zostaje) i idzie za gałką fizyczną przy zamianie gałek;
    - **kalibracja żyroskopu:** pad leży nieruchomo 3 s, nadajnik liczy offset i zapisuje go na nadajniku per pad
      (`sender/gyro_cal.json`); skalibrowany pad nie kalibruje się już przy starcie nadajnika (nie trzeba go wtedy
      odkładać), autobias dalej dostraja offset w pamięci. Wymaga nadajnika v9;
    - na czas testu gra dostaje neutralny stan testowanego pada (przełącznik „Blokuj wyjście do gry”, domyślnie
      włączony): przyciski, gałki, ruch i dotyk nie idą do gry, mysz touchpada, gyro-mysz, skróty PS i tryb
      konsoli nie reagują. Blokada działa tylko przy aktywnym oknie receivera: znika po przejściu do gry (inne okno
      na pierwszym planie), wyjściu z widoku Test, minimalizacji / schowaniu okna i zamknięciu receivera;
    - w trybie TV testowany pad nie steruje oknem; przytrzymanie ○ przez 1 s wychodzi z testu;
    - przy otwartym widoku Test **żaden pad nie uruchamia skrótów** (PS + przycisk, akcje i przejęcie mastera, tryb
      konsoli, PS + klik touchpada) - testowanie przycisków niczego nie przełączy;
    - **wyjście awaryjne z pada: Options + Create** (naciśnięte razem) na padzie master (bez mastera w ustawieniach -
      na dowolnym) kończy test bez myszy, także przy schowanym oknie i gdy testowany jest sam master: blokada znika od razu, a
      trzymane w tej chwili przyciski gra dostaje jako puszczone, dopóki ich nie puścisz. Bez PS - długie PS + △
      wyłączało pada.
- **Skróty z PS:**

  | Skrót | Działanie |
  |---|---|
  | PS + Share | zrzut ekranu |
  | PS + d-pad góra / dół | czułość myszy |
  | PS + d-pad lewo / prawo | zmiana profilu |
  | długie PS | Xbox Game Bar albo Steam Big Picture |

  PS trzymane już w chwili połączenia pada (włączanie pada przytrzymaniem PS) nie otwiera Game Bar i nie idzie do gry.
  Receiver wyłącza otwieranie Game Bar przyciskiem pada (Windows brał PS za przycisk Xbox), a gdy Game Bar nie jest
  zainstalowany, linki `ms-gamebar` dostają cichy program - nie wyskakuje okno „Wybierz aplikację”.

- **Tryb konsoli:** podwójne kliknięcie wybranego przycisku pada (domyślnie **R2**) przełącza ekran i dźwięk między
  monitorem a telewizorem. R2 liczy się tylko przy głębokim naciśnięciu (z histerezą - drżenie spustu nie daje
  podwójnego kliknięcia) i tylko samo: R2 razem z innym przyciskiem (typowo w grze) nie przełącza. **Po połączeniu pada
  master tryb włącza się sam** (`auto_on_master`); podwójne R2 go wyłącza i zostaje wyłączony do następnego połączenia
  mastera. Restart receivera z okna zachowuje stan trybu. Stary `console.json` z PS / mute zmienia się sam na R2.
  **W grze tryb się nie przełącza:** podwójne R2 działa tylko, gdy na pierwszym planie jest program z listy
  `allowed_apps` (pulpit, Steam / Big Picture, launchery - lista do edycji w Ustawieniach); w grze podwójne R2 jest
  po prostu ignorowane (bez mignięcia - to zwykła rozgrywka), a włączenie po połączeniu mastera czeka, aż gra zejdzie
  z pierwszego planu.

  | | Ekran (`SetDisplayConfig`, awaryjnie `DisplaySwitch.exe`) | Dźwięk | Powiadomienie |
  |---|---|---|---|
  | ON | tylko TV (`/external`), na TV 2560×1440 i skalowanie 200 % (`tv_width`, `tv_height`, `tv_scale`) | `audio_tv` | „Tryb konsoli włączony” |
  | OFF | oba ekrany (`/extend`) | `audio_pc` | „Tryb konsoli wyłączony” |
  | brak pakietów z pada przez `timeout_s` | jak OFF | jak OFF | jak OFF |
  | wyłączenie / uśpienie PC (akcja mastera, Start, Big Picture) | jak OFF, przed wyłączeniem | jak OFF | brak |
  | start receivera (autostart przy logowaniu) | `startup_mode`: `extend` (oba ekrany) albo `internal` | `audio_pc` | brak |

  Przy starcie domyślnie oba ekrany są aktywne, więc nie zostajesz bez obrazu. Zwykłe zamknięcie receivera przy
  włączonym trybie konsoli wraca do extend, tak samo wyłączenie Windows (Windows zapamiętuje układ ekranów - bez tego
  po starcie obraz, także ekran logowania, byłby tylko na TV). Błąd polecenia ekranu, dźwięku albo powiadomienia trafia do logu i nie
  przerywa działania.
- **Pad master** (np. gdy grają dzieci): wybrane funkcje działają tylko na jednym padzie - tryb konsoli, akcje
  systemowe, długie PS, PS w grze / Steam, zrzut ekranu, zmiana profilu, mysz, czułość. Inny pad dostaje czerwone
  mignięcie lightbara. Master jest przypisany do pada (MAC), nie do slotu. Szybka zmiana: korona na karcie pada albo
  **PS + R3 przytrzymane 3 s** na padzie (złoty błysk; można wyłączyć). **Touchpad jako mysz idzie za masterem:**
  touchpad mastera steruje kursorem, pozostałe pady mają dotyk DS4 (każdy pad dalej przełącza się PS + klik
  touchpada); gdy mastera nie ma albo nie jest podłączony - wg `mouse_pads`. Akcje systemowe mastera:

  | Domyślnie | Akcja |
  |---|---|
  | PS + Options | pauza gry (Esc) |
  | PS + △ (przytrzymaj ~1 s) | zamknij grę (nie zamyka pulpitu ani Steama) |
  | do ustawienia | uśpij komputer, wyłącz komputer (10 s na `shutdown /a`) |

  Przycisk z kombinacji (i samo PS) nie trafia do gry. Do akcji nie można użyć przycisków skrótów PS (Share, D-pad)
  ani R3. Gdy master nie jest podłączony: każdy może (domyślnie) albo nikt.
- **Ustawienia** (ikona w zasobniku → Ustawienia, albo przycisk w oknie), siedem zakładek:
  - **Tryb konsoli:** przycisk pada wybierasz naciśnięciem go na padzie, czasy to pola liczbowe, urządzenia audio to
    listy aktywnych wyjść Windows, „Przetestuj” włącza tryb na 10 s z wartościami z okna. Zmiana trybu konsoli zapisuje
    `console.json` i restartuje receiver (niezapisane zmiany profilu remapowania przepadają);
  - **Mysz:** czułość i stabilizacja kliknięcia touchpada;
  - **Master:** pad master, co wolno tylko jemu, przyciski akcji. Mysz i master działają od razu, bez restartu
    (`settings.json`);
  - **Lightbar:** kolor per pad / domyślny, tryb, miganie, próg niskiej baterii (od razu);
  - **Wake-on-LAN:** włączenie, przycisk do pary z PS, czas przytrzymania, MAC i broadcast (puste = automatycznie);
  - **Okno:** ekran, na którym pojawia się okno (zawsze na środku; gdy ekranu nie ma - na dostępnym), okno albo
    pełny ekran (też F11 i przycisk w nagłówku), język: polski / English (zmiana po restarcie). Po przełączeniu ekranów
    (tryb konsoli, rozdzielczość / skalowanie TV) okno wraca na środek i mieści się na ekranie (zmniejszone na TV,
    na monitorze wraca do swojego rozmiaru). Połączenie pada wyciąga okno na wierzch - ale nie w trakcie gry (ta sama
    lista programów co w Trybie konsoli);
  - **Kopie:** kopie `settings.json`, `console.json` i `remap.json` zrobione przy aktualizacji (5 ostatnich, w
    `receiver/backups/`) albo ręcznie. „Przywróć” (dwa kliknięcia) najpierw zapisuje kopię bieżących plików - więc
    też da się to cofnąć - a potem uruchamia receiver od nowa.
- **Lightbar** (Ustawienia → Lightbar), osobno dla każdego pada (MAC) albo domyślnie dla wszystkich:
  - **gra ma pierwszeństwo tylko w grze** - poza grą (pulpit, Steam / Big Picture, launchery - ta sama lista programów
    co w Trybie konsoli) zawsze świeci własny kolor, nawet gdy Steam ustawi swój; w grze kolor gry zastępuje własny.
    Opcja **„Gra może zmienić kolor”** wyłączona = zawsze kolor z ustawień. Pierwszy kolor z PC trafia do dziennika;
  - tryb **stały kolor** (wybór koloru; pusty = `idle_lightbar`) albo **kolor wg baterii** (czerwony przy progu → żółty →
    zielony od 80 %);
  - **miganie na czerwono przy niskiej baterii** - także w grze (nad kolorem gry), nie przy ładowaniu; przy czerwonym
    kolorze bazowym miga czerwony / zgaszony;
  - potwierdzenia skrótów (złoty błysk mastera, profil, mysz) zawsze na wierzchu;
  - **próg niskiej baterii** jest wspólny z ostrzeżeniem na karcie pada i powiadomieniem (`low_batt` z toml to tylko
    wartość startowa).
- **Wake-on-LAN z pada** (Ustawienia → Wake-on-LAN): gdy komputer nie odpowiada (wyłączony / uśpiony), **PS + Options
  przytrzymane ~1 s** (przycisk i czas do wyboru) - nadajnik wysyła magic packet. Przy działającym komputerze ta sama
  kombinacja idzie do receivera jak zwykle (np. pauza mastera), więc nic nie koliduje; samo długie PS się nie nadaje
  (tak włącza się pada). Pad potwierdza białym błyskiem i wibracją, błąd (np. nieznany MAC) - czerwonym.
  - MAC komputera: wpisany w oknie albo **automatycznie** - nadajnik odczytuje go z tablicy ARP nadajnika, póki komputer
    działa, i zapamiętuje; adres broadcast: wpisany albo broadcast sieci do PC + `255.255.255.255`; pakiet idzie też
    prosto na IP komputera. Okno pokazuje, czego nadajnik użyje.
  - Ustawienia trafiają do nadajnika (co 5 s i przy zmianie) i są zapisywane na nadajniku w `sender/wol.json` - przy
    wyłączonym komputerze nie ma ich skąd wziąć.
  - Komputer musi mieć włączony Wake-on-LAN w BIOS / UEFI i we właściwościach karty sieciowej (Zarządzanie energią:
    „Zezwalaj temu urządzeniu na wznawianie”, „Tylko magic packet”); najlepiej po kablu - przez Wi-Fi zwykle nie działa.
- **Wyłączanie pada** z PC albo samo po bezczynności (domyślnie 15 min).
- **Opóźnienie pad → gra** (średnia, „zwykle ≤” = 95. percentyl, max; pasek = średnie odcinków) mierzone dla każdej zmiany stanu pada
  (przycisk, gałka, ruch), od chwili, gdy jądro nadajnika dostało raport z pada, do oddania raportu wirtualnemu DS4. Nadajnik zna zegar PC z odpowiedzi na ping
  (jak NTP, pomiar o najmniejszym RTT), więc sieć jest mierzona w jedną stronę, nie jako połowa pingu. Odcinki:
  **Bluetooth** - opóźnienie radia ponad najlepszy przypadek z 2 s (znaczniki czasu pada; stała część ~1-2 ms jest
  niemierzalna), **nadajnik** - jądro → wysłanie, **sieć**, **PC** - odbiór → wirtualny pad. Ze starszym nadajnikiem karta
  pokazuje sam ping; starszy receiver dostaje od nowego nadajnika zwykłe pakiety.
  **Przycisk → gra** to ten sam pomiar tylko dla zmian przycisków, D-pada i spustów (przejście przez próg wciśnięcia,
  bez gałek i żyroskopu): ostatnie 100 naciśnięć / puszczeń bez limitu czasu (naciśnięcia są rzadkie - 5 s dałoby
  kilka próbek), kasowane przy zmianie pada w slocie albo ikoną ↻ na karcie.
- **Wibracja:** jednocześnie przez sterownik (evdev FF) i bezpośrednio raportem HID przez `/dev/hidrawN`
  (nadajnik jako root), z flagami zależnymi od firmware pada, kolor lightbara w tym samym raporcie; ustawienie `rumble` w `sender.toml`. Karta pada
  pokazuje „wibracja: sterownik + HID” albo powód jej braku (brak uprawnień / brak w jądrze / nie znaleziono pada /
  stary nadajnik). Przycisk wibracji na karcie pada sprawdza, czy wibracja dochodzi do pada.
- **Stała kolejność graczy:** nadajnik pamięta, który pad (MAC) był w którym slocie (`slots.json`), a receiver podłącza
  wirtualne pady w kolejności slotów. Gdy pad 2 połączy się sam, slot 1 dostaje najpierw **pad zastępczy** (neutralny
  stan), więc gry i Steam (także bez Steam Input) zawsze widzą pad 2 jako gracza 2. Pad 1 dołącza potem do
  zastępczego bez ponownego podłączania, a zastępczy znika razem z ostatnim padem z wyższego slotu. Wyłączenie:
  Ustawienia → Pady → „Stała kolejność graczy”. HIDMaestro tworzy też pady ze stałą tożsamością slotu (te same
  ścieżki urządzeń po każdym podłączeniu).
- **Niskie opóźnienia:**
  - przyciski wysyłane natychmiast, z powtórkami na wypadek zgubionego pakietu,
  - gałki, ruch i dotyk bez opóźnienia do 1000 Hz (szybsze serie raportów zlewane, ostatni stan zawsze dochodzi),
  - na PC raport wirtualnego pada idzie od razu z wątku, który odebrał pakiet (bez przekazywania do innego wątku),
  - priorytety wątków i procesów, timer 1 ms na Windows,
  - nadajnik (jako root): procesor bez głębokiego uśpienia (PM QoS), proces na najszybszych rdzeniach i z planistą
    czasu rzeczywistego - wybudzenie procesora z uśpienia (na telefonach ze Snapdragonem 845 do ~6.5 ms) nie
    opóźnia pada ani pingu.
