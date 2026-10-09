"""Sender: współdzielony stan (aktywne pady, przydział slotów, zadania w tle)."""

active = {}      # klucz urządzenia (uniq/phys) -> Ctrl
slots = {}       # klucz urządzenia -> numer slotu (stabilny między rozłączeniami)
bg_tasks = set()
