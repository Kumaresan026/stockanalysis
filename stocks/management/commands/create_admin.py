"""
Django management command: create_admin

Creates a superuser from environment variables if one does not already exist.
Run this in .ebextensions container_commands so the admin account is re-created
automatically after every EB deployment (when SQLite is wiped).

Usage:
    python manage.py create_admin

Environment variables required:
    DJANGO_ADMIN_USERNAME  (default: admin)
    DJANGO_ADMIN_EMAIL     (default: admin@example.com)
    DJANGO_ADMIN_PASSWORD  (default: changeme123!)
    # Set these in EB Console → Configuration → Software → Environment Properties
"""

import os
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = "Create a superuser from environment variables (idempotent — skips if exists)."

    def handle(self, *args, **options):
        username = os.getenv('DJANGO_ADMIN_USERNAME', 'admin')
        email    = os.getenv('DJANGO_ADMIN_EMAIL',    'admin@example.com')
        password = os.getenv('DJANGO_ADMIN_PASSWORD', 'changeme123!')

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.WARNING(
                f"Admin user '{username}' already exists — skipping creation."
            ))
            return

        User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Superuser '{username}' created successfully."
        ))
