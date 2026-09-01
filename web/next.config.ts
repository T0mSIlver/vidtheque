import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Explicit caching: data is cached where a function says `"use cache"` and
  // names a lifetime; anything read at request time must sit inside a
  // <Suspense> boundary, and the build fails otherwise.
  cacheComponents: true,
};

export default nextConfig;
