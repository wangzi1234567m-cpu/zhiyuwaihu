import type { Metadata } from 'next';
import './workbench.css';
export const metadata: Metadata = {title: '智语外呼 · 资审关单工作台', description: '商户资质审核告知与业务处理工作台'};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
