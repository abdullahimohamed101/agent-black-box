import { InviteAccept } from "@/components/InviteAccept";
import { Brand } from "@/components/shell/Brand";
import { auth } from "@/copy/auth";
import "../login.css";

export const dynamic = "force-dynamic";

export default function InvitePage() {
  return (
    <main id="main" className="auth-page">
      <Brand />
      <h1>{auth.invite.title}</h1>
      <InviteAccept />
    </main>
  );
}
