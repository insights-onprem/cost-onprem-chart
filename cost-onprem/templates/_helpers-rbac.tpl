{{/*
=============================================================================
insights-rbac Helm Template Helpers
=============================================================================
Helpers for the RBAC v1 authorization backend deployment.
RBAC is always deployed as part of the on-prem chart.
*/}}

{{/*
RBAC API deployment name.
*/}}
{{- define "cost-onprem.rbac.api.name" -}}
{{- printf "%s-rbac-api" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
RBAC worker deployment name.
*/}}
{{- define "cost-onprem.rbac.worker.name" -}}
{{- printf "%s-rbac-worker" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
RBAC migration job name.
*/}}
{{- define "cost-onprem.rbac.migration.name" -}}
{{- printf "%s-rbac-migrate" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
RBAC admin bootstrap job name.
*/}}
{{- define "cost-onprem.rbac.adminBootstrap.name" -}}
{{- printf "%s-rbac-admin-bootstrap" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
RBAC container image.
*/}}
{{- define "cost-onprem.rbac.image" -}}
{{- printf "%s:%s" .Values.rbac.image.repository .Values.rbac.image.tag -}}
{{- end -}}

{{/*
RBAC service host (cluster-internal DNS).
*/}}
{{- define "cost-onprem.rbac.serviceHost" -}}
{{- printf "%s-rbac-api.%s.svc.cluster.local" (include "cost-onprem.fullname" .) .Release.Namespace -}}
{{- end -}}

{{/*
RBAC selector labels for API.
*/}}
{{- define "cost-onprem.rbac.api.selectorLabels" -}}
{{ include "cost-onprem.selectorLabels" . }}
app.kubernetes.io/component: rbac-api
{{- end -}}

{{/*
RBAC selector labels for worker.
*/}}
{{- define "cost-onprem.rbac.worker.selectorLabels" -}}
{{ include "cost-onprem.selectorLabels" . }}
app.kubernetes.io/component: rbac-worker
{{- end -}}

{{/*
RBAC database host helper (reuses the same PostgreSQL instance).
*/}}
{{- define "cost-onprem.rbac.database.host" -}}
{{- include "cost-onprem.database.host" . -}}
{{- end -}}

{{/*
Common environment variables for insights-rbac containers.
Provides DB, Redis, and application configuration for non-Clowder mode.
*/}}
{{- define "cost-onprem.rbac.commonEnv" -}}
- name: API_PATH_PREFIX
  value: {{ include "cost-onprem.rbac.apiPathPrefix" . | quote }}
- name: DATABASE_NAME
  value: {{ .Values.database.rbac.name | quote }}
- name: DATABASE_USER
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-onprem.rbac.database.secretName" . }}
      key: rbac-user
- name: DATABASE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-onprem.rbac.database.secretName" . }}
      key: rbac-password
- name: DATABASE_HOST
  value: {{ include "cost-onprem.rbac.database.host" . | quote }}
- name: DATABASE_PORT
  value: {{ .Values.database.server.port | default "5432" | quote }}
- name: REDIS_HOST
  value: {{ include "cost-onprem.koku.valkey.host" . | quote }}
- name: REDIS_PORT
  value: {{ include "cost-onprem.koku.valkey.port" . | quote }}
{{- if .Values.valkey.auth.enabled }}
{{- if not .Values.valkey.auth.secretName }}
  {{- fail "valkey.auth.enabled is true but valkey.auth.secretName is empty. Provide the name of a Secret containing key 'redis-password'." -}}
{{- end }}
- name: REDIS_USERNAME
  valueFrom:
    secretKeyRef:
      name: {{ .Values.valkey.auth.secretName }}
      key: redis-username
      optional: true
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.valkey.auth.secretName }}
      key: redis-password
{{- end }}
{{- if .Values.valkey.tls.enabled }}
- name: REDIS_SSL
  value: "True"
{{- if .Values.valkey.tls.caCertSecretName }}
- name: REDIS_SSL_CA_CERTS
  value: "/etc/redis-tls/ca.crt"
{{- end }}
{{- end }}
- name: DJANGO_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-onprem.rbac.database.secretName" . }}
      key: django-secret-key
- name: V2_BOOTSTRAP_TENANT
  value: "True"
- name: SYSTEM_DEFAULT_ROOT_WORKSPACE_ROLE_UUID
  value: "00000000-0000-4000-a000-000000000001"
- name: SYSTEM_DEFAULT_TENANT_ROLE_UUID
  value: "00000000-0000-4000-a000-000000000002"
- name: SYSTEM_ADMIN_ROOT_WORKSPACE_ROLE_UUID
  value: "00000000-0000-4000-a000-000000000003"
- name: SYSTEM_ADMIN_TENANT_ROLE_UUID
  value: "00000000-0000-4000-a000-000000000004"
- name: CLOWDER_ENABLED
  value: "false"
  {{/* On-prem has no Back Office Proxy (BOP/mbop); bypass its identity
       verification so RBAC accepts the X-Rh-Identity header directly from
       Envoy. Required for on-prem; SaaS uses BOP for this check. */}}
- name: BYPASS_BOP_VERIFICATION
  value: "True"
- name: KAFKA_ENABLED
  value: "false"
- name: PGSSLMODE
  value: {{ .Values.database.server.sslMode | quote }}
- name: DJANGO_LOG_LEVEL
  value: "INFO"
- name: RBAC_LOG_LEVEL
  value: "INFO"
- name: DJANGO_LOG_FORMATTER
  value: "simple"
- name: DJANGO_LOG_HANDLERS
  value: "console"
- name: ACCESS_CACHE_ENABLED
  value: "True"
{{- end -}}

{{/*
RBAC API path prefix (always /api/rbac — not user-configurable).
*/}}
{{- define "cost-onprem.rbac.apiPathPrefix" -}}
/api/rbac
{{- end -}}

{{/*
RBAC service port (always 8000 — internal only, not user-configurable).
*/}}
{{- define "cost-onprem.rbac.service.port" -}}
8000
{{- end -}}

{{/*
Resolve the bootstrap admin identity from jwtAuth.realmUsers.
Finds the first entry with orgAdmin=true and returns username, orgId,
accountNumber. When bootstrapAdmin.enabled is true and no orgAdmin
user exists, the template fails with an actionable error.
*/}}
{{- define "cost-onprem.rbac.bootstrapAdmin.username" -}}
{{- $found := false -}}
{{- range .Values.jwtAuth.realmUsers -}}
  {{- if and (not $found) .orgAdmin -}}
    {{- $found = true -}}
    {{- .username | required "jwtAuth.realmUsers: orgAdmin entry must have a username" -}}
  {{- end -}}
{{- end -}}
{{- if not $found -}}
  {{- if .Values.rbac.bootstrapAdmin.enabled -}}
    {{- fail "rbac.bootstrapAdmin.enabled is true but no orgAdmin:true entry found in jwtAuth.realmUsers" -}}
  {{- end -}}
  {{- "admin" -}}
{{- end -}}
{{- end -}}

{{- define "cost-onprem.rbac.bootstrapAdmin.orgId" -}}
{{- $found := false -}}
{{- range .Values.jwtAuth.realmUsers -}}
  {{- if and (not $found) .orgAdmin -}}
    {{- $found = true -}}
    {{- .orgId | required "jwtAuth.realmUsers: orgAdmin entry must have an orgId" -}}
  {{- end -}}
{{- end -}}
{{- if not $found -}}
  {{- "org1234567" -}}
{{- end -}}
{{- end -}}

{{- define "cost-onprem.rbac.bootstrapAdmin.accountNumber" -}}
{{- $found := false -}}
{{- range .Values.jwtAuth.realmUsers -}}
  {{- if and (not $found) .orgAdmin -}}
    {{- $found = true -}}
    {{- .accountNumber | required "jwtAuth.realmUsers: orgAdmin entry must have an accountNumber" -}}
  {{- end -}}
{{- end -}}
{{- if not $found -}}
  {{- "7890123" -}}
{{- end -}}
{{- end -}}

{{/*
RBAC database secret name. Reuses the main DB credentials secret
with RBAC-specific keys, or a dedicated secret.
*/}}
{{- define "cost-onprem.rbac.database.secretName" -}}
{{- if .Values.database.existingSecret -}}
  {{- .Values.database.existingSecret -}}
{{- else -}}
  {{- printf "%s-db-credentials" (include "cost-onprem.fullname" .) -}}
{{- end -}}
{{- end -}}
