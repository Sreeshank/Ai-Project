import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Campus Compass | Student information & support",
  description: "Find campus answers backed by approved documents.",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
