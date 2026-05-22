terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    neon = {
      source  = "kislerdm/neon"
      version = "~> 0.3"
    }
    keycloak = {
      source  = "keycloak/keycloak"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Credentials come from env vars:
#   NEON_API_KEY
provider "neon" {}

# Credentials come from env vars:
#   KEYCLOAK_URL
#   KEYCLOAK_CLIENT_ID
#   KEYCLOAK_CLIENT_SECRET
provider "keycloak" {}
