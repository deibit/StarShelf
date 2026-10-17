import logging

import pytest

from starshelf.logging_config import QuietSyncPolling


def access_record(path, status=200, method="GET", level=logging.INFO):
    return logging.LogRecord("uvicorn.access", level, "", 0,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:12345", method, path, "1.1", status), None)


@pytest.mark.parametrize("path", ["/api/sync/status", "/api/sync/status?poll=1"])
def test_successful_status_polls_are_quiet(client, path):
    # Application startup installs the filter for the normal and uvicorn CLI launch modes.
    assert not logging.getLogger("uvicorn.access").filter(access_record(path))


@pytest.mark.parametrize("record", [
    access_record("/api/sync/status", status=500),
    access_record("/api/sync/status", status=403),
    access_record("/api/sync/status", level=logging.ERROR),
    access_record("/api/sync", status=202, method="POST"),
    access_record("/api/repositories"),
    access_record("/api/sync/status/other"),
    logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "Unexpected log format", (), None),
])
def test_other_requests_and_errors_remain_visible(record):
    assert QuietSyncPolling().filter(record)
