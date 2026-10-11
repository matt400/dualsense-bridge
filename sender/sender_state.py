"""Sender: współdzielony stan (aktywne pady, przydział slotów, zadania w tle)."""

active = {}      # klucz urządzenia (uniq/phys) -> Ctrl
slots = {}       # klucz urządzenia -> numer slotu (stabilny między rozłączeniami)
bg_tasks = set()
saved_slots = None   # MAC -> slot zapamiętany w slots_file (wczytywany przy pierwszym przydziale)
