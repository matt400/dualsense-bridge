"""Nadajnik: tryb niskich opóźnień (sender_rt) - PM QoS, najszybsze rdzenie, SCHED_FIFO; bez uprawnień nic nie psuje."""
import os, struct, sys, tempfile, types
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_rt as R

TMP = tempfile.mkdtemp()
ok = lambda m: print("OK  ", m)
assert C.LOW_LATENCY is True and C.CPU_LATENCY_US == 50 and C.RT_PRIORITY == 10, "domyślnie włączone"

# ---------- PM QoS ----------
qos = os.path.join(TMP, "cpu_dma_latency")
assert R.hold_cpu_latency(50, qos) == "CPU wake-up latency <= 50 us"
assert struct.unpack("i", open(qos, "rb").read())[0] == 50 and not R._qos.closed, "wartość zapisana, plik trzymany otwarty"
assert R.hold_cpu_latency(50, os.path.join(TMP, "nie-ma", "x")) is None, "brak pliku / uprawnień -> None"
ok("PM QoS: limit wybudzenia zapisany i trzymany do końca procesu")

# ---------- najszybsze rdzenie ----------
root = os.path.join(TMP, "sys")
for cpu, cap in enumerate((396, 396, 396, 396, 1024, 1024, 1024, 1024)):     # Snapdragon 845: 4 LITTLE + 4 big
    d = os.path.join(root, f"devices/system/cpu/cpu{cpu}")
    os.makedirs(d)
    open(os.path.join(d, "cpu_capacity"), "w").write(f"{cap}\n")
assert R.fast_cpus(root) == {4, 5, 6, 7}
same = os.path.join(TMP, "same")
for cpu in range(2):
    os.makedirs(os.path.join(same, f"devices/system/cpu/cpu{cpu}"))
    open(os.path.join(same, f"devices/system/cpu/cpu{cpu}/cpu_capacity"), "w").write("1024")
assert R.fast_cpus(same) == set() and R.fast_cpus(os.path.join(TMP, "nic")) == set(), "równe rdzenie / brak danych: bez zmian"
calls = []
R.os = types.SimpleNamespace(**{k: getattr(os, k) for k in dir(os) if not k.startswith("__")})
R.os.sched_getaffinity = lambda pid: set(range(8))
R.os.sched_setaffinity = lambda pid, cpus: calls.append(("affinity", cpus))
assert R.pin_fast_cpus(root) == "fast cores 4,5,6,7" and calls == [("affinity", {4, 5, 6, 7})]
ok("przypięcie do najszybszych rdzeni (cpu_capacity), bez zmian przy równych rdzeniach")

# ---------- SCHED_FIFO ----------
R.os.sched_setscheduler = lambda pid, pol, prm: calls.append(("fifo", pol, prm.sched_priority))
assert R.realtime(0) is None and R.realtime(10) == "SCHED_FIFO 10"
assert calls[-1] == ("fifo", os.SCHED_FIFO | os.SCHED_RESET_ON_FORK, 10), "RESET_ON_FORK: bluetoothctl bez priorytetu RT"
def denied(*a):
    raise PermissionError(1, "Operation not permitted")
R.os.sched_setscheduler = R.os.sched_setaffinity = denied
R.os.nice = denied
assert R.realtime(10) is None and R.pin_fast_cpus(root) is None
R.hold_cpu_latency = lambda us: None
R.setup(True, 50, 10)                                    # bez uprawnień: tylko logi, bez wyjątku
ok("SCHED_FIFO z RESET_ON_FORK; bez doas wszystko pomija bez błędu")
# ---------- musl (postmarketOS): funkcja libc zwraca ENOSYS -> syscall wprost ----------
import errno
R._sched_syscall(os.SCHED_OTHER, 0)                      # prawdziwy syscall: numer pasuje do tej architektury
def nosys(*a):
    raise OSError(errno.ENOSYS, "Function not implemented")
R.os.sched_setscheduler, sys_calls = nosys, []
R._sched_syscall, real_sys = (lambda pol, prio: sys_calls.append((pol, prio))), R._sched_syscall
assert R.realtime(10) == "SCHED_FIFO 10" and sys_calls == [(os.SCHED_FIFO | os.SCHED_RESET_ON_FORK, 10)], sys_calls
def sys_denied(pol, prio):
    raise OSError(errno.EPERM, "Operation not permitted")
R._sched_syscall = sys_denied
assert R.realtime(10) is None, "musl bez uprawnień: log, bez wyjątku"
R._sched_syscall = real_sys
ok("musl: ENOSYS z libc -> sched_setscheduler przez syscall (SCHED_FIFO działa na postmarketOS)")
print("RT OK")
