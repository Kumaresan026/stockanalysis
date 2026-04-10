#!/bin/bash

# container_commands run as root, so any files touched (like the SQLite DB during migrate 
# or log files) become owned by root. This postdeploy hook runs after extraction 
# and restores ownership back to the 'webapp' user so Django can read/write them.

chown webapp:webapp /var/app/current/db.sqlite3 || true
chmod 664 /var/app/current/db.sqlite3 || true

chown webapp:webapp /var/app/current/stock_platform.log || true
chmod 664 /var/app/current/stock_platform.log || true

# Also ensure the directory is writable by webapp for SQLite lock files
chown webapp:webapp /var/app/current || true
