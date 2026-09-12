# RBAC UI Test Plan — Cost Management On-Prem

**Date**: 2026-05-21 (Updated: 2026-09-07)  
**Status**: Review  
**Epic**: [COST-7570](https://redhat.atlassian.net/browse/COST-7570) — CoP Authentication & Authorization Migration  
**Story**: [COST-7632](https://redhat.atlassian.net/browse/COST-7632) — RBAC UI  
**POC**: [COST-7654](https://redhat.atlassian.net/browse/COST-7654) — MFE in koku-ui-onprem  
**Parent test plan**: [FLPATH-3551](https://redhat.atlassian.net/browse/FLPATH-3551) / [COST-7571](https://redhat.atlassian.net/browse/COST-7571)

---

## Document Summary

This is the **RBAC UI** test plan. Every case is tagged **UI**, **API**, or **Infra**. API and Cost-shell login/logout that already exist under `tests/` are **mappings only** — do not re-implement them as Playwright IAM cases.

| Layer | Meaning | Execute here? |
|-------|---------|---------------|
| **UI** | Browser (manual or Playwright): chrome, routes, MFE, tables, toasts | **Yes** — remaining IDs below |
| **API** | HTTP to gateway / Keycloak / insights-rbac; no browser | **No** — run existing pytest |
| **Infra** | Chart ConfigMap, nginx, Jobs, pods (not user-facing) | Only if not already covered by helm/gateway tests |

**Total UI/Infra cases owned by this plan**: counted in the table below. API mappings (I, L, H-01–H-05, H-09, B-06, N-03–N-05, N-07, A-01, A-05) are **not** counted.

| Category | IDs | Layer | Count |
|----------|-----|-------|-------|
| A Auth & session | A-02–A-04 | UI | 3 |
| B MFE / infra | B-01–B-05, B-07–B-08 | UI + Infra (B-01, B-03) | 7 |
| C Navigation | C-01–C-10b, C-12–C-14 (no C-11) | UI | 23 |
| D Groups | D-01–D-14 | UI | 14 |
| E Users | E-01–E-11 | UI | 11 |
| F Roles | F-01, F-03–F-08 (no F-02) | UI | 7 |
| G My User Access | G-01–G-06, G-08 (no G-07) | UI | 7 |
| H Enforcement | H-06–H-08, H-10–H-11 | UI | 5 |
| J Negative / resilience | J-01–J-03, J-05–J-11 (no J-04, J-12) | UI | 10 |
| K On-prem UX | K-01, K-04, K-06 | UI | 3 |
| N Security | N-01–N-02, N-06, N-08 | UI | 4 |
| **This-plan total** | | | **94** |
| of which **UI** | | | **92** |
| of which **Infra** | B-01, B-03 | | **2** |

**Not counted — API already in `tests/`**: A-01 (Cost login UI), A-05, B-06, H-01–H-05, H-09, I-01–I-13, L/PERF-RBAC-*, N-03–N-05, N-07.

**Not counted — folded into another UI ID**: C-11→G-05, F-02→F-01/C-05, G-07→J-10, J-04→D-12, J-12→E-04, K-02→E-05, K-03→C-08, K-05→E-11. **N/A**: H-12.

**Priority** (rows with a Pri column; K has none): **54 P0**, **28 P1**, **9 P2**.

**Test coverage**:
- **92 UI** cases this plan executes (browser)
- **2 Infra** cases (nginx `/rbac/` + plugin-manifest)
- **API** enforcement, gateway, JWT, and RBAC perf stay in `tests/suites/auth/`, `tests/suites/e2e/test_rbac_access.py`, `tests/suites/performance/test_rbac_perf.py`

**Key UI behaviors (on-prem)**:
- IAM in the **Global** sidebar as expandable **Identity and Access Management** with **Users / Roles / Groups** only (no IAM Overview in sidebar)
- Second IAM entry point: masthead **user menu** (`user@…`) with **My User Access** then **Logout** (Users/Roles/Groups are not in this menu)
- **My User Access** bundle cards (`openshift`, `settings` only) with expandable permission rows
- Users list default **Status: Active** filter; user detail by **username**; invalid-user error page
- Roles list **Create role** button; detail by **UUID** with permissions sub-table
- Groups list with **Members** column; group detail **Roles / Members** tabs; nested role drill-down
- Skeleton loading states on all IAM list pages during API fetch

**Key additions from senior QE review** (2026-06-03):
- State synchronization tests (D-11, E-07, H-08, H-10–H-11) for bidirectional IAM ↔ Cost integration
- UI-layer security tests (N-01, N-02, N-06, N-08) for XSS / search injection / session cookie
- Risk assessment & mitigation matrix
- Test data management strategy with cleanup scripts
- Acceptance criteria → test case traceability
- Refined defect severity with escalation policy
- Automation roadmap with timelines and tooling decisions

---

## Scope

Federated IAM/RBAC UI embedded in `koku-ui-onprem` (Scalprum MFE), served from `/rbac/` static assets, IAM routes under `/iam/*`, API via same-origin `/api/rbac/` through Envoy gateway.

### Traceability

| Source | Relevance |
|--------|-----------|
| [COST-7570](https://redhat.atlassian.net/browse/COST-7570) | Auth/authz migration epic |
| [COST-7632](https://redhat.atlassian.net/browse/COST-7632) | RBAC UI story |
| [COST-7654](https://redhat.atlassian.net/browse/COST-7654) | MFE POC acceptance criteria |
| [COST-7589](https://redhat.atlassian.net/browse/COST-7589) | On-prem UX scope (stripped SaaS) |
| [FLPATH-3551](https://redhat.atlassian.net/browse/FLPATH-3551) / [COST-7571](https://redhat.atlassian.net/browse/COST-7571) | Parent test plans |
| [PR #175](https://github.com/insights-onprem/cost-onprem-chart/pull/175) | Nginx `location /rbac/` |
| [PR #173](https://github.com/insights-onprem/cost-onprem-chart/pull/173) | Gateway JWT + IAM API automation |
| Lab (`rbac-test.qe.lab.redhat.com`) | Live UI + `/api/rbac/v1/` reviewed 2026-08-27 |

### IAM route map

| Surface | URL pattern | Notes |
|---------|-------------|-------|
| My User Access | `/iam/my-user-access?bundle={openshift\|settings}` | Entry via masthead **user menu**; not in IAM sidebar; **no RHEL bundle** on-prem |
| Users list | `/iam/user-access/users` | Default chip `Active` is present; lab principals currently render as **Inactive** (chip does not hide them) |
| User detail | `/iam/user-access/users/detail/{username}` | Username-based, not UUID |
| Invalid user | `/iam/user-access/users/detail/{username}` | Breadcrumb: `Users > Invalid user` |
| Roles list | `/iam/user-access/roles` | **Create role** button in toolbar |
| Role detail | `/iam/user-access/roles/detail/{uuid}` | Permissions sub-table + app filter |
| Groups list | `/iam/user-access/groups` | **Create group** button in toolbar |
| Group detail (roles) | `/iam/user-access/groups/detail/{uuid}/roles` | Tabs: **Roles**, **Members** |
| Group role detail | `/iam/user-access/groups/detail/{groupUuid}/roles/detail/{roleUuid}` | Nested drill-down from group |

### IAM sidebar structure (latest UI)

There are **two** shell entry points into IAM-related UI. They expose **different** surfaces:

| Entry point | Where | Opens |
|-------------|-------|-------|
| **Sidebar IAM expandable** | Global nav → **Identity and Access Management** | **Users**, **Roles**, **Groups** (admin/org IAM management) |
| **User menu (masthead)** | Upper-right control showing logged-in identity (e.g. `admin@cost-onprem-chart.test`) | **My User Access**, then **Logout** |

#### Sidebar — Identity and Access Management

IAM lives in the **Global** sidebar (`nav[aria-label="Global"]`) as a PatternFly **NavExpandable** labeled **Identity and Access Management**. Expanding it reveals a nested `ul.pf-v6-c-nav__list` (subnav) with:

- **Users** → `/iam/user-access/users`
- **Roles** → `/iam/user-access/roles`
- **Groups** → `/iam/user-access/groups`

**Not** in the IAM expandable: IAM Overview, My User Access.

#### User menu — masthead dropdown

The masthead user control displays the authenticated identity (username / email form such as `user@realm-or-domain`). Opening it shows:

1. **My User Access** → `/iam/my-user-access` (personal roles / bundles)
2. **Logout**

**Not** in the user menu: Users, Roles, Groups (those remain sidebar-only).

**Playwright note:** Primary Cost nav is `nav[aria-label="Global"] > ul.pf-v6-c-nav__list`. The IAM subnav is a second nested `ul.pf-v6-c-nav__list` under `section.pf-v6-c-nav__subnav` — avoid bare `ul.pf-v6-c-nav__list` selectors (strict-mode ambiguity).

---

## 1. Objectives

1. Verify the RBAC MFE loads reliably inside the Cost shell without a second hostname or re-authentication.
2. Verify all IAM surfaces (My User Access, Users, Roles, Groups) render and function against `/api/rbac/v1/`. There is **no** IAM Overview page.
3. Verify on-prem UX constraints: no SaaS-only flows (e.g. Invite Users), IDP-managed users, group-centric permissions.
4. Verify RBAC enforcement is reflected in UI (personas see appropriate data/actions).
5. Provide regression coverage for MFE asset delivery (`/rbac/`), routing (`/iam/*`), and gateway auth.

---

## 2. Out of scope

- Full LDAP/AD federation setup (covered by [COST-7601](https://redhat.atlassian.net/browse/COST-7601) unless UI-specific).
- insights-rbac backend-only Django shell administration.
- Cost report data correctness (covered by existing Koku e2e; cross-check only where RBAC affects visibility).
- Multi-cluster federation scenarios (future work).
- Mobile/tablet responsive UI testing (desktop browsers only).
- Accessibility / WCAG compliance (keyboard nav, screen readers, color contrast, axe).
- Generic Cost UI login/logout/session (Keycloak redirect, valid/invalid credentials, Cost-only session persist, `/logout`, back-button, oauth2-proxy cookie) — already in `tests/suites/ui/test_login_flow.py` and `tests/suites/ui/test_logout_flow.py` (**UI**, Cost shell — not IAM).
- Gateway JWT / RBAC API authz (401/403, fail-closed, org_id, revocation, IAM reader) — already in `tests/suites/auth/test_rbac_gateway.py` and `tests/suites/auth/test_gateway_auth.py` (**API**).
- Persona cost-report isolation — already in `tests/suites/e2e/test_rbac_access.py` (**API**).
- RBAC authorization performance (latency, cache, concurrency, multi-org, replica scaling, ingestion load) — covered by [COST-7643](https://redhat.atlassian.net/browse/COST-7643) / `tests/suites/performance/test_rbac_perf.py` (**API**).

---

## 3. Environment prerequisites

| Requirement | Detail |
|-------------|--------|
| **Cluster** | CoP deployed (`cost-onprem` ns), gateway + UI + Keycloak + insights-rbac |
| **Keycloak** | `deploy-rhbk.sh`; `roles` client scope on `cost-management-ui`; realm `kubernetes` |
| **UI image** | `koku-ui-onprem` with RBAC MFE baked in (`/rbac/plugin-manifest.json` → `insightsRbac`) |
| **Chart** | nginx `location /rbac/` in `cost-onprem/templates/ui/nginx-config.yaml` (PR #175) |
| **DNS/hosts** | `cost-onprem-ui-cost-onprem.apps.<cluster>` → ingress IP |
| **Test users** | Personas from Section 4, mapped to Keycloak users per environment (`tests/rbac_keycloak_users.py` + e2e bootstrap). Usernames are **not fixed** across dev vs cluster. New users: follow **§13 User provisioning** |
| **RBAC seed data** | Groups on lab: CI Test Admin, Cost Admin Default, Default access, Gateway RBAC IAM Readers, RBAC Payment Team, RBAC Cluster Alpha Ops, RBAC Cost Admins (`tests/rbac_bootstrap_scripts.py` + `tests/suites/e2e/test_rbac_access.py`) |
| **Wait policy** | **≥ 3–5 s** after IAM route change before asserting content (MFE lazy-load) |
| **Browser matrix** | Chrome 120+, Firefox 115+, Edge 120+ (primary testing on Chrome) |
| **Seed data version** | Chart/e2e bootstrap scripts (no `rbac-seed.yaml` in-repo) |

---

## 4. Test personas

| Persona | Expected IAM access | Expected Cost data scope |
|---------|---------------------|--------------------------|
| **admin** | Full IAM admin (create/edit groups, manage roles) | All test clusters/projects |
| **carol** | Cost Administrator group | All RBAC test clusters |
| **alice** | Payment team | `payment` project only |
| **bob** | Cluster Alpha ops | `cluster-alpha` only |
| **viewer** | Read-only / limited | Per seeded roles |
| **nobody-unassigned** | No cost/RBAC app access | Denied on cost reports |
| **rbac-iam-admin** | IAM read (principals/groups list) | No cost admin write |

### Persona vs. username conventions

Test steps use **personas** (roles/capabilities), not environment-specific Keycloak usernames.

- Reference personas by name from the table above (e.g. **admin**, **alice**, **viewer**).
- The UI header, profile menu, and `/users/detail/{username}` URLs must match the **actual username** of the logged-in persona for that run.
- Do **not** assume `dev-user` or any other fixed username unless the persona is explicitly named in the step.
- Map personas to Keycloak credentials per environment (chart bootstrap, `tests/rbac_keycloak_users.py`, lab Keycloak); record the mapping in test run notes.
- Usernames vary by environment — map personas to Keycloak credentials in test run notes.

---

## 5. Test strategy layers

| Layer | Kind | Tooling | Purpose |
|-------|------|---------|---------|
| **L1 — Static/MFE delivery** | Infra / UI | curl, ConfigMap, DevTools | `/rbac/plugin-manifest.json`, chunks 200 |
| **L2 — API/gateway** | **API** | `tests/suites/auth/test_rbac_gateway.py` | JWT → `/api/rbac/v1/*` authz. **Not UI.** |
| **L3 — UI navigation & render** | **UI** | Manual + Playwright | Shell ↔ IAM routing, sidebar |
| **L4 — IAM functional CRUD** | **UI** | Manual + future `test_rbac_iam.py` | Groups/Roles/Users workflows |
| **L5 — RBAC enforcement** | **API** (mapped) + **UI** remainder | `test_rbac_access.py` for cost scope; UI only for chrome/workflows | Persona isolation is API; UI checks empty/denied **pages** only where called out |
| **L6 — Negative/security** | **UI** error chrome; **API** fail-closed / JWT | Manual UI + gateway tests | Do not re-test API 401/403 in the browser |

RBAC authorization performance is **API**, not UI. It is already automated in `tests/suites/performance/test_rbac_perf.py` (COST-7643). See Section L.

### Existing `tests/` coverage (do not duplicate)

| Path | Layer | This plan |
|------|-------|-----------|
| `tests/suites/ui/test_login_flow.py` | UI | A-01, Cost session persist — mapped |
| `tests/suites/ui/test_logout_flow.py` | UI | Cost logout / back button / cookie — mapped (A-03 Cost half) |
| `tests/suites/ui/test_navigation.py` | UI | Cost Overview/OCP/Explorer/Settings only. Selector convention = C-14. **No IAM pages.** |
| `tests/suites/ui/test_data_validation.py`, `test_optimizations.py`, `test_sources.py` | UI | Cost data / sources — **out of scope** |
| `tests/suites/auth/test_rbac_gateway.py` | API | Section I; H-04 / H-05 / H-09; J-01 API half |
| `tests/suites/auth/test_gateway_auth.py` | API | JWT 401/200 on ingress/koku status — not IAM chrome |
| `tests/suites/auth/test_ui_oauth.py` | API | Password-grant JWT claims + `org-admin` realm role — not masthead |
| `tests/suites/auth/test_org_admin_identity.py` | API | `GET /rbac/v1/access/` permissions — not G-01 badge |
| `tests/suites/auth/test_keycloak.py` | API | OIDC discovery / token shape |
| `tests/suites/e2e/test_rbac_access.py` | API | H-01–H-04 persona cost scope |
| `tests/suites/performance/test_rbac_perf.py` | API | Section L / COST-7643 |
| `tests/suites/helm/*` | Infra | Keycloak-sync CronJob, rbac-api Valkey env — not IAM MFE |
| `tests/suites/api/*`, `cost_management/*`, `infrastructure/*`, `ros/*`, `interpod/*` | API / Infra | Cost pipeline — **out of scope** |

There is **no** `tests/suites/ui/test_rbac_iam.py`. That is the remaining UI automation gap.

---

## 6. Test cases

### A. Authentication & session (RBAC-UI-AUTH)

Generic Cost UI login, unauthenticated redirect, Cost-only session persistence, and logout are already automated. Do **not** re-implement them as RBAC UI cases.

| Existing ID | Automated test | Coverage |
|-------------|----------------|----------|
| A-01 | `test_ui_redirects_to_keycloak` (`test_login_flow.py`) | Unauthenticated `ui_url` → Keycloak login form. oauth2-proxy wraps the whole UI host, so an unauthenticated `/iam/*` paste is the same redirect — not a separate case. Also: `test_successful_login`, `test_invalid_credentials_shows_error` |
| — | `test_session_persists_across_navigation`, `test_can_access_protected_routes` | Cost-only session (root URL and `/recommendations`). Does **not** cover Cost ↔ IAM |
| A-03 (Cost) | `test_logout_flow.py` | `/logout` → Keycloak; session invalidated on root URL; back button; `_oauth2_proxy` cookie cleared; unauthenticated/double logout |
| A-05 | **J-03** (browser) + **I-05** `test_expired_jwt_rejected` (API) | Idle Keycloak timeout in the UI is J-03. Forged/expired JWT at the gateway is I-05. No third A case |
| — | `test_ui_oauth.py`, `test_org_admin_identity.py` | Admin vs viewer **JWT** `org-admin` role — not masthead username chrome |

Remaining IAM-specific cases (all **UI**; not covered above):

| ID | Title | Steps | Expected | Pri | Layer |
|----|-------|-------|----------|-----|-------|
| A-02 | Single session across Cost ↔ IAM | Login as **admin** persona → Cost Overview → expand IAM → Groups → Cost Overview | No second login; same user in header as logged-in persona. C-09 covers nav freeze only; this case is **no re-auth** (COST-7654 AC-6) | P0 | UI |
| A-03 | Logout invalidates IAM deep-link | Logout (user menu or `/logout`) → paste `/iam/user-access/groups` | Redirect to Keycloak login; no cached IAM table. Back-button and root-URL post-logout already in `test_logout_flow.py` — do not re-test those | P1 | UI |
| A-04 | Viewer vs admin header identity | Login as **viewer** then **admin** personas (separate sessions) | Masthead shows the correct username/email for each persona. C-02b is the admin chrome existence check only | P2 | UI |

### B. MFE delivery & infrastructure (RBAC-UI-INFRA)

B-06 is **API** (`GET /rbac/v1/status/` unauthenticated 401 is I-01; authenticated 200 is gateway smoke in `test_gateway_auth.py` / `test_rbac_gateway.py`). Do not add a UI case for API health.

| ID | Title | Steps | Expected | Pri | Layer |
|----|-------|-------|----------|-----|-------|
| B-01 | plugin-manifest served | `GET /rbac/plugin-manifest.json` (authenticated session or from pod) | 200; `name: insightsRbac`, `baseURL: /rbac/`, `loadScripts` includes `plugin-entry.js` | P0 | Infra |
| B-02 | MFE entry script loads | DevTools Network: load IAM page | `plugin-entry.js` + federated chunks 200, no 404 | P0 | UI |
| B-03 | Nginx `/rbac/` alias | Verify ConfigMap `location /rbac/` | alias `/opt/app-root/src/rbac/`; sample bundle 200 from pod. Also `X-Frame-Options SAMEORIGIN` (N-04) | P0 | Infra |
| B-04 | No second UI route required | Confirm only `cost-onprem-ui` route used for IAM | IAM works on same hostname (COST-7654 AC) | P0 | UI |
| B-05 | API same origin | DevTools: IAM page XHR/fetch | Calls go to `/api/rbac/v1/...` (via UI proxy/gateway), not external SaaS | P0 | UI |
| B-07 | MFE load timing | Navigate to Groups; snapshot at 0s, 2s, 5s | Content visible by ≤5s under normal load; fail if >10s (p95); record observed time in run notes | P0 | UI |
| B-08 | Browser resource consumption | DevTools Performance: load IAM → Groups → Users → Roles | Memory <500MB, CPU <80% sustained, no memory leaks on 10 navigation cycles | P2 | UI |

### C. Navigation & routing (RBAC-UI-NAV)

All rows are **UI**. Expand-role-permissions (former C-11) is **G-05** — do not duplicate. Bundle **role content** is G-02/G-03; C-06/C-07 only assert URL + heading. Cost-page nav is `test_navigation.py` — do not re-test Overview/OCP/Explorer here.

| ID | Title | Steps | Expected | Pri | Layer |
|----|-------|-------|----------|-----|-------|
| C-01 | IAM expandable label in Global nav | Login as **admin**; open Cost Overview; inspect Global sidebar | Expandable labeled **Identity and Access Management** is visible in `nav[aria-label="Global"]` (top-level item alongside Overview/Settings); **not** nested under Cost **Settings** | P0 | UI |
| C-01a | Expand IAM section | Click **Identity and Access Management** expandable toggle | Section expands; nested subnav shows exactly **Users**, **Roles**, **Groups** (no Overview / My User Access in subnav) | P0 | UI |
| C-01b | Collapse IAM section | With IAM expanded, click expandable toggle again | Subnav hides; Users/Roles/Groups links not visible; expandable still present | P1 | UI |
| C-02 | Route: My User Access via user menu | Open masthead **user menu** → click **My User Access**; wait 4s | URL `/iam/my-user-access?bundle=openshift`; heading "My User Access"; settles on OpenShift. Badge/cards/roles: G-01, G-04, G-02 | P0 | UI |
| C-02a | My User Access not in IAM expandable | Expand **Identity and Access Management** | Subnav does **not** include My User Access; item is only in the user menu (above Logout) | P0 | UI |
| C-02b | Masthead shows logged-in identity | Login as **admin** (or any persona); inspect upper-right masthead | User control visible with authenticated identity text (e.g. `admin@cost-onprem-chart.test` / `user@…`); control is clickable | P0 | UI |
| C-02c | User menu contents | Click masthead user control | Dropdown opens with exactly **My User Access** then **Logout** (in that order); no Users / Roles / Groups entries | P0 | UI |
| C-02d | Users/Roles/Groups are sidebar-only | Open user menu; also expand IAM sidebar | Users / Roles / Groups appear **only** under Identity and Access Management expandable; **absent** from user menu | P0 | UI |
| C-02e | Logout still available from user menu | Open user menu → confirm **Logout** | Logout **item visible** below My User Access. Session teardown is A-03 + `test_logout_flow.py` — this row is chrome only | P0 | UI |
| C-03 | Route: Groups via expandable | Expand IAM → click **Groups**; wait 4s | URL `/iam/user-access/groups`; table columns **Select**, **Name**, **Roles**, **Members**, **Last modified**, **Actions**; **Create group** button; **Filter by name**. Group **data** is D-01 | P0 | UI |
| C-04 | Route: Users via expandable | Expand IAM → click **Users**; wait 4s | URL `/iam/user-access/users`; columns Org. Administrator, Username, Email, First name, Last name, Status. Invite-user absence is E-05; list **data** is E-01 | P0 | UI |
| C-05 | Route: Roles via expandable | Expand IAM → click **Roles**; wait 4s | URL `/iam/user-access/roles`; columns Select, Name, Description, Groups, Permissions, Last modified, Actions; **Create role** button. Role **data** is F-01 | P0 | UI |
| C-05a | IAM subnav persists across leaf pages | Expand IAM → Users → Roles → Groups | Expandable stays expanded; all three leaf links remain available without re-expanding | P1 | UI |
| C-06 | Bundle: OpenShift route | My User Access → select **OpenShift** card | URL `?bundle=openshift`; heading "Your OpenShift roles". Which roles appear: **G-02** | P0 | UI |
| C-07 | Bundle: Settings route | My User Access → select **Settings and User Access** card | URL `?bundle=settings`; heading "Your Settings and User Access roles". Which roles appear: **G-03** | P0 | UI |
| C-08 | No RHEL card; unparameterized deep link | (1) User menu → My User Access. (2) Paste `/iam/my-user-access` with **no** query. (3) Paste `?bundle=rhel` | (1) Default entry is **OpenShift**. (2–3) **No RHEL card**. Bare URL still hydrates SaaS leftover "Your Red Hat Enterprise Linux roles" (COST-8160). Absorbs former K-03 | P0 | UI |
| C-09 | Cost → IAM → Cost | Overview → expand IAM → Groups → OpenShift Costs | No nav freeze (max 2s delay); no JS console errors; shell responsive. **No re-auth** is A-02 | P0 | UI |
| C-10 | Deep link Groups | Paste `/iam/user-access/groups` while logged in | Page loads with group table after wait; IAM expandable expanded | P1 | UI |
| C-10a | Deep link Users | Paste `/iam/user-access/users` while logged in | Users list loads; IAM expandable expanded | P1 | UI |
| C-10b | Deep link Roles | Paste `/iam/user-access/roles` while logged in | Roles list loads; IAM expandable expanded | P1 | UI |
| C-12 | Invalid IAM route | Navigate `/iam/nonexistent` | Graceful 404 or redirect, no white-screen crash | P2 | UI |
| C-13 | Browser back/forward | Navigate Groups → Users → back → forward | Correct page state restored | P2 | UI |
| C-14 | Primary vs IAM nav selectors | While on Overview with IAM expanded | Primary list (`nav[aria-label="Global"] > ul.pf-v6-c-nav__list`) and IAM subnav (`section.pf-v6-c-nav__subnav > ul.pf-v6-c-nav__list`) are distinct. Already used in `test_navigation.py` — keep as IAM automation constraint | P0 | UI |

### D. Groups (RBAC-UI-GRP)

All rows are **UI**. Viewer-cannot-create (D-12) absorbs former **J-04**. Do not re-test gateway `POST /rbac/v1/groups/` 403 (that is H-05 / I-04 **API**).

| ID | Title | Steps | Expected | Pri | Layer |
|----|-------|-------|----------|-----|-------|
| D-01 | List groups | Admin → Groups | Shows **Cost Admin Default** (1 role, Members: All org admins, info icon) and **Default access** (6 roles, Members: All, info icon); also CI Test Admin, Gateway RBAC IAM Readers, RBAC Cluster Alpha Ops, RBAC Cost Admins, RBAC Payment Team | P0 | UI |
| D-02 | Group detail — Roles tab | Click **Cost Admin Default** | URL `/iam/user-access/groups/detail/{uuid}/roles`; breadcrumbs `Groups > Cost Admin Default`; description visible; **Roles** tab active | P0 | UI |
| D-03 | Group detail — Members tab | Group detail → click **Members** tab | URL `/iam/user-access/groups/detail/{uuid}/members`; member list loads | P0 | UI |
| D-04 | Group role drill-down | Group Roles tab → click **Cost Administrator** | URL `.../groups/detail/{uuid}/roles/detail/{roleUuid}`; permissions table: Application, Resource type, Operation, Resource definitions, Last modified | P0 | UI |
| D-05 | Create group | Admin → **Create group** → name + description → save | Success toast; group appears in list | P0 | UI |
| D-06 | Edit group | Edit existing test group description | Persists after refresh | P1 | UI |
| D-07 | Add member to group | Add **alice** persona to a test group via Members tab | Member count updates; alice's effective permissions change | P0 | UI |
| D-08 | Remove member | Remove member from test group | Reflected in list and in Cost report scope for that user | P0 | UI |
| D-09 | Assign role to group | Attach Cost role to group via UI | Role count updates on list page; `/api/rbac/v1/access/` reflects change | P0 | UI |
| D-10 | Delete group (non-system) | Delete a user-created test group | Removed from list; API 404 | P1 | UI |
| D-11 | Platform default group | View **Default access** | Visible; Members = "All"; destructive actions restricted or warned | P1 | UI |
| D-12 | Non-admin denied create | Login as **viewer** persona → Groups | Create group hidden or UI shows 403; no silent success. Absorbs former J-04. IAM-reader POST 403 is **API** (I-04) | P0 | UI |
| D-13 | Filter groups by name | Enter name in **Filter by name** | Table narrows to matching groups | P1 | UI |
| D-14 | Roles count display (not a link) | On Groups list, inspect **Roles** column for **Default access** (shows **6**) and a custom group (e.g. RBAC Payment Team) | Count displays the correct number; count is **not** a navigation link (PatternFly may expose it as a button in the a11y tree, but clicking does not change URL). Navigation to group Roles tab is **D-02** (click group **name**). Members column may expand inline for non-default groups only | P1 | UI |

### E. Users (RBAC-UI-USR)

All rows are **UI**. Invalid-user page (E-04) absorbs former **J-12**. No Invite Users (E-05) absorbs former **K-02**. Principals list 200 is **API** (`test_gateway_rbac_principals_iam_reader_returns_200`) — this section is chrome/copy/filters only. Provisioning E-11 absorbs former **K-05**.

| ID | Title | Steps | Expected | Pri | Layer |
|----|-------|-------|----------|-----|-------|
| E-01 | List users | Admin → Users | Table with columns: Org. Administrator, Username, Email, First name, Last name, Status; skeleton loaders during fetch | P0 | UI |
| E-02 | Default Active filter | Load Users page | **Active** filter chip + **Clear filters** visible. **Live gap:** lab users all show **Inactive** and still appear (chip does not filter them out) | P0 | UI |
| E-03 | User detail | Click a username link from the Users list | URL `/iam/user-access/users/detail/{username}`. **Live gap (2026-08-27):** listed users (e.g. `admin`) deep-link to "User not found" even though `/api/rbac/v1/principals/?usernames=admin` returns 200 — likely SaaS IT-user lookup, not local principals | P0 | UI |
| E-04 | Invalid user detail | Navigate `/iam/user-access/users/detail/{nonexistent-username}` | Breadcrumb `Users > Invalid user`; heading "User not found"; message "User with username {username} does not exist."; **Back to previous page** button. Absorbs former J-12 | P0 | UI |
| E-05 | No Invite Users (on-prem) | Scan Users page actions | **No** "Invite user" button; copy directs to external **user management list** link. Absorbs former K-02 | P0 | UI |
| E-06 | IDP-managed users note | Read Users page intro copy | "These are all of the users in your Red Hat organization… go to your user management list" | P1 | UI |
| E-07 | Filter by username | Use **Username** dropdown + "Filter by username" search | Table narrows to matching users | P1 | UI |
| E-08 | Org Administrator column | Inspect Org. Administrator column | Lab shows **No** for every principal including **admin** (org-admin is a Keycloak realm role, not an insights-rbac principal flag). Do not require a checkmark for admin. Org-admin **badge** is G-01; JWT role is **API** (`test_org_admin_identity.py`) | P1 | UI |
| E-09 | Status badges | Inspect Status column | Lab shows **Inactive** for all listed users. Do not require **Active** badges unless Keycloak→RBAC sync starts sending `is_active` | P1 | UI |
| E-10 | Service account visibility | Clear filters → locate a service account from seed data | Listed; appropriate read-only UI | P2 | UI |
| E-11 | Provision new IDP user end-to-end | Follow **§13 User provisioning (Keycloak → COS IAM)** to create `<username>` with scoped access (e.g. add to an existing persona group such as Payment Team). Complete all three steps including first CoP login | User exists in Keycloak `kubernetes` realm; listed as member on assigned IAM group; appears in IAM **Users** after first CoP login; cost scope matches group role. Absorbs former K-05 | P0 | UI |

### F. Roles (RBAC-UI-ROL)

All rows are **UI**. Create-role **button** is part of F-01 and C-05 — former F-02 removed. **F-06** requires **§13 Custom role creation** setup (`rbac.roleCreateAllowList`); skip F-06 on default chart installs where the Add permissions picker is intentionally empty.

| ID | Title | Steps | Expected | Pri | Layer |
|----|-------|-------|----------|-----|-------|
| F-01 | List roles | Admin → Roles | Columns: **Select**, Name, Description, **Groups**, **Permissions**, Last modified, **Actions**; **Create role** in toolbar; lab has 24 roles including Local Test leftovers | P0 | UI |
| F-03 | Role detail | Click any role from the list | URL `/iam/user-access/roles/detail/{uuid}`; breadcrumb `Roles > {name}`; description; permissions table | P0 | UI |
| F-04 | Role permissions table | Role detail page | Columns: Application, Resource type, Operation, Last modified; filter by **Applications** | P0 | UI |
| F-05 | Cost Administrator role | Open **Cost Administrator** detail | Application `cost-management`; Resource type `*`; Operation `*` | P0 | UI |
| F-06 | Custom role create | **Prereq:** complete **§13 Custom role creation** (`roleCreateAllowList` set). As **admin** → **Create role** → from scratch or copy → **Add permissions** → select ≥1 permission → save | Role appears in list; assignable to a group. **Default chart:** Add permissions shows *No permissions* — not a UI defect; configure allow list first | P1 | UI |
| F-07 | Read-only user | **viewer** persona → Roles | List allowed; Create role hidden or UI 403. Viewer Groups create is D-12; IAM-reader POST is **API** I-04 | P1 | UI |
| F-08 | Filter roles by name | **Filter by name** search | Table narrows to matching roles | P1 | UI |

### G. My User Access (RBAC-UI-MUA)

All rows are **UI**. Expand permission row (G-05) absorbs former **C-11**. MUA skeleton (former G-07) is **J-10**. JWT org-admin is **API** (`test_org_admin_identity.py` / `test_ui_oauth.py`) — G-01 is the **badge**.

| ID | Title | Steps | Expected | Pri | Layer |
|----|-------|-------|----------|-----|-------|
| G-01 | Org Administrator badge | Login as **admin** persona (org admin) → My User Access | Purple **Org. Administrator** badge next to page title when persona has org-admin flag; absent for non-org-admin personas | P0 | UI |
| G-02 | OpenShift bundle roles | `?bundle=openshift` as **admin** | **Cost Administrator** only (from Cost Admin Default). Local Test viewer roles are **not** assigned to admin. URL/heading: C-06 | P0 | UI |
| G-03 | Settings bundle roles | `?bundle=settings` as **admin** | **User Access principal viewer** (Default access). Sources administrator / User Access administrator are **not** assigned. URL/heading: C-07 | P0 | UI |
| G-04 | Bundle card UI | Inspect bundle selector cards | Exactly **two** cards: **OpenShift** and **Settings and User Access**; **no RHEL**. Deep-link leftover: C-08 | P0 | UI |
| G-05 | Expand permission row | Expand a role row | Sub-table: Application, Resource type, Operation, Resource definitions. Absorbs former C-11 | P0 | UI |
| G-06 | Role name filter | **Filter by role name** search | Table narrows within active bundle | P1 | UI |
| G-08 | Alice scoped roles | **alice** persona → My User Access | Only roles tied to payment team / limited scope. Cost **report** scope is **API** H-01 | P0 | UI |

### H. RBAC enforcement through UI (RBAC-UI-ENF)

Persona **cost-report** isolation is **API**. Do not re-implement H-01–H-05 or H-09 as Playwright.

| Existing ID | Layer | Automated test |
|-------------|-------|----------------|
| H-01 Alice cost scope | API | `test_alice_*` in `test_rbac_access.py` |
| H-02 Bob cost scope | API | `test_bob_*` |
| H-03 Carol full scope | API | `test_carol_*` |
| H-04 Nobody denied cost | API | `test_no_rbac_user_denied`, `test_gateway_openshift_costs_user_without_rbac_returns_403` |
| H-05 IAM reader list/write | API | `test_gateway_rbac_principals_iam_reader_returns_200`, `test_gateway_rbac_groups_post_iam_reader_forbidden` (I-03 / I-04). UI chrome for viewer create is D-12 |
| H-09 API revocation | API | `test_permission_revocation_honored_after_cache_clear` (I-07). UI-driven revoke is H-06 |
| H-12 Nested groups | — | **N/A** — not in the live IAM UI |

Remaining **UI** cases:

| ID | Title | Steps | Expected | Pri | Layer |
|----|-------|-------|----------|-----|-------|
| H-06 | Permission change propagation | Remove **alice** persona from Payment Team in **IAM UI** → alice refreshes costs | Payment data disappears within cache TTL (300s documented); verify timestamp. API-only revoke is H-09 — this row is the UI workflow | P1 | UI |
| H-07 | Admin sees all IAM | **admin** persona → Users/Groups | Full list **renders** in UI; counts may be checked against API but the case is the page, not a new gateway test | P1 | UI |
| H-08 | Role assignment takes effect immediately | Create new group → assign Cost role → add user → user already logged in refreshes | Cost data scope reflects new role without logout/login | P0 | UI |
| H-10 | New group workflow E2E | Admin creates group → assigns Cost role → adds member (member must exist in Keycloak per **§13**) → member logs in | Cost reports show correct data scope on first login. First-login Users list is E-11 | P0 | UI |
| H-11 | Keycloak sync to RBAC | Delete user in Keycloak → IAM Users list | User marked deleted/inactive within documented sync interval (specify TTL) | P1 | UI |

### I. API / gateway alignment (RBAC-UI-API)

**All of Section I is API.** Map to existing `test_rbac_gateway.py` / PR #173. Do **not** file UI tickets for these.

| ID | Layer | Automated test |
|----|-------|----------------|
| I-01 | API | `test_gateway_rbac_*_unauthenticated_returns_401` (includes `/rbac/v1/status/` — former B-06 unauth) |
| I-02 | API | `test_gateway_*_user_without_rbac_returns_403` |
| I-03 | API | `test_gateway_rbac_principals_iam_reader_returns_200` |
| I-04 | API | `test_gateway_rbac_groups_post_iam_reader_forbidden` |
| I-05 | API | `test_expired_jwt_rejected` |
| I-06 | API | `test_org_id_tenant_isolation_boundary_cases` (also covers N-05 / N-07 class of injection against org_id) |
| I-07 | API | `test_permission_revocation_honored_after_cache_clear` |
| I-08 | API | `test_concurrent_jwt_sessions_no_resource_exhaustion` |
| I-09 | API | `test_jwt_without_required_claims_rejected` |
| I-10 | API | `test_rbac_service_unavailable_denies_access_fail_closed` (cost reports — **not** UI Groups error chrome; UI remainder is J-01) |
| I-11 | API | `test_rbac_iam_reader_cannot_modify_own_permissions` |
| I-12 | API | `test_rbac_cache_ttl_configuration_exists` |
| I-13 | Infra | `test_rbac_migration_job_completed` |

See also: `tests/suites/auth/RBAC_SECURITY_TESTS.md`

### J. Negative, error handling & resilience (RBAC-UI-NEG)

All remaining rows are **UI** error chrome. Former J-04 → D-12. Former J-12 → E-04. API fail-closed / JWT expiry stay in Section I.

| ID | Title | Steps | Expected | Pri | Layer |
|----|-------|-------|----------|-----|-------|
| J-01 | RBAC API down | Scale rbac-api to 0 → open Groups | Error state **in the Groups page** (not infinite spinner). API cost-report fail-closed is I-10 — do not re-assert HTTP status here | P0 | UI |
| J-02 | Missing `/rbac/` nginx block | Remove location /rbac/ → reload IAM | Clear error in console; graceful degradation message | P1 | UI |
| J-03 | Stale session | Expire Keycloak session → click IAM | Redirect login, no partial stale tables. Absorbs former A-05. API JWT expiry is I-05 | P1 | UI |
| J-05 | Large list pagination | Groups/Users with full seed set (1000+ users) | Pagination works; page load <5s; memory <500MB; no browser hang | P2 | UI |
| J-06 | Network interruption during create | Create group → kill network mid-request → restore | Retry logic or clear error message; no duplicate groups | P1 | UI |
| J-07 | Keycloak restart mid-session | Active IAM session → restart Keycloak pod → continue workflow | Graceful re-auth; no data loss in form fields | P2 | UI |
| J-08 | Gateway timeout on slow query | Trigger slow RBAC query (e.g., 1000+ users) → gateway 504 | User-friendly timeout message; no infinite spinner | P1 | UI |
| J-09 | Partial API failure | RBAC API returns 500 on `/users/` but 200 on `/groups/` | Graceful degradation; error shown for Users tab only | P2 | UI |
| J-10 | Skeleton loading states | Navigate Users / Roles / Groups / switch MUA bundle | PatternFly skeleton rows shown during fetch; no permanent blank pane. Absorbs former G-07 | P0 | UI |
| J-11 | Blank MFE on fast nav | Navigate to `/iam/my-user-access` without wait | Content may be blank briefly; resolves within 5s (document as timing risk) | P1 | UI |

### K. UX / on-prem SaaS parity gaps (RBAC-UI-UX)

Per [COST-7589](https://redhat.atlassian.net/browse/COST-7589) / [COST-7632](https://redhat.atlassian.net/browse/COST-7632). Remaining rows are **UI** checks that are not already a dedicated C/E/G case. Former K-02 → E-05. Former K-03 → C-08. Former K-05 → E-11.

| ID | Check | Expected | Layer |
|----|-------|----------|-------|
| K-01 | No console.redhat.com links | No external SaaS dependencies in network tab (except documented user management list link) | UI |
| K-04 | Group-centric assignment | Primary workflow is group ↔ role ↔ members (covered in detail by D-*); this row is the on-prem UX assertion only | UI |
| K-06 | Local test role naming | Roles suffixed "Local Test" acceptable in dev; verify cost-relevant roles present in prod builds (list chrome is F-01) | UI |

### L. Performance & load testing — **API**, covered by COST-7643

RBAC authorization performance is already implemented and validated in `tests/suites/performance/test_rbac_perf.py` ([COST-7643](https://redhat.atlassian.net/browse/COST-7643)). Do **not** duplicate these cases in the UI suite.

| Existing ID | Automated test | Coverage |
|-------------|----------------|----------|
| PERF-RBAC-001 | `test_perf_rbac_001_baseline_isolation` | RBAC share of Koku API latency |
| PERF-RBAC-002 | `test_perf_rbac_002_cache_effectiveness` | Cold vs warm Valkey cache |
| PERF-RBAC-003 | `test_perf_rbac_003_concurrent_auth` | Concurrent authorization load |
| PERF-RBAC-004 | `test_perf_rbac_004_multi_org_scaling` | Multi-org scaling |
| PERF-RBAC-005 | `test_perf_rbac_005_replica_scaling` | 1→2→3 RBAC API replicas under load |
| PERF-RBAC-006 | `test_perf_rbac_006_under_ingestion` | RBAC latency while ingestion is active |

```bash
./scripts/deploy-test-cost-onprem.sh --perf-only --perf-profile medium --perf-suite rbac
```

UI-only smoke timing remains in **B-07** (MFE visible ≤5s) and **B-08** (browser memory/CPU). Large-list pagination resilience remains in **J-05**. Results: `docs/performance/FINDINGS.md` (FINDING-037) and `docs/performance/performance-testing-plan.md`.

### N. Security testing - UI layer (RBAC-UI-SEC)

CSRF, clickjacking, org_id injection, and path traversal are **API / Infra** — do not re-test them as Playwright.

| Existing ID | Layer | Coverage |
|-------------|-------|----------|
| N-03 CSRF POST | API | Cross-origin `POST /api/rbac/v1/groups/` must fail. On-prem uses JWT/oauth2-proxy (see `test_gateway_auth.py` 401 without token, I-04 write deny). Not a CSRF-cookie test |
| N-04 Clickjacking | Infra | `X-Frame-Options SAMEORIGIN` on `/rbac/` in `cost-onprem/templates/ui/nginx-config.yaml` (same ConfigMap as B-03) |
| N-05 URL `org_id` | API | Tenant comes from JWT; I-06 `test_org_id_tenant_isolation_boundary_cases` |
| N-07 Path traversal | API | I-06 includes `../../../etc/passwd` org_id case; gateway 401/404 on `GET /api/rbac/v1/../../etc/passwd` |

Remaining **UI** cases:

| ID | Title | Steps | Expected | Pri | Layer |
|----|-------|-------|----------|-----|-------|
| N-01 | XSS in group name | Create group with name `<script>alert('xss')</script>` | Name displayed as plain text; script not executed | P0 | UI |
| N-02 | XSS in group description | Create group with description containing HTML/JS | Sanitized display; no script execution | P0 | UI |
| N-06 | SQL injection in **UI search** | Search users with `'; DROP TABLE users; --` | Table empty or no-match; no uncaught exception. org_id SQLi is I-06 **API** | P1 | UI |
| N-08 | Session fixation | Attempt to set session cookie before login | Session regenerated post-login; old cookie invalid. Cookie **clear on logout** is `test_oauth_cookie_cleared_after_logout` | P2 | UI |

---

## 7. Test execution matrix (by phase)

| Phase | Focus | Entry criteria | Exit criteria |
|-------|-------|----------------|---------------|
| **Phase 0 — Smoke** | A-02, B-01–B-05, B-07, C-01–C-01b, C-02–C-02e, C-03–C-07, C-14 (run `test_login_flow.py` / `test_logout_flow.py` first) | Cluster + UI + Keycloak up | All P0 infra/nav/auth pass |
| **Phase 1 — Functional IAM** | D, E, F, G (admin workflows) | Phase 0 pass | CRUD cycles complete without API errors |
| **Phase 2 — Persona enforcement** | H-06–H-08, H-10–H-11 (**UI**). Run **API** `test_rbac_access.py` + `test_rbac_gateway.py` for H-01–H-05 / I | RBAC seed + Keycloak users | UI workflows pass; API persona tests green |
| **Phase 3 — Regression & Automation** | Full automated suite + Playwright | PR #173, #175 merged; automation setup complete | Chart pytest green; Playwright suite passing |
| **Phase 4 — Quality & Hardening** | J, N-01/N-02/N-06/N-08 (**UI**) | Phase 3 pass | No S1/S2 defects. API security is I / `test_gateway_auth.py` |
| **Phase 5 — Release Readiness** | K-01, K-04, K-06; sign-off checklist | Phase 4 pass; all P0/P1 tests executed | Sign-off checklist complete; known limitations documented; automation roadmap approved |

---

## 8. Automation roadmap

### Existing automation to run before each UI test session

```bash
# Gateway/API (validates backend for UI)
pytest tests/suites/auth/test_rbac_gateway.py -v
pytest tests/suites/e2e/test_rbac_access.py -k "password_grant_via_gateway" -v

# Full chart (regression)
export PYTHON=/usr/bin/python3.12
./scripts/deploy-test-cost-onprem.sh --skip-deploy --verbose
```

---

## 9. Defect severity guidelines

| Severity | Criteria | Examples |
|----------|----------|----------|
| **S1 — Critical** | IAM completely unusable; auth loop; data leak across tenants | IAM blank after 10s; cannot list groups for any user; alice sees bob's data |
| **S2 — High (infra)** | Infrastructure failure blocking IAM | 404 on `/rbac/plugin-entry.js`; gateway 502 on all `/api/rbac/` calls |
| **S2 — High (functional)** | Core CRUD broken for admins | Cannot create group (500 error); wrong persona scope in Cost data |
| **S3 — Medium** | Workaround exists; non-admin affected | Pagination breaks on page 10; filter reset on navigation; SaaS copy leftover |
| **S4 — Low** | Cosmetic; no functional impact | Tooltip typo; minor CSS misalignment; console warning (not error) |

**Escalation policy**: S1/S2 defects block release sign-off. S3 requires product owner triage. S4 tracked in backlog.

---

## 10. Sign-off checklist (COST-7654 acceptance)

### Functional Requirements
- [ ] RBAC remote builds; `plugin-manifest.json` under `/rbac/`
- [ ] koku-ui-onprem loads MFE via Scalprum; IAM reachable from Global nav **Identity and Access Management** expandable (Users / Roles / Groups)
- [ ] Masthead user menu shows logged-in identity; menu contains **My User Access** then **Logout** only
- [ ] My User Access reachable from user menu and `/iam/my-user-access` with bundle cards (openshift, settings)
- [ ] Users, Roles, Groups pages load with correct columns, filters, and Create actions
- [ ] Authenticated user completes IAM flows against `/api/rbac/` (same origin)
- [ ] `build:onprem` / chart deploy includes RBAC assets (no regression)
- [ ] No second UI hostname required
- [ ] No re-auth when switching Cost ↔ IAM
- [ ] Persona isolation verified — **API**: `test_rbac_access.py` (H-01–H-04); **UI**: H-10, E-11
- [ ] On-prem UX constraints verified (K-01, K-04, K-06; Invite Users = E-05; bundles = C-08 / G-04)

### Security & Infrastructure
- [ ] Gateway security tests green (PR #173) — **API**
- [ ] Nginx `/rbac/` location deployed (PR #175) — **Infra** B-03
- [ ] XSS rendered as text (N-01–N-02); search injection (N-06). CSRF/clickjack/org_id/path are **API/Infra** mappings
- [ ] No S1/S2 defects open

### Performance & Quality
- [ ] MFE load time <5s (p95); recorded in run notes (B-07)
- [ ] All P0 test cases passed
- [ ] RBAC authorization performance covered by COST-7643 (`test_rbac_perf.py`); not re-run as part of this UI plan

### Documentation & Automation
- [ ] Test data cleanup script validated
- [ ] Known limitations documented (Section 16)
- [ ] Automation roadmap approved (Section 18)
- [ ] AC → test case mapping complete (Section 15)

---

## 11. Recommended Jira structure

Under [FLPATH-3551](https://redhat.atlassian.net/browse/FLPATH-3551) / [COST-7571](https://redhat.atlassian.net/browse/COST-7571), add epics or labels:

- `rbac-ui-auth` (A-*) — Authentication & session management
- `rbac-ui-infra` (B-*) — MFE delivery & infrastructure
- `rbac-ui-nav` (C-*) — Navigation & routing
- `rbac-ui-iam-crud` (D–G) — IAM functional CRUD (Groups, Users, Roles, My User Access)
- `rbac-ui-enforcement` (H-06–H-08, H-10–H-11) — UI workflows only; do not file H-01–H-05 / H-09 as UI
- `rbac-ui-negative` (J-*) — UI error chrome
- `rbac-ui-ux-onprem` (K-01, K-04, K-06)
- `rbac-ui-security` (N-01, N-02, N-06, N-08) — UI XSS/search/session; not CSRF/clickjack/API traversal

Gateway **API** cases (I-*) are covered by FLPATH-4308–4329 / `test_rbac_gateway.py`. RBAC performance is COST-7643. Do not file duplicate UI tickets for API mappings.

---

## 12. Performance benchmarks — covered by COST-7643

Do not maintain a separate RBAC UI performance matrix in this plan. Authorization latency, cache effectiveness, concurrent load, multi-org scaling, replica scaling, and ingestion-under-load are already measured as **PERF-RBAC-001 through PERF-RBAC-006** in `tests/suites/performance/test_rbac_perf.py` ([COST-7643](https://redhat.atlassian.net/browse/COST-7643)).

- Run: `./scripts/deploy-test-cost-onprem.sh --perf-only --perf-profile medium --perf-suite rbac`
- Results: `docs/performance/FINDINGS.md` (FINDING-037)
- Plan: `docs/performance/performance-testing-plan.md`

UI smoke checks that stay in this plan: **B-07** (MFE load ≤5s), **B-08** (browser resource consumption), **J-05** (large-list pagination).

---

## 13. Test data management

### Seed Data Location
- **Chart session bootstrap**: `tests/rbac_bootstrap_scripts.py` (`CI Test Admin` + Cost Administrator for SAs and `admin`)
- **Gateway IAM reader**: `render_rbac_iam_reader_bootstrap_script()` in the same module (`Gateway RBAC IAM Readers`)
- **Persona groups/roles**: `tests/suites/e2e/test_rbac_access.py` (`Cost Admin Default`, `RBAC Payment Team`, `RBAC Cluster Alpha Ops`, `RBAC Cost Admins`)
- **Keycloak users**: `tests/rbac_keycloak_users.py` (`admin`, `viewer`, `alice`/`bob`/`carol`, `nobody-unassigned`, `rbac-iam-admin`)

There is **no** `tests/fixtures/rbac-seed.yaml`, `scripts/reset-rbac-test-data.sh`, or `tests/suites/e2e/fixtures/rbac_personas.py` in this repo.

### Cleanup Policy
- **Prefix convention**: Prefer `TEST-*` for groups/roles created by **manual UI** runs
- **Automated cleanup**: e2e/gateway fixtures tear down what they create; chart bootstrap `CLEANUP_SCRIPT` in `rbac_bootstrap_scripts.py` is for platform-default role detach, not a general UI reset

### Reset Procedure
```bash
# Inspect groups created during a UI session:
kubectl exec -n cost-onprem deploy/insights-rbac -- \
  python manage.py shell -c "from management.models import Group; print([g.name for g in Group.objects.filter(name__startswith='TEST-')])"
```

### User provisioning (Keycloak → COS IAM)

Reference procedure for manual test setup and test cases **E-11**, **H-08**, **H-10**.

Keycloak and COS IAM are **separate systems**:

| System | Purpose |
|--------|---------|
| **Keycloak** (`kubernetes` realm) | Authentication — who can log in |
| **insights-rbac / COS IAM** | Authorization — what they can access in CoP |

Creating a user in Keycloak alone does **not** grant CoP permissions unless the user is also assigned IAM group membership (or carries the `org-admin` realm role for full org admin access).

#### Step 1 — Create user in Keycloak

1. Open the Keycloak **Admin Console** at `/admin` on the cluster Keycloak route.
2. Log in with **master** realm admin credentials (from the `keycloak-initial-admin` secret). This is **not** the CoP application login.
3. Switch the realm dropdown to **`kubernetes`** (CoP authenticates against this realm, not `master`).
4. Navigate to **Users** → **Create user**.
5. Set **Username**, **Email**; enable the user.
6. On the **Attributes** tab, add deployment-required attributes:
   - `org_id` — target organization ID (must match the insights-rbac tenant)
   - `account_number` — customer account identifier
7. On the **Credentials** tab, set a password; disable **Temporary** if the password should persist across logins.

**Keycloak Admin UI notes:**

- The Users list defaults to **10 users per page**; additional users may appear on page 2 or later (alphabetical sort).
- Use the search box with the username (or a distinctive prefix) rather than relying on deep links — Admin Console v2 deep links may land on the list view only.

**Optional — full org admin:** **Role mapping** → assign realm role **`org-admin`**. Envoy maps this to `is_org_admin` in the identity header, triggering insights-rbac `admin_default` group access. Skip for scoped personas (e.g. payment-team, cluster-scoped users).

#### Step 2 — Grant permissions in COS IAM

1. Log into the **CoP UI** as an **admin** persona.
2. Navigate to **Identity and Access Management → Groups** (expand Global IAM nav if needed).
3. Open the target group (or create a group and assign a Cost role first — see **H-10**).
4. On the **Members** tab, add the member using the **exact Keycloak username**.
5. Save.

#### Step 3 — First CoP login (activates IAM Users list)

1. Log out; log into the **CoP UI** as the new user (first authentication).
2. As admin, verify **IAM → Users**: the user appears (refresh if needed).
3. Verify **IAM → Groups → {group} → Members** lists the user.
4. Verify cost data scope matches the assigned group role (compare against persona patterns in **H-01**–**H-03**).

**Sync behavior:** The IAM **Users** list is backed by insights-rbac, not Keycloak directly. A user created in Keycloak may **not** appear in IAM **Users** until their **first CoP login**. Group membership can be assigned before first login (via IAM UI, API, or Django shell).

**Cache:** Permission changes may take up to the RBAC cache TTL (~300s) to propagate; see **H-06** for cache-aware validation.

#### Verification checklist

| Check | Where |
|-------|-------|
| User exists and is enabled | Keycloak Admin → `kubernetes` realm → **Users** |
| Required attributes present | Keycloak user **Attributes** (`org_id`, `account_number`) |
| Group membership assigned | CoP IAM → **Groups** → **Members** |
| User visible in IAM | CoP IAM → **Users** (after first CoP login) |
| Permissions active | CoP cost reports or `/api/rbac/v1/access/?application=cost-management` |

See also: `docs/operations/rbac-setup.md` (User and Group Management).

### Custom role creation (F-06)

Reference procedure for **F-06** (and copy-from-existing-role flows that reuse the same **Add permissions** step).

By default, chart `rbac.roleCreateAllowList` is **empty** (`cost-onprem/values.yaml`). The RBAC API then sets no `ROLE_CREATE_ALLOW_LIST` env var on the insights-rbac deployment. The Create role wizard calls:

`GET /api/rbac/v1/permissions/?allowed_only=true&exclude_globals=true`

With an empty allow list, `allowed_only=true` returns **zero** permissions — the UI correctly shows *No permissions*. This matches upstream SaaS behavior and is **not** a UI defect.

#### Step 1 — Enable custom role creation in Helm

1. Set `rbac.roleCreateAllowList` in the cluster values file (comma-separated application names):

```yaml
rbac:
  roleCreateAllowList: "cost-management"
```

2. To allow IAM (`rbac`) permissions in custom roles as well (optional):

```yaml
rbac:
  roleCreateAllowList: "cost-management,rbac"
```

3. `helm upgrade` the release and wait for the RBAC API deployment to roll out (`cost-onprem/templates/rbac/deployment-api.yaml` injects `ROLE_CREATE_ALLOW_LIST` only when this value is non-empty).

#### Step 2 — Verify API before UI

As **admin**, confirm the permissions picker will have data:

```bash
# Through the UI origin (session cookie) or gateway with admin JWT:
curl -sk -b cookies.txt \
  'https://<ui-host>/api/rbac/v1/permissions/?limit=20&allowed_only=true&exclude_globals=true&application=cost-management'
```

Expect `meta.count > 0` and a non-empty `data` array. If `count` is `0`, the allow list is still unset or the RBAC API pod has not picked up the new env var.

Also check application filter options:

`GET /api/rbac/v1/permissions/options/?field=application&allowed_only=true` → should list at least `cost-management`.

**Note:** `exclude_globals=true` hides wildcard rows (e.g. `cost-management:*:*`). Pick a concrete permission such as `cost-management:openshift.cluster:read` for the wizard test.

#### Step 3 — Run F-06 in the UI

1. Log in as **admin** → **Roles** → **Create role**.
2. Step 1: unique name (`TEST-<date>-custom-role`), description, **Create a role from scratch** (or copy an existing role).
3. Step 2 **Add permissions**: table lists permissions; select at least one → **Next**.
4. Step 3 **Review** → save.
5. Confirm the role appears in the Roles list and can be assigned to a test group (prefix `TEST-*`).

#### Step 4 — Cleanup

Delete UI-created test roles via the Roles list actions menu, or inspect with:

```bash
kubectl exec -n cost-onprem deploy/insights-rbac -- \
  python manage.py shell -c "from management.models import Role; print([r.name for r in Role.objects.filter(name__startswith='TEST-')])"
```

**Without Helm change:** custom roles can still be created via Django shell in the RBAC pod (bypasses API allow-list validation). See `docs/operations/rbac-setup.md` — that path is out of scope for F-06 UI testing.

---

## 14. Test risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| MFE lazy-load timing variability on lab hosts | High | Medium | Use 10s max wait + network idle detection in automated tests |
| Keycloak federation flakiness during LDAP sync | Medium | High | Isolated test realm; snapshot/restore scripts; document sync intervals |
| RBAC cache stale reads cause false negatives | Medium | Medium | Document cache TTL (300s); add forced cache-clear tests; log timestamps |
| Browser compatibility issues (Firefox/Edge) | Low | Medium | Test on Chrome 120+, Firefox 115+, Edge 120+ per browser matrix |
| Concurrent admin edits cause race conditions | Medium | High | Exercise D-05–D-09 under parallel admin sessions; document conflict resolution strategy |
| Large dataset pagination breaks on 1000+ items | Low | High | Manual/UI check J-05; monitor prod for dataset growth |
| Test data pollution between runs | Medium | Medium | Mandatory cleanup script in CI; `TEST-*` prefix enforcement |

**Mitigation tracking**: Link high-impact risks to Jira blockers; review mitigation effectiveness in retrospectives.

---

## 15. Acceptance criteria → test case mapping

Maps COST-7654 POC acceptance criteria to test case coverage:

| AC # | Acceptance Criteria | Test Cases | Status |
|------|---------------------|------------|--------|
| AC-1 | RBAC remote builds with plugin-manifest under `/rbac/` | B-01, B-03 | ✓ |
| AC-2 | koku-ui-onprem loads MFE via Scalprum | B-02, C-01–C-08, G-01–G-06, G-08 | ✓ |
| AC-3 | IAM reachable from shell navigation | C-01, C-01a–C-01b, C-02–C-02e, C-03–C-05a, C-09, C-14 | ✓ |
| AC-4 | Authenticated user completes IAM flows against `/api/rbac/` | B-05, D-05, E-03, F-03, F-06 | ✓ |
| AC-5 | No second UI hostname required | B-04 | ✓ |
| AC-6 | No re-auth when switching Cost ↔ IAM | A-02 | ✓ |
| AC-7 | Persona isolation verified | **API** H-01–H-04, H-09; **UI** H-10, E-11 | ✓ |
| AC-8 | On-prem UX constraints (no Invite Users) | E-05, C-08, G-04, K-01, K-04 | ✓ |

**Coverage gap analysis**: All ACs mapped to ≥1 P0/P1 test case. No gaps identified.

---

## 16. Known limitations & workarounds

| Issue | Impact | Workaround | Tracking |
|-------|--------|-----------|----------|
| 3–5s MFE lazy-load delay | False negatives if wait <5s | Hard-code 5s wait in all IAM nav tests (C-*, D-*) | By design |
| RBAC cache TTL 300s | Permission changes delayed | Document in H-06; add cache-clear step for immediate validation | COST-7XXX |
| IAM Users list lags Keycloak | User in Keycloak but not in IAM **Users** | First CoP login required; see §13 Step 3 | By design |
| No API for bulk user creation | Cannot easily seed 1000+ users | Django shell in RBAC pod (no `seed_rbac_users.py` in-repo) | Future enhancement |
| Gateway JWT expiry not synchronized with UI session | UI shows logged in but API returns 401 | Refresh token interceptor in UI; test in J-03 | COST-7XXX |
| Bare `/iam/my-user-access` hydrates `?bundle=rhel` | User-menu default is OpenShift; only the **no-query deep link** (and explicit `?bundle=rhel`) shows "Your Red Hat Enterprise Linux roles" | Assert C-02 on user-menu path; C-08 for deep-link leftover vs COST-7589 | Live 2026-08-27 |
| User detail 404 for listed usernames | E-03 fails: list links to `/users/detail/{username}` but page is "User not found" | Confirm `/api/rbac/v1/principals/?usernames=` still 200; treat as UI defect | Live 2026-08-27 |
| Users Status/Org Admin columns unused | All lab principals **Inactive** and Org. Administrator **No** (including admin) | Do not assert SaaS-style Active/checkmark; G-01 badge is the org-admin signal | Live 2026-08-27 |
| Dummy principal profile fields | first/last/email are `foo`/`bar`/`baz` | Do not assert real names/emails | Keycloak→RBAC sync |
| Create role — empty Add permissions picker | F-06 blocked on default installs: `rbac.roleCreateAllowList` empty → `allowed_only=true` returns 0 rows | Set `rbac.roleCreateAllowList: "cost-management"` (see **§13 Custom role creation**) before F-06; not a UI defect | By design (chart default) |

**Future improvements**: Track in backlog; revisit quarterly.

---

## 17. Defect severity guidelines (refined)

| Severity | Criteria | Examples |
|----------|----------|----------|
| **S1 — Critical** | IAM completely unusable; auth loop; data leak across tenants | IAM blank after 10s; cannot list groups for any user; alice sees bob's data |
| **S2 — High (infra)** | Infrastructure failure blocking IAM | 404 on `/rbac/plugin-entry.js`; gateway 502 on all `/api/rbac/` calls |
| **S2 — High (functional)** | Core CRUD broken for admins | Cannot create group (500 error); wrong persona scope in Cost data |
| **S3 — Medium** | Workaround exists; non-admin affected | Pagination breaks on page 10; filter reset on navigation; SaaS copy leftover |
| **S4 — Low** | Cosmetic; no functional impact | Tooltip typo; minor CSS misalignment; console warning (not error) |

**Escalation**: S1/S2 block release sign-off. S3 requires product owner triage.

---

## 18. Automation roadmap updates

| Priority | Gap | Recommendation | Target Sprint | Owner |
|----------|-----|----------------|---------------|-------|
| **P0** | No Playwright in chart CI for IAM | Implement Playwright suite in `tests/suites/ui/test_rbac_iam.py` | 2026-Q3 Sprint 1 | QE Team |
| **P0** | UI tests need MFE load wait fixture | Add `wait_for_iam_content()` fixture with network idle detection; parameterize persona credentials (no hardcoded usernames) | 2026-Q3 Sprint 1 | QE Team |
| **P1** | Groups CRUD automation | API write-deny exists (I-04); add UI smoke for D-03, D-08 | 2026-Q3 Sprint 2 | QE Team |
| **P1** | Map Jira TCs to FLPATH-3551 | Extend FLPATH-3551 with UI-specific cases (beyond FLPATH-4308–4329) | 2026-Q3 Sprint 2 | QE Lead |
| **P2** | Visual regression (optional) | Storybook parity checks (referenced in COST-7654) | 2026-Q4 | Future |


---

## Related documentation

| Document | Path | Layer | Relevance |
|----------|------|-------|-----------|
| RBAC gateway automated tests | `tests/suites/auth/RBAC_SECURITY_TESTS.md` | API | Section I mapping |
| RBAC setup operations | `docs/operations/rbac-setup.md` | — | Environment setup |
| Gateway test module | `tests/suites/auth/test_rbac_gateway.py` | API | I-*, H-04/H-05/H-09 |
| Gateway JWT (ingress/koku) | `tests/suites/auth/test_gateway_auth.py` | API | 401/200 token tests; not IAM chrome |
| E2E persona tests | `tests/suites/e2e/test_rbac_access.py` | API | H-01–H-04 |
| UI login/logout | `tests/suites/ui/test_login_flow.py`, `test_logout_flow.py` | UI | A-01 and Cost-half of A-03 |
| Cost navigation | `tests/suites/ui/test_navigation.py` | UI | Cost pages only; C-14 selector |
| Nginx `/rbac/` | `cost-onprem/templates/ui/nginx-config.yaml` | Infra | B-03; N-04 X-Frame-Options |
| RBAC authorization performance | `tests/suites/performance/test_rbac_perf.py` | API | COST-7643 PERF-RBAC-001–006 |
| Performance testing plan | `docs/performance/performance-testing-plan.md` | API | Existing perf suite |
| Performance findings | `docs/performance/FINDINGS.md` | API | FINDING-037 |
