import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "MovieShazam — identification locale de scènes",
  description: "Retrouver le film et l'instant d'un extrait vidéo, entièrement sur votre PC.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body className="min-h-screen">
        <div className="flex min-h-screen flex-col md:flex-row">
          <Sidebar />
          <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
            <div className="mx-auto max-w-7xl">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
