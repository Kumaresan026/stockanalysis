#!/bin/bash
# 01-migrate.sh — Run Django migrations on every deploy
# This MUST run before the app starts. Without it, django_session,
# auth_user and other core tables don't exist → 500 on every request.

set -e

# Activate the EB-managed virtualenv
source /var/app/venv/*/bin/activate

cd /var/app/current

echo "[DEPLOY] Running Django migrations..."
python manage.py migrate --noinput
echo "[DEPLOY] Migrations complete."

echo "[DEPLOY] Collecting static files..."
python manage.py collectstatic --noinput --clear 2>/dev/null || true
echo "[DEPLOY] Static files collected."

# Create superuser if env vars are set (idempotent)
if [ -n "$DJANGO_ADMIN_USERNAME" ] && [ -n "$DJANGO_ADMIN_PASSWORD" ]; then
    python manage.py shell -c "
from django.contrib.auth.models import User
u, created = User.objects.get_or_create(username='$DJANGO_ADMIN_USERNAME')
if created or not u.has_usable_password():
    u.set_password('$DJANGO_ADMIN_PASSWORD')
    u.is_staff = True
    u.is_superuser = True
    u.email = '${DJANGO_ADMIN_EMAIL:-admin@example.com}'
    u.save()
    print('[DEPLOY] Admin user created/updated.')
else:
    print('[DEPLOY] Admin user already exists.')
" 2>/dev/null || true
fi

echo "[DEPLOY] 01-migrate.sh complete."
