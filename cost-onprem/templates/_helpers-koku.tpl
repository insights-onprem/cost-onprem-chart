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
Koku API deployment name (unified - handles both reads and writes)
*/}}
{{- define "cost-onprem.koku.api.name" -}}
{{- printf "%s-koku-api" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
Koku MASU (cost processor) name
*/}}
{{- define "cost-onprem.koku.masu.name" -}}
{{- printf "%s-koku-masu" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
Koku Kafka listener name
*/}}
{{- define "cost-onprem.koku.listener.name" -}}
{{- printf "%s-koku-listener" (include "cost-onprem.fullname" .) -}}
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
Cloud provider support (AWS, Azure, GCP). When false, cloud-only celery workers
(download, refresh, hcs, subs*) are not deployed. Hard-coded until cloud support
is introduced (FLPATH-3098).
*/}}
{{- define "cost-onprem.koku.cloudProviderSupported" -}}
false
{{- end -}}

{{/*
=============================================================================
Internal Port Constants
=============================================================================
These are hardcoded application defaults - not configurable via values.yaml
because the koku application itself uses fixed ports.
*/}}

{{/*
Koku API container port (hardcoded in Django/gunicorn)
*/}}
{{- define "cost-onprem.koku.api.port" -}}
8000
{{- end -}}

{{/*
Koku metrics/probes port (hardcoded in the application)
*/}}
{{- define "cost-onprem.koku.metrics.port" -}}
9000
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
{{- .Values.database.koku.name | default "costonprem_koku" -}}
{{- end -}}

{{/*
=============================================================================
Valkey Connection Helpers (cache/broker)
=============================================================================
*/}}

{{/*
Valkey host
*/}}
{{- define "cost-onprem.koku.valkey.host" -}}
{{- printf "%s-valkey" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
Valkey port
*/}}
{{- define "cost-onprem.koku.valkey.port" -}}
{{- .Values.valkey.port | default 6379 -}}
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
Kafka port
Uses configurable value from .Values.costManagement.kafka.port
*/}}
{{- define "cost-onprem.koku.kafka.port" -}}
{{- .Values.costManagement.kafka.port | default "9092" -}}
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
Note: We don't add component here - each resource defines its own specific component
*/}}
{{- define "cost-onprem.koku.labels" -}}
{{ include "cost-onprem.labels" . }}
{{- end -}}

{{/*
Selector labels for Koku API (unified)
*/}}
{{- define "cost-onprem.koku.api.selectorLabels" -}}
{{ include "cost-onprem.selectorLabels" . }}
app.kubernetes.io/component: cost-management-api
{{- end -}}

{{/*
Selector labels for Celery Beat
*/}}
{{- define "cost-onprem.koku.celery.beat.selectorLabels" -}}
{{ include "cost-onprem.selectorLabels" . }}
app.kubernetes.io/component: cost-scheduler
cost-onprem.io/celery-type: beat
{{- end -}}

{{/*
Full labels for Celery Worker (includes all koku labels + component labels)
Usage: {{ include "cost-onprem.koku.celery.worker.labels" (dict "context" . "type" "default") }}
*/}}
{{- define "cost-onprem.koku.celery.worker.labels" -}}
{{- $context := .context -}}
{{- $type := .type -}}
{{ include "cost-onprem.koku.labels" $context }}
app.kubernetes.io/component: cost-worker
cost-onprem.io/celery-type: worker
cost-onprem.io/worker-queue: {{ $type }}
{{- end -}}

{{/*
Selector labels for Celery Worker (takes worker type as argument)
Usage: {{ include "cost-onprem.koku.celery.worker.selectorLabels" (dict "context" . "type" "default") }}
*/}}
{{- define "cost-onprem.koku.celery.worker.selectorLabels" -}}
{{- $context := .context -}}
{{- $type := .type -}}
{{ include "cost-onprem.selectorLabels" $context }}
app.kubernetes.io/component: cost-worker
cost-onprem.io/celery-type: worker
cost-onprem.io/worker-queue: {{ $type }}
{{- end -}}

{{/*
Selector labels for Koku MASU (cost processor)
*/}}
{{- define "cost-onprem.koku.masu.selectorLabels" -}}
{{ include "cost-onprem.selectorLabels" . }}
app.kubernetes.io/component: cost-processor
{{- end -}}

{{/*
Selector labels for Koku Listener
*/}}
{{- define "cost-onprem.koku.listener.selectorLabels" -}}
{{ include "cost-onprem.selectorLabels" . }}
app.kubernetes.io/component: listener
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
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-onprem.koku.database.secretName" . }}
      key: koku-user
- name: DATABASE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "cost-onprem.koku.database.secretName" . }}
      key: koku-password
# Valkey connection
- name: REDIS_HOST
  value: {{ include "cost-onprem.koku.valkey.host" . | quote }}
- name: REDIS_PORT
  value: {{ include "cost-onprem.koku.valkey.port" . | quote }}
- name: CELERY_RESULT_EXPIRES
  value: {{ .Values.costManagement.celery.resultExpires | default "28800" | quote }}
- name: INSIGHTS_KAFKA_HOST
  value: {{ include "cost-onprem.koku.kafka.host" . | quote }}
- name: INSIGHTS_KAFKA_PORT
  value: {{ include "cost-onprem.koku.kafka.port" . | quote }}
- name: S3_ENDPOINT
  value: {{ include "cost-onprem.storage.endpointWithProtocol" . | quote }}
- name: REQUESTED_BUCKET
  value: {{ include "cost-onprem.storage.kokuBucket" . | quote }}
- name: REQUESTED_ROS_BUCKET
  value: {{ include "cost-onprem.storage.rosBucket" . | quote }}
- name: AWS_CA_BUNDLE
  value: /etc/pki/ca-trust/combined/ca-bundle.crt
- name: REQUESTS_CA_BUNDLE
  value: /etc/pki/ca-trust/combined/ca-bundle.crt
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
# S3 Region for signature generation (required for S3v4 signatures)
# Most on-premise S3 backends don't use regions, but boto3 requires it for signature calculation
- name: S3_REGION
  value: {{ include "cost-onprem.storage.s3Region" . | quote }}
# AWS SDK configuration for S3v4 signatures
- name: AWS_CONFIG_FILE
  value: /etc/aws/config
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
Includes tmp mount and combined CA bundle
*/}}
{{- define "cost-onprem.koku.volumeMounts" -}}
- name: tmp
  mountPath: /tmp
- name: aws-config
  mountPath: /etc/aws
  readOnly: true
- name: combined-ca-bundle
  mountPath: /etc/pki/ca-trust/combined
  readOnly: true
{{- end -}}

{{/*
Standard volumes for Koku pods
Includes tmp volume and CA bundle volumes
*/}}
{{- define "cost-onprem.koku.volumes" -}}
- name: tmp
  emptyDir: {}
- name: aws-config
  configMap:
    name: {{ include "cost-onprem.fullname" . }}-aws-config
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
{{- end -}}

{{/*
Init container to combine CA certificates
Combines system CA bundle with OpenShift cluster root CA and Service CA for Python SSL verification
*/}}
{{- define "cost-onprem.koku.initContainer.combineCA" -}}
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
{{- end -}}

