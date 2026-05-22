# adopt the existing 'parsely' realm into Tofu's state
import {
  to = keycloak_realm.parsely_keycloak
  id = "parsely"
}

# adopt the existing 'parsely-web' client. The id is "<realm>/<client_uuid>"
import {
  to = keycloak_openid_client.openid_client
  id = "parsely/${var.keycloak_parsely_client_uuid}"
}

# create keycloak realm
resource "keycloak_realm" "parsely_keycloak" {
  realm             = "parsely"
  enabled           = true
  display_name      = "Parsely"

  # login_theme = "base"

  access_code_lifespan = "1m"

  ssl_required    = "external"
  password_policy = "upperCase(1) and length(8) and forceExpiredPasswordChange(365) and notUsername"
  default_signature_algorithm    = "RS256"

  internationalization {
    supported_locales = [
      "en",
      "de",
      "es"
    ]
    default_locale    = "en"
  }

  security_defenses {
    headers {
      x_frame_options                     = "DENY"
      content_security_policy             = "frame-src 'self'; frame-ancestors 'self'; object-src 'none';"
      content_security_policy_report_only = ""
      x_content_type_options              = "nosniff"
      x_robots_tag                        = "none"
      x_xss_protection                    = "1; mode=block"
      strict_transport_security           = "max-age=31536000; includeSubDomains"
    }
    brute_force_detection {
      permanent_lockout                 = false
      max_login_failures                = 30
      wait_increment_seconds            = 60
      quick_login_check_milli_seconds   = 1000
      minimum_quick_login_wait_seconds  = 60
      max_failure_wait_seconds          = 900
      failure_reset_time_seconds        = 43200
    }
  }
}

# create parsely client
resource "keycloak_openid_client" "openid_client" {
	realm_id  = keycloak_realm.parsely_keycloak.id
	client_id = "parsely-web"

	name    = "Parsely Web"
	enabled = true

	access_type           = "CONFIDENTIAL"
	standard_flow_enabled = true
	valid_redirect_uris = [
		"http://localhost:3000/api/auth/callback/keycloak"  # point to our Next.js app (NextAuth + Keycloak provider)
	]

	# Required so the browser can call Keycloak's token endpoint from the Next.js origin.
	web_origins = [
		"http://localhost:3000"
	]

	login_theme = "keycloak"
}


# create default user
resource "keycloak_user" "user" {
  realm_id = keycloak_realm.parsely_keycloak.id
  username = "justin"
  enabled  = true

  email      = "justin@example.com"
  first_name = "Justin"
  last_name  = "Time"

  # Only applied on creation; updates here have no effect after the first apply.
  initial_password {
    value     = var.justin_password
    temporary = false
  }
}
