{{/*
=============================================================================
Redis Helpers
=============================================================================
*/}}

{{/*
Redis host
*/}}
{{- define "cost-mgmt.redis.host" -}}
redis
{{- end }}

{{/*
Redis port
*/}}
{{- define "cost-mgmt.redis.port" -}}
6379
{{- end }}

{{/*
=============================================================================
Storage (S3) Helpers
=============================================================================
*/}}

{{/*
Storage (S3) endpoint - builds full URL with protocol and port
*/}}
{{- define "cost-mgmt.storage.endpoint" -}}
{{- if .Values.storage -}}
  {{- $protocol := ternary "https" "http" (eq (.Values.storage.useSSL | default false) true) -}}
  {{- $host := .Values.storage.endpoint | default "s3.openshift-storage.svc" -}}
  {{- $port := .Values.storage.port | default "443" -}}
  {{- printf "%s://%s:%s" $protocol $host $port -}}
{{- else if and .Values.costManagement .Values.costManagement.s3Endpoint -}}
  {{- .Values.costManagement.s3Endpoint -}}
{{- else -}}
  {{- "https://s3.openshift-storage.svc:443" -}}
{{- end -}}
{{- end }}

{{/*
Storage credentials secret name
*/}}
{{- define "cost-mgmt.storage.secretName" -}}
{{- if and .Values.storage .Values.storage.secretName -}}
  {{- .Values.storage.secretName -}}
{{- else -}}
  storage-credentials
{{- end -}}
{{- end }}

{{/*
S3 endpoint (alias for Koku compatibility)
*/}}
{{- define "cost-mgmt.koku.s3.endpoint" -}}
{{- include "cost-mgmt.storage.endpoint" . -}}
{{- end -}}

{{/*
Koku database credentials secret name
*/}}
{{- define "cost-mgmt.koku.database.secretName" -}}
{{- if and .Values.costManagement .Values.costManagement.database .Values.costManagement.database.secretName -}}
{{- .Values.costManagement.database.secretName -}}
{{- else if .Values.postgresql.auth.existingSecret -}}
{{- .Values.postgresql.auth.existingSecret -}}
{{- else -}}
{{- include "cost-mgmt-infra.postgresql.secretName" . -}}
{{- end -}}
{{- end -}}

{{/*
=============================================================================
Security Context Helpers
=============================================================================
*/}}

{{/*
Pod-level security context
*/}}
{{- define "cost-mgmt.securityContext.pod" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/*
Container-level security context
*/}}
{{- define "cost-mgmt.securityContext.container" -}}
allowPrivilegeEscalation: false
capabilities:
  drop:
    - ALL
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}

