{{- define "cost-onprem-database.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "cost-onprem-database.fullname" -}}
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

{{- define "cost-onprem-database.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "cost-onprem-database.parentChartName" -}}
{{- .Values.global.parentChartName | default "cost-onprem" -}}
{{- end }}

{{- define "cost-onprem-database.parentFullname" -}}
{{- $parentName := include "cost-onprem-database.parentChartName" . -}}
{{- if contains $parentName .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $parentName | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end }}

{{- define "cost-onprem-database.labels" -}}
helm.sh/chart: {{ include "cost-onprem-database.chart" . }}
{{ include "cost-onprem-database.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "cost-onprem-database.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cost-onprem-database.parentChartName" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: {{ include "cost-onprem-database.parentChartName" . }}
{{- end }}

{{- define "cost-onprem-database.secretName" -}}
{{- if .Values.secretName -}}
{{- .Values.secretName -}}
{{- else -}}
{{- printf "%s-db-credentials" (include "cost-onprem-database.parentFullname" .) -}}
{{- end -}}
{{- end }}

{{- define "cost-onprem-database.storageClass" -}}
{{- .Values.global.storageClass | default "ocs-storagecluster-ceph-rbd" -}}
{{- end }}

{{- define "cost-onprem-database.volumeMode" -}}
{{- .Values.global.volumeMode | default "Filesystem" -}}
{{- end }}
