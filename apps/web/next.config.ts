import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // infra/docker/web.Dockerfile copies .next/standalone into the runtime
  // image (a self-contained server.js with only the deps that route
  // actually needs, not the full node_modules) - without this option Next
  // never produces that directory, and the Docker build fails at
  // "COPY --from=builder /app/.next/standalone /app" (found on a real
  // deployment: "failed to calculate checksum ... not found").
  output: "standalone",

  // "/" must land on the command center. Doing this with redirect() inside
  // app/page.tsx did NOT produce a real HTTP redirect on this Next version -
  // the server answered 200 with an RSC payload that navigates client-side
  // (verified against the live deployment: GET / -> 200, body carrying the
  // /command-center hop). That makes the entry point depend on JS and costs
  // an extra round trip. A config-level redirect is resolved by the server
  // before any React work, so the browser gets a genuine 307.
  async redirects() {
    return [{ source: "/", destination: "/command-center", permanent: false }];
  },
};

export default nextConfig;
