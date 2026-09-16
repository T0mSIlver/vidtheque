// The app's API client. `server-only`: the Python host is server
// configuration; the browser calls `/api/*` on its own origin.
import "server-only";
import { createClient } from "./client";

export { ApiError } from "./client";
export * from "./schemas";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set; see web/.env.example`);
  return value;
}

// Lazy, so importing the module never needs the environment.
let instance: ReturnType<typeof createClient> | undefined;

export function api() {
  instance ??= createClient({
    baseUrl: required("VIDTHEQUE_API_URL"),
    clientIpHeader: process.env.VIDTHEQUE_CLIENT_IP_HEADER,
  });
  return instance;
}
