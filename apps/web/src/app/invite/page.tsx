import { InviteAccept } from "@/components/InviteAccept";

export const dynamic = "force-dynamic";

export default function InvitePage() {
  return (
    <main>
      <h1>Join a workspace</h1>
      <InviteAccept />
    </main>
  );
}
