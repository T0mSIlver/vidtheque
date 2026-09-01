// The app's API client. `server-only` makes importing this from a Client
// Component a build error: the Python host is server configuration, and the
// browser only ever talks to this Next server.
import "server-only";
import { createClient } from "./client";

export { ApiError } from "./client";
export * from "./schemas";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set; see web/.env.example`);
  return value;
}

// Read lazily, at first use, so importing the module never depends on the
// environment (tests, typegen, and `next build` all import it).
let instance: ReturnType<typeof createClient> | undefined;

export function api() {
  instance ??= createClient({
    baseUrl: required("VIDTHEQUE_API_URL"),
    clientIpHeader: process.env.VIDTHEQUE_CLIENT_IP_HEADER,
  });
  return instance;
}
