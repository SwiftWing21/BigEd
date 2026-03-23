"""
Fleet-to-Fleet mTLS — mutual TLS for secure peer communication.

Generates a self-signed CA per fleet cluster, issues peer certificates signed
by that CA, and provides SSL contexts for both server and client roles.

Uses stdlib ``ssl`` for context management and ``cryptography`` for cert
generation.  All config lives in fleet.toml ``[federation.tls]``.

v0.100.00b — Secure Fleet-to-Fleet Communication
"""

import logging
import os
import ssl
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("fleet_tls")

FLEET_DIR = Path(__file__).parent

# ── Defaults ──────────────────────────────────────────────────────────────────

_DEFAULT_CERT_DIR = FLEET_DIR / "certs"
_CA_CERT_NAME = "ca.pem"
_CA_KEY_NAME = "ca-key.pem"
_PEER_CERT_NAME = "peer.pem"
_PEER_KEY_NAME = "peer-key.pem"
_TRUSTED_DIR_NAME = "trusted"

# Certificate validity
_CA_VALIDITY_DAYS = 3650      # 10 years
_PEER_VALIDITY_DAYS = 365     # 1 year
_EXPIRY_WARNING_DAYS = 30     # warn when cert expires within this window


# ── Config helpers ────────────────────────────────────────────────────────────


def _get_tls_config() -> dict:
    """Load [federation.tls] from fleet.toml, with safe defaults."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("federation", {}).get("tls", {})
    except Exception:
        log.warning("fleet_tls: could not load config, using defaults")
        return {}


def _cert_dir() -> Path:
    """Resolve the certificate directory from config or default."""
    tls_cfg = _get_tls_config()
    raw = tls_cfg.get("cert_dir", "fleet/certs")
    # Resolve relative to project root (parent of fleet/)
    p = Path(raw)
    if not p.is_absolute():
        p = FLEET_DIR.parent / raw
    return p


def _trusted_dir() -> Path:
    """Directory for storing trusted peer certificates."""
    return _cert_dir() / _TRUSTED_DIR_NAME


def is_tls_enabled() -> bool:
    """Check whether federation mTLS is enabled in fleet.toml."""
    tls_cfg = _get_tls_config()
    return bool(tls_cfg.get("enabled", False))


# ── Certificate generation (using cryptography library) ───────────────────────


def _ensure_dirs():
    """Create cert directories if they don't exist."""
    _cert_dir().mkdir(parents=True, exist_ok=True)
    _trusted_dir().mkdir(parents=True, exist_ok=True)


def generate_fleet_ca() -> tuple:
    """Generate a self-signed CA certificate for the fleet cluster.

    Returns (ca_cert_path, ca_key_path) on success.
    Raises RuntimeError if the cryptography library is unavailable.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        raise RuntimeError(
            "cryptography library required for mTLS cert generation. "
            "Install with: pip install cryptography"
        )

    _ensure_dirs()
    cd = _cert_dir()
    tls_cfg = _get_tls_config()
    ca_cert_path = cd / tls_cfg.get("ca_cert", _CA_CERT_NAME)
    ca_key_path = cd / tls_cfg.get("ca_key", _CA_KEY_NAME) if "ca_key" not in tls_cfg else cd / tls_cfg["ca_key"]
    # Use the default key name from config or fallback
    ca_key_path = cd / _CA_KEY_NAME

    # Generate RSA key pair
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

    # Build self-signed CA certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "BigEd CC Fleet"),
        x509.NameAttribute(NameOID.COMMON_NAME, "BigEd Fleet CA"),
    ])

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=_CA_VALIDITY_DAYS))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    # Write key (restrictive permissions)
    ca_key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        os.chmod(ca_key_path, 0o600)
    except Exception:
        pass  # Windows may not support Unix permissions

    # Write certificate
    ca_cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    log.info("Fleet CA generated: %s (valid %d days)", ca_cert_path, _CA_VALIDITY_DAYS)
    return str(ca_cert_path), str(ca_key_path)


def generate_peer_cert(fleet_id: str) -> tuple:
    """Generate a peer certificate signed by the fleet CA.

    Parameters
    ----------
    fleet_id : str
        Unique identifier for this peer (typically the device name).

    Returns (peer_cert_path, peer_key_path) on success.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        raise RuntimeError(
            "cryptography library required for mTLS cert generation. "
            "Install with: pip install cryptography"
        )

    _ensure_dirs()
    cd = _cert_dir()
    tls_cfg = _get_tls_config()

    ca_cert_path = cd / tls_cfg.get("ca_cert", _CA_CERT_NAME)
    ca_key_path = cd / _CA_KEY_NAME

    if not ca_cert_path.exists() or not ca_key_path.exists():
        raise FileNotFoundError(
            f"Fleet CA not found at {ca_cert_path}. Run generate_fleet_ca() first."
        )

    # Load CA
    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)

    # Generate peer key
    peer_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    peer_cert_path = cd / tls_cfg.get("peer_cert", _PEER_CERT_NAME)
    peer_key_path = cd / tls_cfg.get("peer_key", _PEER_KEY_NAME)

    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "BigEd CC Fleet"),
        x509.NameAttribute(NameOID.COMMON_NAME, f"fleet-peer-{fleet_id}"),
    ])

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(peer_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=_PEER_VALIDITY_DAYS))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_encipherment=True,
                content_commitment=False, key_cert_sign=False,
                crl_sign=False, data_encipherment=False,
                key_agreement=False, encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
                x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName(fleet_id),
                x509.IPAddress(
                    __import__("ipaddress").IPv4Address("127.0.0.1")
                ),
            ]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # Write peer key
    peer_key_path.write_bytes(
        peer_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        os.chmod(peer_key_path, 0o600)
    except Exception:
        pass

    # Write peer cert
    peer_cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    log.info("Peer cert generated for '%s': %s (valid %d days)", fleet_id, peer_cert_path, _PEER_VALIDITY_DAYS)
    return str(peer_cert_path), str(peer_key_path)


# ── SSL Context factories ────────────────────────────────────────────────────


def get_ssl_context(role: str = "server") -> ssl.SSLContext:
    """Return a configured ssl.SSLContext for fleet mTLS.

    Parameters
    ----------
    role : str
        ``"server"`` — for Flask/dashboard: loads peer cert, requires
        client cert verification against the fleet CA.
        ``"client"`` — for urllib/httpx outbound: loads peer cert,
        verifies server cert against the fleet CA.

    Raises FileNotFoundError if required cert files are missing.
    """
    cd = _cert_dir()
    tls_cfg = _get_tls_config()

    ca_cert_path = cd / tls_cfg.get("ca_cert", _CA_CERT_NAME)
    peer_cert_path = cd / tls_cfg.get("peer_cert", _PEER_CERT_NAME)
    peer_key_path = cd / tls_cfg.get("peer_key", _PEER_KEY_NAME)
    verify_peers = tls_cfg.get("verify_peers", True)

    for p in (ca_cert_path, peer_cert_path, peer_key_path):
        if not p.exists():
            raise FileNotFoundError(f"Required TLS file missing: {p}")

    if role == "server":
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(peer_cert_path), keyfile=str(peer_key_path))
        ctx.load_verify_locations(cafile=str(ca_cert_path))
        # Also load any trusted peer certs
        td = _trusted_dir()
        if td.exists():
            for trusted in td.glob("*.pem"):
                try:
                    ctx.load_verify_locations(cafile=str(trusted))
                except Exception:
                    log.warning("Could not load trusted cert: %s", trusted)
        if verify_peers:
            ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            ctx.verify_mode = ssl.CERT_OPTIONAL
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    elif role == "client":
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_cert_chain(certfile=str(peer_cert_path), keyfile=str(peer_key_path))
        ctx.load_verify_locations(cafile=str(ca_cert_path))
        # Also load trusted peer certs so we trust peers signed by different CAs
        td = _trusted_dir()
        if td.exists():
            for trusted in td.glob("*.pem"):
                try:
                    ctx.load_verify_locations(cafile=str(trusted))
                except Exception:
                    log.warning("Could not load trusted cert: %s", trusted)
        if not verify_peers:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    else:
        raise ValueError(f"role must be 'server' or 'client', got '{role}'")


# ── Certificate info / health ─────────────────────────────────────────────────


def get_cert_info() -> dict:
    """Return certificate health info for the dashboard.

    Returns dict with keys: ca_exists, peer_exists, ca_expiry, peer_expiry,
    peer_fingerprint, peer_subject, days_until_expiry, warning.
    """
    cd = _cert_dir()
    tls_cfg = _get_tls_config()

    ca_cert_path = cd / tls_cfg.get("ca_cert", _CA_CERT_NAME)
    peer_cert_path = cd / tls_cfg.get("peer_cert", _PEER_CERT_NAME)

    info = {
        "tls_enabled": is_tls_enabled(),
        "cert_dir": str(cd),
        "ca_exists": ca_cert_path.exists(),
        "peer_exists": peer_cert_path.exists(),
        "ca_expiry": None,
        "peer_expiry": None,
        "peer_fingerprint": None,
        "peer_subject": None,
        "days_until_expiry": None,
        "warning": None,
        "trusted_peers": 0,
    }

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        if ca_cert_path.exists():
            ca = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
            info["ca_expiry"] = ca.not_valid_after_utc.isoformat()

        if peer_cert_path.exists():
            peer = x509.load_pem_x509_certificate(peer_cert_path.read_bytes())
            info["peer_expiry"] = peer.not_valid_after_utc.isoformat()
            info["peer_subject"] = peer.subject.rfc4514_string()
            info["peer_fingerprint"] = peer.fingerprint(hashes.SHA256()).hex()

            # Days until peer cert expires
            now = datetime.now(timezone.utc)
            delta = peer.not_valid_after_utc - now
            info["days_until_expiry"] = delta.days

            if delta.days < _EXPIRY_WARNING_DAYS:
                info["warning"] = (
                    f"Peer certificate expires in {delta.days} days "
                    f"(on {peer.not_valid_after_utc.date().isoformat()})"
                )
                log.warning("Fleet TLS: %s", info["warning"])

        # Count trusted peer certs
        td = _trusted_dir()
        if td.exists():
            info["trusted_peers"] = len(list(td.glob("*.pem")))

    except ImportError:
        info["warning"] = "cryptography library not installed — cannot read cert details"
    except Exception as exc:
        info["warning"] = f"Error reading certificates: {exc}"

    return info


# ── Auto-setup ────────────────────────────────────────────────────────────────


def auto_setup() -> bool:
    """Auto-generate CA + peer cert if federation TLS is enabled and certs are missing.

    Called during supervisor/dashboard startup.  Returns True if certs were
    generated (or already exist), False on failure.
    """
    tls_cfg = _get_tls_config()
    if not tls_cfg.get("enabled", False):
        return False

    if not tls_cfg.get("auto_generate", True):
        log.info("Fleet TLS: auto_generate disabled, skipping cert setup")
        return False

    cd = _cert_dir()
    ca_cert_path = cd / tls_cfg.get("ca_cert", _CA_CERT_NAME)
    peer_cert_path = cd / tls_cfg.get("peer_cert", _PEER_CERT_NAME)

    generated = False

    # Generate CA if missing
    if not ca_cert_path.exists():
        try:
            generate_fleet_ca()
            generated = True
            log.info("Fleet TLS: auto-generated fleet CA")
        except Exception as exc:
            log.warning("Fleet TLS: failed to generate CA: %s", exc)
            return False

    # Generate peer cert if missing
    if not peer_cert_path.exists():
        try:
            from config import load_config
            cfg = load_config()
            fleet_id = cfg.get("naming", {}).get("device_name", "")
            if not fleet_id:
                import socket
                fleet_id = socket.gethostname()
            generate_peer_cert(fleet_id)
            generated = True
            log.info("Fleet TLS: auto-generated peer cert for '%s'", fleet_id)
        except Exception as exc:
            log.warning("Fleet TLS: failed to generate peer cert: %s", exc)
            return False

    # Check expiry warning
    if peer_cert_path.exists():
        try:
            info = get_cert_info()
            if info.get("warning"):
                log.warning("Fleet TLS: %s", info["warning"])
        except Exception:
            pass

    return True


# ── Cert exchange (peer trust) ────────────────────────────────────────────────


def store_trusted_cert(peer_id: str, cert_pem: str) -> str:
    """Store a peer's certificate in the trusted directory.

    Parameters
    ----------
    peer_id : str
        Identifier for the peer (used as filename).
    cert_pem : str
        PEM-encoded certificate data.

    Returns the path where the cert was stored.
    """
    _ensure_dirs()
    # Sanitize peer_id for use as filename
    safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in peer_id)
    path = _trusted_dir() / f"{safe_id}.pem"
    path.write_text(cert_pem, encoding="utf-8")
    log.info("Stored trusted cert for peer '%s' at %s", peer_id, path)
    return str(path)


def get_local_cert_pem() -> str:
    """Return the local peer certificate as PEM text for exchange."""
    cd = _cert_dir()
    tls_cfg = _get_tls_config()
    peer_cert_path = cd / tls_cfg.get("peer_cert", _PEER_CERT_NAME)
    if not peer_cert_path.exists():
        raise FileNotFoundError(f"Local peer cert not found: {peer_cert_path}")
    return peer_cert_path.read_text(encoding="utf-8")
