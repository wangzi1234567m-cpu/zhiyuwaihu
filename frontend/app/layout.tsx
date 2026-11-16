import type { Metadata } from 'next';
import './style.css';
export const metadata: Metadata = {title: '智语外呼 · 文本联调', description: '合成商户资质告知演示'};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
