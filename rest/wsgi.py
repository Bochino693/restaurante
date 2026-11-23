"""
WSGI config for rest project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rest.settings')

application = get_wsgi_application()

# Na Vercel, aplica as migrações que faltarem antes de atender visitantes
# (ver core/auto_migracao.py). Fora da Vercel não faz nada; falha só vai
# para o log, nunca impede o site de subir.
from core.auto_migracao import migrar_pendentes  # noqa: E402

migrar_pendentes()
