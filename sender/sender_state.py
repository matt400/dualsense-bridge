"""Sender: współdzielony stan (aktywne pady, przydział slotów, zadania w tle)."""

active = {}      # klucz urządzenia (uniq/phys) -> Ctrl
slots = {}       # klucz urządzenia -> numer slotu (stabilny między rozłączeniami)
bg_tasks = set()
main_task = None     # zadanie main() - anulowane przy restarcie po aktualizacji
restart = False      # po zakończeniu main(): os.execv (nowy kod po aktualizacji)
saved_slots = None   # MAC -> slot zapamiętany w slots_file (wczytywany przy pierwszym przydziale)
