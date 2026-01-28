{{/*
Reusable Init Container Templates
*/}}

{{/*
Wait for Database init container - waits for unified database server
Usage: {{ include "cost-onprem.initContainer.waitForDb" (list . "ros") | nindent 8 }}
Parameters:
  - Root context (.)
  - Database type ("ros", "kruize", "koku") - used for naming only
*/}}
{{- define "cost-onprem.initContainer.waitForDb" -}}
{{- $root := index . 0 -}}
{{- $dbType := index . 1 -}}
- name: wait-for-database
  image: "{{ $root.Values.global.initContainers.waitFor.repository }}:{{ $root.Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" $root | nindent 4 }}
  command: ['bash', '-c']
  args:
    - |
      echo "Waiting for unified database server at {{ include "cost-onprem.fullname" $root }}-database:{{ $root.Values.database.server.port }}..."
      until timeout 3 bash -c "echo > /dev/tcp/{{ include "cost-onprem.fullname" $root }}-database/{{ $root.Values.database.server.port }}" 2>/dev/null; do
        echo "Database server not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "Database server is ready"
{{- end -}}

{{/*
Wait for Kafka init container
Usage: {{ include "cost-onprem.initContainer.waitForKafka" . | nindent 8 }}
*/}}
{{- define "cost-onprem.initContainer.waitForKafka" -}}
- name: wait-for-kafka
  image: "{{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" . | nindent 4 }}
  command: ['bash', '-c']
  args:
    - |
      echo "Waiting for Kafka at {{ include "cost-onprem.kafka.host" . }}:{{ include "cost-onprem.kafka.port" . }}..."
      until timeout 3 bash -c "echo > /dev/tcp/{{ include "cost-onprem.kafka.host" . }}/{{ include "cost-onprem.kafka.port" . }}" 2>/dev/null; do
        echo "Kafka not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "Kafka is ready"
{{- end -}}

{{/*
Wait for Storage (ODF) init container
Usage: {{ include "cost-onprem.initContainer.waitForStorage" . | nindent 8 }}
*/}}
{{- define "cost-onprem.initContainer.waitForStorage" -}}
- name: wait-for-storage
  image: "{{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" . | nindent 4 }}
  command: ['bash', '-c']
  args:
    - |
      echo "Waiting for ODF S3 endpoint at {{ include "cost-onprem.storage.endpoint" . }}:{{ include "cost-onprem.storage.port" . }}..."
      until timeout 3 bash -c "echo > /dev/tcp/{{ include "cost-onprem.storage.endpoint" . }}/{{ include "cost-onprem.storage.port" . }}" 2>/dev/null; do
        echo "ODF S3 endpoint not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "ODF S3 endpoint is ready"
{{- end -}}

{{/*
Prepare CA bundle init container (for JWT/Keycloak TLS validation)
Usage: {{ include "cost-onprem.initContainer.prepareCABundle" . | nindent 8 }}
*/}}
{{- define "cost-onprem.initContainer.prepareCABundle" -}}
- name: prepare-ca-bundle
  image: "{{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" . | nindent 4 }}
  command: ['bash', '/scripts/combine-ca.sh']
  volumeMounts:
    - name: ca-scripts
      mountPath: /scripts
      readOnly: true
    - name: ca-source
      mountPath: /ca-source
      readOnly: true
    - name: ca-bundle
      mountPath: /ca-output
{{- end -}}

{{/*
Wait for Kruize init container
Usage: {{ include "cost-onprem.initContainer.waitForKruize" . | nindent 8 }}
*/}}
{{- define "cost-onprem.initContainer.waitForKruize" -}}
- name: wait-for-kruize
  image: "{{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" . | nindent 4 }}
  command: ['bash', '-c']
  args:
    - |
      echo "Waiting for Kruize at {{ include "cost-onprem.fullname" . }}-kruize:{{ .Values.kruize.port }}..."
      until timeout 3 bash -c "echo > /dev/tcp/{{ include "cost-onprem.fullname" . }}-kruize/{{ .Values.kruize.port }}" 2>/dev/null; do
        echo "Kruize not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "Kruize is ready"
{{- end -}}

{{/*
Wait for Koku API init container - waits for both reads and writes services
Usage: {{ include "cost-onprem.initContainer.waitForKoku" . | nindent 8 }}
*/}}
{{- define "cost-onprem.initContainer.waitForKoku" -}}
- name: wait-for-koku
  image: "{{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" . | nindent 4 }}
  command: ['bash', '-c']
  args:
    - |
      KOKU_API_PORT="{{ .Values.costManagement.api.service.port }}"
      
      echo "Waiting for Koku API Reads at {{ include "cost-onprem.fullname" . }}-koku-api-reads:${KOKU_API_PORT}..."
      until timeout 3 bash -c "echo > /dev/tcp/{{ include "cost-onprem.fullname" . }}-koku-api-reads/${KOKU_API_PORT}" 2>/dev/null; do
        echo "Koku API Reads not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "Koku API Reads is ready"
      
      echo "Waiting for Koku API Writes at {{ include "cost-onprem.fullname" . }}-koku-api-writes:${KOKU_API_PORT}..."
      until timeout 3 bash -c "echo > /dev/tcp/{{ include "cost-onprem.fullname" . }}-koku-api-writes/${KOKU_API_PORT}" 2>/dev/null; do
        echo "Koku API Writes not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "Koku API Writes is ready"
      
      echo "Both Koku API services are ready"
{{- end -}}

{{/*
Wait for Cache (Valkey) init container
Usage: {{ include "cost-onprem.initContainer.waitForCache" . | nindent 8 }}
*/}}
{{- define "cost-onprem.initContainer.waitForCache" -}}
{{- $cacheName := include "cost-onprem.cache.name" . -}}
- name: wait-for-{{ $cacheName }}
  image: "{{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" . | nindent 4 }}
  command: ['bash', '-c']
  args:
    - |
      echo "Waiting for {{ $cacheName | title }} at {{ include "cost-onprem.fullname" . }}-{{ $cacheName }}:6379..."
      until timeout 3 bash -c "echo > /dev/tcp/{{ include "cost-onprem.fullname" . }}-{{ $cacheName }}/6379" 2>/dev/null; do
        echo "{{ $cacheName | title }} not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "{{ $cacheName | title }} is ready"
{{- end -}}

{{/*
Wait for Infrastructure PostgreSQL init container (for Koku and Sources API)
This waits for the PostgreSQL database to be ready
Usage: {{ include "cost-onprem.initContainer.waitForInfraDb" . | nindent 8 }}
*/}}
{{- define "cost-onprem.initContainer.waitForInfraDb" -}}
- name: wait-for-infra-db
  image: "{{ .Values.global.initContainers.waitFor.repository }}:{{ .Values.global.initContainers.waitFor.tag }}"
  securityContext:
    {{- include "cost-onprem.securityContext.nonRoot" . | nindent 4 }}
  command: ['bash', '-c']
  args:
    - |
      DB_HOST="{{ include "cost-onprem.koku.database.host" . }}"
      DB_PORT="{{ include "cost-onprem.koku.database.port" . }}"
      echo "Waiting for infrastructure PostgreSQL at ${DB_HOST}:${DB_PORT}..."
      until timeout 3 bash -c "echo > /dev/tcp/${DB_HOST}/${DB_PORT}" 2>/dev/null; do
        echo "Infrastructure PostgreSQL not ready yet, retrying in 5 seconds..."
        sleep 5
      done
      echo "Infrastructure PostgreSQL is ready"
{{- end -}}
