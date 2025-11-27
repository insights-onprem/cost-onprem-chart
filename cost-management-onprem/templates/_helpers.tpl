{{/*
Expand the name of the chart.
*/}}
{{- define "cost-management-onprem.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "cost-management-onprem.fullname" -}}
  {{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
  {{- else -}}
    {{- $name := default .Chart.Name .Values.nameOverride -}}
    {{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
    {{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
    {{- end -}}
  {{- end -}}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "cost-management-onprem.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "cost-management-onprem.labels" -}}
helm.sh/chart: {{ include "cost-management-onprem.chart" . }}
{{ include "cost-management-onprem.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "cost-management-onprem.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cost-management-onprem.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "cost-management-onprem.serviceAccountName" -}}
  {{- if .Values.serviceAccount.create -}}
{{- default (include "cost-management-onprem.fullname" .) .Values.serviceAccount.name -}}
  {{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
  {{- end -}}
{{- end }}

{{/*
Detect if running on OpenShift
*/}}
{{- define "cost-management-onprem.isOpenShift" -}}
{{- if .Capabilities.APIVersions.Has "route.openshift.io/v1" -}}
true
{{- else -}}
false
{{- end -}}
{{- end }}

{{/*
Cache service name (redis or valkey based on platform)
*/}}
{{- define "cost-management-onprem.cache.name" -}}
{{- if eq (include "cost-management-onprem.isOpenShift" .) "true" -}}
valkey
{{- else -}}
redis
{{- end -}}
{{- end }}

{{/*
Get the Sources database host
*/}}
{{- define "cost-management-onprem.sourcesDatabaseHost" -}}
{{- if eq .Values.database.sources.host "internal" -}}
{{- printf "%s-db-sources" (include "cost-management-onprem.fullname" .) -}}
{{- else -}}
{{- .Values.database.sources.host -}}
{{- end -}}
{{- end }}

{{/*
Kafka host resolver
*/}}
{{- define "cost-management-onprem.kafkaHost" -}}
{{- if .Values.kafka.bootstrapServers -}}
  {{- $bootstrapServers := .Values.kafka.bootstrapServers -}}
  {{- if contains "," $bootstrapServers -}}
    {{- $firstServer := regexFind "^[^,]+" $bootstrapServers -}}
    {{- if contains ":" $firstServer -}}
{{- regexFind "^[^:]+" $firstServer -}}
    {{- else -}}
{{- $firstServer -}}
    {{- end -}}
  {{- else -}}
    {{- if contains ":" $bootstrapServers -}}
{{- regexFind "^[^:]+" $bootstrapServers -}}
    {{- else -}}
{{- $bootstrapServers -}}
    {{- end -}}
  {{- end -}}
{{- else -}}
kafka
{{- end -}}
{{- end }}

{{/*
Kafka port resolver
*/}}
{{- define "cost-management-onprem.kafkaPort" -}}
{{- if .Values.kafka.bootstrapServers -}}
  {{- $bootstrapServers := .Values.kafka.bootstrapServers -}}
  {{- if contains "," $bootstrapServers -}}
    {{- $firstServer := regexFind "^[^,]+" $bootstrapServers -}}
    {{- if contains ":" $firstServer -}}
{{- regexFind "[^:]+$" $firstServer -}}
    {{- else -}}
9092
    {{- end -}}
  {{- else -}}
    {{- if contains ":" $bootstrapServers -}}
{{- regexFind "[^:]+$" $bootstrapServers -}}
    {{- else -}}
9092
    {{- end -}}
  {{- end -}}
{{- else -}}
9092
{{- end -}}
{{- end }}

{{/*
Backwards compatibility aliases for cost-mgmt.* naming
*/}}
{{- define "cost-mgmt.name" -}}
{{- include "cost-management-onprem.name" . -}}
{{- end }}

{{- define "cost-mgmt.fullname" -}}
{{- include "cost-management-onprem.fullname" . -}}
{{- end }}

{{- define "cost-mgmt.chart" -}}
{{- include "cost-management-onprem.chart" . -}}
{{- end }}

{{- define "cost-mgmt.labels" -}}
{{- include "cost-management-onprem.labels" . -}}
{{- end }}

{{- define "cost-mgmt.selectorLabels" -}}
{{- include "cost-management-onprem.selectorLabels" . -}}
{{- end }}

{{/*
Storage (S3) endpoint
*/}}
{{- define "cost-mgmt.storage.endpoint" -}}
{{- .Values.costManagement.s3Endpoint | default "" -}}
{{- end }}

{{/*
Storage credentials secret name
*/}}
{{- define "cost-mgmt.storage.secretName" -}}
{{- printf "%s-storage-credentials" (include "cost-management-onprem.fullname" .) -}}
{{- end }}

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
Alias for isOpenShift (short form)
*/}}
{{- define "cost-mgmt.isOpenShift" -}}
{{- include "cost-management-onprem.isOpenShift" . -}}
{{- end -}}
