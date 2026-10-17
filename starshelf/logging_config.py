import logging


class QuietSyncPolling(logging.Filter):
    """Skip successful status polls without hiding other requests or failures."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if record.levelno == logging.INFO and isinstance(args, tuple) and len(args) == 5:
            _, method, path, _, status = args
            if (method == "GET" and isinstance(path, str)
                    and path.split("?", 1)[0] == "/api/sync/status"
                    and isinstance(status, int) and 200 <= status < 300):
                return False
        return True


_polling_filter = QuietSyncPolling()


def configure_access_logging():
    # addFilter ignores the same instance on repeated app startup (e.g. tests).
    logging.getLogger("uvicorn.access").addFilter(_polling_filter)
