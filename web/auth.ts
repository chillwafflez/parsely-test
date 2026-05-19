import NextAuth from "next-auth";
import Keycloak from "next-auth/providers/keycloak";

// Auth.js stores the Keycloak access_token on the OAuth "account" object
// the first time the user signs in. We squirrel it onto the JWT (the
// encrypted session cookie) and then expose it on the session object so
// client components can attach it as a Bearer header when calling the
// FastAPI backend.
declare module "next-auth" {
  interface Session {
    accessToken?: string;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string;
  }
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    // Reads AUTH_KEYCLOAK_ID, AUTH_KEYCLOAK_SECRET, AUTH_KEYCLOAK_ISSUER
    // from env automatically — no need to repeat them here.
    Keycloak,
  ],
  callbacks: {
    async jwt({ token, account }) {
      // `account` is only populated on the initial sign-in. Capture
      // the Keycloak access_token here so we still have it on later
      // session reads.
      if (account?.access_token) {
        token.accessToken = account.access_token;
      }
      return token;
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken;
      return session;
    },
  },
});
