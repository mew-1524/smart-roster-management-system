#!/usr/bin/env bash
# Build/release step for hosts such as Render / Railway / PythonAnywhere
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --no-input
# python manage.py migrate
# Optional: load the fictional demo dataset and demo admin (needs DEMO_ADMIN_PASSWORD when DEBUG is off)
# python manage.py seed_data --create-admin
