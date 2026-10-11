"""Tłumaczenia: każdy tekst z _("...") w kodzie receivera ma wersję angielską, a szablony {..} się zgadzają."""
import ast, glob, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import RX                                                  # noqa  (zaślepki Windows)
import rx_i18n
from rx_buttons import LABELS
from rx_editor import GYRO_MODES, NEW_GAME_HINT, STICK_NAME
from rx_settings import ABSENT, MASTER_ACTIONS, MASTER_FEATURES

keys = set()
for f in glob.glob(os.path.join(RX, "*.py")):
    for n in ast.walk(ast.parse(open(f, encoding="utf-8").read())):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_" and n.args \
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
            keys.add(n.args[0].value)
# teksty trzymane w słownikach i tłumaczone przy wyświetlaniu: _(LABELS[n]) itd.
keys |= set(LABELS.values()) | set(STICK_NAME.values()) | set(GYRO_MODES.values()) | {NEW_GAME_HINT}
keys |= {t for t, _ in MASTER_FEATURES.values()} | {t for t, _ in MASTER_ACTIONS.values()} | set(ABSENT.values())
keys |= {"Wynik", "Fizycznie", "Rozszerz: oba ekrany aktywne", "Tylko monitor", "— nie zmieniaj dźwięku —",
         "Zapisano - {file}", "Usunięto - {file}", "Pad master", "Touchpad jako mysz", "Touchpad jako dotyk DS4",
         "Tryb konsoli", "Wyłącz", "Uruchom ponownie", "Ekran", "Pełny ekran", "Wibracja"}
missing = sorted(k for k in keys if k not in rx_i18n.EN)
assert not missing, "brak tłumaczenia EN:\n" + "\n".join(missing)
fields = re.compile(r"\{(\w*)")
bad = [k for k in keys if sorted(fields.findall(k)) != sorted(fields.findall(rx_i18n.EN[k]))]
assert not bad, f"inne pola {{..}} w tłumaczeniu: {bad}"
print(f"OK   {len(keys)} tekstów ma wersję angielską z tymi samymi polami")
rx_i18n.set_lang("en")
assert rx_i18n._("Zapisz") == "Save" and rx_i18n._("Pad {n}").format(n=2) == "Pad 2" and rx_i18n._("nieznany") == "nieznany"
rx_i18n.set_lang("xx")
assert rx_i18n.lang == "pl" and rx_i18n._("Zapisz") == "Zapisz"
print("OK   przełączanie języka, nieznany język = polski")
print("I18N OK")
