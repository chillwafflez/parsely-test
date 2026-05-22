# Sensitive inputs sourced from env vars (TF_VAR_<name>).
# Example:
#   $env:TF_VAR_justin_password = "..."
#   tofu plan

variable "justin_password" {
  description = "Initial password for the seeded Keycloak user 'justin'. Set via TF_VAR_justin_password."
  type        = string
  sensitive   = true
}

variable "neon_org_id" {
  description = "Neon organization ID"
  type        = string
}

variable "neon_project_id" {
  description = "Existing Neon project ID to import"
  type        = string
}

variable "keycloak_parsely_client_uuid" {
  description = "Internal UUID of the parsely-web client (NOT the client_id string)."
  type        = string
}

variable "aws_region" {
  description = "AWS region for ECR."
  type        = string
  default     = "us-east-1"
}
