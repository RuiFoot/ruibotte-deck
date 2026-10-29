"""HTTPS(보안 연결)와 휴대폰 알림(Web Push).

아이폰·아이패드 사파리는 '알림'과 '화면 켜 두기'를 HTTPS 에서만 허용한다. 집·회사 LAN 주소(192.168.x.x)는 공인 인증서를
받을 수 없으므로, 이 PC가 **자기만의 인증 기관(로컬 CA)** 을 만들고 그걸로 LAN 주소용 서버 인증서를 발급한다.
휴대폰에 CA 인증서를 한 번 설치·신뢰하면 https://<이 PC 주소>:<포트+1> 이 경고 없이 열린다.

- 파일: <데이터 폴더>/tls (ca.key·ca.crt — CA는 10년, server.key·server.crt — 397일, 주소가 바뀌면 다시 발급)
        <데이터 폴더>/push/vapid.pem (Web Push 서버 키)
- CA 개인 키는 이 PC 밖으로 나가지 않는다. 휴대폰에 주는 것은 공개 인증서(ca.crt)뿐.
- 알림 내용은 기기 공개 키로 암호화돼 Apple/Google 푸시 서버를 거쳐 기기로 감 (중간에서 내용을 볼 수 없음).
"""
import base64
import datetime as dt
import ipaddress
import json
import socket
import threading
import time
from pathlib import Path

import desktop

TLS = desktop.DATA / "tls"
PUSH = desktop.DATA / "push"
VAPID_SUB = "mailto:95283019+RuiFoot@users.noreply.github.com"   # 푸시 서버가 요구하는 연락처 (개인 메일 아닌 GitHub noreply)


def https_port(port):
    return int(port) + 1


# ---------- 인증서 ----------
def _names():
    host = socket.gethostname()
    dns = sorted({"localhost", host, f"{host}.local"})
    ips = sorted({"127.0.0.1", *[a["ip"] for a in desktop.lan_addresses()]})
    return dns, ips


def _load(path):
    from cryptography import x509
    return x509.load_pem_x509_certificate(path.read_bytes())


def ensure_certs():
    """CA와 서버 인증서를 준비(없거나, 30일 안에 만료되거나, 이 PC 주소가 빠져 있으면 다시 발급). 반환: (server.crt, server.key)"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    TLS.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    ca_key_p, ca_crt_p = TLS / "ca.key", TLS / "ca.crt"
    srv_key_p, srv_crt_p = TLS / "server.key", TLS / "server.crt"
    pem = lambda k: k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,   # noqa: E731
                                    serialization.NoEncryption())

    if not (ca_key_p.exists() and ca_crt_p.exists()):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"RuiBotte Deck Local CA ({socket.gethostname()})"),
                          x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RuiBotte Deck")])
        ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
              .not_valid_after(now + dt.timedelta(days=3650))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                           key_encipherment=False, data_encipherment=False, key_agreement=False,
                                           encipher_only=False, decipher_only=False), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
              .sign(key, hashes.SHA256()))
        ca_key_p.write_bytes(pem(key))
        ca_crt_p.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        srv_crt_p.unlink(missing_ok=True)

    ca_key = serialization.load_pem_private_key(ca_key_p.read_bytes(), None)
    ca = _load(ca_crt_p)
    dns, ips = _names()

    def fresh():
        if not (srv_key_p.exists() and srv_crt_p.exists()):
            return False
        c = _load(srv_crt_p)
        if c.not_valid_after_utc - now < dt.timedelta(days=30):
            return False
        san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
        return set(ips) <= have and set(dns) <= set(san.get_values_for_type(x509.DNSName))

    if not fresh():
        key = ec.generate_private_key(ec.SECP256R1())
        crt = (x509.CertificateBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname())]))
               .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=397))   # 애플: 398일 이하
               .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in dns]
                                                          + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]), critical=False)
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
               .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
               .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                            data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                                            encipher_only=False, decipher_only=False), critical=True)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
               .sign(ca_key, hashes.SHA256()))
        srv_key_p.write_bytes(pem(key))
        srv_crt_p.write_bytes(crt.public_bytes(serialization.Encoding.PEM) + ca_crt_p.read_bytes())   # 체인 포함
    return srv_crt_p, srv_key_p


def ca_cert_bytes():
    """휴대폰에 설치할 CA 인증서 (공개 정보). 없으면 만듦."""
    ensure_certs()
    return (TLS / "ca.crt").read_bytes()


def cert_info():
    try:
        c = _load(TLS / "server.crt")
        from cryptography import x509
        san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        return {"ready": True, "expires": c.not_valid_after_utc.timestamp(),
                "names": [str(x) for x in san.get_values_for_type(x509.IPAddress)] + san.get_values_for_type(x509.DNSName)}
    except Exception:
        return {"ready": False}


# ---------- Web Push ----------
def vapid():
    """(개인 키 PEM 경로, 브라우저에 줄 공개 키 base64url). 없으면 만듦."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    PUSH.mkdir(parents=True, exist_ok=True)
    p = PUSH / "vapid.pem"
    if not p.exists():
        k = ec.generate_private_key(ec.SECP256R1())
        p.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    k = serialization.load_pem_private_key(p.read_bytes(), None)
    raw = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return p, base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Pusher:
    """구독한 기기들에 알림 보내기. 구독 목록은 DB(push_subs)."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()

    def send_all(self, title, body, kind="", url="/"):
        subs = self.store.push_subs() if self.store else []
        if not subs:
            return 0
        threading.Thread(target=self._send, args=(subs, title, body, kind, url), daemon=True, name="push").start()
        return len(subs)

    def _send(self, subs, title, body, kind, url):
        from pywebpush import WebPushException, webpush
        key_path, _ = vapid()
        payload = json.dumps({"title": title, "body": body, "tag": kind or None, "url": url}, ensure_ascii=False)
        with self.lock:
            for s in subs:
                try:
                    webpush(subscription_info={"endpoint": s["endpoint"], "keys": json.loads(s["keys_json"])}, data=payload,
                            vapid_private_key=str(key_path), vapid_claims={"sub": VAPID_SUB}, ttl=3600, timeout=15)
                    self.store.push_ok(s["endpoint"])
                except WebPushException as e:
                    code = getattr(e.response, "status_code", None)
                    if code in (404, 410):   # 기기가 구독을 끊음 → 정리
                        self.store.push_remove(s["endpoint"])
                    print(time.strftime("%m-%d %H:%M:%S"), f"push 실패 {code} ({s.get('device') or '?'})", flush=True)
                except Exception as e:
                    print(time.strftime("%m-%d %H:%M:%S"), f"push 오류 {type(e).__name__}", flush=True)
