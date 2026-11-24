# vsockmux.py - all TCP connections of the VM over ONE long-lived vsock connection (guest and host side).
#
# Why: vfkit's per-connection vsock proxy lost connections under churn (measured on this Mac:
# 6-8 of 2000 sequential HTTP requests stalled until their timeout or lost the response, also with
# framing). With one connection for the VM's lifetime, nothing is set up or torn down per request.
# Frame: type (1 byte) | stream id (4) | length (4) | data.
#   OPEN  guest -> host, data = the guest port (2 bytes); the host connects ONLY if the port is in
#         its allowlist (port -> one fixed TCP target), else answers RST. This map is the VM's whole
#         network: there is no other route out of the guest.
#   DATA  either way; EOF = that direction ended (half-close); RST = drop the stream.
#   HELLO host -> guest, once, first frame: the host end is really attached (readiness, see open_tunnel).
# Each stream has a bounded send queue and its own writer thread, so a slow reader never blocks
# the others' frames for long and memory per stream is bounded (QUEUE frames of at most 64 KiB).
# Shared by guest/boxinit.py (guest) and vfkit_vm.py (host). Label: NATIVE CHECK (not proved).
import queue
import socket
import struct
import threading

OPEN, DATA, EOF, RST, HELLO = 1, 2, 3, 4, 5
HDR = struct.Struct(">BII")
QUEUE = 256
MUX_PORT = 1024
DEBUG = None  # set to a function(str) to trace frames (FORGE_VFKIT_DEBUG on the host, job "debug" in the guest)


def _dbg(msg):
    if DEBUG is not None:
        try:
            import time
            DEBUG(f"{time.time():.3f} mux {msg}")
        except Exception:  # noqa: BLE001
            pass


def _recv_exact(s, n):
    b = b""
    while len(b) < n:
        d = s.recv(n - len(b))
        if not d:
            raise ConnectionError("mux connection closed")
        b += d
    return b


def vsock_connector(cid, port):
    def connect(timeout):
        s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((cid, port))
        except OSError:
            s.close()
            raise
        return s
    return connect


def open_tunnel(connect, deadline_s=20.0, hello_s=3.0):
    """Guest side: a vsock connection whose HOST end is attached, proved by the host's HELLO frame.
    Why: a guest connect that races vfkit's start (vfkit exposes its vsock listener only after the
    VM runs) can succeed in the guest while vfkit drops the host side at once; the guest sees no
    error and its frames vanish (measured: 1 of 9 boots; the canary then timed out). Without HELLO
    within hello_s the connection is dropped and made again, until deadline_s; then OSError
    (the caller fails closed)."""
    import time
    end = time.monotonic() + deadline_s
    last = None
    while time.monotonic() < end:
        s = None
        try:
            s = connect(hello_s)
            s.settimeout(hello_s)
            typ, _sid, n = HDR.unpack(_recv_exact(s, HDR.size))
            if typ == HELLO and n == 0:
                s.settimeout(None)
                _dbg("guest: HELLO from the host: tunnel ready")
                return s
            last = f"unexpected first frame type {typ}"
        except (OSError, ConnectionError, struct.error) as e:
            last = repr(e)
        _dbg(f"guest: tunnel not ready ({last}); again")
        if s is not None:
            s.close()
        time.sleep(0.1)
    raise OSError(f"vsock tunnel: no HELLO from the host within {deadline_s:.0f} s ({last})")


class Mux:
    """One side of the tunnel over socket `link`. connect(port) (guest) opens a stream; the host
    side gets OPEN frames and calls on_open(sid, port) -> a connected socket or None."""

    def __init__(self, link, on_open=None):
        self.link, self.on_open = link, on_open
        self.wlock = threading.Lock()
        self.streams = {}  # sid -> Stream
        self.slock = threading.Lock()
        self.next_sid = 1
        self.alive = True
        if on_open is not None:  # host side: tell the guest that this end is attached
            self.send(HELLO, 0)
        threading.Thread(target=self._reader, daemon=True).start()

    def send(self, typ, sid, data=b""):
        _dbg(f"send type={typ} sid={sid} len={len(data)}")
        with self.wlock:
            self.link.sendall(HDR.pack(typ, sid, len(data)) + data)

    def _reader(self):
        try:
            while True:
                typ, sid, n = HDR.unpack(_recv_exact(self.link, HDR.size))
                data = _recv_exact(self.link, n) if n else b""
                _dbg(f"recv type={typ} sid={sid} len={n}")
                with self.slock:
                    st = self.streams.get(sid)
                if typ == OPEN and self.on_open is not None and st is None:
                    port = struct.unpack(">H", data)[0] if len(data) == 2 else -1
                    st = Stream(self, sid, None)  # registered now: DATA that follows waits in its inbox
                    with self.slock:
                        self.streams[sid] = st
                    threading.Thread(target=self._open_host, args=(st, port), daemon=True).start()
                elif st is not None:
                    st.inbox.put((typ, data))
        except (OSError, ConnectionError, struct.error) as e:
            _dbg(f"reader ended: {e!r}")
        self.alive = False
        with self.slock:
            for st in list(self.streams.values()):
                st.inbox.put((RST, b""))

    def _open_host(self, st, port):
        try:
            sock = self.on_open(st.sid, port)
        except OSError:
            sock = None
        if sock is None:
            self.drop(st.sid)
            try:
                self.send(RST, st.sid)
            except OSError:
                pass
            return
        st.sock = sock
        st.start()

    def connect(self, port, sock):
        """Guest side: a new stream for local socket sock to the host's port (closed at once if
        the tunnel is gone: a fast refusal, never a silent hang)."""
        if not self.alive:
            sock.close()
            return None
        with self.slock:
            sid = self.next_sid
            self.next_sid += 1
            st = Stream(self, sid, sock)
            self.streams[sid] = st
        self.send(OPEN, sid, struct.pack(">H", port))  # before any DATA of the stream
        st.start()
        return st

    def drop(self, sid):
        with self.slock:
            self.streams.pop(sid, None)


class Stream:
    def __init__(self, mux, sid, sock):
        self.mux, self.sid, self.sock = mux, sid, sock
        self.inbox = queue.Queue(QUEUE)
        self.done_in = threading.Event()
        self.done_out = threading.Event()

    def start(self):
        try:  # small request/response frames: no Nagle delay on the local TCP leg
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        threading.Thread(target=self._pump_out, daemon=True).start()
        threading.Thread(target=self._pump_in, daemon=True).start()

    def _pump_out(self):  # local socket -> frames
        try:
            while True:
                d = self.sock.recv(65536)
                if not d:
                    self.mux.send(EOF, self.sid)
                    break
                self.mux.send(DATA, self.sid, d)
        except OSError:
            try:
                self.mux.send(RST, self.sid)
            except OSError:
                pass
        self.done_out.set()
        self._maybe_close()

    def _pump_in(self):  # frames -> local socket
        try:
            while True:
                typ, data = self.inbox.get()
                if typ == DATA:
                    self.sock.sendall(data)
                elif typ == EOF:
                    self.sock.shutdown(socket.SHUT_WR)
                    break
                else:  # RST
                    self.sock.close()
                    self.done_out.set()
                    break
        except OSError:
            pass
        self.done_in.set()
        self._maybe_close()

    def _maybe_close(self):
        if self.done_in.is_set() and self.done_out.is_set():
            try:
                self.sock.close()
            except OSError:
                pass
            self.mux.drop(self.sid)
