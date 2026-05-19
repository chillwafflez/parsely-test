# Reads api-py/.env from disk and (re)creates the parsely-api-secrets
# Kubernetes Secret in the parsely namespace. Run once after creating the
# cluster, and again whenever your .env values change.
#
# Only sensitive keys go into the Secret — non-secret config lives in
# configmap.yaml so it can be committed to git.

param(
    [string]$EnvFile = (Join-Path $PSScriptRoot ".." ".env"),
    [string]$Namespace = "parsely",
    [string]$SecretName = "parsely-api-secrets"
)

if (-not (Test-Path -LiteralPath $EnvFile)) {
    Write-Error "Env file not found: $EnvFile"
    exit 1
}

$envData = @{}
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
        $key = $matches[1]
        $value = $matches[2]
        if ($value -match '^"(.*)"$' -or $value -match "^'(.*)'$") {
            $value = $matches[1]
        }
        $envData[$key] = $value
    }
}

$required = @('DATABASE_URL', 'AZURE_DI_KEY', 'AZURE_BLOB_CONNECTION_STRING')
foreach ($key in $required) {
    if (-not $envData.ContainsKey($key)) {
        Write-Error "Missing required key in ${EnvFile}: $key"
        exit 1
    }
}

# Recreate to keep this idempotent. --ignore-not-found means first-run
# doesn't error.
kubectl delete secret $SecretName --namespace $Namespace --ignore-not-found

kubectl create secret generic $SecretName `
    --namespace $Namespace `
    --from-literal="DATABASE_URL=$($envData['DATABASE_URL'])" `
    --from-literal="AZURE_DI_KEY=$($envData['AZURE_DI_KEY'])" `
    --from-literal="AZURE_BLOB_CONNECTION_STRING=$($envData['AZURE_BLOB_CONNECTION_STRING'])"
