{{/*
Chart name, possibly overridden. Truncated to 63 chars because K8s names
have a 63-char limit (DNS-1123 label).
*/}}
{{- define "parsely-api.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name = release name + chart name. Used as the base
name for every resource the chart creates, so two releases of the same
chart don't collide.
*/}}
{{- define "parsely-api.fullname" -}}
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
{{- end -}}

{{/*
Standard labels — go on every resource the chart creates so they're
easy to query: `kubectl get all -l app.kubernetes.io/instance=parsely`.
*/}}
{{- define "parsely-api.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "parsely-api.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Selector labels — the minimal stable subset. Selectors are IMMUTABLE
once a Deployment is created, so this set must NEVER include version or
chart-version (which change on upgrade).
*/}}
{{- define "parsely-api.selectorLabels" -}}
app.kubernetes.io/name: {{ include "parsely-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
