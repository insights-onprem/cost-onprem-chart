{{/*
Koku-specific helper functions for cost-management-onprem chart
These helpers extend the base helpers from _helpers.tpl (PR #27)
*/}}

{{/*
=============================================================================
Koku Service Names
=============================================================================
*/}}

{{/*
Koku API service name
*/}}
{{- define "cost-mgmt.koku.api.name" -}}
{{- printf "%s-koku-api" (include "cost-mgmt.fullname" .) -}}
{{- end -}}

{{/*
Koku API reads deployment name
*/}}
{{- define "cost-mgmt.koku.api.reads.name" -}}
{{- printf "%s-koku-api-reads" (include "cost-mgmt.fullname" .) -}}
{{- end -}}

{{/*
Koku API writes deployment name
*/}}
{{- define "cost-mgmt.koku.api.writes.name" -}}
{{- printf "%s-koku-api-writes" (include "cost-mgmt.fullname" .) -}}
{{- end -}}

{{/*
Koku Celery Beat name
*/}}
{{- define "cost-mgmt.koku.celery.beat.name" -}}
{{- printf "%s-celery-beat" (include "cost-mgmt.fullname" .) -}}
{{- end -}}

{{/*
Koku Celery worker name (takes worker type as argument)
Usage: {{ include "cost-mgmt.koku.celery.worker.name" (dict "context" . "type" "default") }}
*/}}
{{- define "cost-mgmt.koku.celery.worker.name" -}}
{{- $context := .context -}}
{{- $type := .type -}}
{{- printf "%s-celery-worker-%s" (include "cost-mgmt.fullname" $context) $type -}}
{{- end -}}

{{/*
Koku database name
*/}}
{{- define "cost-mgmt.koku.database.name" -}}
{{- .Values.costManagement.database.name | default "koku" -}}
{{- end -}}

{{/*
=============================================================================
Trino Service Names
=============================================================================
*/}}

{{/*
Trino coordinator service name
*/}}
{{- define "cost-mgmt.trino.coordinator.name" -}}
{{- printf "%s-trino-coordinator" (include "cost-mgmt.fullname" .) -}}
{{- end -}}

{{/*
Trino worker name
*/}}
{{- define "cost-mgmt.trino.worker.name" -}}
{{- printf "%s-trino-worker" (include "cost-mgmt.fullname" .) -}}
{{- end -}}

{{/*
Hive metastore service name
*/}}
{{- define "cost-mgmt.trino.metastore.name" -}}
{{- printf "%s-hive-metastore" (include "cost-mgmt.fullname" .) -}}
{{- end -}}

{{/*
Hive metastore database name
*/}}
{{- define "cost-mgmt.trino.metastore.database.name" -}}
{{- printf "%s-hive-metastore-db" (include "cost-mgmt.fullname" .) -}}
{{- end -}}

{{/*
=============================================================================
Database Connection Helpers
=============================================================================
*/}}


{{/*
Koku database host

Returns the PostgreSQL service hostname. Uses the explicit host from values.yaml,
or defaults to "postgres" (the service name from the infrastructure chart).
*/}}
{{- define "cost-mgmt.koku.database.host" -}}
{{- .Values.costManagement.database.host | default "postgres" -}}
{{- end -}}

{{/*
Koku database port
*/}}
{{- define "cost-mgmt.koku.database.port" -}}
{{- .Values.costManagement.database.port | default 5432 -}}
{{- end -}}

{{/*
Koku database name
*/}}
{{- define "cost-mgmt.koku.database.dbname" -}}
{{- .Values.costManagement.database.name | default "koku" -}}
{{- end -}}

{{/*
Koku database user
*/}}
{{- define "cost-mgmt.koku.database.user" -}}
{{- .Values.costManagement.database.user | default "koku" -}}
{{- end -}}

{{/*
Koku database connection URL (for Django)
*/}}
{{- define "cost-mgmt.koku.database.url" -}}
{{- printf "postgresql://%s:%s@%s:%v/%s"
    (include "cost-mgmt.koku.database.user" .)
    "$(DATABASE_PASSWORD)"
    (include "cost-mgmt.koku.database.host" .)
    (include "cost-mgmt.koku.database.port" .)
    (include "cost-mgmt.koku.database.dbname" .)
-}}
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
=============================================================================
Redis Connection Helpers (uses shared Redis from infrastructure)
=============================================================================
*/}}

{{/*
Redis host (uses shared Redis service from PR #27 infrastructure)
*/}}
{{- define "cost-mgmt.koku.redis.host" -}}
{{- include "cost-mgmt.redis.host" . -}}
{{- end -}}

{{/*
Redis port
*/}}
{{- define "cost-mgmt.koku.redis.port" -}}
{{- include "cost-mgmt.redis.port" . -}}
{{- end -}}

{{/*
Redis URL for Koku (uses DB 1)
*/}}
{{- define "cost-mgmt.koku.redis.url" -}}
{{- printf "redis://%s:%v/1"
    (include "cost-mgmt.koku.redis.host" .)
    (include "cost-mgmt.koku.redis.port" .)
-}}
{{- end -}}

{{/*
=============================================================================
Kafka Connection Helpers (uses shared Kafka from infrastructure)
=============================================================================
*/}}

{{/*
Kafka hostname (without port)
INSIGHTS_KAFKA_HOST - Koku's EnvConfigurator concatenates this with port
*/}}
{{- define "cost-mgmt.koku.kafka.host" -}}
{{- printf "kafka" -}}
{{- end -}}

{{/*
Kafka bootstrap servers (uses shared Kafka from PR #27)
*/}}
{{- define "cost-mgmt.koku.kafka.bootstrapServers" -}}
{{- include "cost-mgmt.kafka.bootstrapServers" . -}}
{{- end -}}

{{/*
Kafka port
*/}}
{{- define "cost-mgmt.koku.kafka.port" -}}
{{- 9092 -}}
{{- end -}}

{{/*
=============================================================================
MinIO/S3 Connection Helpers (uses shared storage from infrastructure)
=============================================================================
*/}}

{{/*
MinIO endpoint (uses shared MinIO from PR #27)
*/}}
{{- define "cost-mgmt.koku.s3.endpoint" -}}
{{- include "cost-mgmt.storage.endpoint" . -}}
{{- end -}}

{{/*
MinIO bucket name (DEPRECATED - use costManagement.storage.bucketName instead)
This helper is kept for backwards compatibility but should not be used
*/}}
{{- define "cost-mgmt.koku.s3.bucket" -}}
{{- required "costManagement.storage.bucketName is required" .Values.costManagement.storage.bucketName -}}
{{- end -}}

{{/*
MinIO ROS bucket name
*/}}
{{- define "cost-mgmt.koku.s3.rosBucket" -}}
{{- .Values.costManagement.api.reads.env.REQUESTED_ROS_BUCKET | default "ros-report" -}}
{{- end -}}

{{/*
=============================================================================
Trino Connection Helpers
=============================================================================
*/}}

{{/*
Trino coordinator host
*/}}
{{- define "cost-mgmt.koku.trino.host" -}}
{{- if .Values.trino.coordinator.host -}}
  {{- .Values.trino.coordinator.host -}}
{{- else -}}
{{- include "cost-mgmt.trino.coordinator.name" . -}}
{{- end -}}
{{- end -}}

{{/*
Trino coordinator port
*/}}
{{- define "cost-mgmt.koku.trino.port" -}}
{{- .Values.trino.coordinator.service.port | default 8080 -}}
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
=============================================================================
Secret Names
=============================================================================
*/}}

{{/*
Django secret name
*/}}
{{- define "cost-mgmt.koku.django.secretName" -}}
{{- printf "%s-django-secret" (include "cost-mgmt.fullname" .) -}}
{{- end -}}

{{/*
Koku database credentials secret name
*/}}
{{- define "cost-mgmt.koku.database.secretName" -}}
{{- if .Values.costManagement.database.secretName -}}
{{- .Values.costManagement.database.secretName -}}
{{- else -}}
{{- printf "%s-db-credentials" (include "cost-mgmt.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Trino metastore database credentials secret name
*/}}
{{- define "cost-mgmt.trino.metastore.database.secretName" -}}
{{- printf "%s-metastore-db-credentials" (include "cost-mgmt.fullname" .) -}}
{{- end -}}

{{/*
=============================================================================
Labels
=============================================================================
*/}}

{{/*
Common labels for Koku resources
*/}}
{{- define "cost-mgmt.koku.labels" -}}
{{ include "cost-mgmt.labels" . }}
app.kubernetes.io/part-of: cost-management-onprem
app.kubernetes.io/component: cost-management
{{- end -}}

{{/*
Selector labels for Koku API reads
*/}}
{{- define "cost-mgmt.koku.api.reads.selectorLabels" -}}
{{ include "cost-mgmt.selectorLabels" . }}
app.kubernetes.io/component: cost-management-api
api-type: reads
{{- end -}}

{{/*
Selector labels for Koku API writes
*/}}
{{- define "cost-mgmt.koku.api.writes.selectorLabels" -}}
{{ include "cost-mgmt.selectorLabels" . }}
app.kubernetes.io/component: cost-management-api
api-type: writes
{{- end -}}

{{/*
Selector labels for Celery Beat
*/}}
{{- define "cost-mgmt.koku.celery.beat.selectorLabels" -}}
{{ include "cost-mgmt.selectorLabels" . }}
app.kubernetes.io/component: cost-management-celery
celery-type: beat
{{- end -}}

{{/*
Selector labels for Celery Worker (takes worker type as argument)
Usage: {{ include "cost-mgmt.koku.celery.worker.selectorLabels" (dict "context" . "type" "default") }}
*/}}
{{- define "cost-mgmt.koku.celery.worker.selectorLabels" -}}
{{- $context := .context -}}
{{- $type := .type -}}
{{ include "cost-mgmt.selectorLabels" $context }}
app.kubernetes.io/component: cost-management-celery
celery-type: worker
worker-queue: {{ $type }}
{{- end -}}

{{/*
Common labels for Trino resources
*/}}
{{- define "cost-mgmt.trino.labels" -}}
{{ include "cost-mgmt.labels" . }}
app.kubernetes.io/part-of: cost-management-onprem
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
=============================================================================
Storage Class Helpers
=============================================================================
*/}}

{{/*
Storage class for Koku database (uses default if not specified)
*/}}
{{- define "cost-mgmt.koku.database.storageClass" -}}
{{- if .Values.costManagement.database.storage.storageClassName -}}
  {{- .Values.costManagement.database.storage.storageClassName -}}
{{- else -}}
  {{- /* Use default storage class in cluster */ -}}
{{- end -}}
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
=============================================================================
Service Account Helpers
=============================================================================
*/}}

{{/*
Koku service account name
*/}}
{{- define "cost-mgmt.koku.serviceAccountName" -}}
{{- if .Values.costManagement.serviceAccount.create -}}
  {{- .Values.costManagement.serviceAccount.name | default (printf "%s-koku" (include "cost-mgmt.fullname" .)) -}}
{{- else -}}
  {{- .Values.costManagement.serviceAccount.name | default "default" -}}
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
Environment Variable Helpers
=============================================================================
*/}}

{{/*
Common environment variables for Koku API and Celery
*/}}
{{- define "cost-mgmt.koku.commonEnv" -}}
- name: DATABASE_SERVICE_NAME
  value: "database"
- name: DATABASE_ENGINE
  value: "postgresql"
- name: DATABASE_SERVICE_HOST
  # Discovered dynamically via Helm lookup function by PostgreSQL service labels
  # or uses explicit value from costManagement.database.host
  value: {{ include "cost-mgmt.koku.database.host" . | quote }}
- name: DATABASE_SERVICE_PORT
  value: {{ include "cost-mgmt.koku.database.port" . | quote }}
- name: DATABASE_NAME
  value: {{ include "cost-mgmt.koku.database.dbname" . | quote }}
- name: DATABASE_USER
  value: {{ include "cost-mgmt.koku.database.user" . | quote }}
- name: DATABASE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-mgmt.koku.database.secretName" . }}
      key: password
- name: REDIS_HOST
  value: {{ include "cost-mgmt.koku.redis.host" . | quote }}
- name: REDIS_PORT
  value: {{ include "cost-mgmt.koku.redis.port" . | quote }}
- name: CELERY_RESULT_EXPIRES
  value: {{ .Values.costManagement.celery.resultExpires | default "28800" | quote }}
- name: INSIGHTS_KAFKA_HOST
  value: {{ include "cost-mgmt.koku.kafka.host" . | quote }}
- name: INSIGHTS_KAFKA_PORT
  value: {{ include "cost-mgmt.koku.kafka.port" . | quote }}
- name: S3_ENDPOINT
  value: {{ include "cost-mgmt.koku.s3.endpoint" . | quote }}
- name: REQUESTED_BUCKET
  value: {{ required "costManagement.storage.bucketName is required" .Values.costManagement.storage.bucketName | quote }}
{{- if eq (include "cost-management-onprem.isOpenShift" $) "true" }}
- name: AWS_CA_BUNDLE
  value: /etc/pki/ca-trust/combined/ca-bundle.crt
- name: REQUESTS_CA_BUNDLE
  value: /etc/pki/ca-trust/combined/ca-bundle.crt
{{- end }}
- name: AWS_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-mgmt.storage.secretName" . }}
      key: access-key
- name: AWS_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-mgmt.storage.secretName" . }}
      key: secret-key
- name: TRINO_HOST
  value: {{ include "cost-mgmt.koku.trino.host" . | quote }}
- name: TRINO_PORT
  value: {{ include "cost-mgmt.koku.trino.port" . | quote }}
- name: TRINO_S3A_OR_S3
  value: "s3"
- name: DJANGO_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-mgmt.koku.django.secretName" . }}
      key: secret-key
- name: UNLEASH_DISABLED
  value: {{ .Values.unleashDisabled | quote }}
- name: SCHEDULE_REPORT_CHECKS
  value: {{ .Values.costManagement.scheduleReportChecks | default "true" | quote }}
- name: REPORT_DOWNLOAD_SCHEDULE
  value: {{ .Values.costManagement.reportDownloadSchedule | default "*/5 * * * *" | quote }}
- name: POLLING_TIMER
  value: {{ .Values.costManagement.celery.pollingTimer | default "86400" | quote }}
{{- end -}}

{{/*
=============================================================================
Image Helpers
=============================================================================
*/}}

{{/*
Generate the Koku image reference
Uses ImageStream if building in-cluster, otherwise uses external registry
*/}}
{{- define "cost-mgmt.koku.image" -}}
{{- if .Values.costManagement.api.image.useImageStream -}}
{{- printf "image-registry.openshift-image-registry.svc:5000/%s/%s:%s" .Release.Namespace (include "cost-mgmt.koku.api.name" .) (.Values.costManagement.api.image.tag | default "latest") -}}
{{- else -}}
{{- printf "%s:%s" .Values.costManagement.api.image.repository (.Values.costManagement.api.image.tag | default "latest") -}}
{{- end -}}
{{- end -}}

{{/*
=============================================================================
Validation Helpers
=============================================================================
*/}}

{{/*
Validate Celery Beat replicas (must be exactly 1)
*/}}
{{- define "cost-mgmt.koku.celery.beat.validateReplicas" -}}
{{- if ne (.Values.costManagement.celery.beat.replicas | int) 1 -}}
  {{- fail "Celery Beat must have exactly 1 replica. Set costManagement.celery.beat.replicas to 1" -}}
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
{{- end -}}

{{/*
Container-level security context
*/}}
{{- define "cost-mgmt.securityContext.container" -}}
allowPrivilegeEscalation: false
capabilities:
  drop:
  - ALL
{{- end -}}

{{/*
Standard volumeMounts for Koku containers
Includes tmp mount and combined CA bundle when on OpenShift
*/}}
{{- define "cost-mgmt.koku.volumeMounts" -}}
- name: tmp
  mountPath: /tmp
{{- if eq (include "cost-management-onprem.isOpenShift" $) "true" }}
- name: combined-ca-bundle
  mountPath: /etc/pki/ca-trust/combined
  readOnly: true
{{- end }}
{{- end -}}

{{/*
Standard volumes for Koku pods
Includes tmp volume and CA bundle volumes for OpenShift
*/}}
{{- define "cost-mgmt.koku.volumes" -}}
- name: tmp
  emptyDir: {}
{{- if eq (include "cost-management-onprem.isOpenShift" $) "true" }}
- name: ca-scripts
  configMap:
    name: {{ include "cost-mgmt.fullname" . }}-ca-combine
    items:
      - key: combine-ca.sh
        path: combine-ca.sh
        mode: 0755
- name: ca-source
  configMap:
    name: {{ include "cost-mgmt.fullname" . }}-service-ca
- name: combined-ca-bundle
  emptyDir: {}
{{- end }}
{{- end -}}

{{/*
Init container to combine CA certificates (OpenShift only)
Combines system CA bundle with OpenShift cluster root CA and Service CA for Python SSL verification
*/}}
{{- define "cost-mgmt.koku.initContainer.combineCA" -}}
{{- if eq (include "cost-management-onprem.isOpenShift" $) "true" }}
- name: prepare-ca-bundle
  image: {{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}
  command: ['bash', '/scripts/combine-ca.sh']
  volumeMounts:
    - name: ca-scripts
      mountPath: /scripts
      readOnly: true
    - name: ca-source
      mountPath: /ca-source
      readOnly: true
    - name: combined-ca-bundle
      mountPath: /ca-output
  securityContext:
    {{- include "cost-mgmt.securityContext.container" . | nindent 4 }}
{{- end }}
{{- end -}}
