import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // infra/docker/web.Dockerfile copies .next/standalone into the runtime
  // image (a self-contained server.js with only the deps that route
  // actually needs, not the full node_modules) - without this option Next
  // never produces that directory, and the Docker build fails at
  // "COPY --from=builder /app/.next/standalone /app" (found on a real
  // deployment: "failed to calculate checksum ... not found").
  output: "standalone",
};

export default nextConfig;
