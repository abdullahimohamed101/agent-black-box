import { SessionsSettings } from "@/components/SessionsSettings";

// A person's own sessions: every role has them, so no permission gates the page (the API scopes it to the caller).
export default function Page() {
  return <SessionsSettings />;
}
