import { InviteAccept } from "@/components/InviteAccept";
import { Brand } from "@/components/shell/Brand";

export const dynamic = "force-dynamic";

export default function InvitePage() {
  return (
    <main className="auth">
      <Brand />
      <h1>Join a workspace</h1>
      <InviteAccept />
    </main>
  );
}
