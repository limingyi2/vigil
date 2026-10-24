"""Serialize settings read/modify/write cycles across HTTP request threads."""
import threading

LOCK = threading.RLock()


def dispatch(handler, path, body):
    # Hold the lock from the initial snapshot until disk and memory are updated.
    # Ordinary chat requests never wait on slow settings probes.
    if path.startswith(('/api/models/', '/api/persona', '/api/voice/')) or path in (
            '/api/run-mode', '/api/retrieval/settings'):
        with LOCK:
            return handler._dispatch_post(path, body)
    return handler._dispatch_post(path, body)
