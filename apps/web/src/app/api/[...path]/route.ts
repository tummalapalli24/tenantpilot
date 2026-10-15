/**
 * Same-origin proxy: the browser always calls /api/*, and this route forwards to the
 * FastAPI service. API_INTERNAL_URL is read at request time, so one image works in every
 * environment. In Kubernetes the Ingress routes /api straight to the API service, so
 * this handler is only used by `next dev` and docker compose.
 */
const API = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
const FORWARD_REQUEST = ["authorization", "x-tenant-id", "content-type", "x-request-id", "accept"];
const FORWARD_RESPONSE = ["content-type", "x-request-id", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining"];

async function proxy(request: Request, ctx: RouteContext<"/api/[...path]">): Promise<Response> {
  const { path } = await ctx.params;
  const url = new URL(request.url);
  const target = `${API}/api/${path.map(encodeURIComponent).join("/")}${url.search}`;
  const headers = new Headers();
  for (const name of FORWARD_REQUEST) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasBody = !["GET", "HEAD"].includes(request.method);
  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // Required by Node's fetch when streaming a request body.
      ...(hasBody ? { duplex: "half" } : {}),
      cache: "no-store",
    } as RequestInit);
    const responseHeaders = new Headers();
    for (const name of FORWARD_RESPONSE) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return Response.json({ detail: `API unreachable at ${API}` }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
