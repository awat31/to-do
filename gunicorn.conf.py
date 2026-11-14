"""Gunicorn configuration for production."""
import multiprocessing
import os

bind = os.environ.get("BIND", "0.0.0.0:8000")
workers = int(os.environ.get("WEB_CONCURRENCY", min(4, max(2, multiprocessing.cpu_count() - 1))))
threads = int(os.environ.get("THREADS", 2))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 60))
accesslog = "-"
errorlog = "-"
access_log_format = '%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s %(M)sms'
forwarded_allow_ips = "*"
max_requests = 1000  # recycle workers periodically
max_requests_jitter = 100
