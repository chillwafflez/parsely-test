# Creates the two Kubernetes Secrets the Keycloak Helm release expects:
#   - keycloak-db    (Neon DB credentials)
#   - keycloak-admin (initial Keycloak admin credentials)
#
# Both are prompted for as SecureStrings so they never appear in shell
# history. Re-run any time to rotate; follow with `kubectl rollout
# restart deployment/keycloak-keycloakx -n parsely` to pick up changes.

param(
    [string]$Namespace = "parsely",
    [string]$DbSecretName = "keycloak-db",
    [string]$AdminSecretName = "keycloak-admin",
    [string]$DbUsername = "neondb_owner",
    [string]$AdminUsername = "admin"
)

function Read-SecretAsPlain {
    param([string]$Prompt)
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    return [System.Net.NetworkCredential]::new("", $secure).Password
}

$dbPass = Read-SecretAsPlain "Neon DB password for ${DbUsername}"
if (-not $dbPass) {
    Write-Error "DB password cannot be empty."
    exit 1
}

$adminPass = Read-SecretAsPlain "Keycloak admin password to create"
if ($adminPass.Length -lt 8) {
    Write-Error "Keycloak admin password must be at least 8 characters."
    exit 1
}

# Delete-then-create makes each call idempotent.
kubectl delete secret $DbSecretName --namespace $Namespace --ignore-not-found
kubectl create secret generic $DbSecretName `
    --namespace $Namespace `
    --from-literal="username=$DbUsername" `
    --from-literal="password=$dbPass"

kubectl delete secret $AdminSecretName --namespace $Namespace --ignore-not-found
kubectl create secret generic $AdminSecretName `
    --namespace $Namespace `
    --from-literal="username=$AdminUsername" `
    --from-literal="password=$adminPass"
