// Re-exports the Auth.js HTTP handlers under the
// /api/auth/* route. Handles /api/auth/signin, /signout, /callback/*,
// /session, /providers, /csrf, etc. — everything NextAuth needs.
import { handlers } from "@/auth";

export const { GET, POST } = handlers;
