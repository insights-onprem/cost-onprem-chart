{{/*
Expand the name of the chart.
*/}}
{{- define "cost-mgmt-infra.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "cost-mgmt-infra.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "cost-mgmt-infra.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "cost-mgmt-infra.labels" -}}
helm.sh/chart: {{ include "cost-mgmt-infra.chart" . }}
{{ include "cost-mgmt-infra.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "cost-mgmt-infra.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cost-mgmt-infra.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Service account name (always created)
*/}}
{{- define "cost-mgmt-infra.serviceAccountName" -}}
cost-mgmt-infrastructure
{{- end }}

{{/*
PostgreSQL service name
*/}}
{{- define "cost-mgmt-infra.postgresql.serviceName" -}}
postgres
{{- end -}}

{{/*
PostgreSQL secret name
*/}}
{{- define "cost-mgmt-infra.postgresql.secretName" -}}
{{- if .Values.postgresql.auth.existingSecret -}}
{{- .Values.postgresql.auth.existingSecret -}}
{{- else -}}
postgres-credentials
{{- end -}}
{{- end -}}

{{/*
Detect if running on OpenShift
*/}}
{{- define "cost-mgmt-infra.isOpenShift" -}}
{{- if .Capabilities.APIVersions.Has "route.openshift.io/v1" -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/*
=============================================================================
Compatibility Aliases for cost-mgmt.* functions
(Used by Trino/Redis templates that reference "cost-mgmt" naming)
=============================================================================
*/}}

{{- define "cost-mgmt.fullname" -}}
{{- include "cost-mgmt-infra.fullname" . -}}
{{- end -}}

{{- define "cost-mgmt.labels" -}}
{{- include "cost-mgmt-infra.labels" . -}}
{{- end -}}

{{- define "cost-mgmt.selectorLabels" -}}
{{- include "cost-mgmt-infra.selectorLabels" . -}}
{{- end -}}
