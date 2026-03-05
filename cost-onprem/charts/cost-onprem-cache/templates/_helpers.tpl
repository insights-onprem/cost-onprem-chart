{{- define "cost-onprem-cache.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "cost-onprem-cache.fullname" -}}
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

{{- define "cost-onprem-cache.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "cost-onprem-cache.parentChartName" -}}
{{- .Values.global.parentChartName | default "cost-onprem" -}}
{{- end }}

{{- define "cost-onprem-cache.labels" -}}
helm.sh/chart: {{ include "cost-onprem-cache.chart" . }}
{{ include "cost-onprem-cache.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "cost-onprem-cache.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cost-onprem-cache.parentChartName" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: {{ include "cost-onprem-cache.parentChartName" . }}
{{- end }}

{{- define "cost-onprem-cache.fsGroup" -}}
{{- if and (hasKey .Values "securityContext") (hasKey .Values.securityContext "fsGroup") .Values.securityContext.fsGroup -}}
{{- .Values.securityContext.fsGroup -}}
{{- end -}}
{{- end }}
