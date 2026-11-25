#!/usr/bin/env bash
# Build/release step for hosts such as Render / Railway / PythonAnywhere

#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

python manage.py migrate --noinput

python manage.py collectstatic --no-input

python manage.py seed_data --create-admin
