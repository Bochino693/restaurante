from decimal import Decimal
from pathlib import Path
import os
import tempfile
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "troque-isso")
DEBUG = os.environ.get("DEBUG", "True") == "True"

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    ".onrender.com",
    ".vercel.app",
]

# Permite adicionar hosts extras via variável de ambiente, sem precisar redeploy do código
hosts_extra = os.environ.get("ALLOWED_HOSTS_EXTRA", "")
if hosts_extra:
    ALLOWED_HOSTS += [h.strip() for h in hosts_extra.split(",") if h.strip()]

# Necessário no Django 4+ para aceitar POST/CSRF vindos de domínios https externos
CSRF_TRUSTED_ORIGINS = [
    "https://*.onrender.com",
    "https://*.vercel.app",
]

csrf_extra = os.environ.get("CSRF_TRUSTED_ORIGINS_EXTRA", "")
if csrf_extra:
    CSRF_TRUSTED_ORIGINS += [c.strip() for c in csrf_extra.split(",") if c.strip()]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "pedidos"
LOGOUT_REDIRECT_URL = "login"

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'cloudinary_storage',
    'cloudinary',

    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'rest.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.global_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'rest.wsgi.application'

if os.environ.get("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.config(
            default=os.environ.get("DATABASE_URL"),
            # Na Vercel cada função quente reaproveita a conexão (menos
            # latência por requisição); o health check descarta a conexão
            # que o pooler do Supabase fechou enquanto a função dormia.
            conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "60")),
            conn_health_checks=True,
            ssl_require=True,   # Supabase exige SSL — estava False
        )
    }
    # Pooler do Supabase em modo transação (porta 6543) não suporta cursores
    # do lado do servidor.
    if ":6543" in os.environ.get("DATABASE_URL", ""):
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# Cloudinary: só LEGADO. Serve para montar o endereço das fotos antigas e
# para o `espelhar_midia` copiá-las para o banco quando a conta voltar.
# Todas as variáveis são opcionais: sem elas o site sobe e funciona igual.
CLOUDINARY_STORAGE = {
    "CLOUD_NAME": os.environ.get("CLOUDINARY_CLOUD_NAME"),
    "API_KEY": os.environ.get("CLOUDINARY_API_KEY"),
    "API_SECRET": os.environ.get("CLOUDINARY_API_SECRET"),
}

# Fotos e arquivos enviados ficam no PRÓPRIO BANCO (core.midia.MidiaNoBanco),
# servidos pelo site em /midia/banco/... com cache longo. Liga sozinho em
# produção (com DATABASE_URL); no desenvolvimento ficam no disco (media/).
# MIDIA_NO_BANCO=1 ou 0 força um lado ou o outro.
_midia_no_banco = os.environ.get("MIDIA_NO_BANCO", "").strip()
MIDIA_NO_BANCO = (
    _midia_no_banco == "1" if _midia_no_banco in ("0", "1") else bool(os.environ.get("DATABASE_URL"))
)

# Migrações pendentes aplicadas sozinhas quando o site sobe na Vercel
# (core/auto_migracao.py, chamado no rest/wsgi.py). No build da Vercel (CI=1)
# não roda. MIGRAR_AO_INICIAR=1 ou 0 força ligar ou desligar.
_na_vercel = os.environ.get("VERCEL") == "1"
_build_da_vercel = _na_vercel and os.environ.get("CI", "").strip().lower() in ("1", "true")
_migrar_ao_iniciar = os.environ.get("MIGRAR_AO_INICIAR", "").strip().lower()
MIGRAR_AO_INICIAR = (
    _migrar_ao_iniciar in ("1", "true", "yes", "on")
    if _migrar_ao_iniciar
    else _na_vercel and not _build_da_vercel and bool(os.environ.get("DATABASE_URL"))
)

# Quem guarda as fotos já servidas é a borda da Vercel (CDN). Atrás dela, cada
# instância guarda numa pasta temporária o que já entregou (na Vercel, o /tmp
# da função): a foto pedida de novo sai sem consultar o banco.
SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
MIDIA_CACHE_PASTA = os.path.join(tempfile.gettempdir(), "cantinho-midia")

STORAGES = {
    "default": {
        "BACKEND": (
            "core.midia.MidiaNoBanco"
            if MIDIA_NO_BANCO
            else "django.core.files.storage.FileSystemStorage"
        ),
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Arquivos estáticos com cache longo: os links levam "?v=<versão>" (ver
# core.context_processors), então um arquivo novo muda de endereço. O
# s-maxage deixa a borda da Vercel guardar a resposta e poupa a função.
WHITENOISE_MAX_AGE = 60 * 60 * 24 * 30


def _cabecalhos_estaticos(headers, path, url):
    headers["Cache-Control"] = "public, max-age=2592000, s-maxage=2592000, immutable"


WHITENOISE_ADD_HEADERS_FUNCTION = _cabecalhos_estaticos

# Proxy da Vercel/Render: o Django enxerga o HTTPS original.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Horário de funcionamento: segunda a sexta, das 11h às 16h.
LOJA_DIAS = (0, 1, 2, 3, 4)
LOJA_ABRE = int(os.environ.get("LOJA_ABRE", "11"))
LOJA_FECHA = int(os.environ.get("LOJA_FECHA", "16"))

# Relatório de bebidas (Pedidos e Estatísticas): conta como bebida todo
# produto cuja CATEGORIA tem no nome uma destas palavras (maiúscula ou
# minúscula tanto faz): "Bebidas", "Refrigerantes", "Sucos naturais"...
PALAVRAS_BEBIDAS = [
    palavra.strip()
    for palavra in os.environ.get("PALAVRAS_BEBIDAS", "bebida,refri,suco,cerveja,drink").split(",")
    if palavra.strip()
]

# Refri da marmita: a bebida barata já cadastrada (o REFRI-MINI 200ml de
# R$ 2,00) aparece como primeiro adicional de toda marmita deste preço. É
# bebida de categoria de bebidas, com nome de refri/suco e preço até o teto.
MARMITA_PRECO_COM_REFRI = Decimal(os.environ.get("MARMITA_PRECO_COM_REFRI", "10.00"))
REFRI_MARMITA_PRECO_MAX = Decimal(os.environ.get("REFRI_MARMITA_PRECO_MAX", "2.00"))

# WhatsApp que recebe os pedidos do cardápio (só dígitos, com DDI e DDD).
WHATSAPP_LOJA = os.environ.get("WHATSAPP_LOJA", "5511999999999")

# Chave do agente de impressão externo (mantém o valor antigo como padrão
# para não derrubar o agente que já está instalado).
IMPRESSAO_API_KEY = os.environ.get("IMPRESSAO_API_KEY", "chave-secreta-restaurante-2026")

# QZ Tray: certificado próprio (grátis) e chave privada, para o QZ lembrar a
# permissão do site. Gere com `python qz/gerar_certificado.py`.
QZ_CERTIFICADO = os.environ.get("QZ_CERTIFICADO", "")
QZ_CHAVE_PRIVADA = os.environ.get("QZ_CHAVE_PRIVADA", "")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
