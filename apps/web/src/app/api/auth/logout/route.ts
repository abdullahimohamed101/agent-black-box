import { logoutRoute } from "@/server/authRoutes";

export const dynamic = "force-dynamic";

// POST only: a GET has side effects nowhere (a cross-site link cannot sign anyone out).
export const POST = logoutRoute;
