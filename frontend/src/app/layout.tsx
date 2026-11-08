
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ASEA | Autonomous Software Engineering Agent",
  description:
    "Autonomous software engineering platform for repository analysis, bug detection, and validated code repair.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" data-theme="dark">
      <body>{children}</body>
    </html>
  );
}
