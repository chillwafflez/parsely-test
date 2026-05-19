# (Re)creates a docker-registry Kubernetes Secret in the parsely namespace
# using a fresh ECR auth token. Run once after creating the cluster, and
# again every 12 hours (when the token expires) during active use.
#
# Why a script: ECR tokens expire every 12 hours, so the Secret is not
# something you "set and forget" — it has to be refreshed on a cadence.
# In production on EKS, IRSA (IAM Roles for Service Accounts) makes this
# automatic; on kind we do it by hand.

param(
    [string]$Region = "us-east-1",
    [string]$Namespace = "parsely",
    [string]$SecretName = "parsely-ecr-creds"
)

# Resolve account ID from the current AWS identity so we don't have to
# hard-code it anywhere.
$accountId = (aws sts get-caller-identity --query Account --output text).Trim()
if (-not $accountId) {
    Write-Error "Could not resolve AWS account ID. Did you run 'aws configure'?"
    exit 1
}

$registry = "$accountId.dkr.ecr.$Region.amazonaws.com"
$password = aws ecr get-login-password --region $Region

if (-not $password) {
    Write-Error "Could not retrieve ECR login password."
    exit 1
}

# Delete-then-create makes the script idempotent. The token inside the
# Secret expires, so re-running this is the routine path, not an edge case.
kubectl delete secret $SecretName --namespace $Namespace --ignore-not-found

kubectl create secret docker-registry $SecretName `
    --namespace $Namespace `
    --docker-server=$registry `
    --docker-username=AWS `
    --docker-password=$password
