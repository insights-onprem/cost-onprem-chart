{{/*
Expand the name of the chart.
*/}}
{{/* prettier-ignore */}}
{{- define "cost-onprem.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "cost-onprem.fullname" -}}
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
{{- define "cost-onprem.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "cost-onprem.labels" -}}
helm.sh/chart: {{ include "cost-onprem.chart" . }}
{{ include "cost-onprem.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "cost-onprem.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cost-onprem.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: {{ include "cost-onprem.name" . }}
{{- end }}

{{/*
Database host resolver - returns unified database service name if "internal", otherwise returns the configured host
Since all databases (ros, kruize, koku) are on the same unified server, this returns a single common host.
Usage: {{ include "cost-onprem.database.host" . }}
*/}}
{{- define "cost-onprem.database.host" -}}
  {{- if eq .Values.database.server.host "internal" -}}
{{- printf "%s-database" (include "cost-onprem.fullname" .) -}}
  {{- else -}}
{{- .Values.database.server.host -}}
  {{- end -}}
{{- end }}

{{/*
Get the database URL - returns complete postgresql connection string
Uses $(DB_USER) and $(DB_PASSWORD) environment variables for credentials
*/}}
{{- define "cost-onprem.database.url" -}}
{{- printf "postgresql://$(DB_USER):$(DB_PASSWORD)@%s:%s/%s?sslmode=%s" (include "cost-onprem.database.host" .) (.Values.database.server.port | toString) (include "cost-onprem.database.ros.name" .) .Values.database.server.sslMode }}
{{- end }}

{{/*
Get the kruize database host - returns unified database service name (alias for backward compatibility)
*/}}
{{- define "cost-onprem.kruize.databaseHost" -}}
{{- include "cost-onprem.database.host" . -}}
{{- end }}

{{/*
Get the default database credentials secret name (chart-managed secret)
Usage: {{ include "cost-onprem.database.defaultSecretName" . }}
*/}}
{{- define "cost-onprem.database.defaultSecretName" -}}
{{- printf "%s-db-credentials" (include "cost-onprem.fullname" .) -}}
{{- end -}}

{{/*
Get the database credentials secret name - returns existingSecret if set, otherwise returns generated secret name
Usage: {{ include "cost-onprem.database.secretName" . }}
*/}}
{{- define "cost-onprem.database.secretName" -}}
{{- if .Values.database.existingSecret -}}
{{- .Values.database.existingSecret -}}
{{- else -}}
{{- include "cost-onprem.database.defaultSecretName" . -}}
{{- end -}}
{{- end }}

{{/*
=============================================================================
Database Name Helpers
=============================================================================
Standardized database name accessors for all services.
Naming convention: costonprem_<service> (underscores for PostgreSQL compatibility)
*/}}

{{/*
ROS database name
Usage: {{ include "cost-onprem.database.ros.name" . }}
*/}}
{{- define "cost-onprem.database.ros.name" -}}
{{- .Values.database.ros.name | default "costonprem_ros" -}}
{{- end -}}

{{/*
Kruize database name
Usage: {{ include "cost-onprem.database.kruize.name" . }}
*/}}
{{- define "cost-onprem.database.kruize.name" -}}
{{- .Values.database.kruize.name | default "costonprem_kruize" -}}
{{- end -}}

{{/*
Koku database name
Usage: {{ include "cost-onprem.database.koku.name" . }}
*/}}
{{- define "cost-onprem.database.koku.name" -}}
{{- .Values.database.koku.name | default "costonprem_koku" -}}
{{- end -}}

{{/*
Get OpenShift cluster domain from values (detected by install script; default allows offline templating)
Usage: {{ include "cost-onprem.platform.clusterDomain" . }}
*/}}
{{- define "cost-onprem.platform.clusterDomain" -}}
{{- .Values.global.clusterDomain | default "apps.cluster.local" -}}
{{- end }}

{{/*
Get volume mode from values (detected by install script; default allows offline templating)
Usage: {{ include "cost-onprem.storage.volumeMode" . }}
*/}}
{{- define "cost-onprem.storage.volumeMode" -}}
{{- .Values.global.volumeMode | default "Filesystem" -}}
{{- end }}

{{/*
Get storage class name from values (detected by install script; default allows offline templating)
Usage: {{ include "cost-onprem.storage.class" . }}
*/}}
{{- define "cost-onprem.storage.class" -}}
{{- .Values.global.storageClass | default "ocs-storagecluster-ceph-rbd" -}}
{{- end }}

{{/*
Get storage class for database workloads (same as main storage class)
Usage: {{ include "cost-onprem.storage.databaseClass" . }}
*/}}
{{- define "cost-onprem.storage.databaseClass" -}}
{{- include "cost-onprem.storage.class" . -}}
{{- end }}

{{/*
Cache service name (valkey)
*/}}
{{- define "cost-onprem.cache.name" -}}
valkey
{{- end }}

{{/*
Resolve object storage config from .Values.objectStorage.
Returns a dict with keys: endpoint, port, useSSL, existingSecret, s3Region
*/}}
{{- define "cost-onprem.storage.config" -}}
  {{- $os := dict "endpoint" "" "port" 443 "useSSL" true "existingSecret" "" "s3Region" "onprem" -}}
  {{- if .Values.objectStorage -}}
    {{- if and .Values.objectStorage.endpoint (ne .Values.objectStorage.endpoint "") -}}
      {{- $_ := set $os "endpoint" .Values.objectStorage.endpoint -}}
    {{- end -}}
    {{- if .Values.objectStorage.port -}}
      {{- $_ := set $os "port" .Values.objectStorage.port -}}
    {{- end -}}
    {{- if hasKey .Values.objectStorage "useSSL" -}}
      {{- $_ := set $os "useSSL" .Values.objectStorage.useSSL -}}
    {{- end -}}
    {{- if and .Values.objectStorage.existingSecret (ne .Values.objectStorage.existingSecret "") -}}
      {{- $_ := set $os "existingSecret" .Values.objectStorage.existingSecret -}}
    {{- end -}}
    {{- if and .Values.objectStorage.s3 .Values.objectStorage.s3.region -}}
      {{- $_ := set $os "s3Region" .Values.objectStorage.s3.region -}}
    {{- end -}}
  {{- end -}}
  {{- $os | toJson -}}
{{- end }}

{{/*
Storage endpoint (S3-compatible) from values (detected by install script; default allows offline templating)
*/}}
{{- define "cost-onprem.storage.endpoint" -}}
{{- $cfg := include "cost-onprem.storage.config" . | fromJson -}}
{{- $cfg.endpoint | default "s3.openshift-storage.svc.cluster.local" -}}
{{- end }}

{{/*
Storage port (S3 port)
*/}}
{{- define "cost-onprem.storage.port" -}}
{{- $cfg := include "cost-onprem.storage.config" . | fromJson -}}
{{- $cfg.port -}}
{{- end }}

{{/*
Storage endpoint with protocol and port for S3 connections.
Constructs the full S3 endpoint URL including protocol and port.

Returns examples:
  - HTTPS (useSSL=true):  https://s3.openshift-storage.svc:443
  - HTTP (useSSL=false):  http://minio.minio-test.svc.cluster.local
*/}}
{{- define "cost-onprem.storage.endpointWithProtocol" -}}
{{- $endpoint := include "cost-onprem.storage.endpoint" . -}}
{{- $port := include "cost-onprem.storage.port" . -}}
{{- $cfg := include "cost-onprem.storage.config" . | fromJson -}}
{{- $useSSL := $cfg.useSSL -}}

{{- if $useSSL -}}
  {{- if eq (toString $port) "443" -}}
https://{{ $endpoint }}
  {{- else -}}
https://{{ $endpoint }}:{{ $port }}
  {{- end -}}
{{- else -}}
  {{- if eq (toString $port) "80" -}}
http://{{ $endpoint }}
  {{- else -}}
http://{{ $endpoint }}:{{ $port }}
  {{- end -}}
{{- end -}}
{{- end }}

{{/*
Storage bucket name (staging bucket for ingress uploads)
*/}}
{{- define "cost-onprem.storage.bucket" -}}
{{- .Values.ingress.storage.bucket | default "insights-upload-perma" -}}
{{- end }}

{{/*
Koku cost data bucket name
*/}}
{{- define "cost-onprem.storage.kokuBucket" -}}
{{- required "costManagement.storage.bucketName is required" .Values.costManagement.storage.bucketName -}}
{{- end -}}

{{/*
ROS (Resource Optimization Service) data bucket name
*/}}
{{- define "cost-onprem.storage.rosBucket" -}}
{{- .Values.costManagement.storage.rosBucketName | default "ros-data" -}}
{{- end -}}

{{/*
Storage use SSL flag
*/}}
{{- define "cost-onprem.storage.useSSL" -}}
{{- $cfg := include "cost-onprem.storage.config" . | fromJson -}}
{{- $cfg.useSSL -}}
{{- end }}

{{/*
S3 region for signature generation
*/}}
{{- define "cost-onprem.storage.s3Region" -}}
{{- $cfg := include "cost-onprem.storage.config" . | fromJson -}}
{{- $cfg.s3Region -}}
{{- end }}

{{/*
Storage credentials secret name.
Uses existingSecret if set, otherwise generates '<release>-storage-credentials'.
*/}}
{{- define "cost-onprem.storage.secretName" -}}
{{- $cfg := include "cost-onprem.storage.config" . | fromJson -}}
{{- if ne $cfg.existingSecret "" -}}
{{- $cfg.existingSecret -}}
{{- else -}}
{{- printf "%s-storage-credentials" (include "cost-onprem.fullname" .) -}}
{{- end -}}
{{- end }}

{{/*
Check if user provided an existing secret for storage credentials.
Returns "true" or "false" as string.
*/}}
{{- define "cost-onprem.storage.hasExistingSecret" -}}
{{- $cfg := include "cost-onprem.storage.config" . | fromJson -}}
{{- if ne $cfg.existingSecret "" -}}true{{- else -}}false{{- end -}}
{{- end }}

{{/*
Keycloak Dynamic Configuration Helpers
*/}}

{{- define "cost-onprem.keycloak.isInstalled" -}}
{{- if and .Values.jwtAuth .Values.jwtAuth.keycloak (hasKey .Values.jwtAuth.keycloak "installed") -}}
{{- .Values.jwtAuth.keycloak.installed -}}
{{- else -}}
true
{{- end -}}
{{- end }}

{{- define "cost-onprem.keycloak.namespace" -}}
{{- .Values.jwtAuth.keycloak.namespace | default "keycloak" -}}
{{- end }}

{{- define "cost-onprem.keycloak.serviceName" -}}
{{- if and .Values.jwtAuth .Values.jwtAuth.keycloak .Values.jwtAuth.keycloak.serviceName -}}
{{- .Values.jwtAuth.keycloak.serviceName -}}
{{- else -}}
keycloak-service
{{- end -}}
{{- end }}

{{- define "cost-onprem.keycloak.url" -}}
{{- if and .Values.jwtAuth .Values.jwtAuth.keycloak .Values.jwtAuth.keycloak.url -}}
{{- .Values.jwtAuth.keycloak.url -}}
{{- else -}}
https://keycloak.keycloak.svc.cluster.local
{{- end -}}
{{- end }}

{{- define "cost-onprem.keycloak.issuerUrl" -}}
{{- printf "%s/realms/%s" (include "cost-onprem.keycloak.url" .) (.Values.jwtAuth.keycloak.realm | default "cost-management") -}}
{{- end }}

{{- define "cost-onprem.keycloak.jwksUrl" -}}
{{- printf "%s/protocol/openid-connect/certs" (include "cost-onprem.keycloak.issuerUrl" .) -}}
{{- end }}

{{- define "cost-onprem.keycloak.crInfo" -}}
configuredVia: values
apiVersion: k8s.keycloak.org/v2alpha1
operator: RHBK
{{- end }}

{{/*
Kafka service host resolver (supports both internal Strimzi and external Kafka)
*/}}
{{- define "cost-onprem.kafka.host" -}}
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
{{- .Release.Name }}-kafka-kafka-bootstrap.kafka.svc.cluster.local
{{- end -}}
{{- end }}

{{/*
Kafka port resolver (supports both internal Strimzi and external Kafka)
*/}}
{{- define "cost-onprem.kafka.port" -}}
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
Kafka bootstrap servers resolver (supports both internal Strimzi and external Kafka)
*/}}
{{- define "cost-onprem.kafka.bootstrapServers" -}}
{{- if .Values.kafka.bootstrapServers -}}
{{- .Values.kafka.bootstrapServers -}}
{{- else -}}
{{- .Release.Name }}-kafka-kafka-bootstrap.kafka.svc.cluster.local:9092
{{- end -}}
{{- end }}

{{/*
Kafka security protocol resolver (supports both internal Strimzi and external Kafka)
*/}}
{{- define "cost-onprem.kafka.securityProtocol" -}}
{{- .Values.kafka.securityProtocol | default "PLAINTEXT" -}}
{{- end }}

{{/*
Valkey fsGroup from values (install script sets valkey.securityContext.fsGroup on OpenShift from namespace annotations)
*/}}
{{- define "cost-onprem.valkey.fsGroup" -}}
{{- if and (hasKey .Values.valkey "securityContext") (hasKey .Values.valkey.securityContext "fsGroup") .Values.valkey.securityContext.fsGroup -}}
{{- .Values.valkey.securityContext.fsGroup -}}
{{- end -}}
{{- end }}

{{/*
Gateway service name
Returns the fully qualified gateway service name
Usage: {{ include "cost-onprem.gateway.serviceName" . }}
*/}}
{{- define "cost-onprem.gateway.serviceName" -}}
{{- printf "%s-gateway" (include "cost-onprem.fullname" .) -}}
{{- end }}

{{/*
Gateway service port
Returns the gateway service port for HTTP traffic
Usage: {{ include "cost-onprem.gateway.servicePort" . }}
*/}}
{{- define "cost-onprem.gateway.servicePort" -}}
{{- .Values.jwtAuth.envoy.servicePort | default 80 -}}
{{- end }}

{{/*
Gateway Envoy configmap name
Returns the fully qualified gateway Envoy configmap name
Usage: {{ include "cost-onprem.gateway.configMapName" . }}
*/}}
{{- define "cost-onprem.gateway.configMapName" -}}
{{- printf "%s-envoy-config" (include "cost-onprem.gateway.serviceName" .) -}}
{{- end }}