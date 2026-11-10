"""log to logli: UDP 5514, RFC5424 + HMAC. lossy by design - logli down = lines gone, app unbothered."""
import datetime, hashlib, hmac, logging, os, socket

_LVL = {50: 2, 40: 3, 30: 4, 20: 6, 10: 7}  # python levelno -> syslog severity


class Handler(logging.Handler):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.secret = os.environ.get("LOGLI_SECRET", "")
        self.addr = (os.environ.get("LOGLI_HOST", "logli"), int(os.environ.get("LOGLI_PORT", "5514")))
        self.env = os.environ.get("APP_ENV", "production")
        self.v = os.environ.get("KAMAL_VERSION", "")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def emit(self, r):
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        lvl = _LVL.get(max(10, min(r.levelno, 50)) // 10 * 10, 6)
        body = (f'<{128 + lvl}>1 {ts} - {self.app} - - [l env="{self.env}" file="{os.path.relpath(r.pathname)}" '
                f'line="{r.lineno}" v="{self.v}" c=""] {r.getMessage()}')
        sig = hmac.new(self.secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
        try:
            self.sock.sendto(f"{body} sig={sig}".encode(), self.addr)
        except OSError:
            pass


def setup(app):
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(), Handler(app)])
