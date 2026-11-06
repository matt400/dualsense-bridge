"""Sender: low-latency mode (sender_rt) - PM QoS, the fastest cores, SCHED_FIFO; without privileges it breaks nothing."""
import os, struct, sys, tempfile, types
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_rt as R

TMP = tempfile.mkdtemp()
from chk import ok
assert C.LOW_LATENCY is True and C.CPU_LATENCY_US == 50 and C.RT_PRIORITY == 10, "on by default"

# ---------- PM QoS ----------
qos = os.path.join(TMP, "cpu_dma_latency")
assert R.hold_cpu_latency(50, qos) == "CPU wake-up latency <= 50 us"
assert struct.unpack("i", open(qos, "rb").read())[0] == 50 and not R._qos.closed, "value written, the file kept open"
assert R.hold_cpu_latency(50, os.path.join(TMP, "nie-ma", "x")) is None, "no file / no permission -> None"
ok("PM QoS: the wakeup limit written and held until the process ends")

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
assert R.fast_cpus(same) == set() and R.fast_cpus(os.path.join(TMP, "nic")) == set(), "equal cores / no data: no change"
calls = []
R.os = types.SimpleNamespace(**{k: getattr(os, k) for k in dir(os) if not k.startswith("__")})
R.os.sched_getaffinity = lambda pid: set(range(8))
R.os.sched_setaffinity = lambda pid, cpus: calls.append(("affinity", cpus))
assert R.pin_fast_cpus(root) == "fast cores 4,5,6,7" and calls == [("affinity", {4, 5, 6, 7})]
ok("pinning to the fastest cores (cpu_capacity), no change with equal cores")
reads = []
real_read = R._read_fast_cpus
R._read_fast_cpus = lambda r: reads.append(r) or real_read(r)
R._fast_cached = None
first = R.fast_cpus()                               # the real /sys: read once...
first.add(99)                                       # ...and the caller cannot spoil the remembered set
assert R.fast_cpus() == first - {99} and reads == ["/sys"], reads
assert R.fast_cpus(root) == {4, 5, 6, 7} and reads == ["/sys", root], "another root is always read"
R._read_fast_cpus = real_read
ok("fast_cpus(): the real /sys is read once (apply() on the loop does no sysfs reads)")

# ---------- SCHED_FIFO ----------
R.os.sched_setscheduler = lambda pid, pol, prm: calls.append(("fifo", pol, prm.sched_priority))
assert R.realtime(0) is None and R.realtime(10) == "SCHED_FIFO 10"
assert calls[-1] == ("fifo", os.SCHED_FIFO | os.SCHED_RESET_ON_FORK, 10), "RESET_ON_FORK: bluetoothctl without RT priority"
def denied(*a):
    raise PermissionError(1, "Operation not permitted")
R.os.sched_setscheduler = R.os.sched_setaffinity = denied
R.os.nice = denied
assert R.realtime(10) is None and R.pin_fast_cpus(root) is None
R.hold_cpu_latency = lambda us: None
R.setup(True, 50, 10)                                    # no privileges: logs only, no exception
import sys as _sys
_old_interval = _sys.getswitchinterval()
R.pin_fast_cpus = lambda *a: None
R.setup(False, 50, 0)
assert _sys.getswitchinterval() == _old_interval, "without low_latency the GIL interval does not change"
R.setup(True, 50, 0)
assert abs(_sys.getswitchinterval() - R.SWITCH_INTERVAL_S) < 1e-9, "low_latency shortens the GIL switch interval"
_sys.setswitchinterval(_old_interval)
ok("SCHED_FIFO with RESET_ON_FORK; without doas everything is skipped without an error")
# ---------- musl (postmarketOS): funkcja libc zwraca ENOSYS -> syscall wprost ----------
import errno
R._sched_syscall(os.SCHED_OTHER, 0)                      # a real syscall: the number fits this architecture
def nosys(*a):
    raise OSError(errno.ENOSYS, "Function not implemented")
R.os.sched_setscheduler, sys_calls = nosys, []
R._sched_syscall, real_sys = (lambda pol, prio: sys_calls.append((pol, prio))), R._sched_syscall
assert R.realtime(10) == "SCHED_FIFO 10" and sys_calls == [(os.SCHED_FIFO | os.SCHED_RESET_ON_FORK, 10)], sys_calls
def sys_denied(pol, prio):
    raise OSError(errno.EPERM, "Operation not permitted")
R._sched_syscall = sys_denied
assert R.realtime(10) is None, "musl without privileges: a log, no exception"
R._sched_syscall = real_sys
ok("musl: ENOSYS from libc -> sched_setscheduler through syscall (SCHED_FIFO works on postmarketOS)")
print("RT OK")
