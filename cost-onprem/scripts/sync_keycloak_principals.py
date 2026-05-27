"""Synchronize Keycloak realm users into insights-rbac Principals.

This script is mounted into the RBAC container via ConfigMap and executed
under ``manage.py shell`` so the Django ORM is pre-initialized.  It reads
configuration from environment variables set by the Helm CronJob template.

Keycloak Admin REST API reference:
  https://www.keycloak.org/docs-api/latest/rest-api/index.html
"""

import json
import logging
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("keycloak-sync")

PAGE_SIZE = 100
REQUEST_TIMEOUT = 30
TOKEN_REFRESH_MARGIN = 0.8


class KeycloakClient:
    """Minimal Keycloak Admin REST API client using urllib."""

    def __init__(self, base_url, realm, client_id, client_secret, verify_tls=True):
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token = None
        self._token_acquired_at = 0.0
        self._token_expires_in = 0

        if verify_tls:
            self._ssl_ctx = ssl.create_default_context()
        else:
            self._ssl_ctx = ssl.create_default_context()
            self._ssl_ctx.check_hostname = False
            self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def _token_is_fresh(self):
        if not self._access_token or self._token_expires_in <= 0:
            return False
        elapsed = time.monotonic() - self._token_acquired_at
        return elapsed < (self._token_expires_in * TOKEN_REFRESH_MARGIN)

    def authenticate(self):
        """Obtain an access token via client_credentials grant."""
        url = f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/token"
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()

        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=self._ssl_ctx) as resp:
            body = json.loads(resp.read())

        self._access_token = body["access_token"]
        self._token_acquired_at = time.monotonic()
        self._token_expires_in = int(body.get("expires_in", 300))

    def ensure_authenticated(self):
        """Re-authenticate only if the current token is stale or missing."""
        if not self._token_is_fresh():
            self.authenticate()

    def _get(self, path, params=None):
        """Authenticated GET against the Admin API."""
        url = f"{self.base_url}/admin/realms/{self.realm}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self._access_token}")
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=self._ssl_ctx) as resp:
            return json.loads(resp.read())

    def get_user_count(self):
        """Return total user count for the realm."""
        self.ensure_authenticated()
        return self._get("/users/count")

    def list_users_page(self, first=0, max_results=PAGE_SIZE):
        """Fetch one page of users."""
        return self._get("/users", {"first": first, "max": max_results, "briefRepresentation": "false"})

    def list_all_users(self):
        """Paginate through all realm users, refreshing the token as needed."""
        all_users = []
        offset = 0

        while True:
            self.ensure_authenticated()
            page = self.list_users_page(first=offset, max_results=PAGE_SIZE)
            all_users.extend(page)
            log.info("Fetched users page: offset=%d, received=%d", offset, len(page))

            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        return all_users

    def get_role_members(self, role_name):
        """Bulk-fetch all users with a specific realm role."""
        members = []
        offset = 0

        while True:
            self.ensure_authenticated()
            page = self._get(f"/roles/{urllib.parse.quote(role_name)}/users",
                             {"first": offset, "max": PAGE_SIZE})
            members.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        return members


def sync(kc, org_id, account_number, org_admin_role, prune_orphans):
    """Core sync loop: Keycloak users -> RBAC Principals."""
    from api.models import Tenant
    from django.core.cache import cache
    from django.core.management import call_command
    from django.db import transaction
    from management.models import Group, Policy, Principal, Role

    t0 = time.monotonic()

    total_count = kc.get_user_count()
    log.info("Keycloak realm user count: %d", total_count)

    full_sync_success = False
    try:
        kc_users = kc.list_all_users()
        full_sync_success = True
    except Exception:
        log.exception("Failed to fetch all users from Keycloak")
        kc_users = []

    if not kc_users and not full_sync_success:
        log.error("No users fetched and sync failed; aborting")
        return False

    fetched_count = len(kc_users)
    if total_count > 0 and abs(fetched_count - total_count) / total_count > 0.05:
        log.warning(
            "User count drift: Keycloak reported %d users but %d were fetched (%.1f%% delta)",
            total_count, fetched_count, abs(fetched_count - total_count) / total_count * 100,
        )

    admin_usernames = set()
    try:
        admin_members = kc.get_role_members(org_admin_role)
        admin_usernames = {u["username"] for u in admin_members}
        log.info("Org-admin role '%s' members: %d", org_admin_role, len(admin_usernames))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log.warning("Realm role '%s' not found; no users will be org-admin", org_admin_role)
        else:
            raise

    if not Tenant.objects.filter(tenant_name="public").exists():
        log.error("Public tenant does not exist; RBAC migrations have not completed yet. "
                  "This is expected on first install -- the CronJob will retry.")
        return False
    public_tenant = Tenant.objects.get(tenant_name="public")
    admin_default_roles = Role.objects.filter(admin_default=True, tenant=public_tenant)
    if not admin_default_roles.exists():
        log.error("No admin_default roles found in public tenant; RBAC migrations may not have completed. "
                  "This is expected on first install -- the CronJob will retry.")
        return False

    tenant, created = Tenant.objects.get_or_create(
        org_id=org_id,
        defaults={"tenant_name": "acct" + account_number, "ready": True},
    )
    if created:
        log.info("Created tenant for org_id=%s", org_id)

    admin_group, _ = Group.objects.get_or_create(
        name="Cost Admin Default", tenant=tenant,
        defaults={"admin_default": True, "system": True,
                  "description": "Admin default: grants admin_default roles to org-admin users"},
    )
    if not admin_group.admin_default:
        admin_group.admin_default = True
        admin_group.save(update_fields=["admin_default"])

    admin_policy, _ = Policy.objects.get_or_create(
        name="Cost Admin Default Policy", tenant=tenant, group=admin_group,
    )
    for role in admin_default_roles:
        admin_policy.roles.add(role)

    counters = {"created": 0, "updated": 0, "unchanged": 0, "pruned": 0, "skipped_disabled": 0}
    synced_usernames = set()

    with transaction.atomic():
        for kc_user in kc_users:
            username = kc_user.get("username", "")
            if not username or username.startswith("service-account-"):
                continue

            if not kc_user.get("enabled", True):
                counters["skipped_disabled"] += 1
                log.info("AUDIT action=skip_disabled user=\"%s\" reason=keycloak_disabled tenant=\"%s\"", username, org_id)
                continue

            synced_usernames.add(username)
            principal, was_created = Principal.objects.get_or_create(
                username=username, tenant=tenant,
                defaults={"type": "user"},
            )

            is_admin = username in admin_usernames
            in_admin_group = admin_group.principals.filter(pk=principal.pk).exists()

            if is_admin and not in_admin_group:
                admin_group.principals.add(principal)
                action = "created" if was_created else "updated"
                counters[action] += 1
                log.info("AUDIT action=%s user=\"%s\" admin_group=added tenant=\"%s\"", action, username, org_id)
            elif not is_admin and in_admin_group:
                admin_group.principals.remove(principal)
                counters["updated"] += 1
                log.info("AUDIT action=updated user=\"%s\" admin_group=removed tenant=\"%s\"", username, org_id)
            elif was_created:
                counters["created"] += 1
                log.info("AUDIT action=created user=\"%s\" tenant=\"%s\"", username, org_id)
            else:
                counters["unchanged"] += 1

        if prune_orphans:
            if full_sync_success:
                try:
                    orphans = (
                        Principal.objects
                        .filter(tenant=tenant, type="user", cross_account=False)
                        .exclude(username__in=synced_usernames)
                    )
                except Exception:
                    orphans = (
                        Principal.objects
                        .filter(tenant=tenant, type="user")
                        .exclude(username__in=synced_usernames)
                    )
                orphan_count = orphans.count()
                if orphan_count > 0:
                    orphan_names = list(orphans.values_list("username", flat=True)[:50])
                    for name in orphan_names:
                        log.info("AUDIT action=pruned user=\"%s\" tenant=\"%s\"", name, org_id)
                    orphans.delete()
                counters["pruned"] = orphan_count
            else:
                log.warning("Skipping orphan pruning: Keycloak user listing was incomplete")

    cache.clear()
    log.info("RBAC cache cleared")

    try:
        call_command("bootstrap_tenants", "--org-id", org_id, "--force", verbosity=0)
        log.info("bootstrap_tenants completed for org_id=%s", org_id)
    except Exception:
        log.warning("bootstrap_tenants failed (non-fatal)", exc_info=True)

    elapsed = time.monotonic() - t0
    log.info(
        "SYNC COMPLETE: total_kc_users=%d, synced=%d, created=%d, updated=%d, "
        "unchanged=%d, pruned=%d, skipped_disabled=%d, elapsed=%.1fs",
        len(kc_users), len(synced_usernames), counters["created"],
        counters["updated"], counters["unchanged"], counters["pruned"],
        counters["skipped_disabled"], elapsed,
    )
    return True


def main():
    keycloak_url = os.environ.get("KEYCLOAK_URL", "")
    realm = os.environ.get("KEYCLOAK_REALM", "kubernetes")
    client_id = os.environ.get("KEYCLOAK_CLIENT_ID", "")
    client_secret = os.environ.get("KEYCLOAK_CLIENT_SECRET", "")
    verify_tls = os.environ.get("KEYCLOAK_TLS_VERIFY", "true").lower() not in ("false", "0", "no")
    org_id = os.environ.get("SYNC_ORG_ID", "")
    account_number = os.environ.get("SYNC_ACCOUNT_NUMBER", "")
    org_admin_role = os.environ.get("SYNC_ORG_ADMIN_ROLE", "org-admin")
    prune_orphans = os.environ.get("SYNC_PRUNE_ORPHANS", "true").lower() not in ("false", "0", "no")

    missing = []
    if not keycloak_url:
        missing.append("KEYCLOAK_URL")
    if not client_id:
        missing.append("KEYCLOAK_CLIENT_ID")
    if not client_secret:
        missing.append("KEYCLOAK_CLIENT_SECRET")
    if not org_id:
        missing.append("SYNC_ORG_ID")
    if not account_number:
        missing.append("SYNC_ACCOUNT_NUMBER")
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    log.info("Starting Keycloak-to-RBAC sync: realm=%s, org_id=%s, tls_verify=%s, prune=%s",
             realm, org_id, verify_tls, prune_orphans)

    kc = KeycloakClient(keycloak_url, realm, client_id, client_secret, verify_tls)

    try:
        kc.authenticate()
    except Exception:
        log.exception("Failed to authenticate with Keycloak")
        sys.exit(1)

    ok = sync(kc, org_id, account_number, org_admin_role, prune_orphans)
    sys.exit(0 if ok else 1)


main()
