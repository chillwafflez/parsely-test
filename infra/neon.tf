# project resource
resource "neon_project" "parsely_neon_project" {
  name       = "parsely"
  pg_version = 17
  org_id     = var.neon_org_id
  region_id  = "aws-us-west-2"
  history_retention_seconds = 21600 # free accounts have maximum retention window of 6 hours (21600 seconds)

  # configure default branch settings (optional)
  branch {
    name          = "production"
    database_name = "neondb"    # initial database created on startup; the one im using for parsely storage
    role_name     = "neondb_owner"
  }

  # configure default endpoint settings (optional)
#   default_endpoint_settings {
#     autoscaling_limit_min_cu = 0.25
#     autoscaling_limit_max_cu = 1.0
    # suspend_timeout_seconds  = 300
#   }
}

# create an admin role for prod branch
resource "neon_role" "admin_user" {
  project_id  = neon_project.parsely_neon_project.id
  branch_id   = neon_project.parsely_neon_project.default_branch_id
  name        = "admin_user"
}

# create keycloak database on production branch
resource "neon_database" "keycloak" {
  project_id = neon_project.parsely_neon_project.id
  branch_id  = neon_project.parsely_neon_project.default_branch_id
  name       = "keycloak"
  owner_name = "neondb_owner"
}

# create a development branch resource off of production branch of neon project
resource "neon_branch" "dev_branch" {
  project_id = neon_project.parsely_neon_project.id
  name       = "development"
  parent_id  = neon_project.parsely_neon_project.default_branch_id
}

# create an endpoint for the development branch
resource "neon_endpoint" "dev_endpoint" {
	project_id = neon_project.parsely_neon_project.id
	branch_id  = neon_branch.dev_branch.id
	type       = "read_write"

	autoscaling_limit_min_cu = 0.25
	autoscaling_limit_max_cu = 1.0
}