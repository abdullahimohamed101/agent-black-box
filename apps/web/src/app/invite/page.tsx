import { InviteAccept } from "@/components/InviteAccept";
import { Brand } from "@/components/shell/Brand";
import "../login.css";

export const dynamic = "force-dynamic";

export default function InvitePage() {
  return (
    <main id="main" className="auth auth-page">
      <Brand />
      <h1>Join a workspace</h1>
      <InviteAccept />
    </main>
  );
}
