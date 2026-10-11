import sys, os, time, json, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                              # noqa
import rx_i18n; rx_i18n.set_lang("en")              # asercje na angielskich tekstach okna
import rx_web, rx_games
from proto import T_CMD
C.REMAP_UI = True; C.REMAP_UI_PORT = 0
url = rx_web.start()
base = S.profile_key(0)
store.save(base, {"buttons": {"cross": "circle"}}); S.reload_profile(0)
rx_games.recent[:] = ["eldenring.exe", "cyberpunk2077.exe"]

def call(path, body=None, token=rx_web.token):
    req = urllib.request.Request(rx_web.base_url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

# ---- API ----
assert call("/api/pads", token="zly")[0] == 403
code, d = call("/api/pads"); assert code == 200 and d["pads"][0]["profiles"][0]["key"] == base
assert call("/api/apply", {"slot": 0, "key": "inny@gra.exe", "profile": {}})[0] == 400       # cudzy pad
assert call("/api/apply", {"slot": 0, "key": base + "@../../x.exe", "profile": {}})[0] == 400
assert call("/api/apply", {"slot": True, "profile": {}})[0] == 400
code, r = call("/api/apply", {"slot": 0, "key": base + "@doom.exe", "profile": {"gyro": {"mode": "mouse"}}})
assert code == 200 and r["key"] == base + "@doom.exe" and not r["in_file"]
S.set_game("doom.exe"); assert S.active[0] == base + "@doom.exe"
code, r = call("/api/delete", {"slot": 0, "key": base + "@doom.exe"}); assert r["ok"] and S.active[0] == base
assert call("/api/delete", {"slot": 0, "key": base})[0] == 400                                # profilu pada nie usuwamy
assert call("/api/power", {"slot": 0})[0] == 400                                               # pad niepodłączony
S.set_game(None)
print("OK   API: token, walidacja kluczy, profil gry, usuwanie, power")

# ---- edytor w przeglądarce ----
from playwright.sync_api import sync_playwright
S.alive[0] = True
errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1300, "height": 900})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("dialog", lambda dlg: dlg.accept())
    pg.goto(url); pg.wait_for_selector("#profsel option", state="attached"); time.sleep(0.4)
    assert "in use" in pg.inner_text("#profsel"), pg.inner_text("#profsel")
    # nowy profil gry z podpowiedzi
    pg.select_option("#profsel", "__new")
    assert pg.input_value("#newexe") == "eldenring.exe"
    pg.fill("#newexe", "EldenRing"); pg.click("#newok"); time.sleep(0.5)
    key = base + "@eldenring.exe"
    assert pg.input_value("#profsel") == key and store.has(key)
    assert store.get(key)["buttons"] == {"cross": "circle"}, "kopia bieżących ustawień"
    # strefa martwa lewej gałki
    pg.click('.inp[data-name="left_stick"]')
    pg.eval_on_selector("#r_dz", "el => { el.value = 12; el.dispatchEvent(new Event('input')); }")
    pg.eval_on_selector("#r_curve", "el => { el.value = 1.5; el.dispatchEvent(new Event('input')); }")
    # spust
    pg.click('.inp[data-name="r2"]')
    pg.eval_on_selector("#r_tdz", "el => { el.value = 30; el.dispatchEvent(new Event('input')); }")
    # gyro
    pg.select_option("#gmode", "stick"); pg.wait_for_selector("#ghold")
    pg.select_option("#ghold", "l2")
    pg.eval_on_selector("#r_gsens", "el => { el.value = 1.75; el.dispatchEvent(new Event('input')); }")
    time.sleep(0.4)
    live = store.get(key)
    assert live["tuning"] == {"left": {"dz": 0.12, "outer": 1.0, "curve": 1.5, "anti": 0.0}, "r2": {"dz": 30, "max": 255}}, live["tuning"]
    assert live["gyro"]["mode"] == "stick" and live["gyro"]["hold"] == "l2" and live["gyro"]["sens"] == 1.75
    assert "not saved" in pg.inner_text("#status")
    changes = pg.inner_text("#changes")
    assert "Left stick response" in changes and "Gyro: right stick" in changes, changes
    pg.click("#save"); time.sleep(0.4)
    assert "Saved" in pg.inner_text("#status") and json.load(open(C.REMAP_PATH))["profiles"][key]["gyro"]["hold"] == "l2"
    # gra na pierwszym planie -> "in use" przechodzi na profil gry, stan gyro na żywo
    S.set_game("eldenring.exe"); time.sleep(0.4)
    assert "In use right now" in pg.inner_text("#profinfo")
    state(0, lt=200, gyro=(0, -1024 * 40, 0)); time.sleep(0.3)
    assert "Gyro active" in pg.inner_text("#glive"), pg.inner_text("#glive")
    pg.screenshot(path=os.path.join(os.path.dirname(__file__), "editor.png"), full_page=True)
    # przełączenie na profil domyślny i z powrotem zachowuje dane
    pg.select_option("#profsel", base); time.sleep(0.2)
    ch = pg.inner_text("#changes"); print("   changes(default):", repr(ch)); assert "Cross" in ch and "Gyro" not in ch, ch
    pg.select_option("#profsel", key); time.sleep(0.2)
    assert "Gyro: right stick" in pg.inner_text("#changes")
    # wyłączenie pada
    sent.clear(); pg.click("#power"); time.sleep(0.3)
    assert sum(1 for x in sent if x[0] == T_CMD) == 3
    # usunięcie profilu gry
    pg.click("#profdel"); time.sleep(0.5)
    assert not store.has(key) and pg.input_value("#profsel") == base
    b.close()
assert not errors, errors
print("OK   edytor: nowy profil gry, strefy, spust, gyro, zapis, stan na żywo, power, usuwanie - bez błędów JS")
rx_web.stop()
