# Worker strategy:
#  - Without REDIS_URL (single server): 1 worker, 8 threads
#    This prevents split-brain on in-memory rate-limit counters and
#    Flask-Caching SimpleCache (which are per-process, not shared).
#  - With REDIS_URL set: switch to 2 workers × 4 threads for better
#    CPU utilisation; rate-limit and cache are shared via Redis.
#
# --max-requests 800: recycles each worker after 800 requests to prevent
#   slow memory leaks from building up over days.
# --max-requests-jitter 80: randomises the recycle window so all workers
#   don't restart simultaneously under load.
# --timeout 30: Render has a 30 s request timeout; Gunicorn must agree so
#   timed-out AI/payment requests fail fast rather than holding a thread.
# --keep-alive 5: reuses connections from Render's load balancer (HTTP/1.1).
web: gunicorn "smart_mart.app:create_app('production')" --bind 0.0.0.0:$PORT --workers 1 --threads 8 --worker-class gthread --timeout 30 --keep-alive 5 --max-requests 800 --max-requests-jitter 80 --access-logfile - --error-logfile - --log-level info
