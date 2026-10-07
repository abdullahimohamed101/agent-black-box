import Link from "next/link";
import { ApiHealth } from "@/components/ApiHealth";

export default function Home() {
  return (
    <main>
      <h1>Agent Black Box</h1>
      <p className="muted">Flight recorder for AI agents. </p>
      <p>
        <Link href="/w/default/projects/all">Open the dashboard →</Link>
      </p>
      <ApiHealth />
    </main>
  );
}
