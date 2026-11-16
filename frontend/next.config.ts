import type { NextConfig } from 'next';

const config: NextConfig = {
  // Rewritten SSE must flush immediately; compression buffers short event frames.
  compress: false,
  output: process.env.CONTAINER_BUILD === '1' ? 'standalone' : undefined,
  async rewrites() {
    return [{source: '/api/:path*', destination: `${process.env.BACKEND_URL ?? 'http://127.0.0.1:8000'}/api/:path*`}];
  },
};
export default config;
