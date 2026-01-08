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
{{- define "cost-onprem.koku.api.name" -}}
{{- printf "%s-koku-api" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
Koku API reads deployment name
*/}}
{{- define "cost-onprem.koku.api.reads.name" -}}
{{- printf "%s-koku-api-reads" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
Koku API writes deployment name
*/}}
{{- define "cost-onprem.koku.api.writes.name" -}}
{{- printf "%s-koku-api-writes" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
Koku Celery Beat name
*/}}
{{- define "cost-onprem.koku.celery.beat.name" -}}
{{- printf "%s-celery-beat" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
Koku Celery worker name (takes worker type as argument)
Usage: {{ include "cost-onprem.koku.celery.worker.name" (dict "context" . "type" "default") }}
*/}}
{{- define "cost-onprem.koku.celery.worker.name" -}}
{{- $context := .context -}}
{{- $type := .type -}}
{{- printf "%s-celery-worker-%s" (include "cost-onprem.fullname" $context) $type -}}
{{- end -}}

{{/*
=============================================================================
Database Connection Helpers
=============================================================================
*/}}

{{/*
Koku database host - uses unified database server
*/}}
{{- define "cost-onprem.koku.database.host" -}}
{{- if eq .Values.database.server.host "internal" -}}
{{- printf "%s-database" (include "cost-onprem.fullname" .) -}}
{{- else -}}
{{- .Values.database.server.host -}}
{{- end -}}
{{- end -}}

{{/*
Koku database port
*/}}
{{- define "cost-onprem.koku.database.port" -}}
{{- .Values.database.server.port | default 5432 -}}
{{- end -}}

{{/*
Koku database name
*/}}
{{- define "cost-onprem.koku.database.dbname" -}}
{{- .Values.database.koku.name | default "koku" -}}
{{- end -}}

{{/*
Koku database user
*/}}
{{- define "cost-onprem.koku.database.user" -}}
{{- .Values.database.koku.user | default "koku" -}}
{{- end -}}

{{/*
Koku database connection URL (for Django)
*/}}
{{- define "cost-onprem.koku.database.url" -}}
{{- printf "postgresql://%s:%s@%s:%v/%s"
    (include "cost-onprem.koku.database.user" .)
    "$(DATABASE_PASSWORD)"
    (include "cost-onprem.koku.database.host" .)
    (include "cost-onprem.koku.database.port" .)
    (include "cost-onprem.koku.database.dbname" .)
-}}
{{- end -}}

{{/*
=============================================================================
Valkey Connection Helpers (cache/broker)
=============================================================================
*/}}

{{/*
Valkey host
*/}}
{{- define "cost-onprem.koku.redis.host" -}}
{{- printf "%s-valkey" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
Valkey port
*/}}
{{- define "cost-onprem.koku.redis.port" -}}
{{- .Values.valkey.port | default 6379 -}}
{{- end -}}

{{/*
Storage credentials secret name for S3/ODF access
*/}}
{{- define "cost-onprem.storage.secretName" -}}
{{- printf "%s-storage-credentials" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
Redis URL for Koku (uses DB 1)
*/}}
{{- define "cost-onprem.koku.redis.url" -}}
{{- printf "redis://%s:%v/1"
    (include "cost-onprem.koku.redis.host" .)
    (include "cost-onprem.koku.redis.port" .)
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
Uses configurable value from .Values.costManagement.kafka.host
*/}}
{{- define "cost-onprem.koku.kafka.host" -}}
{{- .Values.costManagement.kafka.host | default "kafka" -}}
{{- end -}}

{{/*
Kafka bootstrap servers (uses shared Kafka from PR #27)
*/}}
{{- define "cost-onprem.koku.kafka.bootstrapServers" -}}
{{- include "cost-onprem.kafka.bootstrapServers" . -}}
{{- end -}}

{{/*
Kafka port
Uses configurable value from .Values.costManagement.kafka.port
*/}}
{{- define "cost-onprem.koku.kafka.port" -}}
{{- .Values.costManagement.kafka.port | default "9092" -}}
{{- end -}}

{{/*
=============================================================================
MinIO/S3 Connection Helpers (uses shared storage from infrastructure)
=============================================================================
*/}}

{{/*
MinIO endpoint (uses shared MinIO from PR #27)
*/}}
{{- define "cost-onprem.koku.s3.endpoint" -}}
{{- include "cost-onprem.storage.endpoint" . -}}
{{- end -}}

{{/*
MinIO bucket name (DEPRECATED - use costManagement.storage.bucketName instead)
This helper is kept for backwards compatibility but should not be used
*/}}
{{- define "cost-onprem.koku.s3.bucket" -}}
{{- required "costManagement.storage.bucketName is required" .Values.costManagement.storage.bucketName -}}
{{- end -}}

{{/*
MinIO ROS bucket name
*/}}
{{- define "cost-onprem.koku.s3.rosBucket" -}}
{{- .Values.costManagement.api.reads.env.REQUESTED_ROS_BUCKET | default "ros-report" -}}
{{- end -}}

{{/*
=============================================================================
Secret Names
=============================================================================
*/}}

{{/*
Django secret name
*/}}
{{- define "cost-onprem.koku.django.secretName" -}}
{{- printf "%s-django-secret" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
Koku database credentials secret name (uses unified secret)
*/}}
{{- define "cost-onprem.koku.database.secretName" -}}
{{- include "cost-onprem.database.secretName" . -}}
{{- end -}}

{{/*
=============================================================================
Labels
=============================================================================
*/}}

{{/*
Common labels for Koku resources
Note: We don't override part-of here to keep consistency with selectorLabels
*/}}
{{- define "cost-onprem.koku.labels" -}}
{{ include "cost-onprem.labels" . }}
app.kubernetes.io/component: cost-management
{{- end -}}

{{/*
Selector labels for Koku API reads
*/}}
{{- define "cost-onprem.koku.api.reads.selectorLabels" -}}
{{ include "cost-onprem.selectorLabels" . }}
app.kubernetes.io/component: cost-management-api
api-type: reads
{{- end -}}

{{/*
Selector labels for Koku API writes
*/}}
{{- define "cost-onprem.koku.api.writes.selectorLabels" -}}
{{ include "cost-onprem.selectorLabels" . }}
app.kubernetes.io/component: cost-management-api
api-type: writes
{{- end -}}

{{/*
Selector labels for Celery Beat
*/}}
{{- define "cost-onprem.koku.celery.beat.selectorLabels" -}}
{{ include "cost-onprem.selectorLabels" . }}
app.kubernetes.io/component: cost-management-celery
celery-type: beat
{{- end -}}

{{/*
Selector labels for Celery Worker (takes worker type as argument)
Usage: {{ include "cost-onprem.koku.celery.worker.selectorLabels" (dict "context" . "type" "default") }}
*/}}
{{- define "cost-onprem.koku.celery.worker.selectorLabels" -}}
{{- $context := .context -}}
{{- $type := .type -}}
{{ include "cost-onprem.selectorLabels" $context }}
app.kubernetes.io/component: cost-management-celery
celery-type: worker
worker-queue: {{ $type }}
{{- end -}}

{{/*
=============================================================================
Storage Class Helpers
=============================================================================
*/}}

{{/*
Storage class for Koku database (uses default if not specified)
*/}}
{{- define "cost-onprem.koku.database.storageClass" -}}
{{- if .Values.costManagement.database.storage.storageClassName -}}
  {{- .Values.costManagement.database.storage.storageClassName -}}
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
{{- define "cost-onprem.koku.serviceAccountName" -}}
{{- if .Values.costManagement.serviceAccount.create -}}
  {{- .Values.costManagement.serviceAccount.name | default (printf "%s-koku" (include "cost-onprem.fullname" .)) -}}
{{- else -}}
  {{- .Values.costManagement.serviceAccount.name | default "default" -}}
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
{{- define "cost-onprem.koku.commonEnv" -}}
# On-prem deployment mode: uses PostgreSQL for data processing, disables Unleash
# This is hardcoded (not configurable) to make it explicit this chart is for on-prem only
- name: ONPREM
  value: "True"
- name: DATABASE_SERVICE_NAME
  value: "database"
- name: DATABASE_ENGINE
  value: "postgresql"
- name: DATABASE_SERVICE_HOST
  # Discovered dynamically via Helm lookup function by PostgreSQL service labels
  # or uses explicit value from costManagement.database.host
  value: {{ include "cost-onprem.koku.database.host" . | quote }}
- name: DATABASE_SERVICE_PORT
  value: {{ include "cost-onprem.koku.database.port" . | quote }}
- name: DATABASE_NAME
  value: {{ include "cost-onprem.koku.database.dbname" . | quote }}
- name: DATABASE_USER
  value: {{ include "cost-onprem.koku.database.user" . | quote }}
- name: DATABASE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-onprem.koku.database.secretName" . }}
      key: koku-password
- name: REDIS_HOST
  value: {{ include "cost-onprem.koku.redis.host" . | quote }}
- name: REDIS_PORT
  value: {{ include "cost-onprem.koku.redis.port" . | quote }}
- name: CELERY_RESULT_EXPIRES
  value: {{ .Values.costManagement.celery.resultExpires | default "28800" | quote }}
- name: INSIGHTS_KAFKA_HOST
  value: {{ include "cost-onprem.koku.kafka.host" . | quote }}
- name: INSIGHTS_KAFKA_PORT
  value: {{ include "cost-onprem.koku.kafka.port" . | quote }}
- name: S3_ENDPOINT
  value: {{ include "cost-onprem.koku.s3.endpoint" . | quote }}
- name: REQUESTED_BUCKET
  value: {{ required "costManagement.storage.bucketName is required" .Values.costManagement.storage.bucketName | quote }}
{{- if eq (include "cost-onprem.platform.isOpenShift" $) "true" }}
- name: AWS_CA_BUNDLE
  value: /etc/pki/ca-trust/combined/ca-bundle.crt
- name: REQUESTS_CA_BUNDLE
  value: /etc/pki/ca-trust/combined/ca-bundle.crt
{{- end }}
- name: AWS_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-onprem.storage.secretName" . }}
      key: access-key
- name: AWS_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-onprem.storage.secretName" . }}
      key: secret-key
- name: DJANGO_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-onprem.koku.django.secretName" . }}
      key: secret-key
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
*/}}
{{- define "cost-onprem.koku.image" -}}
{{- printf "%s:%s" .Values.costManagement.api.image.repository (.Values.costManagement.api.image.tag | default "latest") -}}
{{- end -}}

{{/*
=============================================================================
Validation Helpers
=============================================================================
*/}}

{{/*
Validate Celery Beat replicas (must be exactly 1)
*/}}
{{- define "cost-onprem.koku.celery.beat.validateReplicas" -}}
{{- if ne (.Values.costManagement.celery.beat.replicas | int) 1 -}}
  {{- fail "Celery Beat must have exactly 1 replica. Set costManagement.celery.beat.replicas to 1" -}}
{{- end -}}
{{- end -}}

{{/*
Standard volumeMounts for Koku containers
Includes tmp mount and combined CA bundle when on OpenShift
*/}}
{{- define "cost-onprem.koku.volumeMounts" -}}
- name: tmp
  mountPath: /tmp
{{- if eq (include "cost-onprem.platform.isOpenShift" $) "true" }}
- name: combined-ca-bundle
  mountPath: /etc/pki/ca-trust/combined
  readOnly: true
{{- end }}
{{- end -}}

{{/*
Standard volumes for Koku pods
Includes tmp volume and CA bundle volumes for OpenShift
*/}}
{{- define "cost-onprem.koku.volumes" -}}
- name: tmp
  emptyDir: {}
{{- if eq (include "cost-onprem.platform.isOpenShift" $) "true" }}
- name: ca-scripts
  configMap:
    name: {{ include "cost-onprem.fullname" . }}-ca-combine
    items:
      - key: combine-ca.sh
        path: combine-ca.sh
        mode: 0755
- name: ca-source
  configMap:
    name: {{ include "cost-onprem.fullname" . }}-service-ca
- name: combined-ca-bundle
  emptyDir: {}
{{- end }}
{{- end -}}

{{/*
Init container to combine CA certificates (OpenShift only)
Combines system CA bundle with OpenShift cluster root CA and Service CA for Python SSL verification
*/}}
{{- define "cost-onprem.koku.initContainer.combineCA" -}}
{{- if eq (include "cost-onprem.platform.isOpenShift" $) "true" }}
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
    {{- include "cost-onprem.securityContext.container" . | nindent 4 }}
{{- end }}
{{- end -}}

{{/*
Init container for running Django migrations
This should be added to ONE koku deployment (typically koku-api-reads)
to ensure migrations run before the application starts
*/}}
{{- define "cost-onprem.koku.initContainer.migrations" -}}
- name: run-migrations
  image: "{{ include "cost-onprem.koku.image" . }}"
  imagePullPolicy: {{ .Values.costManagement.api.image.pullPolicy }}
  command:
    - bash
    - -c
    - |
      set -e
      echo "=== Koku Django Migrations Init Container ==="
      echo "Timestamp: $(date)"

      # Wait for database to be ready
      echo "Waiting for database..."
      until timeout 5 bash -c "cat < /dev/null > /dev/tcp/${DATABASE_SERVICE_HOST}/${DATABASE_SERVICE_PORT}" 2>/dev/null; do
        echo "Database not ready, waiting..."
        sleep 2
      done
      echo "Database is ready"

      # Set up environment
      mkdir -p /tmp/prometheus
      cd /opt/koku/koku

      # Run migrations
      echo "Running Django migrations..."
      python manage.py migrate --noinput

      echo "Migrations completed successfully"
  env:
  {{- include "cost-onprem.koku.commonEnv" . | nindent 2 }}
  volumeMounts:
  - name: tmp
    mountPath: /tmp
  securityContext:
    {{- include "cost-onprem.securityContext.container" . | nindent 4 }}
  resources:
    requests:
      cpu: 250m
      memory: 512Mi
    limits:
      cpu: 500m
      memory: 1Gi
{{- end -}}
