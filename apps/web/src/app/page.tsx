import { ApiHealth } from "@/components/ApiHealth";

export default function Home() {
  return (
    <main>
      <h1>Agent Black Box</h1>
      <p className="muted">Flight recorder for AI agents. Foundation shell (Phase 0).</p>
      <ApiHealth />
    </main>
  );
}
