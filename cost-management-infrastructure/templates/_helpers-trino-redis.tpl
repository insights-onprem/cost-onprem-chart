{{/*
=============================================================================
Trino & Hive Metastore Helpers
=============================================================================
*/}}

{{/*
Trino coordinator service name
*/}}
{{- define "cost-mgmt.trino.coordinator.name" -}}
trino-coordinator
{{- end -}}

{{/*
Trino worker name
*/}}
{{- define "cost-mgmt.trino.worker.name" -}}
trino-worker
{{- end -}}

{{/*
Hive metastore service name
*/}}
{{- define "cost-mgmt.trino.metastore.name" -}}
hive-metastore
{{- end -}}

{{/*
Hive metastore database name
*/}}
{{- define "cost-mgmt.trino.metastore.database.name" -}}
hive-metastore-db
{{- end -}}

{{/*
Trino metastore database host
*/}}
{{- define "cost-mgmt.trino.metastore.database.host" -}}
{{- if .Values.trino.metastore.database.host -}}
  {{- .Values.trino.metastore.database.host -}}
{{- else -}}
  {{- include "cost-mgmt.trino.metastore.database.name" . -}}
{{- end -}}
{{- end -}}

{{/*
Trino metastore database connection URL (JDBC)
*/}}
{{- define "cost-mgmt.trino.metastore.database.url" -}}
{{- printf "jdbc:postgresql://%s:%v/%s"
    (include "cost-mgmt.trino.metastore.database.host" .)
    (.Values.trino.metastore.database.port | default 5432)
    (.Values.trino.metastore.database.name | default "metastore")
-}}
{{- end -}}

{{/*
Hive metastore URI
*/}}
{{- define "cost-mgmt.trino.metastore.uri" -}}
{{- printf "thrift://%s:%v"
    (include "cost-mgmt.trino.metastore.name" .)
    (.Values.trino.metastore.service.port | default 9083)
-}}
{{- end -}}

{{/*
Trino metastore database credentials secret name
*/}}
{{- define "cost-mgmt.trino.metastore.database.secretName" -}}
{{- printf "%s-metastore-db-credentials" (include "cost-mgmt.fullname" .) -}}
{{- end -}}

{{/*
Common labels for Trino resources
*/}}
{{- define "cost-mgmt.trino.labels" -}}
{{ include "cost-mgmt.labels" . }}
app.kubernetes.io/part-of: cost-management-infrastructure
app.kubernetes.io/component: trino
{{- end -}}

{{/*
Selector labels for Trino Coordinator
*/}}
{{- define "cost-mgmt.trino.coordinator.selectorLabels" -}}
{{ include "cost-mgmt.selectorLabels" . }}
app.kubernetes.io/component: trino-coordinator
{{- end -}}

{{/*
Selector labels for Trino Worker
*/}}
{{- define "cost-mgmt.trino.worker.selectorLabels" -}}
{{ include "cost-mgmt.selectorLabels" . }}
app.kubernetes.io/component: trino-worker
{{- end -}}

{{/*
Selector labels for Hive Metastore
*/}}
{{- define "cost-mgmt.trino.metastore.selectorLabels" -}}
{{ include "cost-mgmt.selectorLabels" . }}
app.kubernetes.io/component: hive-metastore
{{- end -}}

{{/*
Storage class for Trino coordinator (uses default if not specified)
*/}}
{{- define "cost-mgmt.trino.coordinator.storageClass" -}}
{{- if .Values.trino.coordinator.storage.storageClassName -}}
  {{- .Values.trino.coordinator.storage.storageClassName -}}
{{- else -}}
  {{- /* Use default storage class in cluster */ -}}
{{- end -}}
{{- end -}}

{{/*
Storage class for Trino worker (uses default if not specified)
*/}}
{{- define "cost-mgmt.trino.worker.storageClass" -}}
{{- if .Values.trino.worker.storage.storageClassName -}}
  {{- .Values.trino.worker.storage.storageClassName -}}
{{- else -}}
  {{- /* Use default storage class in cluster */ -}}
{{- end -}}
{{- end -}}

{{/*
Storage class for Hive metastore database (uses default if not specified)
*/}}
{{- define "cost-mgmt.trino.metastore.database.storageClass" -}}
{{- if .Values.trino.metastore.database.storage.storageClassName -}}
  {{- .Values.trino.metastore.database.storage.storageClassName -}}
{{- else -}}
  {{- /* Use default storage class in cluster */ -}}
{{- end -}}
{{- end -}}

{{/*
Trino service account name
*/}}
{{- define "cost-mgmt.trino.serviceAccountName" -}}
{{- if and .Values.trino .Values.trino.serviceAccount .Values.trino.serviceAccount.create -}}
  {{- .Values.trino.serviceAccount.name | default (printf "%s-trino" (include "cost-mgmt.fullname" .)) -}}
{{- else if and .Values.trino .Values.trino.serviceAccount .Values.trino.serviceAccount.name -}}
  {{- .Values.trino.serviceAccount.name -}}
{{- else -}}
  {{- "default" -}}
{{- end -}}
{{- end -}}

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
Storage (S3) endpoint
*/}}
{{- define "cost-mgmt.storage.endpoint" -}}
{{- if .Values.storage -}}
  {{- .Values.storage.endpoint | default "http://s3.openshift-storage.svc:80" -}}
{{- else if and .Values.costManagement .Values.costManagement.s3Endpoint -}}
  {{- .Values.costManagement.s3Endpoint -}}
{{- else -}}
  {{- "http://s3.openshift-storage.svc:80" -}}
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

