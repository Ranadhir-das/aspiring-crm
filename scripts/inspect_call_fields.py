import os, sys
sys.path.insert(0, r'E:\react\aspiring-crm')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()
from apps.calls.models import Call
print([f.name for f in Call._meta.get_fields()])

