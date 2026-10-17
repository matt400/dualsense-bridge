"""Sender: tryb niskich opóźnień telefonu (wymaga doas / roota; bez uprawnień - log i dalej bez zmian).

Skoki pingu i opóźnienia wejścia o kilka ms na telefonie nie biorą się z sieci (Ethernet: < 1 ms), tylko z
oszczędzania energii procesora:
  uśpienie klastra   bez ruchu na padzie / w sieci rdzenie i cały klaster zasypiają głęboko; wybudzenie trwa
                     milisekundy (Snapdragon 845: wyjście z "cluster sleep" ~6.5 ms) - tyle czeka pong albo raport
                     pada. PM QoS (/dev/cpu_dma_latency) zabrania stanów z dłuższym wybudzeniem niż cpu_latency_us,
                     dopóki plik jest otwarty (jądro zdejmuje blokadę samo, gdy proces się kończy - także po awarii).
  małe rdzenie       Python na rdzeniu "LITTLE" przy niskim zegarze przetwarza zdarzenie kilka razy dłużej;
                     proces jest przypinany do najszybszych rdzeni (największe cpu_capacity w sysfs).
  planista           SCHED_FIFO (rt_priority): wątek nadajnika nie czeka w kolejce za innymi procesami. Z flagą
                     RESET_ON_FORK - uruchamiane programy (bluetoothctl) dostają zwykły priorytet.
"""
import errno
import glob
import os
import struct

_qos = None        # otwarty /dev/cpu_dma_latency - trzymany do końca procesu


def hold_cpu_latency(us, path="/dev/cpu_dma_latency"):
    """PM QoS: maksymalne opóźnienie wybudzenia procesora w µs. -> opis albo None (brak uprawnień / pliku)."""
    global _qos
    f = None
    try:
        f = open(path, "wb", buffering=0)  # noqa: SIM115 - trzymany otwarty do końca procesu
        f.write(struct.pack("i", us))
    except OSError as e:
        if f is not None:
            f.close()
        print(f"Low latency: CPU idle limit unavailable ({e.strerror}) - run with doas")
        return None
    if _qos is not None:
        _qos.close()
    _qos = f
    return f"CPU wake-up latency <= {us} us"


def fast_cpus(sys_root="/sys"):
    """Rdzenie o największej wydajności (cpu_capacity) albo pusty zbiór, gdy wszystkie są takie same / brak danych."""
    cap = {}
    for p in glob.glob(f"{sys_root}/devices/system/cpu/cpu[0-9]*/cpu_capacity"):
        try:
            with open(p) as f:
                cap[int(os.path.basename(os.path.dirname(p))[3:])] = int(f.read())
        except (OSError, ValueError):
            pass
    if len(set(cap.values())) < 2:
        return set()
    top = max(cap.values())
    return {cpu for cpu, c in cap.items() if c == top}


def pin_fast_cpus(sys_root="/sys"):
    cpus = fast_cpus(sys_root) & os.sched_getaffinity(0)
    if not cpus:
        return None
    try:
        os.sched_setaffinity(0, cpus)
    except OSError as e:
        print(f"Low latency: cannot pin to fast cores ({e.strerror})")
        return None
    return "fast cores " + ",".join(map(str, sorted(cpus)))


# numer wywołania systemowego sched_setscheduler (asm-generic: aarch64, riscv64; inne architektury osobno)
_SYS_SCHED_SETSCHEDULER = {"x86_64": 144, "i686": 156, "i386": 156, "armv7l": 156, "armv6l": 156, "armv8l": 156}


def _sched_syscall(policy, priority):
    """sched_setscheduler wprost przez syscall. musl (Alpine, postmarketOS) celowo nie implementuje tej funkcji libc
    (zawsze ENOSYS), więc os.sched_setscheduler nie działa tam nigdy, także jako root."""
    import ctypes
    import platform
    libc = ctypes.CDLL(None, use_errno=True)
    nr = _SYS_SCHED_SETSCHEDULER.get(platform.machine(), 119)
    param = ctypes.c_int(priority)                  # struct sched_param { int sched_priority; }
    if libc.syscall(ctypes.c_long(nr), 0, policy, ctypes.byref(param)) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))


def realtime(priority):
    if not priority:
        return None
    policy = os.SCHED_FIFO | getattr(os, "SCHED_RESET_ON_FORK", 0)
    try:
        try:
            os.sched_setscheduler(0, policy, os.sched_param(priority))
        except OSError as e:
            if e.errno != errno.ENOSYS:
                raise
            _sched_syscall(policy, priority)     # musl: funkcja libc nie istnieje, jądro obsługuje ją normalnie
    except (OSError, AttributeError) as e:
        hint = " - run with doas" if getattr(e, "errno", None) in (errno.EPERM, errno.EACCES) else ""
        print(f"Low latency: realtime scheduling unavailable ({e}){hint}")
        return None
    return f"SCHED_FIFO {priority}"


def setup(low_latency, cpu_latency_us, rt_priority):
    """Wszystko naraz na starcie nadajnika; każdy krok niezależny."""
    try:
        os.nice(-10)
    except OSError:
        pass
    done = [realtime(rt_priority)]
    if low_latency:
        done += [hold_cpu_latency(cpu_latency_us), pin_fast_cpus()]
    done = [d for d in done if d]
    if done:
        print("Low latency: " + ", ".join(done))
