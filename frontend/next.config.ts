import type { NextConfig } from 'next';

// 静态验收导出：仅供 test/browser.qa.json 一类的浏览器检查在**不启动 dev server** 时
// 加载真实页面产物。未设置 STATIC_ACCEPTANCE 时，构建行为与本仓库默认（生产）路径完全一致。
const staticAcceptance = process.env.STATIC_ACCEPTANCE === '1';

const config: NextConfig = {
  // Rewritten SSE must flush immediately; compression buffers short event frames.
  compress: false,
  output: staticAcceptance ? 'export' : process.env.CONTAINER_BUILD === '1' ? 'standalone' : undefined,
  // 静态验收用相对资源前缀，浏览器可从文件目录直接加载页面及其 JS/CSS。
  ...(staticAcceptance ? {assetPrefix: '.', images: {unoptimized: true as const}} : {}),
  async rewrites() {
    // export 模式不支持 rewrites；验收态没有后端，页面走显式错误态（不伪造成功）。
    if (staticAcceptance) return [];
    return [{source: '/api/:path*', destination: `${process.env.BACKEND_URL ?? 'http://127.0.0.1:8000'}/api/:path*`}];
  },
};
export default config;
