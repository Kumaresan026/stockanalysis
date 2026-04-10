web: gunicorn --bind 127.0.0.1:8000 --workers 1 --threads 15 stock_platform.wsgi:application
worker: python manage.py poll_sqs
