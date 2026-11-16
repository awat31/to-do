"""Gunicorn configuration for production."""
import multiprocessing
import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)

bind = os.environ.get("BIND", "0.0.0.0:8000")
workers = int(os.environ.get("WEB_CONCURRENCY", min(4, max(2, multiprocessing.cpu_count() - 1))))
threads = int(os.environ.get("THREADS", "2"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
certfile = os.environ.get("TODO_SSL_CERTFILE") or None
keyfile = os.environ.get("TODO_SSL_KEYFILE") or None
if bool(certfile) != bool(keyfile):
    raise RuntimeError("Set both TODO_SSL_CERTFILE and TODO_SSL_KEYFILE to enable HTTPS.")
accesslog = "-"
errorlog = "-"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s %(M)sms'
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1,::1")
max_requests = 1000  # recycle workers periodically
max_requests_jitter = 100
