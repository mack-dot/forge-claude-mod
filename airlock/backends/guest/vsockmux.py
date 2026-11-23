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
# Each stream has a bounded send queue and its own writer thread, so a slow reader never blocks
# the others' frames for long and memory per stream is bounded (QUEUE frames of at most 64 KiB).
# Shared by guest/boxinit.py (guest) and vfkit_vm.py (host). Label: NATIVE CHECK (not proved).
import queue
import socket
import struct
import threading

OPEN, DATA, EOF, RST = 1, 2, 3, 4
HDR = struct.Struct(">BII")
QUEUE = 256
MUX_PORT = 1024


def _recv_exact(s, n):
    b = b""
    while len(b) < n:
        d = s.recv(n - len(b))
        if not d:
            raise ConnectionError("mux connection closed")
        b += d
    return b


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
        threading.Thread(target=self._reader, daemon=True).start()

    def send(self, typ, sid, data=b""):
        with self.wlock:
            self.link.sendall(HDR.pack(typ, sid, len(data)) + data)

    def _reader(self):
        try:
            while True:
                typ, sid, n = HDR.unpack(_recv_exact(self.link, HDR.size))
                data = _recv_exact(self.link, n) if n else b""
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
        except (OSError, ConnectionError, struct.error):
            pass
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
        """Guest side: a new stream for local socket sock to the host's port."""
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
