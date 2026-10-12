"""Sender: migracja sender.toml po zmianie wartości domyślnych - zmienia tylko STARE wartości domyślne, własne zostają.

Uruchamiana sama przy starcie nadajnika (sender_config.init, przed wczytaniem pliku), więc po aktualizacji przyciskiem
w oknie na PC nic nie trzeba robić ręcznie. Ręcznie:

  python3 sender_migrate.py [sender.toml]      (bez argumentu: sender.toml obok skryptu)

Plik jest zmieniany linia po linii (komentarze i układ zostają), przed zmianą kopia sender.toml.bak; właściciel
pliku się nie zmienia (usługa działa jako root).
"""
import os
import re
import sys

# klucz -> (stare wartości domyślne, nowa wartość domyślna, powód)
CHANGES = {
    "pad_min_dt": ((0.004,), 0.001, "limiter 4 ms -> 1 ms (szybsze gałki)"),
    "motion_min_dt": ((0.004,), 0.001, "limiter ruchu 4 ms -> 1 ms (szybsze gyro)"),
    "touch_coalesce_ms": ((5, 50), 2, "dotyk co 2 ms"),
}
_LINE = re.compile(r"^(?P<lead>\s*)(?P<key>[A-Za-z_]+)(?P<eq>\s*=\s*)(?P<val>[-+0-9.eE]+)(?P<rest>\s*(#.*)?)$")


def _num(text):
    try:
        return float(text)
    except ValueError:
        return None


def _fmt(v):
    return str(int(v)) if float(v).is_integer() and not isinstance(v, float) else repr(v)


def migrate_text(text):
    """-> (nowy tekst, [(klucz, stara, nowa, powód)]). Zmienia tylko aktywne (niezakomentowane) linie ze starą
    wartością domyślną."""
    out, done = [], []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        m = _LINE.match(body)
        if m and m["key"] in CHANGES:
            old_defaults, new, why = CHANGES[m["key"]]
            v = _num(m["val"])
            if v is not None and any(abs(v - d) < 1e-9 for d in old_defaults):
                line = f"{m['lead']}{m['key']}{m['eq']}{_fmt(new)}{m['rest']}" + line[len(body):]
                done.append((m["key"], m["val"], _fmt(new), why))
        out.append(line)
    return "".join(out), done


def migrate(path, log=print):
    """Migruje plik; -> lista zmian (pusta = nic do zrobienia, brak pliku albo błąd zapisu - wtedy komunikat)."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return []
    except OSError as e:
        log(f"Config migration: cannot read {path}: {e}")
        return []
    new, done = migrate_text(text)
    if not done:
        return []
    try:
        st = os.stat(path)
        with open(path + ".bak", "w", encoding="utf-8") as f:
            f.write(text)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new)
        os.replace(tmp, path)
        if hasattr(os, "chown"):
            for p in (path, path + ".bak"):
                try:
                    os.chown(p, st.st_uid, st.st_gid)       # root (usługa) nie przejmuje pliku użytkownika
                except OSError:
                    pass
    except OSError as e:
        log(f"Config migration: cannot write {path}: {e} - old defaults stay in use")
        return []
    for key, old, val, why in done:
        log(f"Config migration: {os.path.basename(path)}: {key} {old} -> {val} ({why}); backup {os.path.basename(path)}.bak")
    return done


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "sender.toml")
    if not os.path.exists(target):
        print(f"{target}: brak pliku - nic do migracji")
    elif not migrate(target):
        print(f"{target}: nic do zmiany (nowe wartości domyślne albo własne ustawienia)")
