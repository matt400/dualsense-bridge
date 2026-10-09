"""Receiver: okno ze stanem baterii (tkinter), ikona w zasobniku i okno remapowania przycisków."""
import base64
import ctypes
import queue
import time

import rx_state as S
import rx_winapi as winapi
from rx_buttons import profile_size
from rx_config import C
from rx_tray import ICON_OK, Tray, icon_png, render_icon


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
        parts.append(f"ping {rtt:.1f} ms" if rtt is not None else "ping \u2014")
        parts.append(f"pad {pad_hz} Hz")
    if S.link_loss is not None:
        parts.append(f"loss {S.link_loss:.1f}%")
    parts.append(f"{S.pps[i]:.0f} pkt/s")
    return "   \u00b7   ".join(parts)


def link_color(i, now):
    """Zielony = dobrze, pomarańczowy = zauważalne (ping > 5 ms / strata > 1 %), czerwony = źle."""
    inf = S.info[i]
    rtt = inf[0] if inf and now - inf[3] < INFO_MAX_AGE else None
    loss = S.link_loss or 0.0
    if (rtt or 0) > 20 or loss > 5:
        return "#e53935"
    if (rtt or 0) > 5 or loss > 1:
        return "#fb8c00"
    return "#66bb6a"


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

    BG, FG, DIM = "#1e1e1e", "#e6e6e6", "#9aa0a6"
    root = tk.Tk()
    root.withdraw()                                          # start bez okna - pokazuje je użytkownik
    root.title("DualSense bridge")
    root.configure(bg=BG)
    root.resizable(False, False)
    sc = max(1.0, root.winfo_fpixels("1i") / 96)         # skala DPI: pasek rośnie razem z tekstem
    W, H = round(280 * sc), round(20 * sc)
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

    sender_lbl = tk.Label(root, text="", font=("Segoe UI", 10, "bold"), bg=BG, anchor="w", padx=14, pady=(8))
    sender_lbl.grid(row=0, column=0, sticky="ew")

    rows = []
    for i in range(NUM_PADS):
        fr = tk.Frame(root, bg=BG, padx=14, pady=8)
        fr.grid(row=i + 1, column=0, sticky="ew")
        fr.columnconfigure(1, weight=1)
        tk.Label(fr, text=f"Pad {i + 1}", font=("Segoe UI", 11, "bold"), fg=FG, bg=BG,
                 anchor="w").grid(row=0, column=0, sticky="w")
        status = tk.Label(fr, text="", font=("Segoe UI", 9), fg=DIM, bg=BG, anchor="e")
        status.grid(row=0, column=1, sticky="e")
        bar = tk.Canvas(fr, width=W, height=H, bg="#333333", highlightthickness=0)
        bar.grid(row=1, column=0, columnspan=2, pady=(5, 0))
        fill = bar.create_rectangle(0, 0, 0, H, width=0, fill="#4caf50")
        txt = bar.create_text(W // 2, H // 2, text="", fill="white", font=("Segoe UI", 9, "bold"))
        info = tk.Label(fr, text="", font=("Segoe UI", 8), fg=DIM, bg=BG, anchor="w")
        info.grid(row=2, column=0, sticky="w")
        btns = tk.Frame(fr, bg=BG)
        btns.grid(row=2, column=1, rowspan=2, sticky="e")
        btn = dict(font=("Segoe UI", 8), fg=FG, bg="#2a2a2a", activebackground="#3a3a3a", activeforeground=FG,
                   relief="flat", bd=0, padx=8)
        tk.Button(btns, text="Remap", command=lambda i=i: open_editor(i), **btn).pack(side="left", padx=(0, 4))
        off = tk.Button(btns, text="Off", command=lambda i=i: power_off(i), **btn)
        off.pack(side="left")
        link = tk.Label(fr, text="", font=("Segoe UI", 8), fg=DIM, bg=BG, anchor="w")
        link.grid(row=3, column=0, sticky="w")
        rows.append((status, bar, fill, txt, info, link, off))
    tk.Button(root, text="Quit", command=root.destroy, font=("Segoe UI", 8), fg=DIM, bg="#2a2a2a",
              activebackground="#3a3a3a", activeforeground=FG, relief="flat", bd=0, padx=10
              ).grid(row=NUM_PADS + 1, column=0, sticky="e", padx=14, pady=(0, 8))

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
            sender_lbl.config(text=("\u25cf Sender online" + (f"  ({src})" if src else "")) if online
                              else "\u25cf Sender offline", fg="#66bb6a" if online else "#e53935")
        low_any, tips = False, []
        now = time.monotonic()
        for i, (status, bar, fill, txt, info, link, off) in enumerate(rows):
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
            if alive[i]:
                status.config(text="connected", fg="#66bb6a")
            elif want[i].is_set():
                status.config(text="no signal", fg="#fb8c00")
            else:
                status.config(text="not connected", fg=DIM)
            if not known:
                bar.coords(fill, 0, 0, 0, H)
                bar.itemconfig(txt, text="battery n/a")
            else:
                color = ("#42a5f5" if charging else "#e53935" if pct <= LOW_BATT
                         else "#fb8c00" if pct <= 30 else "#4caf50")
                bar.coords(fill, 0, 0, W * pct // 100, H)
                bar.itemconfig(fill, fill=color)
                bar.itemconfig(txt, text=f"{pct}%" + ("  charging" if charging else ""))
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
