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
- name: BYPASS_BOP_VERIFICATION
  value: "True"
- name: KAFKA_ENABLED
  value: "false"
- name: PGSSLMODE
  value: "disable"
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
