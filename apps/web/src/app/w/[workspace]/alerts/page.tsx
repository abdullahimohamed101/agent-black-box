import { redirect } from "next/navigation";

/**
 * Alerts moved under Settings (design pass 2 step 5c). The old address keeps working: it redirects to the Alerts tab and keeps
 * every query parameter, `?tab=` included, so a saved link or an email that points here still opens the same view.
 */
export default async function AlertsRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ workspace: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { workspace } = await params;
  const sp = await searchParams;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(sp))
    for (const one of Array.isArray(v) ? v : v === undefined ? [] : [v]) qs.append(k, one);
  const q = qs.toString();
  redirect(`/w/${workspace}/settings/alerts${q ? `?${q}` : ""}`);
}
