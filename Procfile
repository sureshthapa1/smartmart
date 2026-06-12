# Single worker with 8 threads until Redis is configured (prevents cache/rate-limit split-brain).
# With REDIS_URL set, switch to: --workers 2 --threads 4
web: gunicorn "smart_mart.app:create_app('production')" --bind 0.0.0.0:$PORT --workers 1 --threads 8 --worker-class gthread --timeout 120 --keep-alive 5 --max-requests 1000 --max-requests-jitter 100 --access-logfile -
