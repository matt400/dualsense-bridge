"""Receiver: okno ze stanem pada (tkinter), ikona w zasobniku i okno remapowania przycisków."""
import base64
import ctypes
import queue
import time

import rx_state as S
import rx_winapi as winapi
from rx_buttons import profile_size
from rx_config import C
from rx_tray import ICON_OK, Tray, icon_png, render_icon

# Paleta wspólna z edytorem remapowania (rx_remap_ui.html), żeby oba okna wyglądały jak jedna aplikacja.
BG, CARD, BORDER, TRACK = "#14161c", "#1a1d25", "#272b36", "#2a2e3a"
FG, DIM, MUTED = "#e8eaf0", "#8b91a1", "#555b6b"
OK, WARN, BAD, CHARGE, ACCENT = "#4cd08a", "#ffb300", "#ff6b6b", "#5b9bff", "#ffb300"
FONT = "Segoe UI"


def open_editor(slot):
    """Otwiera edytor remapowania (SVG) w przeglądarce; bez serwera pokazuje komunikat."""
    import rx_web
    if not rx_web.open_ui(slot):
        from tkinter import messagebox
        messagebox.showwarning("Remap", "Remap editor is unavailable (disabled or no free port).\n"
                                        "Check remap_ui / remap_ui_port in the config.")


def ask(title, text):
    from tkinter import messagebox
    return messagebox.askokcancel(title, text)


def power_off(slot):
    import rx_net
    from proto import CMD_POWER_OFF
    if S.alive[slot] and ask("Power off", f"Turn off pad {slot + 1}?"):
        rx_net.send_cmd(slot, CMD_POWER_OFF)


INFO_MAX_AGE = 3.0


def link_text(i, now):
    """Jakość łącza dla wiersza pada: ping i Hz z nadajnika, utrata i pakiety/s liczone tutaj."""
    if not S.alive[i]:
        return ""
    parts = []
    inf = S.info[i]
    if inf and now - inf[3] < INFO_MAX_AGE:
        rtt, pad_hz, motion_hz, _ = inf
        parts.append(f"ping {rtt:.1f} ms" if rtt is not None else "ping —")
        parts.append(f"pad {pad_hz} Hz")
    if S.link_loss is not None:
        parts.append(f"loss {S.link_loss:.1f}%")
    parts.append(f"{S.pps[i]:.0f} pkt/s")
    return "  ·  ".join(parts)


def link_color(i, now):
    """Zielony = dobrze, bursztynowy = zauważalne (ping > 5 ms / strata > 1 %), czerwony = źle."""
    inf = S.info[i]
    rtt = inf[0] if inf and now - inf[3] < INFO_MAX_AGE else None
    loss = S.link_loss or 0.0
    if (rtt or 0) > 20 or loss > 5:
        return BAD
    if (rtt or 0) > 5 or loss > 1:
        return WARN
    return OK


def battery_color(pct, charging, low_batt):
    return CHARGE if charging else BAD if pct <= low_batt else WARN if pct <= 30 else OK


def round_rect(canvas, x0, y0, x1, y1, r, **kw):
    """Prostokąt z zaokrąglonymi rogami (wielokąt wygładzany); r jest przycinane do połowy krótszego boku."""
    return canvas.create_polygon(*rr_points(x0, y0, x1, y1, r), smooth=True, **kw)


def rr_points(x0, y0, x1, y1, r):
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    return [x0 + r, y0, x0 + r, y0, x1 - r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y0 + r,
            x1, y1 - r, x1, y1 - r, x1, y1, x1 - r, y1, x1 - r, y1, x0 + r, y1, x0 + r, y1,
            x0, y1, x0, y1 - r, x0, y1 - r, x0, y0 + r, x0, y0 + r, x0, y0]


def run_gui():
    # lokalne aliasy: listy stanu powstają dopiero w S.init(), więc nie można ich importować wcześniej
    want, alive, plugged, batt_info, mouse_mode = S.want, S.alive, S.plugged, S.batt_info, S.mouse_mode
    stop, cfg, NUM_PADS, LOW_BATT = S.stop, C.cfg, C.NUM_PADS, C.LOW_BATT
    if cfg["gui_wait_pad"]:   # opcja: nie twórz okna ani ikony, dopóki żaden pad się nie połączy
        while not stop.is_set() and not any(w.is_set() for w in want):
            stop.wait(0.25)
        if stop.is_set():
            return
    import tkinter as tk

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # ostry tekst na ekranach z skalowaniem
    except Exception:
        pass

    root = tk.Tk()
    root.withdraw()                                          # start bez okna - pokazuje je użytkownik
    root.title("DualSense bridge")
    root.configure(bg=BG)
    root.resizable(False, False)
    sc = max(1.0, root.winfo_fpixels("1i") / 96)         # skala DPI: pasek rośnie razem z tekstem
    W, H = round(280 * sc), round(10 * sc)
    pad_x = round(14 * sc)
    topmost = bool(cfg["gui_topmost"])
    root.attributes("-topmost", topmost)
    try:
        root._icon_img = tk.PhotoImage(data=base64.b64encode(icon_png(render_icon(ICON_OK))).decode())
        root.iconphoto(True, root._icon_img)
    except Exception:
        pass

    # --- zasobnik: zdarzenia z jego wątku trafiają do kolejki, którą czyta wątek Tk ---
    tray_q = queue.Queue()
    tray = Tray(tray_q.put) if cfg["tray"] else None
    if tray and not tray.start():
        print("Tray unavailable - continuing without tray icon.")
        tray = None

    if tray:   # okno narzędziowe: bez przycisku na pasku zadań i w Alt+Tab (dostęp tylko przez ikonę w zasobniku)
        try:
            root.attributes("-toolwindow", True)
        except tk.TclError:
            pass

    def make_button(parent, text, command, primary=False):
        """Płaski przycisk z podświetleniem po najechaniu; wyłączony przycisk nie reaguje na hover."""
        base, hover = (ACCENT, "#ffc233") if primary else (TRACK, "#353a4a")
        fg = BG if primary else FG
        b = tk.Button(parent, text=text, command=command, font=(FONT, 9, "bold" if primary else "normal"), fg=fg,
                      bg=base, activebackground=hover, activeforeground=fg, disabledforeground=MUTED, relief="flat",
                      bd=0, padx=round(12 * sc), pady=round(4 * sc), cursor="hand2", highlightthickness=0)
        b.bind("<Enter>", lambda _e: str(b.cget("state")) != "disabled" and b.config(bg=hover))
        b.bind("<Leave>", lambda _e: b.config(bg=base))
        return b

    # --- nagłówek ---
    header = tk.Frame(root, bg=BG)
    header.grid(row=0, column=0, sticky="ew", padx=pad_x, pady=(round(12 * sc), round(8 * sc)))
    header.columnconfigure(1, weight=1)
    tk.Label(header, text="DualSense bridge", font=(FONT, 13, "bold"), fg=FG, bg=BG, anchor="w").grid(row=0, column=0)
    sender_lbl = tk.Label(header, text="", font=(FONT, 9), bg=BG, anchor="e")
    sender_lbl.grid(row=0, column=1, sticky="e")

    # --- karty padów ---
    rows = []
    for i in range(NUM_PADS):
        card = tk.Frame(root, bg=CARD, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER)
        card.grid(row=i + 1, column=0, sticky="ew", padx=pad_x, pady=(0, round(10 * sc)))
        inner = tk.Frame(card, bg=CARD, padx=round(14 * sc), pady=round(12 * sc))
        inner.pack(fill="both", expand=True)
        inner.columnconfigure(0, weight=1)
        tk.Label(inner, text=f"Pad {i + 1}", font=(FONT, 11, "bold"), fg=FG, bg=CARD, anchor="w"
                 ).grid(row=0, column=0, sticky="w")
        status = tk.Label(inner, text="", font=(FONT, 9, "bold"), fg=DIM, bg=CARD, anchor="e")
        status.grid(row=0, column=1, sticky="e")

        bar = tk.Canvas(inner, width=W, height=H, bg=CARD, highlightthickness=0)
        bar.grid(row=1, column=0, sticky="w", pady=(round(8 * sc), round(2 * sc)))
        round_rect(bar, 0, 0, W, H, H / 2, fill=TRACK, outline="")
        fill = round_rect(bar, 0, 0, W, H, H / 2, fill=OK, outline="", state="hidden")
        pct_lbl = tk.Label(inner, text="", font=(FONT, 10, "bold"), fg=FG, bg=CARD, anchor="e")
        pct_lbl.grid(row=1, column=1, sticky="e", pady=(round(8 * sc), round(2 * sc)))

        info = tk.Label(inner, text="", font=(FONT, 8), fg=DIM, bg=CARD, anchor="w")
        info.grid(row=2, column=0, columnspan=2, sticky="w", pady=(round(6 * sc), 0))
        link = tk.Label(inner, text="", font=(FONT, 8), fg=DIM, bg=CARD, anchor="w")
        link.grid(row=3, column=0, sticky="w")
        btns = tk.Frame(inner, bg=CARD)
        btns.grid(row=3, column=1, sticky="e", pady=(round(6 * sc), 0))
        make_button(btns, "Remap", lambda i=i: open_editor(i), primary=True).pack(side="left", padx=(0, 6))
        off = make_button(btns, "Off", lambda i=i: power_off(i))
        off.pack(side="left")
        rows.append((status, bar, fill, pct_lbl, info, link, off, card))

    # --- stopka ---
    footer = tk.Frame(root, bg=BG)
    footer.grid(row=NUM_PADS + 1, column=0, sticky="ew", padx=pad_x, pady=(0, round(12 * sc)))
    footer.columnconfigure(0, weight=1)
    make_button(footer, "Quit", root.destroy).grid(row=0, column=1, sticky="e")

    last_row = [None] * NUM_PADS
    warned = [False] * NUM_PADS
    ui = {"title": None, "tip": None, "low": False, "online": None}

    def bring_to_front():
        """Przywróć okno z paska zadań i wysuń na wierzch (także gdy Windows nie oddaje fokusu)."""
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)                    # chwilowy topmost przebija się przed inne okna
        root.after(200, lambda: root.attributes("-topmost", topmost))
        try:
            root.focus_force()
        except tk.TclError:
            pass

    def hide(_e=None):
        """Krzyżyk i minimalizacja chowają okno do zasobnika; bez zasobnika - zwykła minimalizacja."""
        if tray:
            root.withdraw()
        else:
            root.iconify()

    def on_unmap(e):
        if tray and e.widget is root and root.state() == "iconic":
            root.after_idle(hide)

    root.protocol("WM_DELETE_WINDOW", hide)
    root.bind("<Unmap>", on_unmap)

    def poll_tray():
        try:
            while True:
                ev = tray_q.get_nowait()
                if ev == "show":
                    bring_to_front()
                elif ev == "quit":
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(150, poll_tray)

    def refresh():
        """Odświeżenie co 400 ms; błąd w jednym przebiegu nie może zatrzymać odświeżania na zawsze."""
        try:
            update()
        except Exception as e:
            print(f"GUI refresh error: {e!r}")
        root.after(400, refresh)   # tick umożliwia też Ctrl+C w konsoli

    def update():
        online = S.sender_online()
        if online != ui["online"]:
            ui["online"] = online
            p = S.peer
            src = p[0] if p else ""
            sender_lbl.config(text=("●  Sender online" + (f"  ({src})" if src else "")) if online
                              else "●  Sender offline", fg=OK if online else BAD)
        low_any, tips = False, []
        now = time.monotonic()
        for i, (status, bar, fill, pct_lbl, info, link, off, card) in enumerate(rows):
            lt = link_text(i, now)
            if link.cget("text") != lt:          # zmienia się co sekundę - osobno od reszty wiersza
                link.config(text=lt, fg=link_color(i, now))
            pct, flags = batt_info[i]
            charging = bool(flags & 1)
            known = pct <= 100
            low = alive[i] and known and not charging and pct <= LOW_BATT
            low_any = low_any or low
            if want[i].is_set() or alive[i]:
                tips.append(f"Pad {i + 1}: " + (f"{pct}%" + (" (charging)" if charging else "") if known else "n/a"))
            # powiadomienie z zasobnika raz na rozładowanie
            if low and not warned[i]:
                warned[i] = True
                if tray and cfg["low_batt_notify"]:
                    tray.balloon("Low battery", f"Pad {i + 1}: {pct}%")
            elif warned[i] and (charging or (known and pct > LOW_BATT + 5)):
                warned[i] = False
            key = (alive[i], want[i].is_set(), pct, flags, plugged[i], mouse_mode[i], profile_size(S.remap_maps[i]),
                   S.active[i])
            if key == last_row[i]:
                continue        # nic się nie zmieniło - nie dotykaj widgetów
            last_row[i] = key
            off.config(state="normal" if alive[i] else "disabled")
            card.config(highlightbackground=BAD if low else BORDER)    # czerwona ramka przy rozładowanym padzie
            if alive[i]:
                status.config(text="●  connected", fg=OK)
            elif want[i].is_set():
                status.config(text="●  no signal", fg=WARN)
            else:
                status.config(text="○  not connected", fg=DIM)
            if not known:
                bar.itemconfig(fill, state="hidden")
                pct_lbl.config(text="n/a", fg=DIM)
            else:
                bar.coords(fill, *rr_points(0, 0, max(W * pct // 100, H), H, H / 2))   # min. szerokość = kropka
                bar.itemconfig(fill, fill=battery_color(pct, charging, LOW_BATT), state="normal" if pct else "hidden")
                pct_lbl.config(text=f"{pct}%" + (" ⚡" if charging else ""), fg=BAD if low else FG)
            n = profile_size(S.remap_maps[i])
            info.config(text=f"DS4: {'on' if plugged[i] else 'off'}"
                             f"   |   touchpad: {'mouse' if mouse_mode[i] else 'DS4'}"
                             f"   |   profile: {S.profile_label(S.active[i])}" + (f" ({n})" if n else ""))

        title = "DualSense bridge - LOW BATTERY" if low_any else "DualSense bridge"
        if title != ui["title"]:
            ui["title"] = title
            root.title(title)
        tip = ("DualSense bridge\nSender: " + ("online" if online else "offline") + "\n"
               + ("\n".join(tips) if tips else "No pads connected"))
        if tray and (tip != ui["tip"] or low_any != ui["low"]):
            ui["tip"], ui["low"] = tip, low_any
            tray.set_status(tip, low_any)

    poll_tray()
    refresh()
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    left, top, right, bottom = winapi.work_area() or (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    m = round(16 * sc)
    root.geometry(f"+{max(left, right - w - m)}+{max(top, bottom - h - m)}")   # prawy dolny róg, nad paskiem zadań
    if tray is None or cfg["gui_start_visible"]:             # bez ikony okno jest jedynym sposobem obsługi
        bring_to_front()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        if tray:
            tray.stop()
