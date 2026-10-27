"""Gera o certificado próprio (grátis) do QZ Tray. Rode UMA vez:

    python qz\\gerar_certificado.py

Cria nesta pasta:
  digital-certificate.txt  -> certificado (público). Vai para o QZ do caixa.
  private-key.pem          -> chave privada. SECRETA: nunca suba para o GitHub.
  COLAR-NA-VERCEL.txt      -> as 2 variáveis prontas para colar na Vercel (SECRETO).
"""
import base64
import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

pasta = Path(__file__).resolve().parent
chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
nome = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Cantinho Familia"),
    x509.NameAttribute(NameOID.COMMON_NAME, "Cantinho Familia - Impressao"),
])
agora = datetime.datetime.now(datetime.timezone.utc)
cert = (
    x509.CertificateBuilder()
    .subject_name(nome).issuer_name(nome)
    .public_key(chave.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(agora - datetime.timedelta(days=1))
    .not_valid_after(agora + datetime.timedelta(days=365 * 15))
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .sign(chave, hashes.SHA512())
)

cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
chave_pem = chave.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()

# Confere sozinho: assina um texto e valida com o certificado.
teste = b"teste-qz"
sig = chave.sign(teste, padding.PKCS1v15(), hashes.SHA512())
x509.load_pem_x509_certificate(cert_pem.encode()).public_key().verify(
    sig, teste, padding.PKCS1v15(), hashes.SHA512())

def uma_linha(pem):
    return pem.strip().replace("\r", "").replace("\n", "\\n")

(pasta / "digital-certificate.txt").write_text(cert_pem, encoding="utf-8", newline="\n")
(pasta / "private-key.pem").write_text(chave_pem, encoding="utf-8", newline="\n")
(pasta / "COLAR-NA-VERCEL.txt").write_text(
    "NOME DA VARIAVEL 1:  QZ_CERTIFICADO\n"
    "VALOR (copie a linha abaixo inteira):\n"
    + uma_linha(cert_pem) + "\n\n"
    "NOME DA VARIAVEL 2:  QZ_CHAVE_PRIVADA\n"
    "VALOR (copie a linha abaixo inteira):\n"
    + uma_linha(chave_pem) + "\n",
    encoding="utf-8", newline="\n")

print("OK! Certificado gerado e conferido.")
print("Arquivos criados em:", pasta)
print("Proximo passo: abra qz\\COLAR-NA-VERCEL.txt e crie as 2 variaveis na Vercel.")
