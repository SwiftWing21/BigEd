{{/*
BigEd CC Helm chart — common template helpers.
*/}}

{{/*
Expand the name of the chart.
*/}}
{{- define "biged-cc.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
Truncated at 63 chars because Kubernetes name fields are limited to this.
*/}}
{{- define "biged-cc.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "biged-cc.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "biged-cc.labels" -}}
helm.sh/chart: {{ include "biged-cc.chart" . }}
{{ include "biged-cc.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels (stable across upgrades — used in matchLabels).
*/}}
{{- define "biged-cc.selectorLabels" -}}
app.kubernetes.io/name: {{ include "biged-cc.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Selector labels for a specific component.
*/}}
{{- define "biged-cc.componentLabels" -}}
{{ include "biged-cc.selectorLabels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Full labels for a specific component (includes common + component).
*/}}
{{- define "biged-cc.componentFullLabels" -}}
{{ include "biged-cc.labels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Name of the Secret containing API keys.
*/}}
{{- define "biged-cc.secretName" -}}
{{ include "biged-cc.fullname" . }}-secrets
{{- end }}

{{/*
Name of the ConfigMap containing fleet.toml.
*/}}
{{- define "biged-cc.configmapName" -}}
{{ include "biged-cc.fullname" . }}-config
{{- end }}

{{/*
Resolve max_workers from preset if not explicitly overridden.
Matches the RAM-based worker scaling table from CLAUDE.md.
*/}}
{{- define "biged-cc.maxWorkers" -}}
{{- $preset := .Values.config.preset -}}
{{- if eq $preset "minimal" }}3
{{- else if eq $preset "basic" }}6
{{- else if eq $preset "standard" }}10
{{- else if eq $preset "high" }}13
{{- else if eq $preset "server" }}16
{{- else }}{{ .Values.config.maxWorkers }}
{{- end }}
{{- end }}

{{/*
Resolve worker memory_limit_mb from preset.
*/}}
{{- define "biged-cc.workerMemoryLimit" -}}
{{- $preset := .Values.config.preset -}}
{{- if eq $preset "minimal" }}256
{{- else if eq $preset "basic" }}384
{{- else if eq $preset "standard" }}512
{{- else if eq $preset "high" }}512
{{- else if eq $preset "server" }}768
{{- else }}{{ .Values.config.workerMemoryLimitMb }}
{{- end }}
{{- end }}

{{/*
Ollama internal service URL.
*/}}
{{- define "biged-cc.ollamaUrl" -}}
http://{{ include "biged-cc.fullname" . }}-ollama:{{ .Values.ollama.port }}
{{- end }}
