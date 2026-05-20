/**
 * Server-side proxy for the FastAPI `GET /api/documents/{id}/file`
 * endpoint.
 *
 * Why this exists: the FastAPI route is now JWT-protected, but the
 * browser fetches /file via <embed> / <iframe> src attributes, which
 * can't attach a custom Authorization header. Instead of opening the
 * FastAPI route up, we proxy the request through Next.js — the browser
 * sends its NextAuth session cookie (automatic, same-origin), the
 * Route Handler reads the access_token from the session server-side,
 * and re-issues the request to FastAPI with a Bearer header.
 *
 * The access token never leaves the Next.js server. This is the
 * standard BFF (Backend-for-Frontend) pattern for proxying binary
 * content from a token-protected upstream.
 */

import { auth } from "@/auth";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:5180";

// Headers we forward from the upstream FastAPI response back to the
// browser. Anything else (e.g. hop-by-hop headers, server identification)
// stays on the server.
const PASSTHROUGH_HEADERS = [
  "content-type",
  "content-length",
  "content-disposition",
  "accept-ranges",
  "content-range",
  "etag",
  "last-modified",
];

export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const session = await auth();
  if (!session?.accessToken) {
    return new Response("Unauthorized", { status: 401 });
  }

  const { id } = await params;

  // Forward the browser's Range header so PDF viewers can request byte
  // ranges (progressive loading on large files).
  const upstreamHeaders = new Headers({
    Authorization: `Bearer ${session.accessToken}`,
  });
  const range = req.headers.get("range");
  if (range) upstreamHeaders.set("Range", range);

  const upstream = await fetch(`${API_BASE}/api/documents/${id}/file`, {
    headers: upstreamHeaders,
    cache: "no-store",
  });

  // Build the response, preserving the upstream status (200 vs 206
  // Partial Content) and the headers the browser needs to render the
  // PDF inline.
  const responseHeaders = new Headers();
  for (const name of PASSTHROUGH_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}
