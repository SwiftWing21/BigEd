"""Payment processing integration — Stripe (or mock) for usage-based billing.

v0.400.00b: Usage-Based Billing Integration
- PaymentProvider ABC with Stripe + Mock implementations
- Webhook handling, billing portal, payment history
- DB tables: payment_records, payment_customers
"""
import abc
import hashlib
import hmac
import json
import logging
import time
import uuid

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — applied via ensure_payment_tables()
# ---------------------------------------------------------------------------

PAYMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_records (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    amount_cents INTEGER NOT NULL DEFAULT 0,
    currency TEXT DEFAULT 'usd',
    status TEXT NOT NULL DEFAULT 'pending',
    provider_ref TEXT,
    description TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payment_records_tid
    ON payment_records (tenant_id, created_at);

CREATE TABLE IF NOT EXISTS payment_customers (
    tenant_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'mock',
    customer_id TEXT,
    subscription_id TEXT,
    plan TEXT DEFAULT 'free',
    created_at REAL NOT NULL
);
"""

_tables_ensured = False


def _get_conn():
    """Lazy import db to avoid circular imports."""
    import db
    return db.get_conn()


def _retry_write(fn):
    """Lazy proxy to db._retry_write."""
    import db
    return db._retry_write(fn)


def _load_payments_config():
    """Load [payments] section from fleet.toml."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("payments", {})
    except Exception:
        log.warning("payments: could not load fleet.toml [payments]", exc_info=True)
        return {}


def ensure_payment_tables():
    """Create payment tables if they don't exist (idempotent)."""
    global _tables_ensured
    if _tables_ensured:
        return
    try:
        conn = _get_conn()
        conn.executescript(PAYMENT_SCHEMA)
        conn.close()
        _tables_ensured = True
    except Exception:
        log.warning("payments: failed to create tables", exc_info=True)


# ---------------------------------------------------------------------------
# Provider ABC
# ---------------------------------------------------------------------------

class PaymentProvider(abc.ABC):
    """Abstract payment provider interface."""

    @abc.abstractmethod
    def create_customer(self, tenant_id, email=None, name=None):
        """Create a customer record with the payment provider.

        Returns: {"customer_id": str, ...}
        """

    @abc.abstractmethod
    def create_subscription(self, customer_id, price_id, metadata=None):
        """Create a recurring subscription.

        Returns: {"subscription_id": str, "status": str, ...}
        """

    @abc.abstractmethod
    def cancel_subscription(self, subscription_id):
        """Cancel an active subscription.

        Returns: {"status": str, ...}
        """

    @abc.abstractmethod
    def record_usage(self, subscription_id, quantity, timestamp=None):
        """Report metered usage to the provider.

        Returns: {"usage_record_id": str, ...}
        """

    @abc.abstractmethod
    def get_invoice(self, invoice_id):
        """Retrieve an invoice by ID.

        Returns: dict with invoice details.
        """

    @abc.abstractmethod
    def create_checkout_session(self, customer_id, price_id, success_url, cancel_url):
        """Create a hosted checkout session URL.

        Returns: {"session_id": str, "url": str}
        """


# ---------------------------------------------------------------------------
# Stripe implementation
# ---------------------------------------------------------------------------

class StripeProvider(PaymentProvider):
    """Stripe API integration (lazy import of stripe lib)."""

    def __init__(self, api_key):
        # Never log the key — just validate it is set
        if not api_key:
            raise ValueError("payments: Stripe API key is required but not configured")
        self._api_key = api_key
        self._stripe = None

    def _get_stripe(self):
        """Lazy-load the stripe module."""
        if self._stripe is None:
            try:
                import stripe
                stripe.api_key = self._api_key
                self._stripe = stripe
            except ImportError:
                raise RuntimeError(
                    "payments: 'stripe' package not installed. "
                    "Install with: pip install stripe"
                )
        return self._stripe

    def create_customer(self, tenant_id, email=None, name=None):
        stripe = self._get_stripe()
        try:
            params = {"metadata": {"tenant_id": tenant_id}}
            if email:
                params["email"] = email
            if name:
                params["name"] = name
            customer = stripe.Customer.create(**params)
            log.info("payments: created Stripe customer for tenant=%s", tenant_id)
            return {"customer_id": customer.id}
        except Exception:
            log.warning("payments: Stripe create_customer failed for tenant=%s",
                        tenant_id, exc_info=True)
            raise

    def create_subscription(self, customer_id, price_id, metadata=None):
        stripe = self._get_stripe()
        try:
            params = {
                "customer": customer_id,
                "items": [{"price": price_id}],
            }
            if metadata:
                params["metadata"] = metadata
            sub = stripe.Subscription.create(**params)
            log.info("payments: created subscription id=%s for customer=%s",
                     sub.id, customer_id)
            return {"subscription_id": sub.id, "status": sub.status}
        except Exception:
            log.warning("payments: Stripe create_subscription failed for customer=%s",
                        customer_id, exc_info=True)
            raise

    def cancel_subscription(self, subscription_id):
        stripe = self._get_stripe()
        try:
            sub = stripe.Subscription.delete(subscription_id)
            log.info("payments: cancelled subscription id=%s", subscription_id)
            return {"status": sub.status}
        except Exception:
            log.warning("payments: Stripe cancel_subscription failed for id=%s",
                        subscription_id, exc_info=True)
            raise

    def record_usage(self, subscription_id, quantity, timestamp=None):
        stripe = self._get_stripe()
        try:
            # Look up the metered subscription item
            sub = stripe.Subscription.retrieve(subscription_id)
            si_id = sub["items"]["data"][0]["id"] if sub.get("items", {}).get("data") else None
            if not si_id:
                raise ValueError("payments: no subscription item found for usage reporting")
            params = {
                "subscription_item": si_id,
                "quantity": int(quantity),
            }
            if timestamp:
                params["timestamp"] = int(timestamp)
            record = stripe.SubscriptionItem.create_usage_record(**params)
            return {"usage_record_id": record.id}
        except Exception:
            log.warning("payments: Stripe record_usage failed for sub=%s",
                        subscription_id, exc_info=True)
            raise

    def get_invoice(self, invoice_id):
        stripe = self._get_stripe()
        try:
            inv = stripe.Invoice.retrieve(invoice_id)
            return {
                "id": inv.id,
                "amount_due": inv.amount_due,
                "currency": inv.currency,
                "status": inv.status,
                "hosted_invoice_url": getattr(inv, "hosted_invoice_url", None),
            }
        except Exception:
            log.warning("payments: Stripe get_invoice failed for id=%s",
                        invoice_id, exc_info=True)
            raise

    def create_checkout_session(self, customer_id, price_id, success_url, cancel_url):
        stripe = self._get_stripe()
        try:
            session = stripe.checkout.Session.create(
                customer=customer_id,
                line_items=[{"price": price_id, "quantity": 1}],
                mode="subscription",
                success_url=success_url,
                cancel_url=cancel_url,
            )
            return {"session_id": session.id, "url": session.url}
        except Exception:
            log.warning("payments: Stripe create_checkout_session failed",
                        exc_info=True)
            raise


# ---------------------------------------------------------------------------
# Mock implementation — for testing without real payments
# ---------------------------------------------------------------------------

class MockProvider(PaymentProvider):
    """In-memory mock provider for development and testing."""

    def create_customer(self, tenant_id, email=None, name=None):
        cid = f"mock_cus_{tenant_id}_{uuid.uuid4().hex[:8]}"
        log.info("payments [mock]: created customer %s for tenant=%s", cid, tenant_id)
        return {"customer_id": cid}

    def create_subscription(self, customer_id, price_id, metadata=None):
        sid = f"mock_sub_{uuid.uuid4().hex[:8]}"
        log.info("payments [mock]: created subscription %s", sid)
        return {"subscription_id": sid, "status": "active"}

    def cancel_subscription(self, subscription_id):
        log.info("payments [mock]: cancelled subscription %s", subscription_id)
        return {"status": "canceled"}

    def record_usage(self, subscription_id, quantity, timestamp=None):
        rid = f"mock_usage_{uuid.uuid4().hex[:8]}"
        log.info("payments [mock]: recorded usage qty=%s for sub=%s",
                 quantity, subscription_id)
        return {"usage_record_id": rid}

    def get_invoice(self, invoice_id):
        return {
            "id": invoice_id,
            "amount_due": 0,
            "currency": "usd",
            "status": "paid",
            "hosted_invoice_url": None,
        }

    def create_checkout_session(self, customer_id, price_id, success_url, cancel_url):
        sid = f"mock_cs_{uuid.uuid4().hex[:8]}"
        return {"session_id": sid, "url": success_url}


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

_provider_instance = None


def get_provider():
    """Return the configured payment provider (cached singleton).

    Falls back to MockProvider when Stripe is not configured or the stripe
    package is not installed.
    """
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    cfg = _load_payments_config()
    provider_name = cfg.get("provider", "mock")

    if provider_name == "stripe":
        api_key = cfg.get("stripe_api_key", "")
        if not api_key:
            log.warning("payments: provider=stripe but no stripe_api_key configured; "
                        "falling back to mock")
            _provider_instance = MockProvider()
        else:
            try:
                _provider_instance = StripeProvider(api_key)
            except Exception:
                log.warning("payments: failed to initialize StripeProvider; "
                            "falling back to mock", exc_info=True)
                _provider_instance = MockProvider()
    else:
        _provider_instance = MockProvider()

    return _provider_instance


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def setup_billing(tenant_id, plan, payment_method=None):
    """Create customer + subscription for a tenant.

    Returns: {"customer_id": str, "subscription_id": str, "status": str}
    """
    ensure_payment_tables()
    provider = get_provider()
    cfg = _load_payments_config()

    try:
        # Create customer
        cust = provider.create_customer(tenant_id)
        customer_id = cust["customer_id"]

        # Determine price ID for the plan
        price_ids = _parse_price_ids(cfg)
        price_id = price_ids.get(plan, price_ids.get("default", ""))

        subscription_id = None
        status = "active"
        if price_id:
            sub = provider.create_subscription(
                customer_id, price_id,
                metadata={"tenant_id": tenant_id, "plan": plan},
            )
            subscription_id = sub.get("subscription_id")
            status = sub.get("status", "active")

        # Persist to DB
        now = time.time()

        def _do():
            conn = _get_conn()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO payment_customers
                       (tenant_id, provider, customer_id, subscription_id, plan, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (tenant_id, cfg.get("provider", "mock"), customer_id,
                     subscription_id, plan, now),
                )
                conn.commit()
            finally:
                conn.close()

        _retry_write(_do)
        log.info("payments: billing setup complete for tenant=%s plan=%s",
                 tenant_id, plan)
        return {
            "customer_id": customer_id,
            "subscription_id": subscription_id,
            "status": status,
        }
    except Exception:
        log.warning("payments: setup_billing failed for tenant=%s", tenant_id,
                    exc_info=True)
        return {"customer_id": None, "subscription_id": None, "status": "error"}


def process_usage_billing(tenant_id):
    """Calculate overage from billing.py and charge if needed.

    Returns: {"tenant_id": str, "overage_cents": int, "charged": bool}
    """
    ensure_payment_tables()
    try:
        import billing as _billing
        invoice = _billing.calculate_invoice(tenant_id)
        pricing = _billing.get_pricing()
    except Exception:
        log.warning("payments: could not load billing data for tenant=%s",
                    tenant_id, exc_info=True)
        return {"tenant_id": tenant_id, "overage_cents": 0, "charged": False}

    base_monthly = pricing.get("base_monthly", 0)
    total = invoice.get("total", 0.0)
    overage_usd = max(0.0, total - base_monthly)
    overage_cents = int(round(overage_usd * 100))

    if overage_cents <= 0:
        return {"tenant_id": tenant_id, "overage_cents": 0, "charged": False}

    # Look up subscription
    customer_info = _get_customer_info(tenant_id)
    if not customer_info or not customer_info.get("subscription_id"):
        log.warning("payments: no subscription for tenant=%s; cannot charge overage",
                    tenant_id)
        return {"tenant_id": tenant_id, "overage_cents": overage_cents, "charged": False}

    try:
        provider = get_provider()
        provider.record_usage(
            customer_info["subscription_id"],
            overage_cents,
            timestamp=int(time.time()),
        )
        _record_payment(tenant_id, overage_cents, "usd", "succeeded",
                        description=f"Usage overage: ${overage_usd:.2f}")
        log.info("payments: charged overage %d cents for tenant=%s",
                 overage_cents, tenant_id)
        return {"tenant_id": tenant_id, "overage_cents": overage_cents, "charged": True}
    except Exception:
        log.warning("payments: overage charge failed for tenant=%s",
                    tenant_id, exc_info=True)
        return {"tenant_id": tenant_id, "overage_cents": overage_cents, "charged": False}


def get_billing_portal_url(tenant_id):
    """Generate a Stripe billing portal URL for the tenant.

    Returns: portal URL string, or empty string on failure.
    """
    cfg = _load_payments_config()
    if cfg.get("provider") != "stripe":
        return ""

    customer_info = _get_customer_info(tenant_id)
    if not customer_info or not customer_info.get("customer_id"):
        log.warning("payments: no customer_id for tenant=%s", tenant_id)
        return ""

    try:
        import stripe
        stripe.api_key = cfg.get("stripe_api_key", "")
        session = stripe.billing_portal.Session.create(
            customer=customer_info["customer_id"],
            return_url=cfg.get("return_url", "http://127.0.0.1:5555"),
        )
        return session.url
    except ImportError:
        log.warning("payments: stripe package not installed")
        return ""
    except Exception:
        log.warning("payments: billing portal URL failed for tenant=%s",
                    tenant_id, exc_info=True)
        return ""


def handle_webhook(payload, signature):
    """Process Stripe webhook events with signature verification.

    Args:
        payload: raw request body (bytes or str)
        signature: Stripe-Signature header value

    Returns: {"event": str, "handled": bool}
    """
    cfg = _load_payments_config()
    webhook_secret = cfg.get("stripe_webhook_secret", "")

    if cfg.get("provider") != "stripe" or not webhook_secret:
        log.warning("payments: webhook received but Stripe not configured")
        return {"event": "unknown", "handled": False}

    # Verify signature
    try:
        import stripe
        stripe.api_key = cfg.get("stripe_api_key", "")
        event = stripe.Webhook.construct_event(
            payload, signature, webhook_secret,
        )
    except ImportError:
        log.warning("payments: stripe package not installed for webhook verification")
        return {"event": "unknown", "handled": False}
    except Exception:
        log.warning("payments: webhook signature verification failed", exc_info=True)
        return {"event": "invalid_signature", "handled": False}

    event_type = event.get("type", "unknown")
    log.info("payments: processing webhook event=%s", event_type)

    ensure_payment_tables()
    try:
        if event_type == "invoice.payment_succeeded":
            _handle_payment_succeeded(event)
        elif event_type == "customer.subscription.updated":
            _handle_subscription_updated(event)
        elif event_type == "customer.subscription.deleted":
            _handle_subscription_deleted(event)
        elif event_type == "invoice.payment_failed":
            _handle_payment_failed(event)
        else:
            log.info("payments: unhandled webhook event type=%s", event_type)
            return {"event": event_type, "handled": False}
    except Exception:
        log.warning("payments: webhook handler failed for event=%s",
                    event_type, exc_info=True)
        return {"event": event_type, "handled": False}

    return {"event": event_type, "handled": True}


def get_payment_history(tenant_id, limit=50):
    """Return payment records for a tenant, most recent first.

    Returns: list of dicts with payment record fields.
    """
    ensure_payment_tables()
    try:
        conn = _get_conn()
        rows = conn.execute(
            """SELECT id, tenant_id, amount_cents, currency, status,
                      provider_ref, description, created_at
               FROM payment_records
               WHERE tenant_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (tenant_id, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        log.warning("payments: get_payment_history failed for tenant=%s",
                    tenant_id, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_price_ids(cfg):
    """Parse comma-separated price IDs from config into a plan-keyed dict.

    Format: "free:price_xxx,pro:price_yyy,enterprise:price_zzz"
    or just "price_xxx" for a single default.
    """
    raw = cfg.get("stripe_price_ids", "")
    if not raw:
        return {}
    result = {}
    for part in raw.split(","):
        part = part.strip()
        if ":" in part:
            plan, pid = part.split(":", 1)
            result[plan.strip()] = pid.strip()
        elif part:
            result["default"] = part
    return result


def _get_customer_info(tenant_id):
    """Fetch payment_customers row for a tenant."""
    ensure_payment_tables()
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM payment_customers WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        log.warning("payments: _get_customer_info failed for tenant=%s",
                    tenant_id, exc_info=True)
        return None


def _record_payment(tenant_id, amount_cents, currency, status,
                    provider_ref=None, description=None):
    """Insert a payment_records row."""
    now = time.time()

    def _do():
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO payment_records
                   (tenant_id, amount_cents, currency, status,
                    provider_ref, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (tenant_id, int(amount_cents), currency, status,
                 provider_ref, description, now),
            )
            conn.commit()
        finally:
            conn.close()

    try:
        _retry_write(_do)
    except Exception:
        log.warning("payments: _record_payment failed for tenant=%s",
                    tenant_id, exc_info=True)


def _tenant_from_stripe_customer(customer_id):
    """Reverse-lookup tenant_id from a Stripe customer_id."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT tenant_id FROM payment_customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        conn.close()
        return row["tenant_id"] if row else None
    except Exception:
        log.warning("payments: tenant lookup failed for customer=%s",
                    customer_id, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Webhook event handlers
# ---------------------------------------------------------------------------

def _handle_payment_succeeded(event):
    """Handle invoice.payment_succeeded — record the payment."""
    data = event.get("data", {}).get("object", {})
    customer_id = data.get("customer", "")
    tenant_id = _tenant_from_stripe_customer(customer_id)
    if not tenant_id:
        log.warning("payments: payment_succeeded for unknown customer=%s", customer_id)
        return
    _record_payment(
        tenant_id,
        amount_cents=data.get("amount_paid", 0),
        currency=data.get("currency", "usd"),
        status="succeeded",
        provider_ref=data.get("id"),
        description=f"Invoice {data.get('id', 'unknown')}",
    )


def _handle_subscription_updated(event):
    """Handle customer.subscription.updated — sync plan status."""
    data = event.get("data", {}).get("object", {})
    customer_id = data.get("customer", "")
    tenant_id = _tenant_from_stripe_customer(customer_id)
    if not tenant_id:
        return
    new_status = data.get("status", "")
    log.info("payments: subscription updated for tenant=%s status=%s",
             tenant_id, new_status)

    def _do():
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE payment_customers SET subscription_id = ? WHERE tenant_id = ?",
                (data.get("id"), tenant_id),
            )
            conn.commit()
        finally:
            conn.close()

    try:
        _retry_write(_do)
    except Exception:
        log.warning("payments: subscription update DB write failed for tenant=%s",
                    tenant_id, exc_info=True)


def _handle_subscription_deleted(event):
    """Handle customer.subscription.deleted — clear subscription."""
    data = event.get("data", {}).get("object", {})
    customer_id = data.get("customer", "")
    tenant_id = _tenant_from_stripe_customer(customer_id)
    if not tenant_id:
        return
    log.info("payments: subscription cancelled for tenant=%s", tenant_id)

    def _do():
        conn = _get_conn()
        try:
            conn.execute(
                """UPDATE payment_customers
                   SET subscription_id = NULL, plan = 'free'
                   WHERE tenant_id = ?""",
                (tenant_id,),
            )
            conn.commit()
        finally:
            conn.close()

    try:
        _retry_write(_do)
    except Exception:
        log.warning("payments: subscription delete DB write failed for tenant=%s",
                    tenant_id, exc_info=True)


def _handle_payment_failed(event):
    """Handle invoice.payment_failed — record the failure."""
    data = event.get("data", {}).get("object", {})
    customer_id = data.get("customer", "")
    tenant_id = _tenant_from_stripe_customer(customer_id)
    if not tenant_id:
        log.warning("payments: payment_failed for unknown customer=%s", customer_id)
        return
    _record_payment(
        tenant_id,
        amount_cents=data.get("amount_due", 0),
        currency=data.get("currency", "usd"),
        status="failed",
        provider_ref=data.get("id"),
        description=f"Payment failed: invoice {data.get('id', 'unknown')}",
    )


# ---------------------------------------------------------------------------
# Dashboard endpoint helpers (called from dashboard.py)
# ---------------------------------------------------------------------------

def register_payment_routes(app):
    """Register payment API routes on the Flask/Bottle app.

    Endpoints:
        POST /api/payments/webhook           — Stripe webhook handler
        GET  /api/payments/<tid>/portal      — redirect to billing portal
        GET  /api/payments/<tid>/history     — payment history
        POST /api/payments/<tid>/setup       — initial billing setup
    """
    try:
        from bottle import request, response, abort
    except ImportError:
        log.warning("payments: bottle not available; skipping route registration")
        return

    @app.post("/api/payments/webhook")
    def _webhook():
        sig = request.get_header("Stripe-Signature", "")
        body = request.body.read()
        result = handle_webhook(body, sig)
        if result.get("event") == "invalid_signature":
            response.status = 400
            return {"error": "invalid signature"}
        return result

    @app.get("/api/payments/<tenant_id>/portal")
    def _portal(tenant_id):
        url = get_billing_portal_url(tenant_id)
        if not url:
            response.status = 404
            return {"error": "billing portal not available"}
        response.status = 302
        response.set_header("Location", url)
        return {"url": url}

    @app.get("/api/payments/<tenant_id>/history")
    def _history(tenant_id):
        limit = int(request.params.get("limit", 50))
        return {"payments": get_payment_history(tenant_id, limit=limit)}

    @app.post("/api/payments/<tenant_id>/setup")
    def _setup(tenant_id):
        try:
            body = request.json or {}
        except Exception:
            body = {}
        plan = body.get("plan", "free")
        payment_method = body.get("payment_method")
        result = setup_billing(tenant_id, plan, payment_method)
        if result.get("status") == "error":
            response.status = 500
        return result

    log.info("payments: registered 4 API routes under /api/payments/")
