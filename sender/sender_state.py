"""Sender: współdzielony stan (aktywne pady, przydział slotów, zadania w tle)."""
import asyncio

active = {}      # klucz urządzenia (uniq/phys) -> Ctrl
slots = {}       # klucz urządzenia -> numer slotu (stabilny między rozłączeniami)
bg_tasks = set()
main_task = None     # zadanie main() - anulowane przy restarcie po aktualizacji
restart = False      # po zakończeniu main(): os.execv (nowy kod po aktualizacji)
saved_slots = None   # MAC -> slot zapamiętany w slots_file (wczytywany przy pierwszym przydziale)


def spawn(coro):
    """Zadanie w tle trzymane w bg_tasks (pętla asyncio trzyma tylko słabe referencje - GC mógłby je przerwać)."""
    t = asyncio.get_running_loop().create_task(coro)
    bg_tasks.add(t)
    t.add_done_callback(bg_tasks.discard)
    return t


def ctrl_by_idx(idx):
    """Ctrl pada w slocie idx albo None."""
    return next((c for c in active.values() if c.idx == idx), None)
