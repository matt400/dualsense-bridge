import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                              # noqa
import rx_web
C.REMAP_UI = True; C.REMAP_UI_PORT = 0
url = rx_web.start()
base = S.profile_key(0)
store.save(base + "@a.exe", {}); store.save(base + "@b.exe", {})
S.set_game("msedge.exe"); S.alive[0] = True
from playwright.sync_api import sync_playwright
errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(); pg = b.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(url); pg.wait_for_selector("#profsel option", state="attached"); time.sleep(0.3)
    assert S.active[0] == base
    pg.select_option("#profsel", base + "@a.exe"); time.sleep(0.3)
    assert S.active[0] == base + "@a.exe", S.active[0]
    assert "In use right now" in pg.inner_text("#profinfo")
    print("OK   wybór profilu w edytorze włącza go na padzie (podgląd = edytowany profil)")
    # zmiana i natychmiastowe przełączenie (przed upływem 150 ms) - zmiana ma trafić do profilu a.exe
    pg.click('.inp[data-name="left_stick"]')
    pg.eval_on_selector("#r_dz", "el => { el.value = 20; el.dispatchEvent(new Event('input')); }")
    pg.select_option("#profsel", base + "@b.exe"); time.sleep(0.4)
    assert store.get(base + "@a.exe")["tuning"].get("left", {}).get("dz") == 0.2, store.get(base + "@a.exe")
    assert store.get(base + "@b.exe")["tuning"] == {} and S.active[0] == base + "@b.exe"
    print("OK   szybkie przełączenie profilu nie gubi ostatniej zmiany ani nie przenosi jej do innego profilu")
    # to samo przy zmianie zakładki pada
    pg.click('.inp[data-name="left_stick"]')
    pg.eval_on_selector("#r_dz", "el => { el.value = 30; el.dispatchEvent(new Event('input')); }")
    pg.click('#tabs .tab[data-i="1"]'); time.sleep(0.4)
    assert store.get(base + "@b.exe")["tuning"]["left"]["dz"] == 0.3
    print("OK   przełączenie zakładki pada też zapisuje czekającą zmianę")
    b.close()
assert not errors, errors
rx_web.stop()
print("WEB2 OK")
